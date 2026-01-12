import logging
import time
from config import Config, UI_STATE
from sender import Sender
from watcher import JournalWatcher

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Global Instances ---
config = Config()
sender = None
watcher = None

def update_ui_state(status, message):
    """Callback to update the global UI state from background threads."""
    UI_STATE["status"] = message or status
    
    st_lower = status.lower()
    if "running" in st_lower or "sent" in st_lower or "monitoring" in st_lower:
        UI_STATE["color"] = "green"
    elif "error" in st_lower or "failed" in st_lower or "invalid" in st_lower:
        UI_STATE["color"] = "red"
    else:
        UI_STATE["color"] = "gray"

def start_background_service():
    """Initializes and starts the background services."""
    global sender, watcher

    logging.info("🚀 Starting SkyLink background service...")

    cache_file = config.app_data_dir / 'deduplication_cache.json'
    sender = Sender(cache_path=cache_file, config=config)
    sender.set_status_callback(update_ui_state)  # Wire the callback
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
    logging.info("🛑 Stopping SkyLink background service...")
    if watcher:
        watcher.stop()
    if sender:
        sender.stop()
        sender.join()
    logging.info("✅ Background services stopped.")

if __name__ == '__main__':
    start_background_service()
