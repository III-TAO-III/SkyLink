import os
import asyncio
import requests
import json
import logging
import queue
import threading
import time
import hashlib
from utils import calculate_hash, filter_event_fields
from config import CURRENT_SESSION, EDDN_REQUIRED_EVENTS  # <-- ИСПОЛЬЗУЕМ ГЛОБАЛЬНЫЙ СПИСОК

# Глобальный регистр ошибок авторизации (хранится в оперативной памяти)
FAILED_ACCOUNTS = set()

# Офлайн-очередь: таймаут жизни пакета и пауза между переотправками
OFFLINE_QUEUE_TIMEOUT_SEC = 120   # 2 минуты — затем пакет удаляется
OFFLINE_RETRY_PAUSE_SEC = 10      # пауза между попытками отправки

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class Sender(threading.Thread):
    def __init__(self, cache_path, config):
        super().__init__(daemon=True)
        self.cache_path = cache_path
        self.config = config
        self.event_queue = queue.Queue()
        self.offline_queue = queue.Queue()
        self.hashes = {}
        self.load_hashes()
        self.stop_event = threading.Event()
        self.status_callback = None

    def load_hashes(self):
        """Loads hashes from the cache file or creates it if it doesn't exist."""
        # При установке/переустановке установщик оставляет маркер — обнуляем кэш для полной переотправки пакетов
        marker = self.cache_path.parent / ".clear_dedup_cache"
        if marker.exists():
            try:
                if self.cache_path.exists():
                    self.cache_path.unlink()
                marker.unlink()
                logging.info("Deduplication cache cleared after install/reinstall.")
            except OSError as e:
                logging.warning("Could not clear dedup cache marker: %s", e)
        if self.cache_path.exists():
            try:
                with open(self.cache_path, 'r') as f:
                    # Handle empty file case
                    content = f.read()
                    if not content:
                        self.hashes = {}
                    else:
                        self.hashes = json.loads(content)
                logging.info(f"Deduplication cache loaded from: {os.path.abspath(self.cache_path)}")
            except (json.JSONDecodeError, IOError) as e:
                logging.error(f"Failed to load deduplication cache: {e}")
                self.hashes = {}
        else:
            logging.info("Cache file not found. Creating a new one.")
            self.hashes = {}
            self.save_hashes()

    def save_hashes(self):
        """Saves hashes to the cache file."""
        abs_path = os.path.abspath(self.cache_path)
        logging.info(f"💾 Saving cache to: {abs_path}")
        try:
            with open(self.cache_path, 'w') as f:
                json.dump(self.hashes, f, indent=2)
        except IOError as e:
            logging.error(f"Failed to save deduplication cache: {e}")

    def set_status_callback(self, callback):
        """Sets a callback function to be called on status changes."""
        self.status_callback = callback

    def update_status(self, status, message):
        """Updates the application status via the callback."""
        if self.status_callback:
            self.status_callback(status, message)
        logging.info(f"Status: {status} - {message}")

    @staticmethod
    def _find_key_insensitive(target_name, accounts_dict):
        """Finds API key by commander name (case-insensitive). Returns key or None."""
        if target_name is None:
            return None
        if target_name in accounts_dict:
            return accounts_dict[target_name]
        target_lower = target_name.lower()
        for name, key in accounts_dict.items():
            if name.lower() == target_lower:
                return key
        return None

    def _resolve_api_key(self, commander_name):
        """Resolves API key for the given commander (session, then accounts, then disk). Returns key or None."""
        api_key = CURRENT_SESSION.get("api_key")
        if api_key:
            return api_key
        api_key = self._find_key_insensitive(commander_name, self.config.accounts)
        if api_key:
            return api_key
        self.config.load_accounts()
        api_key = self._find_key_insensitive(commander_name, self.config.accounts)
        if api_key:
            CURRENT_SESSION["api_key"] = api_key
            logging.info(f"🔑 Key loaded from disk for: {commander_name}")
        return api_key

    @staticmethod
    def purge_commander_cache(commander_name, cache_path):
        """Removes all cache entries for a given commander."""
        if not cache_path.exists():
            return

        try:
            with open(cache_path, 'r') as f:
                content = f.read()
                if not content:
                    hashes = {}
                else:
                    hashes = json.loads(content)
        except (IOError, json.JSONDecodeError):
            return

        keys_to_delete = [key for key in hashes if key.startswith(f"{commander_name}|")]
        
        if not keys_to_delete:
            return

        for key in keys_to_delete:
            del hashes[key]

        try:
            with open(cache_path, 'w') as f:
                json.dump(hashes, f, indent=2)
            logging.info(f"Cache purged for commander: {commander_name}")
        except IOError:
            logging.error(f"Failed to save purged cache for commander: {commander_name}")

    def queue_event(self, event):
        """Adds an event to the processing queue."""
        self.event_queue.put(event)

    def run(self):
        """Processes the event queue and sends data to the API."""
        while not self.stop_event.is_set():
            try:
                event = self.event_queue.get(timeout=1)
                self.process_event(event)
            except queue.Empty:
                self.retry_offline_queue()
                continue

    def stop(self):
        """Stops the sender thread."""
        self.stop_event.set()

    def process_event(self, event):
        """Processes a single event: schema update, filtering, and deduplication."""
        event_type = event.get('event')
        if not event_type:
            return

        # --- GLOBAL FILTER: Игнорируем SquadronCarrier ---
        if event.get('CarrierType') == 'SquadronCarrier':
            return
        # -------------------------------------------------

        # 1. Update schema before doing anything else
        self.config.update_field_schema(event_type, event)
        
        # 2. Filter the event based on field rules
        field_rules = self.config.field_rules.get("filters", {}).get(event_type, {})
        filtered_event = filter_event_fields(event, field_rules)
        
        commander_name = CURRENT_SESSION.get("commander", "Unknown")
        api_key = self._resolve_api_key(commander_name)
        
        # 3. Handle deduplication based on event rules (hash only when commander has API key)
        rule = self.config.event_rules.get(event_type)
        cache_key = None
        if rule and rule.get('deduplicate'):
            cache_key = f"{commander_name}|{event_type}"
            if api_key:
                # Create a copy for hashing, excluding volatile fields
                content_to_hash = filtered_event.copy()
                content_to_hash.pop("timestamp", None)
                content_to_hash.pop("event", None)
                content_str = f"{commander_name}|{json.dumps(content_to_hash, sort_keys=True)}"
                event_hash = hashlib.sha256(content_str.encode('utf-8')).hexdigest()
                if self.hashes.get(cache_key) == event_hash:
                    logging.info(f"Skipping duplicate event for {commander_name}: {event_type}")
                    return
                self.hashes[cache_key] = event_hash
                # save_hashes() after send — on success persist, on failure remove hash

        # 4. EDDN: if this event type goes to EDDN, send it and mark eddnsent on payload for portal
        # ИСПОЛЬЗУЕМ ГЛОБАЛЬНЫЙ СПИСОК ИЗ CONFIG
        eddn_ok = False
        if event_type in EDDN_REQUIRED_EVENTS:
            try:
                # Импорт внутри метода, чтобы избежать круговых зависимостей и проблем при инициализации
                from src.services.eddn_sender import send_to_eddn
                eddn_ok = asyncio.run(send_to_eddn(event, game_state=CURRENT_SESSION))
            except Exception as e:
                logging.warning("EDDN send failed: %s", e)
                eddn_ok = False
        
        # Ставим флаг только если событие было отправлено (True/False). 
        # Если событие не для EDDN, флаг не ставится (или можно ставить None/False по желанию)
        if event_type in EDDN_REQUIRED_EVENTS:
            filtered_event["eddnsent"] = eddn_ok

        # 5. Send the filtered event; при ошибке сети/сервера кладём в офлайн-очередь с меткой времени
        success, queue_on_failure = self._send_to_api(filtered_event)
        
        if not success and cache_key is not None and cache_key in self.hashes:
            self.hashes.pop(cache_key)
            self.save_hashes()
        elif success and cache_key is not None and cache_key in self.hashes:
            self.save_hashes()
        
        if not success and queue_on_failure:
            self.offline_queue.put((filtered_event, time.time()))

    def _log_event_details(self, event):
        """Logs detailed information for specific events."""
        event_type = event.get('event')
        if event_type == 'Location':
            docked_status = "Docked: True" if event.get('Docked', False) else "Docked: False"
            logging.info(f"[>] Location: {event.get('StarSystem', 'N/A')} ({docked_status})")
        elif event_type == 'Loadout':
            jump_range = event.get('MaxJumpRange', 0)
            logging.info(f"[>] Loadout: {event.get('Ship', 'N/A')} (Jump: {jump_range:.2f} ly)")
        elif event_type == 'Materials':
            raw_count = len(event.get('Raw', []))
            encoded_count = len(event.get('Encoded', []))
            logging.info(f"[>] Materials: Updated (Raw: {raw_count}, Encoded: {encoded_count})")
        else:
            logging.info(f"Successfully sent event: {event_type}")

    def _send_to_api(self, event):
        """Sends a single event to the API. Returns (success, queue_on_failure)."""
        cmdr_name = CURRENT_SESSION.get("commander") or "Unknown"
        api_key = CURRENT_SESSION.get("api_key")

        if not api_key:
            api_key = self._find_key_insensitive(cmdr_name, self.config.accounts)
        if not api_key:
            self.config.load_accounts()
            api_key = self._find_key_insensitive(cmdr_name, self.config.accounts)
            if api_key:
                CURRENT_SESSION["api_key"] = api_key
                logging.info(f"🔑 Key loaded from disk for: {cmdr_name}")

        # 3. Если и теперь нет — сдаемся (не ставим в очередь)
        if not api_key:
            logging.warning(f"Cannot send event: No active API Key for commander {cmdr_name}")
            return (False, False)

        if not self.config.API_URL:
            logging.error("API URL is not configured. Cannot send event.")
            return (False, False)

        headers = {
            'Content-Type': 'application/json',
            'User-Agent': self.config.USER_AGENT,
            'x-api-key': api_key,
            'x-commander': cmdr_name
        }

        try:
            response = requests.post(self.config.API_URL, headers=headers, json=event, timeout=10)
            
            # --- 1. УСПЕШНАЯ ОТПРАВКА (200 OK) ---
            if response.status_code == 200:
                self._log_event_details(event)
                
                # [НОВОЕ] Логика Желтого статуса
                event_type = event.get('event')
                if event_type == 'Shutdown':
                    logging.info("🛑 Game Shutdown detected. Switching to standby.")
                    self.update_status('Waiting', 'Game closed. Waiting for Commander...')
                else:
                    event_type = event.get('event', 'Event')
                    self.update_status('Running', f'Event {event_type} sent')

                # Раз успех — убираем из черного списка
                FAILED_ACCOUNTS.discard(cmdr_name)
                return (True, False)

            # --- 2. ОШИБКА АВТОРИЗАЦИИ (Красный) — в очередь не ставим
            elif response.status_code in [401, 403]:
                logging.error(f"⛔ Auth failed for {cmdr_name} (Status: {response.status_code})")
                FAILED_ACCOUNTS.add(cmdr_name)
                self.update_status('Error', f'Auth Error {response.status_code} for {cmdr_name}')
                return (False, False)

            # --- 3. ОШИБКА СЕРВЕРА — ставим в офлайн-очередь
            else:
                logging.error(f"Failed to send event: {response.status_code} - {response.text}")
                self.update_status('Error', 'Failed to send event, queuing.')
                return (False, True)

        # --- 4. ОШИБКА СЕТИ — ставим в офлайн-очередь
        except requests.RequestException as e:
            logging.error(f"Network error while sending event: {e}")
            self.update_status('Error', 'Network error, queuing event.')
            return (False, True)

    def retry_offline_queue(self):
        """Tries to send events from the offline queue. Пакеты старше 2 минут удаляются."""
        if self.offline_queue.empty():
            return
        logging.info(f"Retrying {self.offline_queue.qsize()} events from the offline queue.")
        while not self.offline_queue.empty():
            item = self.offline_queue.get()
            if isinstance(item, tuple):
                event, first_queued = item
            else:
                event, first_queued = item, time.time()
            if time.time() - first_queued > OFFLINE_QUEUE_TIMEOUT_SEC:
                logging.warning(f"Dropping event {event.get('event', '?')} after {OFFLINE_QUEUE_TIMEOUT_SEC}s timeout.")
                continue
            success, queue_on_failure = self._send_to_api(event)
            if not success and queue_on_failure:
                self.offline_queue.put((event, first_queued))
            if self.offline_queue.qsize() > 0:
                time.sleep(OFFLINE_RETRY_PAUSE_SEC)
            else:
                self.update_status('Running', 'Offline queue cleared.')