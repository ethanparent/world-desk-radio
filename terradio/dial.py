"""The dial itself: position -> nearest station, settle debounce, fallback tuning (reaching
further out until something plays).

Nothing in here knows about keyboards or GPIO. An input layer only calls:

    dial.turn(dlat=..., dlon=...)   # a knob moved (relative)
    dial.set_position(lat, lon)     # or jump to an absolute spot
    dial.set_position(lat, lon, station=s)  # ...and pick that station, even if others share the spot
    dial.set_volume(0-100)
    dial.sleep() / dial.wake()      # standby: silence until woken
    dial.tick()                     # ~20 times a second, from the main loop

The laptop prototype feeds it arrow keys; the Raspberry Pi version will feed
it gpiozero RotaryEncoder steps instead.
"""

import dataclasses
import math
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import numpy as np

from .stations import haversine_km, report_click

# When the stations at a spot won't start, the dial keeps reaching further out, and locks onto
# the nearest one that does (a whole broadcaster can go down at once: on 2026-11-14 every
# 0nlineradio, Technolovers and rautemusik stream did, and they fill the middle of Germany).
SEARCH = 12         # at most this many stations tried in one go...
SEARCH_POOL = 60    # ...from this many nearest
HOST_FAILS = 2      # this many failures from one stream server and the rest of its stations are
                    # passed over for that search (not one: big hosts like laut.fm carry thousands)
# A game world has a handful of stations far apart, so every spot is "near" one of them. Rolling
# there doesn't drop the station on air (on Earth any move retunes): it keeps playing until another
# station is clearly nearer, under HOLD times as far as the one playing.
HOLD = 0.75


def _unit_vector(lat, lon):
    la, lo = math.radians(lat), math.radians(lon)
    return (math.cos(la) * math.cos(lo), math.cos(la) * math.sin(lo), math.sin(la))


@dataclass
class DialState:
    lat: float
    lon: float
    nearest: dict = None
    distance_km: float = None
    status: str = "idle"          # idle | turning | tuning | playing | no signal
    playing: dict = None          # the station actually on air (may be a fallback)
    station_count: int = 0
    volume: int = 80              # 0-100
    message: str = ""
    song: str = ""                # what's playing on the station, if it says
    skipped: int = 0              # stations left out because they seem to be off the air
    tried: list = field(default_factory=list)


