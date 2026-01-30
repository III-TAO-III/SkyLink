import logging
import time
from config import Config, UI_STATE
from sender import Sender
from watcher import JournalWatcher

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Global Instances ---
# Убираем жесткое создание Config() здесь. 
# Пусть config будет None, пока мы его не инициализируем.
config = None 
sender = None
watcher = None

def update_ui_state(status, message):
    """Callback to update the global UI state from background threads."""
    UI_STATE["status"] = message or status

    # При 401/403 запрашиваем автооткрытие окна из трея (GUI обработает в update_ui_loop)
    msg = (message or "").lower()
    if status and status.lower() == "error" and "auth error" in msg and ("401" in (message or "") or "403" in (message or "")):
        UI_STATE["request_show_window"] = True

    st_lower = status.lower()
    if "running" in st_lower or "sent" in st_lower or "monitoring" in st_lower:
        UI_STATE["color"] = "green"
    elif "error" in st_lower or "failed" in st_lower or "invalid" in st_lower:
        UI_STATE["color"] = "red"
    else:
        UI_STATE["color"] = "gray"

# --- ИЗМЕНЕНИЕ: Добавляем аргумент shared_config ---
def start_background_service(shared_config=None):
    """Initializes and starts the background services."""
    global sender, watcher, config

    logging.info("🚀 Starting SkyLink background service...")

    # Если нам передали конфиг из GUI — используем его.
    # Если нет (запустили main.py отдельно) — создаем новый.
    if shared_config:
        config = shared_config
    else:
        config = Config()

    cache_file = config.app_data_dir / 'deduplication_cache.json'
    
    # Теперь Sender использует ТОТ ЖЕ config, что и GUI
    sender = Sender(cache_path=cache_file, config=config)
    sender.set_status_callback(update_ui_state)
    sender.start()

    if config.journal_path:
        watcher = JournalWatcher(journal_dir=config.journal_path, sender_instance=sender, config=config)
        watcher.start()
        logging.info("👀 Journal watcher started.")
    else:
        logging.error("Could not find the Elite Dangerous journal directory. Watcher not started.")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        stop_background_service()

def stop_background_service():
    """Stops the background services gracefully."""
    global watcher, sender 

    logging.info("🛑 Stopping SkyLink background service...")
    
    if watcher:
        watcher.stop()
    if sender:
        sender.stop()
        
    if sender:
        sender.join(timeout=1.0) 
        
    logging.info("✅ Background services stopped (or forced).")

if __name__ == '__main__':
    start_background_service()