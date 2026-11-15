"""Favorite spots on the dial, kept in favorites.json.

Each favorite is a dial position plus the station that was on it when saved:

    [{"name": "KBS Cool FM", "place": "Seoul, South Korea",
      "lat": 37.57, "lon": 126.98, "stationuuid": "..."}]

next() cycles through them in the order they were saved.
"""

import json
from pathlib import Path


class Favorites:
    def __init__(self, path):
        self.path = Path(path)
        self.items = self._load()
        self._cursor = -1             # index of the favorite last recalled or saved

    def _load(self):
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8") or "[]")
            return [d for d in data if isinstance(d, dict) and "lat" in d and "lon" in d]
        except (ValueError, TypeError):
            # Keep the broken file for a look later rather than overwriting it.
            backup = self.path.with_suffix(".broken.json")
            self.path.replace(backup)
            print(f"{self.path.name} couldn't be read; moved it to {backup.name} and started afresh.")
            return []

    def _save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.items, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    def __len__(self):
        return len(self.items)

    def find(self, station):
        """Index of the favorite saved on this station, or None."""
        if station:
            for i, f in enumerate(self.items):
                if f.get("stationuuid") == station["stationuuid"]:
                    return i
        return None

    def add(self, lat, lon, station=None, place=""):
        """Save a spot. Returns (index, added); added is False if that station is already saved."""
        i = self.find(station)
        if i is None:
            self.items.append({
                "name": station["name"] if station else f"{lat:.2f}, {lon:.2f}",
                "place": place,
                "lat": round(lat, 4),
                "lon": round(lon, 4),
                "stationuuid": station["stationuuid"] if station else None,
            })
            self._save()
            i = len(self.items) - 1
            added = True
        else:
            added = False
        self._cursor = i
        return i, added

    def recall(self, i):
        """Favorite number i (from 0), which next() then continues from; None if there's no such one."""
        if not 0 <= i < len(self.items):
            return None
        self._cursor = i
        return self.items[i]

    def remove(self, i):
        """Forget favorite number i (from 0). Returns False if there's no such one."""
        if not 0 <= i < len(self.items):
            return False
        del self.items[i]
        self._save()
        if i <= self._cursor:
            self._cursor -= 1         # next() carries on with the one after it
        return True

    def next(self):
        """The favorite after the last one recalled or saved, as (index, item); None if there are none."""
        if not self.items:
            return None
        self._cursor = (self._cursor + 1) % len(self.items)
        return self._cursor, self.items[self._cursor]
