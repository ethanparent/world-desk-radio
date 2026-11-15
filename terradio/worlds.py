"""Game worlds (PLAN.md, Stage 7): a game's map on a pretend planet, with that game's radio.

Each world is a folder, worlds/<id>/, made by a tool such as tools/fnv_world.py from a copy of the
game you own. It isn't in git (the audio is the game's, for this radio only):

    worlds/mojave/world.json            the map and the stations (below)
    worlds/mojave/stations/<station>/   that station's audio, and playlist.json: the files in
                                        the order they air, with their lengths and song names

world.json:

    {"title": "Mojave Wasteland",                 shown in the World menu, and as the "country"
     "theme": "radar",                            the theme it starts in (optional)
     "zooms": [1700, 600, 200],                   km across (optional: worked out from the map)
     "places": [{"name": "Goodsprings", "lat": 0.1, "lon": -4.6, "zoom": 4.0}, ...],
                                                  labeled like Earth's cities, from that web-map zoom on
     "area": [south, north, west, east],          the map's extent, for its zooms and where the dial
                                                  may go (optional: the coast's)
     "coast":   [[[lat, lon], ...], ...],         the edge of the land (or a lake's shore), as lines
     "borders": [[[lat, lon], ...], ...],         lines inside it (regions, a river): the borders layer
     "icons": {"playing": {"file": "icons/playing.png"},                  (optional) the world's own
               "tuning": {"file": "icons/tuning.png", "window": 64},    symbols for the signal slot:
               "no signal": {"file": "icons/no-signal.png"}},           8-bit masks sized for the
                                                  480 px face; "window": a strip shown that wide,
                                                  sliding along it (its last `window` px repeat its first)
     "stations": [{"id": "radio-new-vegas", "name": "Radio New Vegas", "place": "The Strip",
                   "lat": 0.21, "lon": -1.73},              audio from stations/radio-new-vegas/
                  {"id": "k-rose", "name": "K-Rose", "lat": 0, "lon": 0, "url": "https://..."}]}
                                                            or a stream, like any Earth station

The map sits on an Earth-sized globe that is otherwise all sea, near 0° 0°, about 1,500 km across,
so the radio's zooms, crosshair, Tune knob and favorites all work as they do on Earth.

A station that plays from files is "live" by the clock, like a game's radio: it has been playing
its list round and round since EPOCH, so tuning in lands mid-song, and coming back later finds it
further on. Tracks.cue() says what the player should play from now.
"""

import bisect
import json
import math
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _worlds_dir():
    """Where the worlds are: $TERRADIO_WORLDS, else ~/terradio-worlds if there is one (the Pi:
    a few GB is too much for the overlay's small writable image, which holds the checkout), else
    worlds/ in the checkout (the laptop)."""
    if os.environ.get("TERRADIO_WORLDS"):
        return Path(os.environ["TERRADIO_WORLDS"])
    home = Path.home() / "terradio-worlds"
    return home if home.is_dir() else ROOT / "worlds"


WORLDS_DIR = _worlds_dir()
EARTH = "earth"
EPOCH = 1767225600            # 2026-01-01 00:00 UTC: every station's schedule counts from here
CUE_S = 12 * 3600             # the player is handed this much of the schedule at a time ...
CUE_FILES = 250               # ... but no more files than this: mpv on the Pi takes 15 s to start on
                              # 10,000 (Camp Guardian's 66 s loop over 12 hours), 1.4 s on 250
MIN_LEFT_S = 2.0              # less than this left of a track: start at the next one instead
STATION_PREFIX = "world:"     # world stations' ids: "world:mojave:radio-new-vegas"
DEG_KM = 111.195              # km in a degree (the pretend planets are Earth-sized)
WHOLE_SHARE = 1.12            # the whole-map zoom: the map's longer side and a little room
EDGE_SHARE = 0.03             # the dial can go this share of the map past its edge, no further


class WorldError(Exception):
    pass


