"""Which stations seem to be off the air, learned from tuning to them.

A station that fails to start on FAILS separate tries is skipped for SKIP_DAYS, then gets another
chance (stations come back). A failure only counts when another station did start on that same
try, so a dropped internet connection doesn't condemn every station in reach. Kept in
health.json, which the monthly station refresh leaves alone (stations are known by their uuid).
"""

import json
import threading
import time
from pathlib import Path

FAILS = 2
SKIP_DAYS = 3
# A game world's stations (worlds.STATION_PREFIX) are never skipped: there are only a few, and
# one that won't start is a fault to fix, not a station gone off the air.
NEVER_SKIP = "world:"


class StationHealth:
    def __init__(self, path=None, clock=time.time):
        self.path = Path(path) if path else None
        self.clock = clock
        self._lock = threading.Lock()
        self._fails = self._load()      # uuid -> [failures in a row, time of the last]

    def _load(self):
        if not self.path or not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return {k: list(v) for k, v in data.items()} if isinstance(data, dict) else {}
        except (OSError, ValueError, TypeError):
            return {}

    def _save(self):
        if not self.path:
            return
        tmp = self.path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(self._fails), encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            pass

    def record(self, worked, failed):
        """After one try at tuning: the station that started (or None) and those that didn't."""
        with self._lock:
            if worked is None or not (failed or worked in self._fails):
                return False
            self._fails.pop(worked, None)
            now = self.clock()
            for uuid in failed:
                if str(uuid).startswith(NEVER_SKIP):
                    continue
                count = self._fails.get(uuid, [0, 0])[0]
                self._fails[uuid] = [count + 1, now]
            self._save()
        return True

    def skipped(self):
        """The uuids to skip for now."""
        now = self.clock()
        with self._lock:
            return {uuid for uuid, (count, last) in self._fails.items()
                    if count >= FAILS and now - last < SKIP_DAYS * 86400 and not str(uuid).startswith(NEVER_SKIP)}
