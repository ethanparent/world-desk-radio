"""The radio's last spot, zoom, theme and volume, so it comes back the way you left it.

Kept in settings.json next to favorites.json. Changes are written once things have been still
for SAVE_AFTER_S (the Pi's SD card shouldn't be written many times a second), and on exit.

    settings = Settings(path)
    settings.get("lat", 51.51)
    settings.update(lat=..., lon=..., zoom=..., theme=..., volume=...)   # every frame is fine
    settings.tick()                                                       # every frame
    settings.save()                                                       # on exit
"""

import json
import time
from pathlib import Path

SAVE_AFTER_S = 5.0


class Settings:
    def __init__(self, path, clock=time.monotonic):
        self.path = Path(path)
        self.clock = clock
        self.data = self._load()
        self._changed_at = None       # when unsaved changes were last made

    def _load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def update(self, **values):
        if any(self.data.get(k) != v for k, v in values.items()):
            self.data.update(values)
            self._changed_at = self.clock()

    def tick(self):
        if self._changed_at is not None and self.clock() - self._changed_at >= SAVE_AFTER_S:
            self.save()

    def save(self):
        if self._changed_at is None:
            return
        tmp = self.path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            return            # a read-only card or a full disk: try again next time
        self._changed_at = None
