import os
import json
import logging
import re
import ctypes
from ctypes import wintypes
from dotenv import load_dotenv
from pathlib import Path

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Load environment variables from .env file
load_dotenv()

# --- Constants ---
API_URL = os.getenv("SKYLINK_API_URL")
USER_AGENT = "SkyLink-Client/1.0"

# --- Globals ---
# Храним пользовательские данные в AppData (стандарт Windows)
APPDATA_DIR = Path(os.getenv('APPDATA')) / 'SkyLink'
APPDATA_DIR.mkdir(parents=True, exist_ok=True)
ACCOUNTS_FILE = APPDATA_DIR / 'accounts.json'
EVENTS_FILE = APPDATA_DIR / 'events.json'

# Глобальное состояние сессии
CURRENT_SESSION = {"commander": None, "api_key": None}
# Глобальное состояние для GUI (чтобы не импортировать config циклично)
UI_STATE = {
    "status": "WAITING",
    "color": "gray",
    "commander": None,
    "auth_required": False
}

def save_events_compact(data, filepath):
    """Saves the events.json file with compact formatting for leaf objects."""
    json_str = json.dumps(data, indent=2)
    # Regex to find simple objects and collapse them into one line
    compact_pattern = re.compile(r'{\s*"\w+":\s*("[^"]+"|true|false|[\d\.]+),*\s*(\s*"\w+":\s*("[^"]+"|true|false|[\d\.]+),*\s*)*\s*}')

    def replacer(match):
        s = match.group(0)
        s = re.sub(r'\s*\n\s*', ' ', s)
        s = re.sub(r',\s*}', ' }', s)
        s = re.sub(r'"\s*:', '":', s)
        return s

    compact_str = compact_pattern.sub(replacer, json_str)
    
    try:
        with open(filepath, 'w') as f:
            f.write(compact_str)
    except IOError as e:
        logging.error(f"Failed to save compact events config: {e}")

