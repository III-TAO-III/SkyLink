import os
import requests
import json
import logging
import queue
import threading
import time
import hashlib
from utils import calculate_hash, filter_event_fields
from config import CURRENT_SESSION

# Глобальный регистр ошибок авторизации (хранится в оперативной памяти)
FAILED_ACCOUNTS = set()

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
        # Это защищает нас от CarrierLocation, CarrierJump и любых других событий,
        # связанных с общими флотоносцами эскадрильи.
        if event.get('CarrierType') == 'SquadronCarrier':
            return
        # -------------------------------------------------

        # 1. Update schema before doing anything else
        self.config.update_field_schema(event_type, event)
        
        # 2. Filter the event based on field rules
        field_rules = self.config.field_rules.get("filters", {}).get(event_type, {})
        filtered_event = filter_event_fields(event, field_rules)
        
        # 3. Handle deduplication based on event rules
        rule = self.config.event_rules.get(event_type)
        if rule and rule.get('deduplicate'):
            commander_name = CURRENT_SESSION.get("commander", "Unknown")
            
            # Create a composite key for the cache
            cache_key = f"{commander_name}|{event_type}"
            
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
            self.save_hashes()

        # 4. Send the filtered event
        self._send_to_api(filtered_event)

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
        """Sends a single event to the API with dynamic, session-based headers."""
        # Берем имя текущего пилота ИЗ СЕССИИ. Это самый надежный источник.
        cmdr_name = CURRENT_SESSION.get("commander", "Unknown")

        # --- НАЧАЛО ИЗМЕНЕНИЙ (Умный поиск ключа) ---
        api_key = CURRENT_SESSION.get("api_key")

        # 1. Если ключа нет в сессии, ищем в памяти конфига
        if not api_key:
            api_key = self.config.accounts.get(cmdr_name)

        # 2. ФИНАЛЬНЫЙ АРГУМЕНТ: Если ключа всё еще нет — возможно, конфиг устарел?
        # Читаем файл с диска прямо сейчас!
        if not api_key:
            # logging.info(f"Key not found in memory for {cmdr_name}, checking disk...") # Можно раскомментировать для отладки
            self.config.load_accounts() # <--- ПРИНУДИТЕЛЬНАЯ ПЕРЕЗАГРУЗКА
            api_key = self.config.accounts.get(cmdr_name)

            # Если нашли после перезагрузки — обновляем сессию
            if api_key:
                CURRENT_SESSION["api_key"] = api_key
                logging.info(f"🔑 Key loaded from disk for: {cmdr_name}")

        # 3. Если и теперь нет — значит, ключа реально нет
        if not api_key:
            logging.warning(f"Cannot send event: No active API Key for commander {cmdr_name}")
            return

        if not self.config.API_URL:
            logging.error("API URL is not configured. Cannot send event.")
            return

        headers = {
            'Content-Type': 'application/json',
            'User-Agent': self.config.USER_AGENT,
            'x-api-key': api_key,    # <--- ИСПОЛЬЗУЕМ НАЙДЕННЫЙ ПЕРЕМЕННУЮ api_key
            'x-commander': cmdr_name
        }

        try:
            response = requests.post(self.config.API_URL, headers=headers, json=event, timeout=10)
            
            # --- ЛОГИКА СТАТУСОВ ---
            if response.status_code == 200:
                self._log_event_details(event)
                self.update_status('Running', 'Event sent successfully.')
                # Если все ок — убираем из черного списка (вдруг починили)
                FAILED_ACCOUNTS.discard(cmdr_name)

            elif response.status_code in [401, 403]:
                # 401/403 = Ключ неверный. Баним визуально.
                logging.error(f"⛔ Auth failed for {cmdr_name} (Status: {response.status_code})")
                FAILED_ACCOUNTS.add(cmdr_name)
                self.update_status('Error', f'Auth Error {response.status_code} for {cmdr_name}')

            else:
                # Другие ошибки (500 и т.д.)
                logging.error(f"Failed to send event: {response.status_code} - {response.text}")
                self.offline_queue.put(event)
                self.update_status('Error', 'Failed to send event, queuing.')

        except requests.RequestException as e:
            logging.error(f"Network error while sending event: {e}")
            self.offline_queue.put(event)
            self.update_status('Error', 'Network error, queuing event.')

    def retry_offline_queue(self):
        """Tries to send events from the offline queue."""
        if not self.offline_queue.empty():
            logging.info(f"Retrying {self.offline_queue.qsize()} events from the offline queue.")
            while not self.offline_queue.empty():
                event = self.offline_queue.get()
                self._send_to_api(event)
                if self.offline_queue.qsize() > 0:
                    time.sleep(2)  # Wait a bit before retrying the next one
                else:
                    self.update_status('Running', 'Offline queue cleared.')

