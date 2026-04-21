# cache_manager.py
import time
import hashlib
import json
from typing import Any, Optional

class SmartCache:
    def __init__(self):
        self._storage = {}

    def set(self, key: str, value: Any, ttl: int = 300):
        self._storage[key] = {
            "data": value,
            "expires": time.time() + ttl
        }

    def get(self, key: str) -> Optional[Any]:
        item = self._storage.get(key)
        if item and time.time() < item["expires"]:
            return item["data"]
        return None

    def generate_ai_key(self, asset_name: str, price: float) -> str:
        """Kreira jedinstveni ključ za AI analizu na osnovu imena i cene.
        Cena se zaokružuje na 2 značajne cifre da se izbegnu česti cache miss-evi
        pri malim fluktuacijama (npr. $201.73 i $201.75 → isti ključ)."""
        import math
        if price > 0:
            magnitude = 10 ** (math.floor(math.log10(price)) - 1)
            rounded_price = round(price / magnitude) * magnitude
        else:
            rounded_price = 0
        raw_string = f"{asset_name}_{rounded_price}"
        return hashlib.md5(raw_string.encode()).hexdigest()

# Globalna instanca
market_cache = SmartCache()