"""The link from the radio (the Pi) to its face (the ESP32-S3 round screen): one JSON object
per line over the USB serial cable. PLAN.md ("Messages from the Pi to the face") lists them.

The Pi works out everything on the screen and sends it as a FaceState; the face only draws.

    link = FaceLink("COM5", stations)        # "/dev/ttyACM0" on the Pi, or any file-like object
    while True:
        ...
        link.update(face_state(dial.snapshot(), controls))   # every frame: it decides what to send

Each message is one line of UTF-8 JSON ending in a newline:

    face -> Pi  {"t":"hello","v":1}                         booted or reset: please send everything
    Pi -> face  {"t":"stations","n":12765,"i":0,"pos":"…"}  station dots, 256 per line (pack_positions)
                {"t":"state","lat":37.5665,"lon":126.978,…}  what to draw (FaceState)
                {"t":"favs","pos":"…"}                       favorite spots, starred on the map
                {"t":"world","name":"Mojave Wasteland","coast":812,"border":240,"lat":0.1,"lon":-0.2,
                 "zoom":1700}                                a game world's map (its lines follow) and its
                                                             whole-map view, or {"t":"world"}: Earth
                {"t":"lines","layer":"coast","n":812,"i":0,"pos":"…"}  a world's lines, packed like the
                                                             stations, BREAK between lines
                {"t":"places","n":187,"i":0,"pos":"…","zoom":[400,…],"names":["New Vegas",…]}
                                                             a world's places to label, as Earth's cities
                {"t":"icon","name":"tuning","w":300,"h":34,"win":64,"n":10200,"i":0,"px":"…"}
                                                             a world's own signal symbol (worlds.Icon), an
                                                             8-bit mask, 1 KB of it to a line
                {"t":"sleep","fade":1.5}  {"t":"wake","fade":1.5}  standby: fade to dark, and back

On the laptop, loopback() joins a FaceLink to a VirtualFace (virtual_face.py) instead of a cable.
"""

import base64
import dataclasses
import io
import json
import math
import time
from collections import deque
from dataclasses import dataclass

import numpy as np

from . import worldtime
from .stations import haversine_km, place

PROTOCOL = 2                  # the "v" in hello; bump it when a message changes incompatibly
BAUD = 921600                 # ignored over the S3's own USB port (/dev/ttyACM0); only a USB-UART chip uses it
STATE_GAP_S = 1 / 30          # at most 30 state messages a second ...
GAP_SLACK_S = 0.003           # ... give or take frame timing (two frames at 60 fps are 33 ms apart, just)
HEARTBEAT_S = 1.0             # resend the state at least this often, so the face knows the Pi is there
STATIONS_PER_LINE = 256
STATION_LINES_PER_UPDATE = 8  # spread the station list over a few frames instead of stalling on it
TEXT_MAX = 64                 # names and places are cut to this many characters (the screen fits fewer)
POS_SCALE = 180               # station positions travel as whole 1/180ths of a degree (about 600 m)
BREAK = -32768                # a lines message: this (lat, lon) pair ends one line and starts the next
PLACES_PER_LINE = 24          # a world's places: names make these lines long, so fewer to a line
ICON_CHUNK = 1024             # a world's symbols: this many bytes of mask to a line (the face takes 2 KB lines)
REOPEN_S = 2.0                # how often to retry a serial port that is missing or was unplugged


@dataclass(frozen=True)
class Panel:
    """A card over the dimmed map, such as the Bluetooth list or Wi-Fi setup. It travels inside
    the state message as "panel"; the face draws it (map_view shows how)."""
    title: str
    lines: tuple = ()         # a few lines of text under the title
    items: tuple = ()         # a list to choose from: (label, detail) pairs, e.g. ("JBL Flip 6", "connected")
    sel: int = -1             # the highlighted item (-1: none)
    qr: str = ""              # text to show as a QR code (a Wi-Fi network to join)
    notes: tuple = ()         # a line or two at the bottom