@dataclass
class Tracks:
    """A station's audio files in the order they air, played round and round since EPOCH."""
    folder: Path
    files: list               # file names in folder
    seconds: list             # their lengths
    titles: list              # what each is ("Dean Martin - Blue Moon"; "" for the DJ and the like)
    _starts: list = field(default_factory=list, repr=False)

    def __post_init__(self):
        self._starts, t = [], 0.0
        for s in self.seconds:
            self._starts.append(t)
            t += s
        self.total = t

    def at(self, when=None):
        """(index of the track on air, seconds into it) at Unix time `when` (default now)."""
        when = time.time() if when is None else when
        p = (when - EPOCH) % self.total
        i = max(0, bisect.bisect_right(self._starts, p) - 1)
        return i, p - self._starts[i]

    def title_at(self, when=None):
        i, _ = self.at(when)
        return self.titles[i]

    def cue(self, when=None, span_s=CUE_S, max_files=CUE_FILES):
        """What to play from `when`: [(path, seconds to skip)], the first track part-way through,
        then the ones after it, round the list as often as fits in span_s and max_files. (When the
        player gets to the end, the dial hands it the next stretch: Dial._world_tick.)"""
        i, offset = self.at(when)
        if self.seconds[i] - offset < MIN_LEFT_S:
            i, offset = (i + 1) % len(self.files), 0.0
        out, played = [(self.folder / self.files[i], offset)], self.seconds[i] - offset
        while played < span_s and len(out) < max_files:
            i = (i + 1) % len(self.files)
            out.append((self.folder / self.files[i], 0.0))
            played += self.seconds[i]
        return out

    @classmethod
    def load(cls, folder):
        """From folder/playlist.json: {"tracks": [{"file": ..., "seconds": ..., "title": ...}]}."""
        data = json.loads((folder / "playlist.json").read_text(encoding="utf-8"))
        tracks = [t for t in data["tracks"] if t.get("seconds", 0) > 0 and (folder / t["file"]).exists()]
        if not tracks:
            raise WorldError(f"{folder}: no audio files found")
        return cls(folder, [t["file"] for t in tracks], [float(t["seconds"]) for t in tracks],
                   [t.get("title", "") for t in tracks])


@dataclass
class World:
    id: str
    title: str
    folder: Path
    stations: list            # station dicts, shaped like Radio Browser's (stations.py)
    coast: list               # [(lats, lons)] lines: the edge of the land
    borders: list             # [(lats, lons)] lines drawn in the borders layer
    theme: str = None
    zooms: tuple = ()         # the zoom levels, km across: the whole map, closer, close up
    places: list = field(default_factory=list)   # [(name, lat, lon, zoom)]: labels, as Earth's cities
    home: tuple = None        # (lat, lon, km): the whole map's view, for idle and long jumps
    bounds: tuple = None      # (south, north, west, east): the dial stays inside
    area: tuple = None        # (south, north, west, east): the map's extent (default: the coast's)
    icons: dict = field(default_factory=dict)   # "playing" / "tuning" / "no signal" -> Icon: its own symbols

    def __post_init__(self):
        if self.area is not None:
            south, north, west, east = self.area
        else:
            pts = [(a, b) for lats, lons in self.coast for a, b in zip(lats, lons)] or \
                  [(s["geo_lat"], s["geo_long"]) for s in self.stations]
            south, north = min(a for a, _ in pts), max(a for a, _ in pts)
            west, east = min(b for _, b in pts), max(b for _, b in pts)
        lat, lon = (south + north) / 2, (west + east) / 2
        span_km = max(north - south, (east - west) * math.cos(math.radians(lat))) * DEG_KM
        whole = _round(span_km * WHOLE_SHARE)
        if not self.zooms:
            self.zooms = (whole, _round(whole * 0.35), _round(whole * 0.12, 10))
        self.home = (round(lat, 5), round(lon, 5), self.zooms[0])
        m = EDGE_SHARE * max(north - south, east - west)
        self.bounds = (south - m, north + m, west - m, east + m)


def _round(km, step=50):
    return max(step, int(round(km / step)) * step)


def is_world_station(station):
    return bool(station) and str(station.get("stationuuid", "")).startswith(STATION_PREFIX)


def available(folder=None):
    """[(id, title)] for every world installed, Earth first."""
    folder = WORLDS_DIR if folder is None else folder
    found = []
    if folder.is_dir():
        for path in sorted(folder.iterdir()):
            try:
                title = json.loads((path / "world.json").read_text(encoding="utf-8")).get("title") or path.name
            except (OSError, ValueError):
                continue
            found.append((path.name, title))
    return [(EARTH, "Earth")] + found