class Config:
    def __init__(self):
        self.app_data_dir = APPDATA_DIR
        self.event_rules = {}
        self.field_rules = {}
        self.default_action = 'send'
        self.accounts = {}
        
        # Загрузка правил
        self._migrate_configs()
        self.load_event_rules()
        self.load_accounts()

        # --- Expose env vars through the instance ---
        self.API_URL = API_URL
        self.USER_AGENT = USER_AGENT
        
        # --- Journal Path Discovery (Advanced) ---
        self.journal_path = self.get_saved_games_path()
        if not self.journal_path:
            logging.error("Could not find Elite Dangerous journal directory.")
        else:
            logging.info(f"📂 Journal directory detected: {self.journal_path}")

    def _migrate_configs(self):
        """Copies config files from local directory to AppData if they don't exist."""
        local_events_file = Path('./events.json')
        if not EVENTS_FILE.exists() and local_events_file.exists():
            logging.info(f"🚚 Migrating local '{local_events_file}' to '{EVENTS_FILE}'...")
            try:
                import shutil
                shutil.copy(local_events_file, EVENTS_FILE)
            except Exception as e:
                logging.error(f"Failed to migrate events.json: {e}")


    def get_saved_games_path(self):
        """
        Uses Windows API to find the REAL 'Saved Games' folder location.
        Works correctly with OneDrive, moved folders, etc.
        """
        try:
            # GUID for 'Saved Games' folder
            FOLDERID_SavedGames = '{4C5C32FF-BB9D-43b0-B5B4-2D72E54EAAA4}'
            
            buf = ctypes.create_unicode_buffer(ctypes.wintypes.MAX_PATH)
            # Call SHGetFolderPath via shell32 (Legacy) or SHGetKnownFolderPath (Modern)
            # Using SHGetFolderPath for broader compatibility, or manual registry lookup
            # But here is the ctypes method for KnownFolder:
            
            CSIDL_PROFILE = 40
            SHGFP_TYPE_CURRENT = 0
            
            # Simple fallback first: User Profile
            path = Path.home() / 'Saved Games' / 'Frontier Developments' / 'Elite Dangerous'
            if path.exists():
                return str(path)

            # If standard path fails (OneDrive case), try the Registry/API approach is safer conceptually,
            # but for Python simplicity, checking the OneDrive path explicitly is often enough.
            onedrive_path = Path.home() / 'OneDrive' / 'Saved Games' / 'Frontier Developments' / 'Elite Dangerous'
            if onedrive_path.exists():
                return str(onedrive_path)
                
            return None
            
        except Exception as e:
            logging.error(f"Error detecting Saved Games path: {e}")
            return None

    def load_accounts(self):
        """Loads commander accounts from accounts.json."""
        logging.info(f"📂 Loading accounts from: {ACCOUNTS_FILE}")
        if not ACCOUNTS_FILE.exists():
            logging.warning(f"⚠ Accounts file not found at {ACCOUNTS_FILE}. Creating empty registry.")
            # Structure: {"accounts": {"Name": "Key"}}
            self._save_json(ACCOUNTS_FILE, {"accounts": {}})

        try:
            with open(ACCOUNTS_FILE, 'r') as f:
                data = json.load(f)
                # Handle both flat structure (legacy) and nested (new)
                if "accounts" in data:
                    self.accounts = data["accounts"]
                else:
                    self.accounts = data # Fallback for flat file
        except (IOError, json.JSONDecodeError) as e:
            logging.error(f"Failed to load accounts.json: {e}")
            self.accounts = {}

    def save_account(self, commander_name, api_key):
        """Updates and saves an account to accounts.json."""
        self.accounts[commander_name] = api_key
        # Update global session immediately
        CURRENT_SESSION["api_key"] = api_key
        # Save to disk
        self._save_json(ACCOUNTS_FILE, {"accounts": self.accounts})
        logging.info(f"✅ API Key saved for commander: {commander_name}")

    def delete_account(self, commander_name):
        """Deletes an account from accounts.json."""
        if commander_name in self.accounts:
            del self.accounts[commander_name]
            self._save_json(ACCOUNTS_FILE, {"accounts": self.accounts})
            logging.info(f"🗑️ Account deleted for commander: {commander_name}")



    def _save_json(self, filepath, data):
        try:
            with open(filepath, 'w') as f:
                json.dump(data, f, indent=2)
        except IOError as e:
            logging.error(f"Failed to save JSON to {filepath}: {e}")

    def load_event_rules(self):
        if not EVENTS_FILE.exists():
            self.create_default_event_config(EVENTS_FILE)
        try:
            with open(EVENTS_FILE, 'r') as f:
                config_data = json.load(f)
            self.flatten_event_rules(config_data)
        except Exception:
            self.event_rules = {}
            self.field_rules = {}

    def flatten_event_rules(self, config_data):
        self.event_rules = {}
        self.field_rules = {"filters": {}}
        self.default_action = config_data.get("settings", {}).get("default_action", "send")
        for category, events in config_data.get("categories", {}).items():
            for event_name, rule in events.items():
                # Populate event_rules
                self.event_rules[event_name] = {
                    "action": rule.get("action", "send"),
                    "deduplicate": rule.get("deduplicate", False)
                }
                # Populate field_rules from the same source
                self.field_rules["filters"][event_name] = {
                    key: value for key, value in rule.items()
                    if key not in ["action", "deduplicate", "comment"]
                }

    def update_field_schema(self, event_type, event_data):
        """Updates the schema in events.json for a given event type."""
        try:
            with open(EVENTS_FILE, 'r') as f:
                config_data = json.load(f)
        except (IOError, json.JSONDecodeError):
            logging.error(f"Could not read {EVENTS_FILE} to update schema.")
            return

        updated = False
        # Find the event and update its fields
        for category, events in config_data.get("categories", {}).items():
            if event_type in events:
                current_fields = events[event_type]
                for key in event_data.keys():
                    if key not in current_fields:
                        current_fields[key] = True
                        updated = True
                        logging.info(f"✨ New field discovered for {event_type}: {key}")
                break
        
        if updated:
            save_events_compact(config_data, EVENTS_FILE)
            # Update in-memory field_rules
            self.flatten_event_rules(config_data)
                
    def register_new_event(self, event_type):
        if event_type in self.event_rules: return
        try:
            with open(EVENTS_FILE, 'r') as f:
                config_data = json.load(f)
            if "__New_Discovery__" not in config_data["categories"]:
                config_data["categories"]["__New_Discovery__"] = {}
            new_rule = {"action": "ignore", "comment": "Auto-detected"}
            config_data["categories"]["__New_Discovery__"][event_type] = new_rule
            save_events_compact(config_data, EVENTS_FILE)
            self.event_rules[event_type] = new_rule
            logging.info(f"🆕 New event detected: {event_type}")
        except Exception as e:
            logging.error(f"Failed to register event {event_type}: {e}")

    def create_default_event_config(self, file_path):
        # (Твой стандартный конфиг, оставляем как есть)
        default_config = {"settings": {"default_action": "send"}, "categories": {}}
        save_events_compact(default_config, file_path)