@dataclass(frozen=True)
class FaceState:
    """Everything on the face's screen, as raw values: the face does the formatting
    (map_view.face_hud shows how). The field names are the state message's JSON keys."""
    lat: float                # dial position (under the crosshair), degrees, 5 decimals (about 1 m)
    lon: float
    zoom: int = 2000          # km across the window: 2000, 500, or 0 for the whole world
    theme: str = "night"      # color theme, one of themes.THEMES
    status: str = "idle"      # turning | tuning | playing | no signal | idle
    name: str = None          # the station shown, cut to TEXT_MAX characters; None = no station at all
    place: str = ""           # its region and country
    slat: float = 0.0         # its position, circled on the map (5 decimals)
    slon: float = 0.0
    km: int = 0               # how far the crosshair is from it, rounded; 0 means under 1 km, -1 not shown
                              # (a game world's distances mean nothing)
    vol: int = 80             # volume, 0-100
    ring: bool = False        # show the volume ring (for a moment after the volume changes)
    mute: bool = False
    fav: bool = False         # the station is a favorite: a star by its name
    toast: str = ""           # short message, e.g. "3rd nearest" or "Saved as favorite 1"
    song: str = ""            # what's playing, from the stream ("Artist - Title"), cut to TEXT_MAX
    local: int = None         # time at the station, minutes after midnight (None = unknown)
    clock: int = None         # time here, minutes after midnight
    idle: bool = False        # nobody has touched the controls for a while: show the clock
    liked: bool = False       # the song playing is one you liked: a heart by it
    alarm: int = None         # the alarm's time, minutes after midnight, when it's on
    timer: int = None         # minutes left on the sleep timer, when it's running
    bt: bool = False          # Bluetooth audio connected: a small Bluetooth symbol by the coordinates
    panel: Panel = None       # a card over the map (Bluetooth, Wi-Fi setup), or None
    sky: str = None           # the weather at the station, one of weather.SKIES (None = unknown)
    temp: int = None          # and its temperature, in the units the radio is set to
    borders: bool = False     # draw the borders between countries (a setting on the phone page)
    cities: bool = False      # label the major cities (a setting too)
    spin: bool = False        # idle: turn the globe slowly, like a desk globe (a setting too)
    date: str = None          # today where the radio is, "2026-10-21", for the idle clock
    bright: int = 100         # screen brightness, 10-100 % (a slider on the phone page)


def cut(text):
    """Cut long text with "…" (the screen cuts it shorter still) and drop what UTF-8 can't carry."""
    text = (text or "").encode("utf-8", "replace").decode("utf-8")
    return text if len(text) <= TEXT_MAX else text[:TEXT_MAX - 1].rstrip() + "…"


def face_state(st, controls, *, bt=False, alarm=None, timer=None, weather=None, borders=True, cities=True,
               spin=False, bright=100, when=None):
    """What the screen shows for a DialState (dial.snapshot()) and the Controls, at Unix time
    `when` (default now). alarm and timer: minutes, or None when off. weather: called with the
    station shown, returns (sky, temperature) or None. spin: turn the globe when idle."""
    when = time.time() if when is None else when
    shown = st.playing if st.status == "playing" and st.playing else st.nearest
    fs = FaceState(lat=round(st.lat, 5), lon=round(st.lon, 5), zoom=controls.zoom_km,
                   theme=controls.theme, status=st.status, vol=st.volume,
                   ring=controls.volume_shown(), mute=controls.muted, toast=controls.toast(),
                   clock=worldtime.local_minutes(when), idle=controls.idle, bt=bt,
                   panel=controls.panel_view(), alarm=alarm, timer=timer, borders=borders, cities=cities,
                   spin=spin and controls.idle, date=worldtime.local_date(when), bright=bright)
    if not shown:
        return fs
    km = haversine_km(st.lat, st.lon, shown["geo_lat"], shown["geo_long"])
    game = bool(shown.get("world"))     # a game world: no distance, and the time is yours
    return dataclasses.replace(
        fs, name=cut(shown["name"]), place=cut(place(shown)),
        slat=round(shown["geo_lat"], 5), slon=round(shown["geo_long"], 5),
        km=-1 if game else 0 if km < 1 else round(km), fav=controls.is_favorite(shown),
        song=cut(st.song) if shown is st.playing else "",
        liked=shown is st.playing and controls.is_liked(st.song, shown),
        local=fs.clock if game else worldtime.station_minutes(shown, when),
        **_weather(weather(shown) if weather else None))


