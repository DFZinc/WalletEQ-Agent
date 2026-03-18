"""
Cache
-----
Persists analyzed wallets and scanned tokens between runs.
Nothing is ever re-fetched if it already exists in cache.

wallet_cache.json — every wallet analyzed, profile + score
token_cache.json  — every token processed, with timestamp + TTL
"""

import json
import os
import logging
from datetime import datetime, timezone, timedelta

log = logging.getLogger(__name__)

WALLET_CACHE_FILE = "wallet_cache.json"
TOKEN_CACHE_FILE  = "token_cache.json"


class Cache:
    def __init__(
        self,
        wallet_file: str    = WALLET_CACHE_FILE,
        token_file: str     = TOKEN_CACHE_FILE,
        token_ttl_hours: int = 24,
    ):
        self.wallet_file     = wallet_file
        self.token_file      = token_file
        self.token_ttl_hours = token_ttl_hours
        self._wallets: dict  = self._load(wallet_file)
        self._tokens:  dict  = self._load(token_file)
        log.info(f"Cache: {len(self._wallets)} wallets, {len(self._tokens)} tokens")

    # ── Wallet cache ──────────────────────────────────────────────────

    def has_wallet(self, address: str) -> bool:
        return address.lower() in self._wallets

    def get_wallet(self, address: str) -> dict | None:
        return self._wallets.get(address.lower())

    def save_wallet(self, address: str, profile: dict, score: dict):
        self._wallets[address.lower()] = {
            "profile":     profile,
            "score":       score,
            "analyzed_at": datetime.now(timezone.utc).isoformat(),
        }
        self._save(self.wallet_file, self._wallets)

    def wallet_count(self) -> int:
        return len(self._wallets)

    # ── Token cache ───────────────────────────────────────────────────

    def has_token(self, address: str) -> bool:
        entry = self._tokens.get(address.lower())
        if not entry:
            return False
        scanned_at = datetime.fromisoformat(entry["scanned_at"])
        return datetime.now(timezone.utc) - scanned_at < timedelta(hours=self.token_ttl_hours)

    def save_token(self, address: str, symbol: str, price_change: float = 0.0, volume_usd: float = 0.0):
        addr    = address.lower()
        existing = self._tokens.get(addr, {})
        self._tokens[addr] = {
            "symbol":       symbol,
            "price_change": round(price_change, 2),
            "peak_volume":  max(existing.get("peak_volume", 0.0), volume_usd),
            "first_seen":   existing.get("first_seen") or datetime.now(timezone.utc).isoformat(),
            "last_seen":    datetime.now(timezone.utc).isoformat(),
            "scanned_at":   datetime.now(timezone.utc).isoformat(),
        }
        self._save(self.token_file, self._tokens)

    def disable_token(self, address: str):
        addr = address.lower()
        if addr in self._tokens:
            self._tokens[addr]["disabled"] = True
            self._save(self.token_file, self._tokens)

    def enable_token(self, address: str):
        addr = address.lower()
        if addr in self._tokens:
            self._tokens[addr]["disabled"] = False
            self._save(self.token_file, self._tokens)

    def is_token_disabled(self, address: str) -> bool:
        return self._tokens.get(address.lower(), {}).get("disabled", False)

    def token_count(self) -> int:
        return len(self._tokens)

    # ── Persistence ───────────────────────────────────────────────────

    def _load(self, filepath: str) -> dict:
        if os.path.exists(filepath):
            try:
                with open(filepath, "r") as f:
                    return json.load(f)
            except Exception as e:
                log.warning(f"Cache load error ({filepath}): {e}")
        return {}

    def _save(self, filepath: str, data: dict):
        try:
            with open(filepath, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            log.error(f"Cache save error ({filepath}): {e}")