class Dial:
    def __init__(self, stations, player, *, lat=0.0, lon=0.0, settle_s=0.4,
                 start_timeout_s=5.0, fallbacks=5, search=SEARCH, on_change=None, health=None, bounds=None):
        """bounds: (south, north, west, east) the dial stays within when turned (a game world's
        map), or None for the whole globe."""
        self.player = player
        self.bounds = bounds
        self.health = health          # health.StationHealth: stations that keep failing get skipped
        self.settle_s = settle_s
        self.start_timeout_s = start_timeout_s
        self.fallbacks = fallbacks    # restarting the station on air (new volume): it or these nearest
        self.search = search          # tuning a spot: up to this many tries, reaching further out
        self.on_change = on_change or (lambda state: None)

        # Precompute 3D unit vectors: the nearest station on a sphere is the one
        # with the largest dot product, which is much cheaper than haversine.
        # numpy does all ~13k in a fraction of a millisecond, which matters on the Pi.
        self._stations = list(stations)
        self._vecs = np.array([_unit_vector(s["geo_lat"], s["geo_long"]) for s in self._stations],
                              dtype=np.float64).reshape(-1, 3)

        self._lock = threading.RLock()
        self._last_turn = None        # monotonic time of last movement while unsettled
        self._volume_at = None        # monotonic time of a volume change not yet applied
        self._chosen = None           # a station picked by set_position(station=...), until the next move
        self._generation = 0          # bumps on every turn; stale tuner threads give up
        self._fade_s = None           # a slow fade-in for the next station (the alarm's)
        self._penalty = None          # pushes skipped stations out of every search
        self.state = DialState(lat=lat, lon=lon, station_count=len(self._stations),
                               volume=getattr(player, "volume", 80))
        self._refresh_skips()
        self._refresh_nearest()
        if hasattr(player, "on_title"):
            player.on_title = self._song_changed

    # --- lookups ---------------------------------------------------------

    def nearest(self, lat, lon, k=1):
        """The k stations nearest (lat, lon), closest first."""
        if not self._stations:
            return []
        dots = self._vecs @ np.array(_unit_vector(lat, lon))
        if self._penalty is not None:
            dots = dots + self._penalty
        if k == 1:
            best = [int(np.argmax(dots))]
        else:
            # Stable, so stations at the very same spot keep their list order.
            best = np.argsort(-dots, kind="stable")[:k].tolist()
        return [self._stations[i] for i in best]

    def _refresh_skips(self):
        """Stations that seem to be off the air drop out of every search (health.py)."""
        skipped = self.health.skipped() if self.health else set()
        self._penalty = (np.array([-10.0 if s.get("stationuuid") in skipped else 0.0 for s in self._stations])
                         if skipped else None)
        self.state.skipped = len(skipped)

    def random_station(self, rng):
        """A random station that isn't being skipped, spread over the world: first a random
        country, then a station in it (so it isn't nearly always Europe or the US)."""
        skip = self.health.skipped() if self.health else set()
        by_country = {}
        for s in self._stations:
            if s.get("stationuuid") not in skip:
                by_country.setdefault(s.get("countrycode") or "?", []).append(s)
        if not by_country:
            return None
        return rng.choice(by_country[rng.choice(sorted(by_country))])

    def _refresh_nearest(self):
        st = self.state
        found = [self._chosen] if self._chosen else self.nearest(st.lat, st.lon, 1)
        st.nearest = found[0] if found else None
        st.distance_km = (haversine_km(st.lat, st.lon, st.nearest["geo_lat"], st.nearest["geo_long"])
                          if st.nearest else None)

    def _changed(self):
        self.on_change(self.state)

    def snapshot(self):
        """A copy of the state, safe to read while a tuner thread is changing it."""
        with self._lock:
            return dataclasses.replace(self.state, tried=list(self.state.tried))

    # --- input -----------------------------------------------------------

    def turn(self, dlat=0.0, dlon=0.0):
        with self._lock:
            self._chosen = None
            self._fade_s = None
            st = self.state
            lat = max(-90.0, min(90.0, st.lat + dlat))
            lon = (st.lon + dlon + 180.0) % 360.0 - 180.0
            if self.bounds is not None:       # a game world: rolling stops at the edge of the map
                south, north, west, east = self.bounds
                lat, lon = max(south, min(north, lat)), max(west, min(east, lon))
            self._moved(lat, lon)

    def set_position(self, lat, lon, station=None, fade_s=None):
        """Jump to (lat, lon). Pass station to tune that one rather than whichever of the
        stations at that spot happens to come first (many share a city's center point), and
        fade_s to have it fade in slowly, without static first (the alarm)."""
        with self._lock:
            self._chosen = station
            self._fade_s = fade_s
            self._moved(*_clamped(lat, lon))

    def _holds(self, lat, lon):
        """In a game world, the station on air stays on while the dial rolls, until another one
        is clearly nearer (HOLD), or the move picked a different station (set_position)."""
        st = self.state
        s = st.playing if st.status == "playing" else None
        if s is None or not s.get("tracks") or self._fade_s is not None:
            return False
        if self._chosen is not None and self._chosen is not s:
            return False
        other = next((c for c in self.nearest(lat, lon, 2) if c is not s), None)
        if other is None:
            return True
        here = haversine_km(lat, lon, s["geo_lat"], s["geo_long"])
        return haversine_km(lat, lon, other["geo_lat"], other["geo_long"]) >= HOLD * here

    def _moved(self, lat, lon):
        st = self.state
        if self._holds(lat, lon):
            st.lat, st.lon = lat, lon
            self._chosen = st.playing         # still the one on air: nothing to tune
            self._last_turn = None
            self._refresh_nearest()
            self._changed()
            return
        st.lat, st.lon = lat, lon
        self._generation += 1
        self._last_turn = time.monotonic()
        if st.status != "turning":
            st.status = "turning"
            st.playing = None
            st.message = ""
            st.song = ""
            self.player.stop_stream()
            if self._fade_s is None:      # waking to the alarm: no burst of static first
                self.player.start_static()
        self._refresh_nearest()
        self._changed()

    def set_volume(self, volume):
        """0-100. The state (and the screen) change at once. On the Pi the sound does too (the
        system mixer, or mpv's control socket: player.live_volume); on the laptop it changes once
        the knob has rested for settle_s, because its players have to restart to change volume."""
        with self._lock:
            volume = max(0, min(100, int(round(volume))))
            if volume != self.state.volume:
                self.state.volume = volume
                if getattr(self.player, "live_volume", False):
                    self.player.set_volume(volume)
                else:
                    self._volume_at = time.monotonic()
                self._changed()

    def _apply_volume(self):
        st = self.state
        self.player.set_volume(st.volume)
        if st.status != "playing" or self.player.stream_volume == st.volume:
            return
        # Restart the station that's on air at the new volume (falling back as usual).
        self._generation += 1
        st.status = "tuning"
        st.tried = []
        candidates = [st.playing] + [s for s in self.nearest(st.lat, st.lon, self.fallbacks)
                                     if s is not st.playing]
        st.playing = None
        st.song = ""
        self._changed()
        threading.Thread(target=self._tune, args=(self._generation, candidates[:self.fallbacks], False),
                         daemon=True).start()

    # --- time ------------------------------------------------------------

    def tick(self):
        """Call regularly. Starts tuning once the dial has been still for settle_s."""
        with self._lock:
            now = time.monotonic()
            if self._volume_at is not None and now - self._volume_at >= self.settle_s:
                self._volume_at = None
                self._apply_volume()
            self._world_tick()
            if self._last_turn is None or now - self._last_turn < self.settle_s:
                return
            self._last_turn = None
            st = self.state
            st.status = "tuning"
            st.tried = []
            candidates = self.nearest(st.lat, st.lon, SEARCH_POOL)
            if self._chosen:
                candidates = [self._chosen] + [c for c in candidates if c is not self._chosen]
            gen = self._generation
            fade, self._fade_s = self._fade_s, None
            self._changed()
        threading.Thread(target=self._tune, args=(gen, candidates, True, fade, self.search), daemon=True).start()

    def _world_tick(self):
        """A game world's station on air (worlds.py): its song comes from the clock, and if its
        player ends (it's handed half a day at a time), it carries on from where the clock is."""
        st = self.state
        tracks = st.playing.get("tracks") if st.status == "playing" and st.playing else None
        if tracks is None:
            return
        if not self.player.stream_alive():
            self._generation += 1
            st.status, st.tried = "tuning", []
            self._changed()
            threading.Thread(target=self._tune, args=(self._generation, [st.playing], False), daemon=True).start()
            return
        title = tracks.title_at()
        if title != st.song:
            st.song = title
            self._changed()

    def _tune(self, gen, candidates, report=True, fade_s=None, tries=None):
        """Try the candidates in order (at most `tries` of them) until one starts. Once a stream
        server has failed HOST_FAILS times, the rest of its stations are passed over (not blamed)."""
        stale = lambda: gen != self._generation
        tries = len(candidates) if tries is None else tries
        failed, host_fails = [], {}
        for s in candidates:
            if len(failed) >= tries:
                break
            if stale():
                return
            host = None if s.get("tracks") else urlsplit(s.get("url_resolved") or "").hostname
            if host and host_fails.get(host, 0) >= HOST_FAILS:
                continue
            with self._lock:
                self.state.message = f"Tuning {s['name']} ..."
                self._changed()
            tracks = s.get("tracks")         # a game world's station plays from files (worlds.py)
            ok = self.player.play_stream(s["url_resolved"], self.start_timeout_s, canceled=stale,
                                         fade_s=fade_s, cue=tracks.cue() if tracks else None)
            with self._lock:
                if stale():
                    if ok:
                        self.player.stop_stream()
                    return
                if ok:
                    if self.health and self.health.record(s.get("stationuuid"), failed):
                        self._refresh_skips()       # before "playing", so nobody sees the old list
                    self.player.fade_out_static()
                    st = self.state
                    st.status = "playing"
                    st.playing = s
                    st.message = ""
                    st.song = tracks.title_at() if tracks else self.player.title
                    # Lock on, like a radio's automatic frequency control: the dial settles on
                    # the station it found, without tuning again (the face glides there).
                    st.lat, st.lon = _clamped(s["geo_lat"], s["geo_long"])
                    self._chosen = s
                    self._refresh_nearest()
                    if self.player.stream_volume != st.volume:
                        self._volume_at = time.monotonic()  # volume moved while this was starting
                    self._changed()
                    if report and not tracks:
                        threading.Thread(target=report_click, args=(s["stationuuid"],), daemon=True).start()
                    return
                self.state.tried.append(s["name"])
                failed.append(s.get("stationuuid"))
                if host:
                    host_fails[host] = host_fails.get(host, 0) + 1
        with self._lock:
            if not stale():
                # Nothing answered: leave the static hissing, like a real dead spot on the band.
                self.player.start_static()
                self.state.status = "no signal"
                self.state.message = f"None of the {len(failed)} nearest stations would start."
                self._changed()

    def sleep(self):
        """Standby: silence, and nothing tunes until wake()."""
        with self._lock:
            self._generation += 1
            self._last_turn = self._volume_at = None
            self.player.stop_stream()
            self.player.stop_static()
            st = self.state
            st.status, st.playing, st.song, st.message = "off", None, "", ""
            self._changed()

    def wake(self, fade_s=None):
        """Back from standby: tune the spot the dial is on, as if the knob had just moved. With
        fade_s, the station fades in over that long, without static first."""
        with self._lock:
            if self.state.status == "off":
                self._fade_s = fade_s
                self._moved(self.state.lat, self.state.lon)

    def _song_changed(self, title):
        """The player heard a new song title (from its own thread)."""
        with self._lock:
            st = self.state
            if st.status == "playing" and st.song != title:
                st.song = title
                self._changed()

    def close(self):
        with self._lock:
            self._generation += 1
        self.player.close()


def _clamped(lat, lon):
    """A position inside the map: latitude -90..90, longitude -180..180."""
    return max(-90.0, min(90.0, lat)), (lon + 180.0) % 360.0 - 180.0