def _weather(report):
    return {"sky": report[0], "temp": report[1]} if report else {}


# --- messages ----------------------------------------------------------------

def _line(msg):
    return json.dumps(msg, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"


def hello_message():
    return _line({"t": "hello", "v": PROTOCOL})


def sleep_message(fade_s=0):
    """fade_s: the face fades to black over this long, then turns its backlight off."""
    return _line({"t": "sleep", "fade": fade_s} if fade_s else {"t": "sleep"})


def wake_message(fade_s=0):
    """fade_s: the face fades in from black over this long (the alarm's sunrise)."""
    return _line({"t": "wake", "fade": fade_s} if fade_s else {"t": "wake"})


def state_message(fs):
    """A FaceState as one line. Fields that are false or empty are left out, to keep it short."""
    msg = {"t": "state", "lat": fs.lat, "lon": fs.lon, "zoom": fs.zoom, "theme": fs.theme,
           "status": fs.status}
    if fs.name is not None:
        msg.update(name=fs.name, place=fs.place, slat=fs.slat, slon=fs.slon, km=fs.km)
    msg["vol"] = fs.vol
    for key in ("ring", "mute", "fav", "idle", "liked", "bt", "borders", "cities", "spin"):
        if getattr(fs, key):
            msg[key] = 1
    for key in ("toast", "song"):
        if getattr(fs, key):
            msg[key] = getattr(fs, key)
    for key in ("local", "clock", "alarm", "timer"):    # 0 is midnight, so only None is left out
        if getattr(fs, key) is not None:
            msg[key] = getattr(fs, key)
    if fs.sky is not None and fs.temp is not None:
        msg.update(sky=fs.sky, temp=fs.temp)
    if fs.date:
        msg["date"] = fs.date
    if fs.bright != 100:
        msg["bright"] = fs.bright
    if fs.panel is not None:
        p = fs.panel
        panel = {"title": p.title}
        for key in ("lines", "notes"):
            if getattr(p, key):
                panel[key] = list(getattr(p, key))
        if p.items:
            panel["items"] = [list(item) for item in p.items]
        if p.sel != -1:
            panel["sel"] = p.sel
        if p.qr:
            panel["qr"] = p.qr
        msg["panel"] = panel
    return _line(msg)


def favorites_message(positions):
    """Favorite spots [(lat, lon)], packed like the stations message."""
    lats, lons = zip(*positions) if positions else ((), ())
    return _line({"t": "favs", "pos": base64.b64encode(pack_positions(lats, lons)).decode("ascii")})


def favorites_from_message(msg):
    q = np.frombuffer(base64.b64decode(msg["pos"]), dtype="<i2").reshape(-1, 2)
    return [(a / POS_SCALE, b / POS_SCALE) for a, b in q.tolist()]


def pack_positions(lats, lons):
    """Positions as the face keeps them: little-endian int16 pairs (lat, lon), each a whole
    number of 1/180ths of a degree (lat -16200..16200, lon -32400..32400)."""
    q = np.empty((len(lats), 2), dtype="<i2")
    q[:, 0] = np.round(np.asarray(lats, dtype=np.float64) * POS_SCALE)
    q[:, 1] = np.round(np.asarray(lons, dtype=np.float64) * POS_SCALE)
    return q.tobytes()


def stations_messages(stations, per_line=STATIONS_PER_LINE):
    """The station list as lines: n = how many in all, i = index of the first in this line,
    pos = base64 of pack_positions for up to per_line of them."""
    packed = pack_positions([s["geo_lat"] for s in stations], [s["geo_long"] for s in stations])
    n = len(stations)
    return [_line({"t": "stations", "n": n, "i": i,
                   "pos": base64.b64encode(packed[i * 4:(i + per_line) * 4]).decode("ascii")})
            for i in range(0, max(n, 1), per_line)]


def world_messages(world=None, per_line=STATIONS_PER_LINE):
    """A game world's map (worlds.World) as lines: a world message, then its coastlines and
    borders as lines messages. For Earth (None), a world message with no name."""
    if world is None:
        return [_line({"t": "world"})]
    out = []
    counts = {}
    for layer, lines in (("coast", world.coast), ("border", world.borders)):
        lats, lons = [], []
        for la, lo in lines:
            if lats:
                lats.append(BREAK / POS_SCALE)
                lons.append(BREAK / POS_SCALE)
            lats += la
            lons += lo
        packed = pack_positions(lats, lons)
        n = counts[layer] = len(lats)
        out += [_line({"t": "lines", "layer": layer, "n": n, "i": i,
                       "pos": base64.b64encode(packed[i * 4:(i + per_line) * 4]).decode("ascii")})
                for i in range(0, n, per_line)]
    places = world.places
    counts["places"] = len(places)
    for i in range(0, len(places), PLACES_PER_LINE):
        chunk = places[i:i + PLACES_PER_LINE]
        out.append(_line({"t": "places", "n": len(places), "i": i,
                          "pos": base64.b64encode(pack_positions([p[1] for p in chunk], [p[2] for p in chunk])).decode(),
                          "zoom": [round(p[3] * 100) for p in chunk], "names": [cut(p[0]) for p in chunk]}))
    out += icon_messages(world.icons)
    lat, lon, zoom = world.home
    return [_line({"t": "world", "name": cut(world.title), **counts, "lat": lat, "lon": lon, "zoom": zoom})] + out


def icon_messages(icons):
    """A world's own signal symbols ({state: worlds.Icon}) as icon messages, ICON_CHUNK bytes of
    mask to a line. A face that doesn't know them ignores them and draws its usual ((•))."""
    out = []
    for state, icon in icons.items():
        for i in range(0, len(icon.mask), ICON_CHUNK):
            out.append(_line({"t": "icon", "name": state, "w": icon.w, "h": icon.h, "win": icon.window,
                              "n": len(icon.mask), "i": i,
                              "px": base64.b64encode(icon.mask[i:i + ICON_CHUNK]).decode("ascii")}))
    return out


class IconReceiver:
    """Puts a world's symbols back together from icon messages (the virtual face)."""

    def __init__(self):
        self._parts = {}

    def add(self, msg):
        """Takes one message; returns (state, worlds.Icon) once that symbol is all in, else None."""
        from .worlds import ICON_MAX, ICON_STATES, Icon
        name, w, h, win, n, i = (msg["name"], int(msg["w"]), int(msg["h"]), int(msg.get("win") or 0),
                                 int(msg["n"]), int(msg["i"]))
        if name not in ICON_STATES or n != w * h or not (0 < w <= ICON_MAX[0] and 0 < h <= ICON_MAX[1]):
            raise ValueError("bad icon")
        key = (w, h, win, n)
        if name not in self._parts or self._parts[name][0] != key:
            self._parts[name] = (key, bytearray(n), set())
        _, buf, got = self._parts[name]
        data = base64.b64decode(msg["px"])
        buf[i:i + len(data)] = data[:max(0, n - i)]
        got.add(i)
        if got >= set(range(0, n, ICON_CHUNK)):
            del self._parts[name]
            return name, Icon(bytes(buf), w, h, win)
        return None


def lines_from_points(lats, lons):
    """A received layer's points (StationsReceiver) -> [(lats, lons)] lines, split at BREAK."""
    out, a, b = [], [], []
    for la, lo in zip(lats, lons):
        if round(la * POS_SCALE) == BREAK:
            out.append((a, b))
            a, b = [], []
        else:
            a.append(la)
            b.append(lo)
    out.append((a, b))
    return [line for line in out if len(line[0]) >= 2]


def decode(line):
    """One line (bytes or str) -> dict. Raises ValueError if it isn't a JSON object."""
    msg = json.loads(line)
    if not isinstance(msg, dict):
        raise ValueError("not a JSON object")
    return msg


def state_from_message(msg):
    """A decoded state message -> FaceState (missing fields take their defaults)."""
    station = {}
    if "name" in msg:
        station = dict(name=msg["name"], place=msg.get("place", ""), slat=float(msg["slat"]),
                       slon=float(msg["slon"]), km=int(msg["km"]))
    return FaceState(lat=float(msg["lat"]), lon=float(msg["lon"]), zoom=int(msg["zoom"]),
                     theme=msg["theme"], status=msg["status"], vol=int(msg["vol"]),
                     ring=bool(msg.get("ring")), mute=bool(msg.get("mute")), fav=bool(msg.get("fav")),
                     idle=bool(msg.get("idle")), bt=bool(msg.get("bt")), toast=msg.get("toast", ""),
                     borders=bool(msg.get("borders")), cities=bool(msg.get("cities")),
                     spin=bool(msg.get("spin")),
                     date=str(msg["date"]) if msg.get("date") else None, bright=int(msg.get("bright", 100)),
                     song=msg.get("song", ""), local=_int_or_none(msg.get("local")),
                     clock=_int_or_none(msg.get("clock")), panel=_panel(msg.get("panel")),
                     liked=bool(msg.get("liked")), alarm=_int_or_none(msg.get("alarm")),
                     timer=_int_or_none(msg.get("timer")),
                     **_weather((str(msg["sky"]), int(msg["temp"])) if "sky" in msg and "temp" in msg else None),
                     **station)


def _panel(d):
    if d is None:
        return None
    return Panel(title=d["title"], lines=tuple(d.get("lines", ())), notes=tuple(d.get("notes", ())),
                 items=tuple(tuple(item) for item in d.get("items", ())), sel=int(d.get("sel", -1)),
                 qr=d.get("qr", ""))


def _int_or_none(value):
    return None if value is None else int(value)


class StationsReceiver:
    """Puts the station list back together from stations messages, as the face does."""

    def __init__(self):
        self._q = self._got = None

    def add(self, msg):
        """Take one stations message. Returns (lats, lons) once the whole list is in, else None."""
        n, i = int(msg["n"]), int(msg["i"])
        q = np.frombuffer(base64.b64decode(msg["pos"]), dtype="<i2").reshape(-1, 2)
        if i == 0 or self._q is None or len(self._q) != n:
            self._q, self._got = np.zeros((n, 2), dtype="<i2"), np.zeros(n, dtype=bool)
        self._q[i:i + len(q)] = q
        self._got[i:i + len(q)] = True
        if not self._got.all():
            return None
        lats, lons = self._q[:, 0] / POS_SCALE, self._q[:, 1] / POS_SCALE
        self._q = self._got = None
        return lats, lons


# --- the link ----------------------------------------------------------------

class FaceLink:
    """The Pi's end of the cable. Sends the state when it changes (at most 30 times a second, and
    at least once a second), and the station list whenever the face says hello."""

    def __init__(self, port, stations, *, world=None, baud=BAUD, log=print, clock=time.monotonic):
        """port: a serial port name (needs pyserial), or any object with write(bytes); if it also
        has pyserial's in_waiting and read(), the face's hello is heard too. world: the game
        world (worlds.World) the stations are in, or None for Earth."""
        self.log, self.clock, self.baud = log, clock, baud
        self.port_name = port if isinstance(port, str) else None
        self.port = None if self.port_name else port
        self._text = isinstance(port, io.TextIOBase)
        self._stations = world_messages(world) + stations_messages(stations)
        self._favorites = None                 # the last favs line
        self._outbox = deque(self._stations)   # a face that booted before us never got its answer
        self._inbox = bytearray()
        self._last_line, self._last_sent = None, -math.inf
        self._force = True
        self._asleep = False
        self._retry_at = 0.0
        self._warned = False
        if self.port_name:
            self._open()

    def update(self, fs, now=None):
        """Call every frame with the current FaceState."""
        now = self.clock() if now is None else now
        if not self._connected(now):
            return
        self.poll()
        if not self._asleep:
            line = state_message(fs)
            since = now - self._last_sent
            if self._force or (since >= STATE_GAP_S - GAP_SLACK_S
                               and (line != self._last_line or since >= HEARTBEAT_S)):
                self._write(line)
                self._last_line, self._last_sent, self._force = line, now, False
        for _ in range(min(STATION_LINES_PER_UPDATE, len(self._outbox))):
            self._write(self._outbox.popleft())

    def poll(self):
        """Read what the face has said, and answer a hello with the station list and the state."""
        port = self.port
        if port is None:
            return
        try:
            # Inside the try: on a real port, even asking for in_waiting talks to the cable, so an
            # unplugged face raises OSError right here (getattr's default only covers ports that
            # can't read at all).
            waiting = getattr(port, "in_waiting", None)
            if waiting is None:
                return
            if waiting:
                self._inbox += port.read(waiting)
        except OSError as e:
            self._lost(e)
            return
        while b"\n" in self._inbox:
            line, _, rest = self._inbox.partition(b"\n")
            self._inbox = bytearray(rest)
            try:
                msg = decode(line)
            except ValueError:
                # Not a message: boot chatter or a debug print from the face. Show it, it may help.
                text = line.decode("utf-8", "replace").strip()
                if text:
                    self.log(f"Face says: {text}")
                continue
            if msg.get("t") == "hello":
                if msg.get("v") != PROTOCOL:
                    self.log(f"Face speaks protocol {msg.get('v')}, this radio {PROTOCOL}; "
                             "flash the face with the matching face.ino.")
                self._resend_all()
        if len(self._inbox) > 4096:     # a long run of junk with no newline
            self._inbox.clear()

    def send_stations(self, stations, world=None):
        """Send a new station list (after the monthly refresh)."""
        self._stations = world_messages(world) + stations_messages(stations)
        self._outbox = deque(self._stations)

    def set_favorites(self, positions):
        """Favorite spots [(lat, lon)]. Call any time; sent only when they change (and after hello)."""
        line = favorites_message(positions)
        if line != self._favorites:
            self._favorites = line
            self._outbox.append(line)

    def _resend_all(self):
        self._outbox = deque(self._stations + ([self._favorites] if self._favorites else []))
        self._force = True

    def sleep(self, fade_s=0):
        """Standby: the face fades out and turns its screen off; state messages stop until wake()."""
        self._asleep = True
        self._write(sleep_message(fade_s))

    def wake(self, fade_s=0):
        self._asleep = False
        self._write(wake_message(fade_s))
        self._force = True

    def close(self):
        if self.port_name and self.port is not None:
            self.port.close()
        self.port = None

    # --- the port ------------------------------------------------------------

    def _connected(self, now):
        if self.port is not None:
            return True
        if self.port_name is None or now < self._retry_at:
            return False
        self._retry_at = now + REOPEN_S
        return self._open()

    def _open(self):
        try:
            import serial   # pyserial: only needed with a real cable
        except ImportError:
            raise SystemExit("Talking to the face needs pyserial:  python -m pip install -r requirements.txt")
        try:
            # serial_for_url also takes pyserial's URLs, e.g. loop:// to test without a face.
            self.port = serial.serial_for_url(self.port_name, self.baud, timeout=0, write_timeout=0.5)
        except (OSError, ValueError) as e:   # serial.SerialException is an OSError
            if not self._warned:
                self.log(f"Face: can't open {self.port_name} ({e}). Trying again every {REOPEN_S:.0f} s.")
                self._warned = True
            return False
        self.log(f"Face: connected on {self.port_name}.")
        self._warned = False
        self._inbox.clear()
        self._resend_all()
        return True

    def _lost(self, error):
        self.log(f"Face: connection lost ({error}).")
        if self.port_name:
            try:
                self.port.close()
            except OSError:
                pass
        self.port = None
        self._retry_at = self.clock() + REOPEN_S

    def _write(self, line):
        if self.port is None:
            return
        try:
            self.port.write(line.decode("utf-8") if self._text else line)
        except OSError as e:
            self._lost(e)


class _LoopbackEnd:
    """One end of an in-memory cable, with the bits of pyserial's interface FaceLink uses."""

    def __init__(self):
        self._inbox = bytearray()
        self.peer = None

    def write(self, data):
        self.peer._inbox += data
        return len(data)

    @property
    def in_waiting(self):
        return len(self._inbox)

    def read(self, size=1):
        data = bytes(self._inbox[:size])
        del self._inbox[:size]
        return data


def loopback():
    """Two joined ends of a pretend cable: (radio_end, face_end)."""
    a, b = _LoopbackEnd(), _LoopbackEnd()
    a.peer, b.peer = b, a
    return a, b
