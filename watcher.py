import logging
import time
import os
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from utils import parse_json_line
from config import Config, CURRENT_SESSION
from sender import Sender

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class JournalWatcher:
    def __init__(self, journal_dir, sender_instance, config):
        self.journal_dir = Path(journal_dir)
        self.sender = sender_instance
        self.config = config
        self.latest_log_file = None
        self.last_file_position = 0
        self.observer = Observer()

    def find_latest_log_file(self):
        """Finds the most recently modified journal log file."""
        log_files = list(self.journal_dir.glob('Journal.*.log'))
        if not log_files:
            logging.warning("No journal files found in the specified directory.")
            return None
        
        latest_file = max(log_files, key=lambda f: f.stat().st_mtime)
        logging.info(f"Monitoring latest journal file: {latest_file}")
        return latest_file

    def process_new_lines(self):
        """Reads new lines from the latest log file and processes them."""
        if self.latest_log_file and self.latest_log_file.exists():
            with open(self.latest_log_file, 'r', encoding='utf-8') as f:
                f.seek(self.last_file_position)
                new_lines = f.readlines()
                self.last_file_position = f.tell()
                
                for line in new_lines:
                    self.process_line(line)

    def process_line(self, line):
        """Parses a line and processes the event based on defined rules."""
        event_data = parse_json_line(line)
        if not event_data or 'event' not in event_data:
            return

        event_type = event_data['event']

        # --- Session Switching Logic ---
        if event_type in ["Commander", "LoadGame"]:
            commander_name = event_data.get("Name") or event_data.get("Commander")
            if commander_name:
                self.update_session(commander_name)
        
        rule = self.config.event_rules.get(event_type)
        
        if not rule:
            self.config.register_new_event(event_type)
            rule = self.config.event_rules.get(event_type)

        action = self.config.default_action
        if rule:
            action = rule.get('action', action)

        if action == 'send':
            logging.info(f"Processing event: {event_type}")
            self.sender.queue_event(event_data)
        else:
            logging.debug(f"Ignoring event based on rule or default action: {event_type}")

    def update_session(self, commander_name):
        """Updates the current session based on the detected commander."""
        if CURRENT_SESSION["commander"] == commander_name:
            return # No change

        CURRENT_SESSION["commander"] = commander_name
        api_key = self.config.accounts.get(commander_name)
        
        if api_key:
            CURRENT_SESSION["api_key"] = api_key
            logging.info(f"🚀 Switched session to Commander: {commander_name}")
        else:
            CURRENT_SESSION["api_key"] = None
            logging.warning(f"🚨 No API Key found for Commander: {commander_name}. Events will not be sent.")

    def start(self):
        """Starts the journal watcher."""
        self.latest_log_file = self.find_latest_log_file()
        if self.latest_log_file:
            # Go to the end of the file on start to only process new events
            self.last_file_position = self.latest_log_file.stat().st_size
        
        event_handler = JournalFileHandler(self)
        self.observer.schedule(event_handler, str(self.journal_dir), recursive=False)
        self.observer.start()
        logging.info(f"Started watching directory: {self.journal_dir}")

    def stop(self):
        """Stops the journal watcher."""
        self.observer.stop()
        self.observer.join()
        logging.info("Journal watcher stopped.")

class JournalFileHandler(FileSystemEventHandler):
    def __init__(self, watcher):
        self.watcher = watcher

    def on_modified(self, event):
        """Called when a file or directory is modified."""
        if not event.is_directory and Path(event.src_path) == self.watcher.latest_log_file:
            self.watcher.process_new_lines()

    def on_created(self, event):
        """Called when a file or directory is created."""
        if not event.is_directory and 'Journal' in Path(event.src_path).name:
            logging.info(f"New journal file detected: {event.src_path}")
            self.watcher.latest_log_file = Path(event.src_path)
            self.watcher.last_file_position = 0
            self.watcher.process_new_lines()

if __name__ == '__main__':
    # For testing purposes
    # Create a dummy journal directory and file
    dummy_dir = Path('./dummy_journal')
    dummy_dir.mkdir(exist_ok=True)
    
    # You would replace this with your actual journal path from config
    journal_path = str(dummy_dir)
    
    # Dummy sender
    class DummySender:
        def send_event(self, event):
            print(f"Dummy Sender received event: {event}")

    sender = DummySender()
    watcher = JournalWatcher(journal_dir=journal_path, sender_instance=sender)
    watcher.start()

    try:
        logging.info("Watcher is running. Appending to dummy log file...")
        # Simulate game writing to the log file
        log_file = watcher.find_latest_log_file()
        if not log_file:
            log_file = dummy_dir / f"Journal.{time.strftime('%Y%m%d%H%M%S')}.01.log"
        
        with open(log_file, 'a', encoding='utf-8') as f:
            f.write('{"event": "LoadGame", "Commander": "Test"}\\n')
        time.sleep(2)
        with open(log_file, 'a', encoding='utf-8') as f:
            f.write('{"event": "Music", "MusicTrack": "NoTrack"}\\n') # This should be ignored
        time.sleep(2)
        with open(log_file, 'a', encoding='utf-8') as f:
            f.write('{"event": "FSDJump", "StarSystem": "Sol"}\\n')
        time.sleep(5)

    finally:
        watcher.stop()
        # Clean up dummy files
        for item in dummy_dir.iterdir():
            item.unlink()
        dummy_dir.rmdir()
        logging.info("Watcher test finished.")
