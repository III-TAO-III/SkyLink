import os
import sys
import json
import logging
from pathlib import Path
from dotenv import load_dotenv
from logging.handlers import RotatingFileHandler

# Load environment variables from .env file
load_dotenv()

# --- Constants ---
# Жестко прописанный адрес для тех, у кого нет .env (твоего друга)
DEFAULT_API_URL = "https://skybioml.net/api/telemetry/skylink"
DEFAULT_HEARTBEAT_URL = "https://skybioml.net/api/system/skylinkbeat"

# Если в системе нет переменной (как в EXE), берем адрес выше
API_URL = os.getenv("SKYLINK_API_URL", DEFAULT_API_URL)
HEARTBEAT_URL = os.getenv("SKYLINK_HEARTBEAT_URL", DEFAULT_HEARTBEAT_URL)

USER_AGENT = "SkyLink-Client/1.0"

# --- Paths ---
# User data is stored in AppData (Windows standard)
APPDATA_DIR = Path(os.getenv('APPDATA')) / 'SkyLink'
APPDATA_DIR.mkdir(parents=True, exist_ok=True) # Создаем папку

ACCOUNTS_FILE = APPDATA_DIR / 'accounts.json'
DISCOVERY_FILE = APPDATA_DIR / 'discovery.json'
LOG_FILE = APPDATA_DIR / 'skylink_client.log' # <-- Новый файл для логов

# --- Configure Logging ---
# Настраиваем логгер здесь, ПОСЛЕ создания путей.
# Мы создаем список "хендлеров" — куда сливать информацию.
log_handlers = [
    logging.StreamHandler(sys.stdout) # 1. Консоль (как было раньше)
]

# 2. Файл (добавляем всегда, чтобы и в Dev, и в EXE можно было почитать историю)
try:
    log_handlers.append(RotatingFileHandler(LOG_FILE, maxBytes=5*1024*1024, backupCount=2, encoding='utf-8'))
except Exception as e:
    print(f"Warning: Could not set up file logging: {e}")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=log_handlers # Применяем оба канала
)

# --- Globals ---
# Global session state
CURRENT_SESSION = {"commander": None, "api_key": None}
# Global state for GUI (to avoid circular imports)
UI_STATE = {
    "status": "WAITING",
    "color": "gray",
    "commander": None,
    "auth_required": False
}


def get_resource_path(relative_path):
    """
    Get the absolute path to a resource, works for both development and
    PyInstaller-packed executables.
    """
    if hasattr(sys, '_MEIPASS'):
        # Running in a PyInstaller bundle
        base_path = sys._MEIPASS
    else:
        # Running in a normal Python environment
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)


