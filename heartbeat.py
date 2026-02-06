"""
HeartbeatService: periodic "skylinkbeat" signals for all configured accounts.
Runs in a dedicated daemon thread; does not block main UI or other services.
"""
import logging
import threading
import requests


class HeartbeatService(threading.Thread):
    """Sends POST to HEARTBEAT_URL every 30 seconds for each account. Silent fail on errors."""

    def __init__(self, config, failed_accounts_ref):
        super().__init__(daemon=True)
        self.config = config
        self.failed_accounts = failed_accounts_ref  # reference to sender.FAILED_ACCOUNTS
        self._stop_event = threading.Event()

    def stop(self):
        """Signal the thread to exit on next wait()."""
        self._stop_event.set()

    def run(self):
        while not self._stop_event.is_set():
            if not self.config.accounts:
                logging.debug("Heartbeat: no accounts configured, skipping beat.")
                self._stop_event.wait(30)
                continue

            for cmdr_name, api_key in self.config.accounts.items():
                if self._stop_event.is_set():
                    break
                try:
                    headers = {
                        "x-api-key": api_key,
                        "x-commander": cmdr_name,
                        "User-Agent": self.config.USER_AGENT,
                    }
                    response = requests.post(
                        self.config.HEARTBEAT_URL,
                        headers=headers,
                        timeout=5,
                    )
                    if response.status_code == 200:
                        logging.debug("Heartbeat OK for %s", cmdr_name)
                    elif response.status_code in (401, 403):
                        self.failed_accounts.add(cmdr_name)
                        logging.warning(
                            "Heartbeat auth failed for %s: %s",
                            cmdr_name,
                            response.status_code,
                        )
                    else:
                        logging.warning(
                            "Heartbeat failed for %s: %s %s",
                            cmdr_name,
                            response.status_code,
                            response.text[:100] if response.text else "",
                        )
                except requests.RequestException as e:
                    logging.warning("Heartbeat network error for %s: %s", cmdr_name, e)

            self._stop_event.wait(30)
