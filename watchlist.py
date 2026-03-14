"""
Watchlist
---------
Persistent store for wallets that passed scoring.
Tracks their ongoing activity each cycle.
"""

import json
import os
import logging
from datetime import datetime

log = logging.getLogger(__name__)

WATCHLIST_FILE = "watchlist.json"


class Watchlist:
    def __init__(self, filepath: str = WATCHLIST_FILE):
        self.filepath = filepath
        self._data: dict = self._load()
        log.info(f"Watchlist: {len(self._data)} wallets")

    def add(self, entry: dict) -> bool:
        address = entry["address"].lower()
        if address in self._data:
            return False
        self._data[address] = {
            "address":   address,
            "profile":   entry["profile"],
            "score":     entry["score"],
            "found_on":  entry.get("found_on", ""),
            "found_at":  entry.get("found_at", datetime.utcnow().isoformat()),
            "activity":  [],
        }
        self._save()
        return True

    def get_all(self) -> list[dict]:
        return list(self._data.values())

    def log_activity(self, address: str, trade: dict):
        addr = address.lower()
        if addr not in self._data:
            return
        self._data[addr]["activity"].append(trade)
        self._data[addr]["activity"] = self._data[addr]["activity"][-200:]
        self._save()

    def count(self) -> int:
        return len(self._data)

    def _load(self) -> dict:
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, "r") as f:
                    return json.load(f)
            except Exception as e:
                log.warning(f"Watchlist load error: {e}")
        return {}

    def _save(self):
        try:
            with open(self.filepath, "w") as f:
                json.dump(self._data, f, indent=2)
        except Exception as e:
            log.error(f"Watchlist save error: {e}")
