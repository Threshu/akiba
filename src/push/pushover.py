"""
Pushover backend (https://pushover.net/api).

Native iOS / Android / Apple Watch apps. Priorities -2..1 are used; with
Critical Alerts enabled in the iOS app, priority 1 (high) breaks through
Silent / Focus mode without having to be acknowledged.

Quick manual test (no GUI):
    set PUSHOVER_USER=...   set PUSHOVER_TOKEN=...
    python -m src.push.pushover [priority]
"""
from __future__ import annotations

import logging

import requests

from src.push.base import PushError, PushMessage, PushProvider, clamp_priority

logger = logging.getLogger(__name__)

API_URL = "https://api.pushover.net/1/messages.json"
VALIDATE_URL = "https://api.pushover.net/1/users/validate.json"
TIMEOUT = 10
TITLE_LIMIT = 250
MESSAGE_LIMIT = 1024


class PushoverProvider(PushProvider):
    name = "pushover"

    def __init__(self, user_key: str, app_token: str) -> None:
        self.user_key = (user_key or "").strip()
        self.app_token = (app_token or "").strip()

    def build_payload(self, message: PushMessage) -> dict:
        payload = {
            "token": self.app_token,
            "user": self.user_key,
            "title": message.title[:TITLE_LIMIT],
            "message": message.body[:MESSAGE_LIMIT],
            # Never 2 (emergency): it would repeat until acknowledged.
            "priority": clamp_priority(message.priority),
        }
        if message.devices:
            payload["device"] = ",".join(message.devices)
        return payload

    def list_devices(self) -> list:
        """Device names registered on the account (users/validate API)."""
        if not self.user_key or not self.app_token:
            raise PushError("Pushover user key and API token are required.")
        try:
            response = requests.post(VALIDATE_URL, data={"token": self.app_token, "user": self.user_key},
                                     timeout=TIMEOUT)
            data = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise PushError(f"Network error: {exc.__class__.__name__}") from exc
        if data.get("status") != 1:
            raise PushError("Pushover: " + "; ".join(str(e) for e in data.get("errors") or ["invalid"]))
        return [str(d) for d in data.get("devices") or []]

    def send(self, message: PushMessage) -> None:
        if not self.user_key or not self.app_token:
            raise PushError("Pushover user key and API token are required.")
        try:
            response = requests.post(API_URL, data=self.build_payload(message), timeout=TIMEOUT)
        except requests.RequestException as exc:
            raise PushError(f"Network error: {exc.__class__.__name__}") from exc

        try:
            data = response.json()
        except ValueError:
            data = {}
        if response.status_code == 200 and data.get("status") == 1:
            logger.info("pushover sent: %s", message.title)
            return
        errors = data.get("errors") or [f"HTTP {response.status_code}"]
        raise PushError("Pushover: " + "; ".join(str(e) for e in errors))


if __name__ == "__main__":
    import os
    import sys

    logging.basicConfig(level=logging.INFO)
    prio = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    provider = PushoverProvider(os.environ.get("PUSHOVER_USER", ""),
                                os.environ.get("PUSHOVER_TOKEN", ""))
    try:
        provider.send(PushMessage("3FR #6 (test)", "Nowak Jan - Kowalski Piotr\n34. Nf3", prio))
        print("OK")
    except PushError as err:
        print("FAILED:", err)
        sys.exit(1)
