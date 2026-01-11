import os
import json
import logging
import re
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
APPDATA_DIR = Path(os.getenv('APPDATA')) / 'SkyLink'
APPDATA_DIR.mkdir(parents=True, exist_ok=True)
ACCOUNTS_FILE = APPDATA_DIR / 'accounts.json'
CURRENT_SESSION = {"commander": None, "api_key": None}

def save_events_compact(data, filepath):
    """Saves the events.json file with compact formatting for leaf objects."""
    json_str = json.dumps(data, indent=2)
    
    # Regex to find simple objects and collapse them
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
        self.app_data_dir = Path(os.getenv('APPDATA')) / 'SkyLink'
        self.app_data_dir.mkdir(parents=True, exist_ok=True)
        self.event_rules = {}
        self.field_rules = {}
        self.default_action = 'send'  # Default fallback
        self.accounts = {}
        self.load_event_rules()
        self.load_field_rules()
        self.load_accounts()

        # --- Expose env vars through the instance ---
        self.API_URL = API_URL
        self.USER_AGENT = USER_AGENT
        
        # --- Journal Path ---
        self.journal_path = self.get_default_journal_dir()
        if not self.journal_path:
            logging.error("Could not find Elite Dangerous journal directory.")

    def load_accounts(self):
        """Loads commander accounts from accounts.json."""
        logging.info(f"📂 Loading accounts from: {ACCOUNTS_FILE}")
        if not ACCOUNTS_FILE.exists():
            logging.warning(f"⚠ Accounts file not found at {ACCOUNTS_FILE}. Please create it.")
            self._save_json(ACCOUNTS_FILE, {"accounts": {"YourCommanderName": "your_api_key_here"}})

        try:
            with open(ACCOUNTS_FILE, 'r') as f:
                self.accounts = json.load(f).get("accounts", {})
        except (IOError, json.JSONDecodeError) as e:
            logging.error(f"Failed to load or parse accounts.json: {e}")
            self.accounts = {}

    def save_account(self, commander_name, api_key):
        """Updates and saves an account to accounts.json."""
        self.accounts[commander_name] = api_key
        self._save_json(ACCOUNTS_FILE, {"accounts": self.accounts})



    def load_field_rules(self):
        """Loads the field filter rules from fields.json."""
        fields_file = Path('./fields.json')
        if not fields_file.exists():
            logging.warning("fields.json not found, creating a new one.")
            self._save_json(fields_file, {"filters": {}})
        
        try:
            with open(fields_file, 'r') as f:
                self.field_rules = json.load(f)
        except (IOError, json.JSONDecodeError) as e:
            logging.error(f"Failed to load or parse fields.json: {e}")
            self.field_rules = {"filters": {}}

    def update_field_schema(self, event_type, event_data):
        """Updates the schema in fields.json for a given event type."""
        fields_file = Path('./fields.json')
        if event_type not in self.field_rules.get("filters", {}):
            self.field_rules.setdefault("filters", {})[event_type] = {}

        current_fields = self.field_rules["filters"][event_type]
        updated = False
        for key in event_data.keys():
            if key not in current_fields:
                current_fields[key] = True
                updated = True
                logging.info(f"✨ New field discovered for {event_type}: {key}")

        if updated:
            self._save_json(fields_file, self.field_rules)

    def _save_json(self, filepath, data):
        """Helper to save data to a JSON file."""
        try:
            with open(filepath, 'w') as f:
                json.dump(data, f, indent=2)
        except IOError as e:
            logging.error(f"Failed to save JSON to {filepath}: {e}")

    def load_event_rules(self):
        """Loads and flattens the event configuration from events.json."""
        event_config_file = Path('./events.json')
        if not event_config_file.exists():
            logging.warning("events.json not found, creating a default one.")
            self.create_default_event_config(event_config_file)

        try:
            with open(event_config_file, 'r') as f:
                config_data = json.load(f)
            self.flatten_event_rules(config_data)
            logging.info("Event rules loaded and flattened successfully.")
        except (json.JSONDecodeError, IOError) as e:
            logging.error(f"Failed to load or parse events.json: {e}")
            self.event_rules = {}

    def flatten_event_rules(self, config_data):
        """Flattens the categorized event rules and sets the default action."""
        self.event_rules = {}
        # Get the default action from settings, fallback to 'send'
        self.default_action = config_data.get("settings", {}).get("default_action", "send")
        
        for category, events in config_data.get("categories", {}).items():
            for event_name, rule in events.items():
                self.event_rules[event_name] = rule
                
    def register_new_event(self, event_type):
        """Adds a new event to the __New_Discovery__ category in events.json."""
        if event_type in self.event_rules:
            return  # Event already known

        event_config_file = Path('./events.json')
        try:
            with open(event_config_file, 'r') as f:
                config_data = json.load(f)

            if "__New_Discovery__" not in config_data["categories"]:
                config_data["categories"]["__New_Discovery__"] = {}

            new_rule = {"action": "send", "comment": "Auto-detected"}
            config_data["categories"]["__New_Discovery__"][event_type] = new_rule
            
            save_events_compact(config_data, event_config_file)

            # Update in-memory rules
            self.event_rules[event_type] = new_rule
            logging.info(f"🆕 New event detected and added to config: {event_type}")

        except (IOError, json.JSONDecodeError) as e:
            logging.error(f"Failed to register new event type {event_type}: {e}")

    def create_default_event_config(self, file_path):
        """Creates a default events.json file with categorized rules."""
        default_config = {
          "settings": {
            "default_action": "send",
            "ignore_older_than_seconds": 60
          },
          "categories": {
            "Status": {
              "Commander": { "action": "send" }, "LoadGame": { "action": "send" }, "Rank": { "action": "send", "deduplicate": True },
              "Progress": { "action": "send", "deduplicate": True }, "Reputation": { "action": "send", "deduplicate": True },
              "EngineerProgress": { "action": "send", "deduplicate": True }, "Statistics": { "action": "send" },
              "SquadronStartup": { "action": "send" }, "Powerplay": { "action": "send" }
            },
            "Ship": {
              "Loadout": { "action": "send", "deduplicate": True }, "Materials": { "action": "send", "deduplicate": True },
              "Cargo": { "action": "send", "deduplicate": True }, "ShipLocker": { "action": "send", "deduplicate": True },
              "SuitLoadout": { "action": "send" }
            },
            "Travel": {
              "Location": { "action": "send" }, "FSDJump": { "action": "send" }, "Docked": { "action": "send" },
              "Undocked": { "action": "send" }, "CarrierJump": { "action": "send" }, "CarrierLocation": { "action": "send" }
            },
            "Missions": {
              "MissionAccepted": { "action": "send" }, "MissionCompleted": { "action": "send" }, "MissionFailed": { "action": "send" }
            },
            "Combat": {
              "Died": { "action": "send" }
            },
            "Ignored": {
              "Music": { "action": "ignore" }, "ReceiveText": { "action": "ignore" }, "FuelScoop": { "action": "ignore" },
              "FSSSignalDiscovered": { "action": "ignore" }, "Friends": { "action": "ignore" }, "Fileheader": { "action": "ignore" },
              "ReservoirReplenished": { "action": "ignore" }
            }
          }
        }
        try:
            save_events_compact(default_config, file_path)
            self.flatten_event_rules(default_config)
            logging.info(f"Created default {file_path}")
        except IOError as e:
            logging.error(f"Failed to create default event config: {e}")




    def get_default_journal_dir(self):
        """Finds the default Elite Dangerous journal directory."""
        saved_games_path = Path.home() / 'Saved Games' / 'Frontier Developments' / 'Elite Dangerous'
        if saved_games_path.exists():
            logging.info(f"Found Elite Dangerous journal directory: {saved_games_path}")
            return str(saved_games_path)
        else:
            logging.warning("Could not find default Elite Dangerous journal directory.")
            return None

