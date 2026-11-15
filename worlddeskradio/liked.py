"""Songs you liked: saved with a quick tap of both knob buttons while a song is playing, kept
in liked.json (newest last) and listed on the phone page, each with a button that finds it on
Spotify (spotify_url: a search in the Spotify app, no account setup needed).
"""

import json
import re
import threading
import time
import urllib.parse
from pathlib import Path

from .stations import place

MAX = 500
# What stations put around a song title that a search doesn't want
_LEAD = re.compile(r"^\s*(?:now playing|playing now|on air|np)\s*[:\-]\s*", re.I)
_TAGS = re.compile(r"\[[^\]]*\]|\((?=[^)]*\b(?:official|video|audio|lyrics?|hq|hd|explicit|clean)\b)[^)]*\)", re.I)


def search_query(song):
    """A station's song title, tidied up for a search: "Daughtry - Every Time You Turn Around |
    Album: Leave This Town" -> "Daughtry Every Time You Turn Around"."""
    text = re.sub(r"\s+[\u2013\u2014]\s+", " - ", song or "")      # en and em dashes as " - "
    text = text.split(" | ")[0]                                       # "| Album: …", "| 2009"
    text = _TAGS.sub(" ", _LEAD.sub("", text))                        # "[Live]", "(Official Video)"
    artist, dash, title = text.partition(" - ")
    text = f"{artist} {title}" if dash else text
    return " ".join(text.replace('"', " ").split())


def spotify_url(song):
    """A link that opens Spotify (the app on a phone, else the web player) searching for the song."""
    return "https://open.spotify.com/search/" + urllib.parse.quote(search_query(song), safe="")


class LikedSongs:
    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._items = self._load()

    def _load(self):
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return [d for d in data if isinstance(d, dict) and d.get("song")]
        except (OSError, ValueError, TypeError):
            return []

    def _save(self):
        tmp = self.path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(self._items, indent=1, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            pass

    def has(self, song, station):
        if not song or not station:
            return False
        with self._lock:
            return any(d["song"] == song and d.get("uuid") == station.get("stationuuid")
                       for d in self._items[-50:])

    def add(self, song, station):
        """Returns False if that song on that station is already saved."""
        if self.has(song, station):
            return False
        with self._lock:
            self._items.append({"id": int(time.time() * 1000), "song": song, "station": station["name"],
                                "place": place(station), "uuid": station.get("stationuuid"),
                                "when": time.strftime("%Y-%m-%d %H:%M")})
            del self._items[:-MAX]
            self._save()
        return True

    def items(self):
        """Newest first."""
        with self._lock:
            return [dict(d) for d in reversed(self._items)]

    def delete(self, item_id):
        with self._lock:
            before = len(self._items)
            self._items = [d for d in self._items if d.get("id") != item_id]
            if len(self._items) != before:
                self._save()