def load(world_id, folder=None, log=print):
    """The World in worlds/<world_id>/. Stations whose audio is missing are left out (with a note)."""
    path = (WORLDS_DIR if folder is None else folder) / world_id
    try:
        data = json.loads((path / "world.json").read_text(encoding="utf-8"))
    except OSError as e:
        raise WorldError(f"No world called {world_id!r} ({e})") from None
    except ValueError as e:
        raise WorldError(f"{path / 'world.json'} isn't valid JSON ({e})") from None
    title = data.get("title") or world_id
    stations = []
    for s in data.get("stations", []):
        try:
            station = {
                "stationuuid": f"{STATION_PREFIX}{world_id}:{s['id']}",
                "name": s["name"], "state": s.get("place", ""), "country": title, "countrycode": "",
                "geo_lat": float(s["lat"]), "geo_long": float(s["lon"]),
                "tags": s.get("tags", ""), "language": s.get("language", ""), "world": world_id,
                "codec": "", "bitrate": 0, "votes": 0, "clickcount": 0, "homepage": "",
            }
            if s.get("url"):
                station["url_resolved"] = s["url"]
            else:
                station["tracks"] = Tracks.load(path / "stations" / s["id"])
                station["url_resolved"] = station["stationuuid"]
        except (KeyError, TypeError, ValueError, OSError, WorldError) as e:
            log(f"World {world_id}: leaving out station {s.get('id') or s.get('name')!r} ({e})")
            continue
        stations.append(station)
    if not stations:
        raise WorldError(f"World {world_id!r} has no stations that can play")
    return World(world_id, title, path, stations, _lines(data.get("coast")), _lines(data.get("borders")),
                 data.get("theme"), tuple(data.get("zooms") or ()), _places(data.get("places")),
                 area=_area(data.get("area")), icons=_icons(path, data.get("icons"), log))


ICON_STATES = ("playing", "tuning", "no signal")
ICON_MAX = (1024, 64)        # the biggest symbol taken (w, h): the face keeps them in memory


@dataclass(frozen=True)
class Icon:
    """One of a world's symbols for the signal slot, at the 480 px face's size: an 8-bit mask,
    drawn in the theme's color like the usual ((•)). window > 0: a strip, shown that wide and
    sliding along (its last `window` columns repeat its first, so it goes round smoothly)."""
    mask: bytes
    w: int
    h: int
    window: int = 0


def _icons(folder, raw, log=print):
    """world.json's "icons" -> {state: Icon}; ones that are missing or won't load are left out."""
    out = {}
    if not isinstance(raw, dict):
        return out
    from PIL import Image
    for state, entry in raw.items():
        if state not in ICON_STATES or not isinstance(entry, dict):
            continue
        try:
            file = (folder / str(entry["file"])).resolve()
            if folder.resolve() not in file.parents:
                raise ValueError("it's outside the world's folder")
            with Image.open(file) as im:
                mask = im.convert("L")
            window = int(entry.get("window") or 0)
            if mask.width > ICON_MAX[0] or mask.height > ICON_MAX[1] or not 0 <= window < mask.width:
                raise ValueError(f"{mask.width}x{mask.height}, window {window}: too big")
        except (OSError, KeyError, TypeError, ValueError) as e:
            log(f"World: leaving out the {state!r} symbol ({e})")
            continue
        out[state] = Icon(mask.tobytes(), mask.width, mask.height, window)
    return out


def _area(raw):
    """(south, north, west, east), or None if it's missing or not four numbers."""
    try:
        south, north, west, east = (float(v) for v in raw)
    except (TypeError, ValueError):
        return None
    return (south, north, west, east) if south < north and west < east else None


def _places(raw):
    """[(name, lat, lon, zoom)], the ones to label first (lowest zoom) first."""
    out = []
    for p in raw or []:
        try:
            out.append((str(p["name"]), float(p["lat"]), float(p["lon"]), float(p.get("zoom", 6))))
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(out, key=lambda p: p[3])


def _lines(raw):
    out = []
    for line in raw or []:
        if len(line) >= 2:
            lats, lons = zip(*((float(a), float(b)) for a, b in line))
            out.append((list(lats), list(lons)))
    return out


# --- making playlists (on the laptop, from the game's files) ---------------------------------

def seconds_of(path):
    """An audio file's length, from ffprobe."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise WorldError("ffprobe (part of FFmpeg) is needed to measure the audio: winget install Gyan.FFmpeg")
    out = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                          str(path)], capture_output=True, text=True,
                         creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
    try:
        return float(out.stdout.strip())
    except ValueError:
        raise WorldError(f"ffprobe couldn't read {path}: {out.stderr.strip()[:200]}") from None


def write_playlist(folder, tracks):
    """tracks: [(file name in folder, title)] in airing order. Measures each and writes playlist.json."""
    rows = [{"file": name, "seconds": round(seconds_of(folder / name), 3), "title": title}
            for name, title in tracks]
    (folder / "playlist.json").write_text(json.dumps({"tracks": rows}, indent=1, ensure_ascii=False),
                                          encoding="utf-8")
    return rows