class Config:
    def __init__(self):
        self.app_data_dir = APPDATA_DIR
        self.accounts_file = ACCOUNTS_FILE
        self.discovery_file = DISCOVERY_FILE
        
        self.event_rules = {}
        self.field_rules = {}
        self.accounts = {}
        self.discovered_fields = {} # In-memory cache for new fields
        self.default_action = 'send'
        
        # Load configurations
        self.load_internal_rules()
        self.load_accounts()
        self.load_discovered_fields()

        # --- Expose env vars through the instance ---
        self.API_URL = API_URL
        self.HEARTBEAT_URL = HEARTBEAT_URL
        self.USER_AGENT = USER_AGENT
        
        # --- Journal Path Discovery ---
        self.journal_path = self.get_saved_games_path()
        if not self.journal_path:
            logging.error("Could not find Elite Dangerous journal directory.")
        else:
            logging.info(f"📂 Journal directory detected: {self.journal_path}")

    def get_saved_games_path(self):
        """
        Uses standard paths to find the 'Saved Games' folder location.
        Handles default and OneDrive locations.
        """
        try:
            # Standard path
            path = Path.home() / 'Saved Games' / 'Frontier Developments' / 'Elite Dangerous'
            if path.exists():
                return str(path)

            # Check OneDrive path
            onedrive_path = Path.home() / 'OneDrive' / 'Saved Games' / 'Frontier Developments' / 'Elite Dangerous'
            if onedrive_path.exists():
                return str(onedrive_path)
                
            return None
            
        except Exception as e:
            logging.error(f"Error detecting Saved Games path: {e}")
            return None

    def load_accounts(self):
        """Loads commander accounts from accounts.json."""
        logging.info(f"📂 Loading accounts from: {self.accounts_file}")
        if not self.accounts_file.exists():
            logging.warning(f"⚠ Accounts file not found at {self.accounts_file}. Creating empty registry.")
            self._save_json(self.accounts_file, {"accounts": {}})
            self.accounts = {}
            return

        try:
            with open(self.accounts_file, 'r', encoding='utf-8') as f:
                # Handle empty file case
                content = f.read()
                if not content:
                    data = {}
                else:
                    data = json.loads(content)
                self.accounts = data.get("accounts", {})
        except (IOError, json.JSONDecodeError) as e:
            logging.error(f"Failed to load accounts.json: {e}")
            self.accounts = {}

    def save_account(self, commander_name, api_key):
        """Updates and saves an account to accounts.json."""
        self.accounts[commander_name] = api_key
        CURRENT_SESSION["api_key"] = api_key
        self._save_json(self.accounts_file, {"accounts": self.accounts})
        logging.info(f"✅ API Key saved for commander: {commander_name}")

    def delete_account(self, commander_name):
        """Deletes an account from accounts.json."""
        if commander_name in self.accounts:
            del self.accounts[commander_name]
            self._save_json(self.accounts_file, {"accounts": self.accounts})
            logging.info(f"🗑️ Account deleted for commander: {commander_name}")

    def _save_json(self, filepath, data):
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
        except IOError as e:
            logging.error(f"Failed to save JSON to {filepath}: {e}")

    def load_internal_rules(self):
        """
        Loads event rules from the internal `events.json` file.
        This file is read-only and part of the application bundle.
        """
        internal_events_path = get_resource_path('events.json')
        logging.info(f"📂 Loading internal rules from: {internal_events_path}")
        try:
            with open(internal_events_path, 'r', encoding='utf-8') as f:
                config_data = json.load(f)
            self.flatten_event_rules(config_data)
        except FileNotFoundError:
            logging.error(f"CRITICAL: Internal 'events.json' not found at {internal_events_path}.")
            self.event_rules = {}
            self.field_rules = {}
        except json.JSONDecodeError as e:
            logging.error(f"CRITICAL: Failed to parse internal 'events.json': {e}")
            self.event_rules = {}
            self.field_rules = {}

    def flatten_event_rules(self, config_data):
        """Flattens the hierarchical rule structure into efficient lookup dictionaries."""
        self.event_rules = {}
        self.field_rules = {"filters": {}}
        self.default_action = config_data.get("settings", {}).get("default_action", "send")
        
        for category, events in config_data.get("categories", {}).items():
            for event_name, rule in events.items():
                self.event_rules[event_name] = {
                    "action": rule.get("action", "send"),
                    "deduplicate": rule.get("deduplicate", False)
                }
                self.field_rules["filters"][event_name] = {
                    key: value for key, value in rule.items()
                    if key not in ["action", "deduplicate", "comment"]
                }

    def load_discovered_fields(self):
        """Loads the discovery log from discovery.json."""
        if self.discovery_file.exists():
            try:
                with open(self.discovery_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                    if not content:
                        self.discovered_fields = {}
                    else:
                        self.discovered_fields = json.loads(content)
            except (IOError, json.JSONDecodeError) as e:
                logging.error(f"Could not load discovery file: {e}")
                self.discovered_fields = {}

    def update_field_schema(self, event_type, event_data):
        """
        Logs new, unknown fields to `discovery.json` for analysis.
        Does NOT modify the active rule set.
        """
        # Get the known fields for this event from the internal rules
        known_fields_with_metadata = self.field_rules.get("filters", {}).get(event_type, {})
        known_field_names = known_fields_with_metadata.keys()
        
        # Get fields already discovered for this event
        if event_type not in self.discovered_fields:
            self.discovered_fields[event_type] = []

        discovered_in_session = self.discovered_fields[event_type]
        
        updated = False
        for key in event_data.keys():
            # A field is new if it's not in the internal rules AND not already discovered
            if key not in known_field_names and key not in discovered_in_session:
                self.discovered_fields[event_type].append(key)
                updated = True
                logging.info(f"✨ New field discovered for '{event_type}': {key}. Logged to discovery.json.")
        
        if updated:
            self._save_json(self.discovery_file, self.discovered_fields)
