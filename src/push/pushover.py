"""
Pushover backend (https://pushover.net/api).

Native iOS / Android / Apple Watch apps. Priority 2 (emergency) repeats the
alert every ``retry`` seconds until it is acknowledged on the phone or watch.

Quick manual test (no GUI):
    set PUSHOVER_USER=...   set PUSHOVER_TOKEN=...
    python -m src.push.pushover [priority]
"""
from __future__ import annotations

import logging

import requests

from src.push.base import (
    PRIORITY_EMERGENCY, PushError, PushMessage, PushProvider,
)

logger = logging.getLogger(__name__)

API_URL = "https://api.pushover.net/1/messages.json"
TIMEOUT = 10
TITLE_LIMIT = 250
MESSAGE_LIMIT = 1024


class PushoverProvider(PushProvider):
    name = "pushover"

    def __init__(self, user_key: str, app_token: str,
                 emergency_retry: int = 60, emergency_expire: int = 600) -> None:
        self.user_key = (user_key or "").strip()
        self.app_token = (app_token or "").strip()
        self.emergency_retry = max(30, int(emergency_retry))
        self.emergency_expire = max(self.emergency_retry, min(10800, int(emergency_expire)))

    def build_payload(self, message: PushMessage) -> dict:
        payload = {
            "token": self.app_token,
            "user": self.user_key,
            "title": message.title[:TITLE_LIMIT],
            "message": message.body[:MESSAGE_LIMIT],
            "priority": message.priority,
        }
        if message.priority == PRIORITY_EMERGENCY:
            payload["retry"] = self.emergency_retry
            payload["expire"] = self.emergency_expire
        return payload

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
