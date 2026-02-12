import hashlib
import json
import logging

import requests


def calculate_hash(data, exclude_keys=None):
    """Calculates a SHA-256 hash of the given data, optionally excluding keys."""
    if not isinstance(data, dict):
        logging.error("Invalid data type for hashing. Expected a dictionary.")
        return None

    # Create a copy to avoid modifying the original dictionary
    data_to_hash = data.copy()

    if exclude_keys:
        for key in exclude_keys:
            data_to_hash.pop(key, None)

    # Sort the dictionary to ensure consistent hash results
    data_string = json.dumps(data_to_hash, sort_keys=True)

    return hashlib.sha256(data_string.encode("utf-8")).hexdigest()


def filter_event_fields(event_data, field_rules):
    """Filters event data based on a dictionary of boolean field rules."""
    if not isinstance(event_data, dict) or not isinstance(field_rules, dict):
        return event_data  # Return original data if inputs are invalid

    filtered_data = {}

    # Always keep 'event' and 'timestamp'
    if "event" in event_data:
        filtered_data["event"] = event_data["event"]
    if "timestamp" in event_data:
        filtered_data["timestamp"] = event_data["timestamp"]

    for key, value in event_data.items():
        if key in filtered_data:
            continue  # Already added

        # Add key if it's in the rules and the rule is True
        if field_rules.get(key, False):
            filtered_data[key] = value

    return filtered_data


def parse_json_line(line):
    """Parses a JSON string from a line of text."""
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        # This can happen if a line is not a valid JSON object, which might be the case for some file entries
        logging.debug(f"Could not decode JSON from line: {line.strip()}")
        return None


if __name__ == "__main__":
    # For testing purposes
    test_data1 = {"event": "Loadout", "timestamp": "2023-01-01T12:00:00Z", "Ship": "Cobra MkIII"}
    test_data2 = {"event": "Loadout", "timestamp": "2023-01-01T13:00:00Z", "Ship": "Cobra MkIII"}
    test_data3 = {"event": "Loadout", "timestamp": "2023-01-01T12:00:00Z", "Ship": "Anaconda"}

    # Test without excluding keys
    hash1_no_exclude = calculate_hash(test_data1)
    hash2_no_exclude = calculate_hash(test_data2)
    print(
        f"Without excluding timestamp, hashes are different: {hash1_no_exclude != hash2_no_exclude}"
    )

    # Test with excluding timestamp
    hash1_excluded = calculate_hash(test_data1, exclude_keys=["timestamp"])
    hash2_excluded = calculate_hash(test_data2, exclude_keys=["timestamp"])
    hash3_excluded = calculate_hash(test_data3, exclude_keys=["timestamp"])

    print(f"Hash 1 (excluded): {hash1_excluded}")
    print(f"Hash 2 (excluded): {hash2_excluded}")
    print(f"Hash 3 (excluded): {hash3_excluded}")

    print(f"With timestamp excluded, hashes 1 and 2 are equal: {hash1_excluded == hash2_excluded}")
    print(
        f"With timestamp excluded, hashes 1 and 3 are different: {hash1_excluded != hash3_excluded}"
    )

    json_line = '{"event": "Test", "data": "some_data"}'
    parsed_json = parse_json_line(json_line)
    print(f"Parsed JSON: {parsed_json}")


def verify_api_key(api_key, api_url):
    """
    Verifies the API key with the server and returns the associated Commander Name.
    Returns: (is_valid, commander_name_or_error)
    """
    if not api_key or not api_url:
        return False, "Missing API Key or URL"

    # Формируем URL для проверки (добавляем /verify к базовому API URL)
    # Если базовый URL заканчивается на /skylink, то получится /skylink/verify
    # Убираем слеш в конце base_url, если он есть, чтобы не было //verify
    base_url = api_url.rstrip("/")
    verify_url = f"{base_url}/verify"

    try:
        response = requests.get(verify_url, headers={"x-api-key": api_key}, timeout=5)

        if response.status_code == 200:
            data = response.json()
            if data.get("valid"):
                return True, data.get("commander")
            else:
                return False, "Key is invalid (Server rejected)"
        elif response.status_code == 401:
            return False, "Invalid API Key"
        else:
            return False, f"Server Error: {response.status_code}"

    except Exception as e:
        logging.error(f"Verification failed: {e}")
        return False, "Connection Error"
