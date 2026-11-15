"""The round map: a night-style globe that turns under a fixed crosshair.

Draws square Pillow images with only numpy + Pillow: 480x480 to preview the
round ESP32-S3 face on the laptop (the face itself draws the same design in C++),
or 240x240 for the fallback 1.28" round screen, which the Pi would drive itself.
Everything outside the circle is black.

    view = MapView(stations)              # or MapView(stations, size=240)
    image = view.render(lat, lon, radius_px, hud)

The projection is orthographic: the globe as you'd see it from far out in
space, centered on (lat, lon). radius_px is the globe's radius on screen, and it
sets the zoom (at 480 px: about 1,530 px for the 2,000 km view, 220 px for the
whole world); radius_for_view() works it out. Camera eases the view after the dial so each click glides.
FaceScreen puts the two together and draws a FaceState (face_link.py), the same state the face gets.

Try it without a window:
    python -m terradio.map_view --png map.png --at 51.5,-0.1 [--km 500 | --world] [--size 240]
                                 [--theme radar]
    python -m terradio.map_view --sheet themes.png   # every theme side by side
    python -m terradio.map_view --bench          # frame-time measurement, both sizes
"""

import argparse
import dataclasses
import datetime
import math
import statistics
import time
import unicodedata
from dataclasses import dataclass

import numpy as np
import segno
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from . import coastlines
from .coastlines import unit_vectors
from .stations import haversine_km, place
from .themes import THEMES, get_theme
from .worldtime import fmt_time

SIZE = 480                         # pixels across: the round face (240 for the 1.28" fallback)
LAYOUT = 320                       # positions below are designed at 320 px and scaled to SIZE
EARTH_KM = 6371.0
WORLD_MARGIN = 14                  # world view: gap between the globe's edge and the window's (at 320)
DETAIL_RADIUS = 400                # above this globe radius (at 320), draw the finer 50m coastlines
CLOSE_RADIUS = 2500                # above this (at 320), the closest zoom: 10m coastlines, finer grid
MIN_TEXT_PX = 10                   # smaller than this is unreadable on a ~6 px/mm screen

LIGHT_MAX = 8                      # stations per pixel at which the light layer tops out
GLOW_MAX = 110                     # brightest the glow between dots gets (out of 255)
CORE_GAIN = (1.5, 14.0)            # how bright a lone station's dot is: whole world .. closest zoom
GLOW_RADIUS = 1.5                  # how far the glow around the dots reaches (at 320): a tight halo
CROSSFADE_S = 0.4                  # a new theme, or the idle clock, fades in over this long
TOAST_IN_S, TOAST_OUT_S = 0.15, 0.4
ON_AIR_S = 3.0                     # once a station plays, the on-air symbol shows this long ...
TOP_FADE_S = 0.3                   # ... then crossfades back to the time there
IDLE_ZOOM_S = 0.6                  # the slow zoom out to the whole world when the radio goes idle
LOCK_GLIDE_S = 0.25                # the gentle glide when the dial locks onto a station on air
# A jump further than FLY_MIN_DEG (surprise me, a faraway favorite, the alarm) is flown: the
# camera backs out towards the whole world, turns the globe and comes back in, over FLY_S.
FLY_MIN_DEG = 25
FLY_S = 2.4
# Idle, the globe can turn like a desk globe: the land drifts left to right, as the Earth turns.
SPIN_DEG_S = 2.0                   # one full turn every 3 minutes
SPIN_RAMP_S = 4.0                  # it eases in over this long
SPIN_FPS = 15                      # and steps 15 times a second: smooth at this speed, light on the face
# A game world (worlds.py) doesn't spin when idle (its map would turn away): the whole map drifts
# gently under the window, in a slow loop, DRIFT_SHARE of the view's width either way.
DRIFT_SHARE = 0.08
DRIFT_PERIODS_S = (170.0, 260.0)   # north-south and east-west: never quite the same path twice
DRIFT_RAMP_S = 20.0                # it eases in over this long
FLY_MIN_SHARE = 0.3                # a game world: jumps further than this share of the map are flown
# The crosshair is for aiming: this long after a station locks on, with nobody at the controls,
# it fades away (slowly), and it's back the moment anything is touched (quickly).
CROSSHAIR_S = 15.0
CROSSHAIR_OUT_S, CROSSHAIR_IN_S = 1.0, 0.15
TUNE_CYCLE_S = 1.2                 # the tuning symbol's waves sweep outward once in this long
WAVE_CYCLE_S = 4.0                 # a world's scrolling tuning symbol goes round once in this long
# A line too long for the circle scrolls once, then rests cut short with "…": it waits, glides
# to its end at reading speed, waits again, and fades back to its start.
SCROLL_HOLD_S = 2.0
SCROLL_PX_S = 19                   # at the 320 px layout: 28 px/s on the 480 px face
SCROLL_FADE_S = 0.3
EDGE_FADE = 10                     # soft ends of a scrolling line (at 320)
# Where each line of text goes: (font, size, height), all at the 320 px layout.
LINES = {"title": ("bold", 18, 224), "subtitle": ("regular", 13, 246),
         "detail": ("regular", 12, 266)}
# The slot under the coordinates holds one thing at a time: the time at the station, the status
# symbol, or a toast.
TOP_Y = 46
# The idle clock: the alarm and sleep timer at the top (in the slot under where the coordinates
# usually are), and over the time, the date (then the station and the song, on the usual lines).
REMINDER_Y, DATE_Y, CLOCK_Y = TOP_Y, 186, 214
SIGNAL_Y = 47                      # the symbol sits a pixel lower, to look centered
PANEL_DIM = 0.84                   # how far the map fades behind a card (Bluetooth, Wi-Fi setup)
QR_SIZE = 100                      # a QR code's side (at 320): about 25 mm on the face
QR_COLORS = ((238, 241, 245), (6, 8, 14))   # light square, dark dots: what phone cameras read best
# Cities (a setting): each is labeled once the view is CITY_ZOOM_GAP past the web-map zoom Natural
# Earth gives it, so none in the whole-world view, a few big ones at 2,000 km, more at 500 km.
# Labels stay clear of the text at the top and bottom, the crosshair and each other.
CITY_ZOOM_GAP = 1.2
CITY_MAX = 8                       # the most labels on screen at once
CITY_TEXT = 10                     # label size (at 320)
CITY_BAND = (60, 208)              # labels go between these heights (at 320), clear of the text
CITY_CLEAR = 30                    # and no nearer the center than this, clear of the crosshair


# Fonts, tried in order. Pillow finds bare file names in the system font
# folders (C:\Windows\Fonts, /usr/share/fonts). Segoe UI is on Windows; DejaVu
# and Noto are on Raspberry Pi OS. If none is found, Pillow's built-in font is used.
FONT_FILES = {
    "regular": ("segoeui.ttf", "DejaVuSans.ttf", "NotoSans-Regular.ttf"),
    "bold": ("seguisb.ttf", "DejaVuSans-Bold.ttf", "NotoSans-SemiBold.ttf"),
    "cjk": ("malgun.ttf", "NotoSansCJK-Regular.ttc", "msyh.ttc"),  # Korean, Chinese, Japanese
    "cjk-bold": ("malgunbd.ttf", "NotoSansCJK-Bold.ttc", "msyhbd.ttc"),
}


def radius_for_view(width_deg, size=SIZE):
    """Globe radius in pixels that makes the window show width_deg of arc edge to edge.

    180 or more means the whole world: the full globe disc inside the window.
    """
    if width_deg >= 180:
        return (LAYOUT / 2 - WORLD_MARGIN) * size / LAYOUT
    return (size / 2) / math.sin(math.radians(width_deg) / 2)


def width_for_zoom(zoom_km):
    """Degrees of arc across the window for a zoom in km across (0 = the whole world), the same
    sum as Controls.view_width_deg."""
    return 180.0 if not zoom_km else math.degrees(zoom_km / EARTH_KM)


def _view_matrix(lat, lon, radius):
    """3x3 matrix taking unit vectors to (screen x offset, screen y offset, depth).

    Columns are the local east, south and up directions at (lat, lon), with the
    first two scaled to pixels. Depth > 0 means the point faces the viewer.
    """
    p, l = math.radians(lat), math.radians(lon)
    sp, cp, sl, cl = math.sin(p), math.cos(p), math.sin(l), math.cos(l)
    east = (-sl, cl, 0.0)
    north = (-sp * cl, -sp * sl, cp)
    up = (cp * cl, cp * sl, sp)
    return np.array([[east[i] * radius, -north[i] * radius, up[i]] for i in range(3)],
                    dtype=np.float32)


@dataclass(frozen=True)
class Hud:
    """Everything drawn on top of the map. Front ends build it with make_hud()."""
    title: str = ""            # station name
    subtitle: str = ""         # region, country
    detail: str = ""           # how far the station is while rolling; the song while it plays
    tone: str = "dim"          # color of the detail line: dim | live | warn
    coords: str = ""           # dial position, at the top
    toast: str = ""            # short message, e.g. "Saved to favorites"
    volume: int = None         # 0-100 to show the volume ring, None to hide it
    marker: tuple = None       # (lat, lon) of the station to circle
    marker_live: bool = False
    favorite: bool = False     # draw a star by the title
    clock: str = ""            # the idle screen's big clock (replaces the title)
    toast_alpha: float = 1.0   # the toast fades in and out
    crosshair: float = 1.0     # how visible the crosshair is (it fades while you listen)
    bt: bool = False           # Bluetooth audio connected: a small symbol after the coordinates
    panel: object = None       # a card over the dimmed map (face_link.Panel), or None
    liked: bool = False        # a heart before the song: one you liked
    reminder: str = ""         # the idle clock's small line: the alarm and the sleep timer
    signal: str = ""           # status symbol at the top: tuning | playing | no signal ("" = none)
    signal_phase: float = 0.0  # where the tuning waves are in their sweep, 0-1
    signal_scroll: float = 0.0  # a world's scrolling tuning symbol (the Pip-Boy's wave): how far round, 0-1
    signal_alpha: float = 1.0  # how visible it is (it shares its slot with the time and toasts)
    local: str = ""            # the time at the station, in the same slot
    weather: tuple = None      # and the weather there, after the time: (sky, "18°")
    local_alpha: float = 1.0   # how visible the time and the weather are
    scroll: tuple = ()         # lines on the move: ((field, offset in px or None, alpha), ...)
    borders: bool = False      # country borders on the map
    cities: bool = False       # major cities on the map, labeled
    date: str = ""             # the idle clock's date line, over the time: "Wed Oct 21"


def _fmt_coords(lat, lon):
    return f"{abs(lat):.2f}° {'N' if lat >= 0 else 'S'}   {abs(lon):.2f}° {'E' if lon >= 0 else 'W'}"


def _fmt_km(km):
    return "under 1 km" if km < 1 else f"{km:,.0f} km"


_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def fmt_date(iso):
    """"2026-10-21" -> "Wed Oct 21" (English whatever the computer's language)."""
    try:
        d = datetime.date.fromisoformat(iso)
    except (TypeError, ValueError):
        return ""
    return f"{_WEEKDAYS[d.weekday()]} {_MONTHS[d.month - 1]} {d.day}"


class _DefaultControls:
    """Stand-in Controls for make_hud: 2,000 km, no ring, nothing saved."""
    zoom_km, theme, muted, idle = 2000, "night", False, False

    def __init__(self, toast=""):
        self._toast = toast

    def toast(self):
        return self._toast

    def volume_shown(self):
        return False

    def is_favorite(self, station):
        return False

    def panel_view(self):
        return None

    def is_liked(self, song, station):
        return False


def make_hud(state, *, toast="", when=None):
    """What the screen shows for a DialState, for the command line below. The radio itself
    goes through face_link.face_state and face_hud."""
    from .face_link import face_state
    return face_hud(face_state(state, _DefaultControls(toast), when=when))


def face_hud(fs):
    """Turn a FaceState (face_link.py: what the Pi tells the face) into what the screen shows.
    face.ino formats the text the same way."""
    return dataclasses.replace(_face_hud(fs), borders=fs.borders, cities=fs.cities)


def _face_hud(fs):
    marker = (fs.slat, fs.slon) if fs.name is not None else None
    if fs.panel is not None:
        # A card (Bluetooth, Wi-Fi setup) over the dimmed map; the volume ring still shows.
        return Hud(panel=fs.panel, volume=fs.vol if fs.ring else None, crosshair=0.0)
    if fs.idle:
        # Nobody's touching it: a big clock, the station and the song, nothing else.
        reminder = []
        if fs.timer is not None:
            reminder.append(f"Sleep in {fs.timer} min")
        if fs.alarm is not None:
            reminder.append(f"Alarm {fmt_time(fs.alarm)}")
        return Hud(clock=fmt_time(fs.clock) if fs.clock is not None else "", subtitle=fs.name or "",
                   date=fmt_date(fs.date) if fs.date else "",
                   crosshair=0.0, reminder=" · ".join(reminder), liked=fs.liked and bool(fs.song),
                   detail=fs.song, marker=marker, marker_live=fs.status == "playing")
    coords = _fmt_coords(fs.lat, fs.lon)
    volume = fs.vol if fs.ring else None
    if fs.name is None:
        return Hud(title="No stations", coords=coords, toast=fs.toast, volume=volume)
    # Under the coordinates: the time at the station while you roll and while you listen, and
    # the status symbol while tuning or when nothing comes in. (On the face the on-air symbol
    # also shows for ON_AIR_S when a station starts: FaceScreen does that.) A toast takes over.
    playing = fs.status == "playing"
    local = fmt_time(fs.local) if fs.local is not None else ""
    weather = (fs.sky, f"{fs.temp}°".replace("-", "−")) if fs.sky is not None and fs.temp is not None \
        else None                           # a real minus sign: "−4°"
    about = bool(local or weather)          # what it's like at the station: the time, the weather
    signal = fs.status if fs.status in ("tuning", "playing", "no signal") else ""
    show_signal = bool(signal) and not (playing and about)
    free = 0.0 if fs.toast else 1.0
    # Under the place: the distance only matters while you roll (how far the nearest station
    # is, out over an ocean); once a station plays, that line is the song's.
    # (A game world's distances mean nothing: km is -1 there, and the line stays empty.)
    detail = (f"{_fmt_km(fs.km)} away" if fs.km >= 0 else "") if fs.status in ("turning", "idle")         else fs.song if playing else ""
    return Hud(title=fs.name, subtitle=fs.place, detail=detail, coords=coords, toast=fs.toast,
               volume=volume, marker=marker, marker_live=playing, favorite=fs.fav, signal=signal, bt=fs.bt,
               liked=fs.liked and playing and bool(fs.song),
               signal_alpha=free if show_signal else 0.0, local=local, weather=weather,
               local_alpha=free if about and not show_signal else 0.0)


def face_view(fs, size=SIZE, home=None):
    """Where a face showing this FaceState settles: (globe radius in pixels, Hud). Idle, it
    backs out to the whole world, or in a game world to its whole map (home: (lat, lon, km))."""
    idle_zoom = home[2] if home is not None else 0
    return radius_for_view(width_for_zoom(idle_zoom if fs.idle else fs.zoom), size), face_hud(fs)


class Camera:
    """Eases the view towards the dial, so each knob click becomes a short glide."""

    def __init__(self, lat, lon, radius, glide_s=0.07, zoom_s=0.12):
        self.lat, self.lon, self.radius = lat, lon, radius
        self.glide_s, self.zoom_s = glide_s, zoom_s

    def update(self, lat, lon, radius, dt):
        """Move part of the way to the target. Returns True while still moving."""
        k = 1 - math.exp(-dt / self.glide_s)
        kz = 1 - math.exp(-dt / self.zoom_s)
        dlat = lat - self.lat
        dlon = (lon - self.lon + 180) % 360 - 180          # the short way round
        dzoom = math.log(radius / self.radius)
        # Stop once the rest of the move is under ~1/20 of a pixel.
        snap = 0.05 / (self.radius * math.pi / 180)
        if abs(dlat) < snap and abs(dlon) * math.cos(math.radians(lat)) < snap and abs(dzoom) < 0.002:
            moved = (self.lat, self.lon, self.radius) != (lat, lon, radius)
            self.lat, self.lon, self.radius = lat, lon, radius
            return moved
        self.lat += dlat * k
        self.lon = (self.lon + dlon * k + 180) % 360 - 180
        self.radius *= math.exp(dzoom * kz)
        return True


class _Fonts:
    def __init__(self):
        self._cache = {}

    def get(self, role, size, text):
        if any(_is_cjk(ch) for ch in text):
            role = "cjk-bold" if role == "bold" else "cjk"
        key = (role, size)
        if key not in self._cache:
            font = None
            for name in FONT_FILES[role] + FONT_FILES["regular"]:
                try:
                    font = ImageFont.truetype(name, size)
                    break
                except OSError:
                    continue
            self._cache[key] = font or ImageFont.load_default(size)
        return self._cache[key]


def _is_cjk(ch):
    o = ord(ch)
    return 0x2E80 <= o <= 0x9FFF or 0xAC00 <= o <= 0xD7AF or 0xF900 <= o <= 0xFAFF or 0xFF00 <= o <= 0xFFEF


def _printable(text):
    """Drop emoji and pictographs, which none of the fonts can draw. (Unicode counts ° © ® as
    symbols too, but every font has those.)"""
    return "".join(ch for ch in text if ord(ch) < 0x10000 and (unicodedata.category(ch) != "So"
                                                               or ch in "°©®")).strip()


def _taps(v, wide):
    """For dots at pixel-center coordinates v: the first pixel each touches along one axis,
    and the weights for it and the pixels after it (2 taps, or 3 when wide)."""
    if wide:
        a = np.floor(v - 0.5)
        f = v - 0.5 - a
        return a.astype(np.int32), ((1 - f) / 2, np.full_like(f, 0.5), f / 2)
    a = np.floor(v)
    f = v - a
    return a.astype(np.int32), (1 - f, f)


class _Stopwatch:
    """Adds the time since the last call to out[name]. Does nothing if out is None."""

    def __init__(self, out):
        self.out = out
        self.t = time.perf_counter()

    def __call__(self, name):
        if self.out is not None:
            now = time.perf_counter()
            self.out[name] = self.out.get(name, 0.0) + (now - self.t)
            self.t = now


class MapView:
    def __init__(self, stations, size=SIZE, theme=THEMES[0]):
        self.size = size
        self.k = size / LAYOUT                      # layout scale
        # Text grows with the screen, but shrinks less on small ones so it stays legible.
        self.text_k = self.k if self.k >= 1 else math.sqrt(self.k)
        # Big screens: spread each dot over 3x3 pixels instead of 2x2 so it keeps its size,
        # and blur the glow at half size to save time (any smaller and the tight halo turns blocky).
        self._wide_dots = self.k >= 1.25
        self._glow_shrink = 2 if self.k >= 1.25 else 1
        self._world_radius = radius_for_view(180, size)
        self.set_stations(stations)
        self.coast = {res: coastlines.load(res) for res in coastlines.RESOLUTIONS}
        self._borders = {}             # country borders, loaded the first time they're shown
        self._world = None             # a game world's lines in place of Earth's (set_world)
        self._icons = {}               # ... and its own signal symbols: state -> (mask, window px)
        self._cities = None            # and the cities
        self._city_labels = {}
        # Latitude/longitude grid every 30° (world), 10° (2,000 km) or 2° (500 km).
        self.grid = {30: coastlines.graticule(30, 2.0), 10: coastlines.graticule(10, 1.0),
                     2: coastlines.graticule(2, 0.5)}
        c = size / 2
        yy, xx = np.mgrid[0:size, 0:size].astype(np.float32) + 0.5
        self._y = yy
        self._r = np.hypot(xx - c, yy - c)          # each pixel's distance from the center
        self._lut_cache = {}
        self._fonts = _Fonts()
        self._patches = {}
        self._strips = {}
        self._edges = {}
        self._icons = {}
        self._signal = self._build_signal()
        self._crosshair = self._build_crosshair()
        self.set_theme(theme)

    def set_stations(self, stations):
        """The dots to draw: dicts with geo_lat and geo_long (the face gets them from the Pi)."""
        self.station_xyz = unit_vectors(np.array([s["geo_lat"] for s in stations], dtype=np.float64),
                                        np.array([s["geo_long"] for s in stations], dtype=np.float64))

    def set_world(self, coast=None, borders=None, places=None, *, icons=None, earth=False):
        """Draw a game world (worlds.py) instead of Earth: its coastlines and borders as lists of
        (lats, lons) lines, used at every zoom, and its places [(name, lat, lon, zoom)] labeled as
        Earth's cities are. icons: its own signal symbols ({state: worlds.Icon}). earth=True goes
        back to Earth."""
        self._icons = self._scale_icons(icons or {})
        def poly(lines):
            lines = [line for line in (lines or []) if len(line[0]) >= 2]
            return coastlines.Polylines.from_lines(lines) if lines else None
        if earth:
            self._world = None
            return
        places = sorted(places or [], key=lambda p: p[3])
        cities = coastlines.Cities([p[0] for p in places],
                                   unit_vectors(np.array([p[1] for p in places], dtype=np.float64),
                                                np.array([p[2] for p in places], dtype=np.float64)),
                                   np.array([p[3] for p in places], dtype=np.float64))
        self._world = {"coast": poly(coast), "border": poly(borders), "places": cities}

    def set_theme(self, theme):
        """Switch to another Theme. Rebuilds the cached layers that hold colors."""
        self.theme = theme
        self._bg_radius, self._bg = None, None
        self._overlays = self._build_overlays()
        # Station light -> color: the glow color at low levels, the dot-core color at the cores.
        ramp = np.clip((np.arange(256) - GLOW_MAX) / (255 - GLOW_MAX), 0, 1)[:, None]
        self._palette = np.round(np.array(theme.dot_glow) * (1 - ramp) + np.array(theme.dot_core) * ramp
                                 ).astype(np.uint8).tobytes()

    # --- layers ------------------------------------------------------------

    def _background(self, radius):
        """Globe, limb shading and atmosphere glow for this zoom (cached, since zoom rarely changes)."""
        if self._bg_radius == radius:
            return self._bg
        r = self._r
        rr = np.minimum(r / np.float32(radius), 1.0)
        shade = 0.45 + 0.55 * np.sqrt(1 - rr * rr)  # darker towards the globe's edge
        rim = np.exp(-np.maximum(radius - r, 0) / (3.0 * self.k)) * 0.5
        halo = np.exp(-np.maximum(r - radius, 0) / (6.0 * self.k)) * 0.55
        inside = np.clip(radius - r + 0.5, 0, 1)    # anti-aliased limb
        bands = []
        t = self.theme
        for g, a, sp in zip(t.globe, t.atmosphere, t.space):
            globe = shade * g + rim * a
            space = sp + halo * a
            band = np.clip(np.round(globe * inside + space * (1 - inside)), 0, 255)
            bands.append(Image.fromarray(band.astype(np.uint8), "L"))
        self._bg = Image.merge("RGB", bands)
        self._bg_radius = radius
        return self._bg

    def _draw_lines(self, draw, poly, m, radius, fill, margin=200):
        """Draw the visible parts of many polylines straight onto the frame."""
        s, c = self.size, self.size / 2
        # Anything beyond the corners of the (margin-padded) square is off-screen.
        half_angle = math.asin(min(1.0, (c + margin) * math.sqrt(2) / radius))
        points, starts = poly.gather(m[:, 2], half_angle)
        if len(points) < 2:
            return
        p = points @ m
        x = p[:, 0] + c
        y = p[:, 1] + c
        vis = (p[:, 2] > 0) & (x > -margin) & (x < s + margin) & (y > -margin) & (y < s + margin)
        # pair[i]: draw the segment from point i to point i+1.
        pair = vis[:-1] & vis[1:] & ~starts[1:]
        if not pair.any():
            return
        used = np.zeros(len(vis), dtype=bool)
        used[:-1] |= pair
        used[1:] |= pair
        sel = np.flatnonzero(used)
        joined = (np.diff(sel) == 1) & pair[sel[:-1]]
        bounds = np.concatenate(([0], np.flatnonzero(~joined) + 1, [len(sel)])) * 2
        flat = np.column_stack((x[sel], y[sel])).ravel().tolist()
        for a, b in zip(bounds[:-1].tolist(), bounds[1:].tolist()):
            draw.line(flat[a:b], fill=fill)

    def _draw_cities(self, img, draw, m, radius):
        """The biggest cities in view for this zoom: a small ring and the name beside it (on the
        right, or the left if that's taken), with a thin outline in the globe's color so lines and
        station glow under the name don't hide it. A city whose name doesn't fit isn't drawn at all."""
        # The web-map zoom with about as many km to a pixel: 2 pi R / 2^z / 256 = R / radius.
        limit = math.log2(2 * math.pi * radius / 256) - CITY_ZOOM_GAP
        if self._world is not None:
            cities = self._world["places"]      # a game world: its own places
        else:
            if self._cities is None:
                if limit < 1:
                    return              # the whole world: none to draw, so don't load them yet
                self._cities = coastlines.load_cities()
            cities = self._cities
        if limit < 1 or not len(cities.names):
            return
        n = cities.upto(limit)
        if not n:
            return
        c, k, t = self.size / 2, self.k, self.theme
        p = cities.xyz[:n] @ m
        x, y = p[:, 0] + c, p[:, 1] + c
        top, bottom = CITY_BAND[0] * k, CITY_BAND[1] * k
        seen = np.flatnonzero((p[:, 2] > 0) & (y > top) & (y < bottom) & (np.hypot(x - c, y - c) < c - 16 * k))
        color = t.text_dim
        ring, gap, pad = 2.2 * k, 4 * k, 3 * k
        edge2, clear = (c - 12 * k) ** 2, CITY_CLEAR * k
        taken = []                      # (x0, y0, x1, y1) of each label drawn

        def fits(x0, y0, x1, y1):
            return (y0 >= top and y1 <= bottom
                    and max((x0 - c) ** 2, (x1 - c) ** 2) + max((y0 - c) ** 2, (y1 - c) ** 2) <= edge2
                    and not (x0 < c + clear and x1 > c - clear and y0 < c + clear and y1 > c - clear)
                    and not any(x0 < b[2] + pad and x1 > b[0] - pad and y0 < b[3] + pad and y1 > b[1] - pad
                                for b in taken))

        for i in seen.tolist():
            cx, cy = float(x[i]), float(y[i])
            halo, mask, top_off = self._city_label(cities.names[i])
            w, ty = mask.width, cy + top_off
            for left in (cx + ring + gap, cx - ring - gap - w):
                box = (min(left, cx - ring), ty, max(left + w, cx + ring), ty + mask.height)
                if fits(*box):
                    img.paste(t.globe, (round(left) - 1, round(ty) - 1), halo)
                    img.paste(color, (round(left), round(ty)), mask)
                    draw.ellipse([cx - ring, cy - ring, cx + ring, cy + ring], outline=color,
                                 width=max(1, round(0.7 * k)))
                    taken.append(box)
                    break
            if len(taken) >= CITY_MAX:
                break

    def _city_label(self, name):
        """A city's name as (outline mask, text mask, top): the text starts at its mask's left
        edge, its middle `top` pixels below the mask's top, and the outline is a pixel bigger all
        round. Cached."""
        label = self._city_labels.get(name)
        if label is None:
            px = max(MIN_TEXT_PX, round(CITY_TEXT * self.text_k))
            text = _printable(name)
            font = self._fonts.get("regular", px, text)
            l, t, r, b = font.getbbox(text or " ", anchor="lm")
            outlined = Image.new("L", (r - l + 2, b - t + 2))
            ImageDraw.Draw(outlined).text((1 - l, 1 - t), text, font=font, fill=255, anchor="lm")
            label = self._city_labels[name] = (outlined.filter(ImageFilter.MaxFilter(3)),
                                               outlined.crop((1, 1, r - l + 1, b - t + 1)), t)
            if len(self._city_labels) > 300:
                self._city_labels = {name: label}
        return label

    def _luts(self, t):
        """Brightness curves for zoom t (0 = world, 1 = tuning): stations per pixel -> dot core,
        and blurred core -> glow. Both are 1 - e^(-gain * x), cached per zoom."""
        t = round(t, 2)
        if t not in self._lut_cache:
            core_gain = CORE_GAIN[0] + (CORE_GAIN[1] - CORE_GAIN[0]) * t   # zoomed in: a bright dot
            glow_gain = 7 + 4 * t         # ... with a clear halo
            self._lut_cache[t] = (
                [round(255 * (1 - math.exp(-core_gain * v * LIGHT_MAX / 255))) for v in range(256)],
                [round(GLOW_MAX * (1 - math.exp(-v * glow_gain / GLOW_MAX))) for v in range(256)])
        return self._lut_cache[t]

    def _station_light(self, m, radius, detailed):
        """One brightness layer for all stations: sharp dot cores plus a soft glow around them."""
        s, c = self.size, self.size / 2
        p = self.station_xyz @ m
        x = p[:, 0] + (c - 0.5)                      # pixel-center coordinates
        y = p[:, 1] + (c - 0.5)
        ok = (p[:, 2] > 0) & (x >= 1) & (x < s - 2) & (y >= 1) & (y < s - 2)
        x, y = x[ok], y[ok]
        scale = 255 / LIGHT_MAX                      # stations per pixel -> 0-255
        if detailed:
            # Share each dot between its nearest pixels by how close it is to each
            # (bilinear), so dots slide smoothly instead of jumping a pixel at a time.
            ax, wx = _taps(x, self._wide_dots)
            ay, wy = _taps(y, self._wide_dots)
            base = ay * s + ax
            n = len(wx)
            acc = np.bincount(
                np.concatenate([base + dy * s + dx for dy in range(n) for dx in range(n)]),
                weights=np.concatenate([wy[dy] * wx[dx] * scale for dy in range(n) for dx in range(n)]),
                minlength=s * s)
        else:
            i = y.astype(np.int32) * s + x.astype(np.int32)
            acc = np.bincount(i, minlength=s * s) * scale
        np.minimum(acc, 255, out=acc)
        # Zoomed in, each dot is bright on its own; in world view thousands pile
        # up, so lower the gain and let crowded areas glow like city lights.
        core_lut, glow_lut = self._luts(min(1.0, max(0.0, math.log(radius / self._world_radius) / math.log(7))))
        core = Image.fromarray(acc.astype(np.uint8).reshape(s, s), "L").point(core_lut)
        # Blur at reduced size and scale back up: a round glow for a fraction of the cost.
        blur = ImageFilter.GaussianBlur(GLOW_RADIUS * self.k / self._glow_shrink)
        glow = (core.reduce(self._glow_shrink).filter(blur)
                .resize((s, s), Image.BILINEAR).point(glow_lut))
        return ImageChops.lighter(core, glow)

    def _build_overlays(self):
        """Fixed top layers: black outside the window, and a soft rim and bands behind the text
        (fading towards the theme's shade color). {False: the map, True: the idle clock, whose
        band reaches higher, behind the date and the big time}."""
        s, c, k = self.size, self.size / 2, self.k
        r, y = self._r, self._y
        outside = np.clip(r - (c - 0.5), 0, 1)
        rim = np.clip((r - (c - 22 * k)) / (22 * k), 0, 1) ** 2 * 0.6
        top = np.clip((54 * k - y) / (54 * k), 0, 1) * 0.45
        bands = [Image.fromarray(np.round(v * (1 - outside)).astype(np.uint8), "L") for v in self.theme.shade]
        out = {}
        for idle, (start, span, power, most) in ((False, (190, 100, 1.4, 0.7)), (True, (150, 110, 1.0, 0.75))):
            bottom = np.clip((y - start * k) / (span * k), 0, 1) ** power * most
            dark = 1 - (1 - outside) * (1 - rim) * (1 - bottom) * (1 - top)
            out[idle] = Image.merge("RGBA", bands + [Image.fromarray(np.round(dark * 255).astype(np.uint8), "L")])
        return out

    def _build_crosshair(self):
        """The crosshair's shape as a small mask, and where it goes: (mask, x, y). Its color and
        opacity come from the theme when it's drawn."""
        c, k = self.size / 2, self.k
        inner, outer, ring, width = round(12 * k), round(27 * k), round(6 * k), max(1, round(k))
        o = outer + width + 1                       # the center, inside the mask
        mask = Image.new("L", (2 * o + 1, 2 * o + 1))
        d = ImageDraw.Draw(mask)
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            d.line([o + dx * inner, o + dy * inner, o + dx * outer, o + dy * outer], fill=255, width=width)
        d.ellipse([o - ring, o - ring, o + ring, o + ring], outline=255, width=width)
        return mask, int(c) - o, int(c) - o

    # --- text --------------------------------------------------------------

    def _text(self, img, text, role, size, color, y, alpha=1.0):
        """Center one line of text at height y, cut short with … to fit inside the circle.

        Rendered lines are cached, so only text that changed costs anything.
        """
        if not text:
            return None
        c = self.size / 2
        patch = self._patch(text, role, size, y)
        if patch is None:
            return None
        mask, l, t = patch
        if alpha < 1:
            mask = mask.point([round(v * alpha) for v in range(256)])
        x0 = round(c + l)
        img.paste(color, (x0, round(y + t)), mask)
        return x0, x0 + mask.width

    def _patch(self, text, role, size, y):
        """One line of text as (mask, left, top) around the middle of height y, cut short to fit
        inside the circle. Cached."""
        key = (text, role, size, y)
        patch = self._patches.get(key)
        if patch is None:
            px = max(MIN_TEXT_PX, round(size * self.text_k))
            patch = self._patches[key] = self._text_patch(_printable(text), role, px, y)
            if len(self._patches) > 300:
                self._patches.clear()
        return patch

    def _draw_about(self, img, hud):
        """The slot under the coordinates: the time at the station, and the weather there after
        it ("3:12 pm", a cloud, "18°"), centered as one line."""
        if hud.local_alpha <= 0 or not (hud.local or hud.weather):
            return
        t, k, y = self.theme, self.k, TOP_Y * self.k
        if not hud.weather:
            self._text(img, hud.local, "regular", 13, t.text, y, hud.local_alpha)
            return
        sky, temp = hud.weather
        parts = []                                  # (mask, top, color, space before it)
        if hud.local:
            mask, _, top = self._patch(hud.local, "regular", 13, y)
            parts.append((mask, y + top, t.text, 0))
        icon = self._weather_icon(sky)
        parts.append((icon, y - icon.height / 2 + 0.6 * k, t.text, 11 * k if parts else 0))
        mask, _, top = self._patch(temp, "regular", 13, y)
        parts.append((mask, y + top, t.text, 4 * k))
        x = self.size / 2 - sum(m.width + gap for m, _, _, gap in parts) / 2
        for mask, top, color, gap in parts:
            x += gap
            if hud.local_alpha < 1:
                mask = mask.point([round(v * hud.local_alpha) for v in range(256)])
            img.paste(color, (round(x), round(top)), mask)
            x += mask.width

    def _weather_icon(self, sky):
        """A small weather symbol (weather.SKIES) as a smooth mask, about as tall as the digits
        beside it: drawn 4x bigger and shrunk. Cached."""
        icon = self._icons.get(sky)
        if icon is not None:
            return icon
        big = 4
        u = self.k * big                            # one unit, in big pixels
        w, h = round(19 * self.k) * big, round(15 * self.k) * big
        cx, cy = w / 2, h / 2

        def canvas():
            mask = Image.new("L", (w, h))
            return mask, ImageDraw.Draw(mask)

        def circle(d, x, y, r, fill=255):
            d.ellipse([cx + (x - r) * u, cy + (y - r) * u, cx + (x + r) * u, cy + (y + r) * u], fill=fill)

        def line(d, x0, y0, x1, y1, width=1.3):
            d.line([cx + x0 * u, cy + y0 * u, cx + x1 * u, cy + y1 * u], fill=255, width=round(width * u))
            for x, y in ((x0, y0), (x1, y1)):           # round ends
                circle(d, x, y, width / 2)

        def sun(x, y, r):
            mask, d = canvas()
            circle(d, x, y, r)
            for i in range(8):
                a = math.pi * i / 4
                line(d, x + 1.6 * r * math.cos(a), y + 1.6 * r * math.sin(a),
                     x + 2.15 * r * math.cos(a), y + 2.15 * r * math.sin(a), width=0.32 * r + 0.4)
            return mask

        def moon(x, y, r):
            mask, d = canvas()
            circle(d, x, y, r)
            circle(d, x + 0.55 * r, y - 0.4 * r, 0.82 * r, fill=0)
            return mask

        def cloud(x, y, s=1.0):
            mask, d = canvas()
            circle(d, x - 3.4 * s, y + 1.3 * s, 2.5 * s)
            circle(d, x - 0.4 * s, y - 0.6 * s, 3.6 * s)
            circle(d, x + 3.2 * s, y + 1.0 * s, 2.8 * s)
            d.rectangle([cx + (x - 3.4 * s) * u, cy + (y + 1.3 * s) * u,
                         cx + (x + 3.2 * s) * u, cy + (y + 3.8 * s) * u], fill=255)
            return mask

        def behind(back, front, gap=1.1):
            """`back` peeking out from behind `front`, with a thin gap around front's edge."""
            grown = front.filter(ImageFilter.MaxFilter(2 * round(gap * u / 2) + 1))
            return ImageChops.lighter(ImageChops.subtract(back, grown), front)

        def marks(draw_marks):
            mask, d = canvas()
            draw_marks(d)
            return mask

        if sky == "sun":
            mask = sun(0, 0, 2.9)
        elif sky == "moon":
            mask = moon(-0.6, 0.2, 4.6)
        elif sky in ("sun-cloud", "moon-cloud"):
            back = sun(-2.6, -2.4, 2.1) if sky == "sun-cloud" else moon(-2.6, -2.6, 3.3)
            mask = behind(back, cloud(1.4, 1.2, 0.9))
        elif sky == "rain":
            mask = ImageChops.lighter(cloud(0, -2.2), marks(
                lambda d: [line(d, x, 2.9, x - 1.0, 5.4) for x in (-2.6, 0.6, 3.8)]))
        elif sky == "snow":
            mask = ImageChops.lighter(cloud(0, -2.2), marks(
                lambda d: [circle(d, x, y, 0.95) for x, y in ((-2.8, 3.9), (0.4, 5.3), (3.6, 3.9))]))
        elif sky == "storm":
            bolt = [(0.9, 0.6), (-1.7, 4.0), (-0.1, 4.0), (-1.2, 7.2), (2.3, 3.0), (0.7, 3.0), (2.1, 0.6)]
            mask = behind(cloud(0, -2.2), marks(
                lambda d: d.polygon([(cx + x * u, cy + y * u) for x, y in bolt], fill=255)), gap=0.9)
        elif sky == "fog":                          # a cloud thinning out into mist
            mask = ImageChops.lighter(cloud(0, -2.4, 0.85), marks(
                lambda d: [line(d, x0, y, x1, y, width=1.3) for y, x0, x1 in ((3.2, -4.8, 4.2), (5.8, -3.2, 3.6))]))
        else:
            mask = cloud(0, 0)
        left, _, right, _ = mask.getbbox() or (0, 0, w, h)
        left, right = left // big * big, -(-right // big) * big    # whole small pixels: no smudge
        icon = self._icons[sky] = mask.crop((left, 0, right, h)).reduce(big)
        return icon

    def _line_width(self, size, y):
        """How wide a line of text at height y may be, inside the circle."""
        c = self.size / 2
        edge = abs(y - c) + size * 0.6
        return 2 * math.sqrt(max(0.0, c * c - edge * edge)) - 30 * self.k

    def _strip(self, text, role, size, y):
        """The whole line, uncut: (mask, left, top, room). Cached."""
        key = (text, role, size, y)
        strip = self._strips.get(key)
        if strip is None:
            px = max(MIN_TEXT_PX, round(size * self.text_k))
            text = _printable(text)
            font = self._fonts.get(role, px, text)
            l, t, r, b = font.getbbox(text or " ", anchor="lm")
            mask = Image.new("L", (max(1, r - l), max(1, b - t)))
            ImageDraw.Draw(mask).text((-l, -t), text, font=font, fill=255, anchor="lm")
            strip = self._strips[key] = (mask, l, t, self._line_width(px, y))
            if len(self._strips) > 60:
                self._strips = {key: strip}
        return strip

    def text_overflow(self, text, field):
        """How many pixels a line is too long for its place (0 if it fits)."""
        if not text:
            return 0.0
        role, size, y = LINES[field]
        mask, _, _, room = self._strip(text, role, size, y * self.k)
        return max(0.0, mask.width - room)

    def _scrolled(self, img, text, role, size, color, y, offset, alpha):
        """Draw part of a long line, `offset` pixels in, with soft ends where more text hides."""
        mask, _, t, room = self._strip(text, role, size, y)
        width = int(room)
        offset = min(max(0.0, offset), mask.width - width)
        window = mask.crop((round(offset), 0, round(offset) + width, mask.height))
        ends = (offset > 0.5, offset < mask.width - width - 0.5)
        key = (width, mask.height, ends)
        edge = self._edges.get(key)
        if edge is None:
            ramp = np.ones(width, dtype=np.float32)
            n = max(1, round(EDGE_FADE * self.k))
            fade = (np.arange(n, dtype=np.float32) + 0.5) / n
            if ends[0]:
                ramp[:n] = fade
            if ends[1]:
                ramp[-n:] = fade[::-1]
            edge = self._edges[key] = Image.fromarray(
                np.round(np.tile(ramp * 255, (mask.height, 1))).astype(np.uint8), "L")
        window = ImageChops.multiply(window, edge)
        if alpha < 1:
            window = window.point([round(v * alpha) for v in range(256)])
        x0 = round(self.size / 2 - width / 2)
        img.paste(color, (x0, round(y + t)), window)
        return x0, x0 + width

    def _build_signal(self):
        """The status symbol's parts, ((•)), as smooth masks: drawn 4x bigger and shrunk."""
        k, big = self.k, 4
        r_dot, r_near, r_far = 3.3 * k, 9 * k, 15 * k
        w = max(1.5, 1.6 * k)
        half_w, half_h = r_far + w + 2, 0.62 * r_far + w + 2
        size = (int(2 * half_w) + 1, int(2 * half_h) + 1)
        cx, cy = size[0] * big / 2, size[1] * big / 2

        def part(draw_it):
            mask = Image.new("L", (size[0] * big, size[1] * big))
            draw_it(ImageDraw.Draw(mask))
            return mask.reduce(big)

        def dot(d, r=r_dot):
            d.ellipse([cx - r * big, cy - r * big, cx + r * big, cy + r * big], fill=255)

        def ring(d, r=r_dot):
            d.ellipse([cx - r * big, cy - r * big, cx + r * big, cy + r * big], outline=255,
                      width=round(w * big))

        def arcs(r):
            def draw_it(d):
                box = [cx - r * big, cy - r * big, cx + r * big, cy + r * big]
                d.arc(box, -38, 38, fill=255, width=round(w * big))
                d.arc(box, 142, 218, fill=255, width=round(w * big))
            return draw_it

        def slash(d):
            a = 0.62 * r_far * big
            d.line([cx - a, cy + a, cx + a, cy - a], fill=255, width=round(w * big))

        return {"size": size, "dot": part(dot), "ring": part(ring), "near": part(arcs(r_near)),
                "far": part(arcs(r_far)), "slash": part(slash)}

    def _scale_icons(self, icons):
        """A world's symbols (worlds.Icon, made for the 480 px face) as masks for this size."""
        out = {}
        for state, icon in icons.items():
            mask = Image.frombytes("L", (icon.w, icon.h), icon.mask)
            s = self.size / 480
            if s != 1:
                mask = mask.resize((max(1, round(icon.w * s)), max(1, round(icon.h * s))), Image.LANCZOS)
            out[state] = (mask, round(icon.window * s))
        return out

    def _draw_world_signal(self, img, hud):
        """A world's own symbol in the signal slot (the Pip-Boy's in the Mojave), in the colors the
        usual one uses. The tuning one may be a strip: a window slides along it (signal_scroll)."""
        mask, window = self._icons[hud.signal]
        if window:
            loop = mask.width - window
            x = round(hud.signal_scroll * loop) % max(1, loop)
            mask = mask.crop((x, 0, x + window, mask.height))
        if hud.signal_alpha < 1:
            mask = mask.point([round(v * hud.signal_alpha) for v in range(256)])
        t = self.theme
        color = {"playing": t.live, "tuning": t.text, "no signal": t.warn}[hud.signal]
        img.paste(color, (round(self.size / 2 - mask.width / 2), round(SIGNAL_Y * self.k - mask.height / 2)), mask)

    def _draw_signal(self, img, hud):
        if not hud.signal or hud.signal_alpha <= 0:
            return
        if hud.signal in self._icons:
            self._draw_world_signal(img, hud)
            return
        t, parts = self.theme, self._signal
        x0 = round(self.size / 2 - parts["size"][0] / 2)
        y0 = round(SIGNAL_Y * self.k - parts["size"][1] / 2)

        def put(name, color):
            mask = parts[name]
            if hud.signal_alpha < 1:
                mask = mask.point([round(v * hud.signal_alpha) for v in range(256)])
            img.paste(color, (x0, y0), mask)

        if hud.signal == "playing":                 # on air: steady, in the live color
            for name in ("dot", "near", "far"):
                put(name, t.live)
        elif hud.signal == "tuning":                # tuning: the waves sweep outward
            put("dot", t.text_dim)
            for i, name in enumerate(("near", "far")):
                b = 0.5 + 0.5 * math.cos(2 * math.pi * (hud.signal_phase - 0.3 * i))
                put(name, tuple(round(lo + (hi - lo) * b) for lo, hi in zip(t.text_dim, t.text)))
        elif hud.signal == "no signal":             # nothing: a hollow dot, struck through
            put("near", t.text_dim)
            put("far", t.text_dim)
            put("ring", t.warn)
            put("slash", t.warn)

    def _text_patch(self, text, role, size, y):
        if not text:
            return None
        c = self.size / 2
        edge = abs(y - c) + size * 0.6
        max_w = 2 * math.sqrt(max(0.0, c * c - edge * edge)) - 30 * self.k
        font = self._fonts.get(role, size, text)
        if font.getlength(text) > max_w:
            while text and font.getlength(text + "…") > max_w:
                text = text[:-1]
            text = text.rstrip() + "…"
        l, t, r, b = font.getbbox(text, anchor="mm")
        mask = Image.new("L", (max(1, r - l), max(1, b - t)))
        ImageDraw.Draw(mask).text((-l, -t), text, font=font, fill=255, anchor="mm")
        return mask, l, t

    @staticmethod
    def _heart(draw, x, y, r, fill):
        """A small heart centered on (x, y), about 2r wide."""
        pts = []
        for i in range(48):
            a = 2 * math.pi * i / 48
            hx = 16 * math.sin(a) ** 3
            hy = 13 * math.cos(a) - 5 * math.cos(2 * a) - 2 * math.cos(3 * a) - math.cos(4 * a)
            pts += [x + hx * r / 16, y - (hy + 3) * r / 16]
        draw.polygon(pts, fill=fill)

    @staticmethod
    def _star(draw, x, y, r, fill):
        pts = []
        for i in range(10):
            a = -math.pi / 2 + i * math.pi / 5
            rad = r if i % 2 == 0 else r * 0.45
            pts += [x + rad * math.cos(a), y + rad * math.sin(a)]
        draw.polygon(pts, fill=fill)

    # --- frame -------------------------------------------------------------

    def render(self, lat, lon, radius, hud=None, timings=None, *, favorites=()):
        """One frame centered on (lat, lon) with the globe radius_px = radius. Returns an RGB image.

        favorites: [(lat, lon)] to mark with stars.
        Pass a dict as timings to have each stage's seconds added to it.
        """
        lap = _Stopwatch(timings)
        s, c, k = self.size, self.size / 2, self.k
        m = _view_matrix(lat, lon, radius)
        detailed = radius > DETAIL_RADIUS * k
        hud = hud or Hud()

        img = self._background(radius).copy()
        lap("background")

        draw = ImageDraw.Draw(img)
        close = radius > CLOSE_RADIUS * k
        t = self.theme
        self._draw_lines(draw, self.grid[2 if close else 10 if detailed else 30], m, radius, t.grid)
        res = "10m" if close else "50m" if detailed else "110m"
        world = self._world
        if hud.borders:
            if world is None and res not in self._borders:
                self._borders[res] = coastlines.load(res, coastlines.BORDERS)
            borders = world["border"] if world is not None else self._borders[res]
            if borders is not None:
                self._draw_lines(draw, borders, m, radius, t.border)
        coast = world["coast"] if world is not None else self.coast[res]
        if coast is not None:
            self._draw_lines(draw, coast, m, radius, t.coast if detailed else t.coast_world)
        lap("coastlines")

        light = self._station_light(m, radius, detailed)
        lap("stations")

        # Color the station light with the palette and lay it over the map,
        # using the light itself as the opacity.
        colored = Image.frombuffer("P", (s, s), light.tobytes(), "raw", "P", 0, 1)
        colored.putpalette(self._palette)
        img.paste(colored.convert("RGB"), (0, 0), light)
        lap("compose")
        if hud.cities:                  # over the station light, so crowded places stay readable
            self._draw_cities(img, draw, m, radius)
            lap("cities")

        if favorites:
            v = unit_vectors(np.array([f[0] for f in favorites]), np.array([f[1] for f in favorites])) @ m
            for fx, fy, depth in v.tolist():
                if depth > 0 and math.hypot(fx, fy) < c - 8 * k:
                    self._star(draw, fx + c, fy + c, 5.5 * k, t.space)       # dark edge
                    self._star(draw, fx + c, fy + c, 4.5 * k, t.live)

        if hud.marker:
            v = unit_vectors(np.array([hud.marker[0]]), np.array([hud.marker[1]])) @ m
            mx, my = v[0, 0] + c, v[0, 1] + c
            if v[0, 2] > 0 and math.hypot(mx - c, my - c) < c - 8 * k:
                rr = 10 * k               # sits around the crosshair's ring when on target
                draw.ellipse([mx - rr, my - rr, mx + rr, my + rr],
                             outline=t.live if hud.marker_live else t.text_dim, width=max(1, round(k)))
        overlay = self._overlays[bool(hud.clock)]
        img.paste(overlay, (0, 0), overlay)
        if hud.crosshair > 0:
            mask, x0, y0 = self._crosshair
            a = hud.crosshair * t.crosshair[3] / 255
            img.paste(t.crosshair[:3], (x0, y0), mask.point([round(v * a) for v in range(256)]))
        if hud.panel is not None:
            img = self._panel(img, hud.panel)
            self._volume_ring(ImageDraw.Draw(img), hud.volume)
            lap("hud")
            return img

        span = self._text(img, hud.coords, "regular", 11, t.text_dim, 24 * k)
        if span and hud.bt:
            self._bluetooth_symbol(draw, span[1] + 9 * k, 24 * k, t.text_dim)
        self._draw_signal(img, hud)
        self._draw_about(img, hud)
        self._text(img, hud.toast, "bold", 13, t.text, TOP_Y * k, hud.toast_alpha)
        if hud.clock:
            self._text(img, hud.clock, "bold", 34, t.text, CLOCK_Y * k)
        self._text(img, hud.date, "regular", 14, t.text_dim, DATE_Y * k)
        self._text(img, hud.reminder, "regular", 12, t.text_dim, REMINDER_Y * k)
        tone = {"live": t.live, "warn": t.warn}.get(hud.tone, t.text_dim)
        colors = {"title": t.text, "subtitle": t.text_dim, "detail": tone}
        moving = {field: (offset, alpha) for field, offset, alpha in hud.scroll}
        spans = {}
        for field, (role, size, y) in LINES.items():
            text = getattr(hud, field)
            if not text:
                continue
            offset, alpha = moving.get(field, (None, 1.0))
            if offset is None:
                spans[field] = self._text(img, text, role, size, colors[field], y * k, alpha)
            else:
                spans[field] = self._scrolled(img, text, role, size, colors[field], y * k, offset, alpha)
        if spans.get("title") and hud.favorite:
            self._star(draw, spans["title"][0] - 10 * k, 225 * k, 6 * k, t.live)
        if spans.get("detail") and hud.liked:
            self._heart(draw, spans["detail"][0] - 9 * k, 266 * k, 4.5 * k, t.live)

        self._volume_ring(draw, hud.volume)
        lap("hud")
        return img

    def _volume_ring(self, draw, volume):
        if volume is None:
            return
        s, k, t = self.size, self.k, self.theme
        box = [7 * k, 7 * k, s - 7 * k, s - 7 * k]
        width = max(2, round(3 * k))
        draw.arc(box, 135, 405, fill=t.track, width=width)
        if volume > 0:
            draw.arc(box, 135, 135 + 270 * volume / 100, fill=t.live, width=width)

    def _bluetooth_symbol(self, draw, x, y, color):
        """The Bluetooth rune, a little taller than the text beside it."""
        k = self.k
        h, w = 4.8 * k, 2.6 * k
        draw.line([(x - w, y - h / 2), (x + w, y + h / 2), (x, y + h), (x, y - h), (x + w, y - h / 2),
                   (x - w, y + h / 2)], fill=color, width=max(1, round(0.9 * k)), joint="curve")

    def _panel(self, img, panel):
        """A card over the map, faded nearly to the background: a title, a few lines, a QR code
        or a list to turn through (the highlighted row in the middle, its neighbors fading
        above and below, like a drum), and notes at the bottom."""
        k, t = self.k, self.theme
        img = Image.blend(img, Image.new("RGB", img.size, t.space), PANEL_DIM)
        y = 66
        self._text(img, panel.title, "bold", 17, t.text, y * k)
        y += 24
        for line in panel.lines:
            self._text(img, line, "regular", 13, t.text_dim if panel.qr or panel.items else t.text, y * k)
            y += 18
        if panel.qr:
            qr = self._qr(panel.qr)
            img.paste(qr, (round(self.size / 2 - qr.width / 2), round((y + 2) * k)))
        if panel.items:
            middle, step = 150, 28
            for i, (label, detail) in enumerate(panel.items):
                d = i - panel.sel
                if abs(d) > 2:
                    continue
                if d == 0:
                    self._text(img, label, "bold", 17, t.text, (middle - 4) * k)
                    tone = t.live if detail == "connected" else t.warn if detail == "couldn't connect" \
                        else t.text_dim
                    self._text(img, detail, "regular", 12, tone, (middle + 13) * k)
                else:
                    gap = step + 8 if d > 0 else step        # room for the detail under the middle row
                    self._text(img, label, "regular", 13, t.text_dim, (middle + d * gap) * k,
                               0.85 if abs(d) == 1 else 0.4)
        y = 252 if len(panel.notes) < 2 else 246
        for note in panel.notes:
            self._text(img, note, "regular", 11, t.text_dim, y * k)
            y += 15
        return img

    def _qr(self, text):
        """A QR code as a light rounded square with dark dots (cached: it rarely changes)."""
        cached = getattr(self, "_qr_cache", None)
        if cached and cached[0] == text:
            return cached[1]
        matrix = segno.make(text, error="m").matrix
        side = round(QR_SIZE * self.k)
        n = len(matrix) + 4                       # two modules of light border on each side
        light, dark = QR_COLORS
        big = 4                                   # draw 4x bigger and shrink: even, smooth modules
        image = Image.new("RGB", (side * big, side * big), (0, 0, 0))
        d = ImageDraw.Draw(image)
        d.rounded_rectangle([0, 0, side * big - 1, side * big - 1], radius=round(5 * self.k * big), fill=light)
        cell = side * big / n
        for r, row in enumerate(matrix):
            for c, on in enumerate(row):
                if on:
                    x0, y0 = (c + 2) * cell, (r + 2) * cell
                    d.rectangle([round(x0), round(y0), round(x0 + cell) - 1, round(y0 + cell) - 1], fill=dark)
        qr = image.reduce(big)
        mask = Image.new("L", (side * big, side * big))
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, side * big - 1, side * big - 1],
                                               radius=round(5 * self.k * big), fill=255)
        qr.putalpha(mask.reduce(big))
        self._qr_cache = (text, qr)
        return qr


class FaceScreen:
    """The face's screen, frame by frame: a MapView plus a Camera gliding after the dial.

    Fed a FaceState (face_link.py) every frame. The laptop preview feeds it straight from the
    radio; the virtual face feeds it only what arrives in messages, as the real face will.
    """

    def __init__(self, stations, size=SIZE):
        self.view = MapView(stations, size)
        self.camera = None
        self.favorites = ()
        self._time = None
        self._shown = None             # what the last frame showed
        self._last = None              # the last frame
        self._mode = None              # (theme, idle): a change crossfades
        self._fade = None              # (frame faded from, start time) during a crossfade
        self._plain = None             # the last frame before any crossfade
        self._toast = ("", 0.0)        # the toast on screen and how visible it is
        self._scrolls = {}             # field -> (text, when its scroll started)
        self._hands = None             # (idle, ring, toast): signs someone is at the controls
        self._moving = False           # the camera is still gliding
        self._top = None               # how visible the symbol and the time are, 0-1
        self._top_settled = True
        self._playing_since = None
        self._cross = self._cross_want = None   # how visible the crosshair is, and should be
        self._flight = None            # a long jump being flown (fly_to)
        self.home = None               # a game world: its whole-map view (lat, lon, km), for idle and long jumps
        self._level = 1.0              # how bright the screen is, 0 (black) to 1: standby fades it
        self._level_goal, self._level_rate = 1.0, 0.0
        self._shown_factor = 1.0       # the brightness of the last frame: standby fade x brightness
        self._black = Image.new("RGB", (size, size))
        self._quiet_since = None       # since when nobody has been at the controls (while on air)
        self._spin_since = None        # idle and turning: since when

    def set_stations(self, stations):
        self.view.set_stations(stations)
        self.redraw()

    def set_favorites(self, favorites):
        favorites = tuple(tuple(f) for f in favorites)
        if favorites != self.favorites:
            self.favorites = favorites
            self.redraw()

    def redraw(self):
        """Make the next frame() draw even if nothing has changed."""
        self._shown = None

    def frame(self, fs, now):
        """The frame to show now, or None if it would look the same as the last one."""
        if fs.theme != self.view.theme.name:
            try:
                self.view.set_theme(get_theme(fs.theme))
            except ValueError:
                pass            # a theme this screen doesn't know: keep the one it has
        home = self.home
        radius, hud = face_view(fs, self.view.size, home)
        dt = 0.0 if self._time is None else now - self._time
        self._time = now
        if self._level != self._level_goal:
            step = dt * self._level_rate
            self._level = (min(self._level_goal, self._level + step) if self._level_goal > self._level
                           else max(self._level_goal, self._level - step))
        lat, lon = fs.lat, fs.lon
        if fs.idle and home is not None:
            # A game world, idle: the whole map, drifting slowly under the window.
            lat, lon = home[0], home[1]
            if fs.spin:
                if self._spin_since is None:
                    self._spin_since = now
                dlat, dlon = drift_offset(now - self._spin_since, width_for_zoom(home[2]))
                lat, lon = lat + dlat, lon + dlon
            else:
                self._spin_since = None
        elif fs.idle and fs.spin:
            # Idle: the globe turns about its axis, around the station's latitude, so the
            # station comes back round once a turn. Touching anything flies it back.
            if self._spin_since is None:
                self._spin_since = now
            lon = (fs.lon - spin_offset(now - self._spin_since) + 180) % 360 - 180
        else:
            self._spin_since = None
        if self.camera is None:
            self.camera = Camera(lat, lon, radius)
        else:
            self.camera.zoom_s = IDLE_ZOOM_S if fs.idle else 0.12
            # On air, the only move is the dial locking onto the station: make it a gentle glide.
            # A game world going idle glides out to its whole map as slowly as it zooms.
            self.camera.glide_s = (IDLE_ZOOM_S if fs.idle and home is not None
                                   else LOCK_GLIDE_S if fs.status == "playing" else 0.07)
            target = (lat, lon, radius)
            if self._flight is not None and self._flight["target"] != target:
                self._flight = None           # it moved on mid-flight: fly from here instead
            fly_min = FLY_MIN_DEG if home is None else FLY_MIN_SHARE * width_for_zoom(home[2])
            if (self._flight is None and not (fs.idle and home is not None)
                    and _arc_deg(self.camera.lat, self.camera.lon, lat, lon) > fly_min):
                self._start_flight(target, now)
            if self._flight is not None:
                self._fly(now)
                self._moving = True
            elif (self._spin_since is not None and abs(math.log(radius / self.camera.radius)) < 0.002
                  and (home is None or _arc_deg(self.camera.lat, self.camera.lon, lat, lon) < 0.02)):
                # Zoomed out: follow the turn (or drift) exactly, one step at a time (no glide in between).
                self._moving = (self.camera.lat, self.camera.lon, self.camera.radius) != (lat, lon, radius)
                self.camera.lat, self.camera.lon, self.camera.radius = lat, lon, radius
            else:
                self._moving = self.camera.update(lat, lon, radius, dt)
        cam = self.camera
        hud = self._fade_toast(hud, dt)
        hud = self._fade_top(hud, fs, now, dt)
        hud = self._fade_crosshair(hud, fs, now, dt)
        hud = self._scroll(hud, fs, now)
        if hud.signal == "tuning":
            hud = dataclasses.replace(hud, signal_phase=signal_phase(now), signal_scroll=signal_scroll(now))
        mode = (self.view.theme.name, fs.idle, fs.panel.title if fs.panel else None)
        if self._mode is not None and mode != self._mode and self._last is not None:
            self._fade = (self._last, now)
        self._mode = mode
        shown = (cam.lat, cam.lon, cam.radius, hud, mode, self.favorites)
        # How bright the screen is: the standby fade (slow at the dark end, like dawn) times the
        # brightness setting (the face sets its backlight instead).
        factor = self._level ** 2 * max(10, min(100, fs.bright)) / 100
        if shown == self._shown and self._fade is None and factor == self._shown_factor:
            return None
        if shown != self._shown:
            self._plain = self.view.render(cam.lat, cam.lon, cam.radius, hud, favorites=self.favorites)
            self._shown = shown
        out = self._plain
        if self._fade is not None:
            start_frame, start = self._fade
            t = (now - start) / CROSSFADE_S
            if t >= 1:
                self._fade = None
            else:
                out = Image.blend(start_frame, out, t * t * (3 - 2 * t))
        if factor < 1:
            out = Image.blend(self._black, out, factor)
        self._shown_factor = factor
        self._last = out
        return out

    def fade_out(self, now, seconds):
        """Standby: fade to black over `seconds` (0: at once)."""
        self._slide_level(0.0, seconds)

    def wake(self, now, fade_s):
        """Back from standby: brighten from wherever it is over fade_s (the alarm: a minute), or
        at once. Waking while it's still fading out just turns it around."""
        self._time = now                  # it hasn't been drawing while dark: don't count that time
        self._slide_level(1.0, fade_s)
        self.redraw()

    def _slide_level(self, goal, seconds):
        self._level_goal = goal
        if seconds:
            self._level_rate = 1.0 / seconds
        else:
            self._level = goal

    def _start_flight(self, target, now):
        cam = self.camera
        r0, r1 = math.log(cam.radius), math.log(target[2])
        world = math.log(radius_for_view(width_for_zoom(self.home[2]) if self.home is not None else 180,
                                         self.view.size))
        # Back out to the whole world (a game world: its whole map) halfway, but never further: from the idle globe (already the
        # whole world) it turns towards the station first, then dives in.
        self._flight = {"target": target, "start": now,
                        "a": unit_vectors(np.array([cam.lat]), np.array([cam.lon]))[0].astype(np.float64),
                        "b": unit_vectors(np.array([target[0]]), np.array([target[1]]))[0].astype(np.float64),
                        "r": (r0, r1), "dip": max(0.0, (r0 + r1) / 2 - world), "world": world}

    def _fly(self, now):
        """Along the great circle, backing out to the whole world halfway (no further)."""
        f, cam = self._flight, self.camera
        u = min(1.0, (now - f["start"]) / FLY_S)
        if u >= 1:
            cam.lat, cam.lon, cam.radius = f["target"]
            self._flight = None
            return
        e = u * u * (3 - 2 * u)
        a, b = f["a"], f["b"]
        angle = math.acos(max(-1.0, min(1.0, float(a @ b))))
        p = (a * math.sin((1 - e) * angle) + b * math.sin(e * angle)) / math.sin(angle) if angle > 1e-6 else b
        cam.lat = math.degrees(math.asin(max(-1.0, min(1.0, p[2]))))
        cam.lon = math.degrees(math.atan2(p[1], p[0]))
        r0, r1 = f["r"]
        cam.radius = math.exp(max(f["world"], r0 + (r1 - r0) * e - f["dip"] * math.sin(math.pi * u)))

    def _fade_toast(self, hud, dt):
        """Fade a new toast in, and an old one out, instead of popping them on and off."""
        text, alpha = self._toast
        if hud.toast:
            if hud.toast != text:
                text, alpha = hud.toast, alpha if text else 0.0
            alpha = min(1.0, alpha + dt / TOAST_IN_S)
        elif text:
            alpha = max(0.0, alpha - dt / TOAST_OUT_S)
            if alpha == 0.0:
                text = ""
        self._toast = (text, alpha)
        return dataclasses.replace(hud, toast=text, toast_alpha=round(alpha, 3) if text else 1.0)

    def _fade_top(self, hud, fs, now, dt):
        """The slot under the coordinates crossfades between the time and the symbol, and both
        step aside for a toast. When a station starts, the on-air symbol stays ON_AIR_S."""
        if fs.status == "playing":
            if self._playing_since is None:
                self._playing_since = now
        else:
            self._playing_since = None
        confirming = self._playing_since is not None and now - self._playing_since < ON_AIR_S
        about = bool(hud.local or hud.weather)
        want = {"signal": 1.0 if hud.signal and (fs.status != "playing" or confirming or not about)
                else 0.0}
        want["local"] = 1.0 if about and not want["signal"] else 0.0
        if self._top is None:
            self._top = dict(want)
        step = dt / TOP_FADE_S
        for key, goal in want.items():
            level = self._top[key]
            self._top[key] = min(goal, level + step) if goal > level else max(goal, level - step)
        self._top_settled = self._top == want and not confirming
        free = 1.0 - (hud.toast_alpha if hud.toast else 0.0)
        return dataclasses.replace(hud, signal_alpha=round(self._top["signal"] * free, 3),
                                   local_alpha=round(self._top["local"] * free, 3))

    def _fade_crosshair(self, hud, fs, now, dt):
        """Hide the crosshair CROSSHAIR_S after a station locks on with nobody at the controls
        (and on the idle clock); bring it straight back when the ball rolls or anything is touched."""
        if fs.status != "playing" or fs.toast or fs.ring or self._quiet_since is None:
            self._quiet_since = now
        want = 0.0 if fs.idle or fs.panel is not None or now - self._quiet_since >= CROSSHAIR_S else 1.0
        if self._cross is None:
            self._cross = want
        elif want > self._cross:
            self._cross = min(want, self._cross + dt / CROSSHAIR_IN_S)
        else:
            self._cross = max(want, self._cross - dt / CROSSHAIR_OUT_S)
        self._cross_want = want
        return dataclasses.replace(hud, crosshair=round(self._cross, 3))

    @property
    def crosshair_level(self):
        return self._cross

    def _scroll(self, hud, fs, now):
        """Long lines scroll once when they change, and again when someone reaches for a
        control (the face sees the radio wake, the volume ring or a message appear)."""
        hands = (fs.idle, fs.ring, bool(fs.toast))
        before = self._hands or hands
        self._hands = hands
        again = (before[0] and not fs.idle) or (fs.ring and not before[1]) or (fs.toast and not before[2])
        moving = []
        for field in (("subtitle", "detail") if fs.idle else ("title", "detail")):
            text = getattr(hud, field)
            began = self._scrolls.get(field)
            if began is None or began[0] != text or again:
                began = self._scrolls[field] = (text, now)
            offset, alpha = self._scroll_at(text, field, now - began[1])
            if offset is not None or alpha < 1:
                moving.append((field, offset, alpha))
        return dataclasses.replace(hud, scroll=tuple(moving))

    def _scroll_at(self, text, field, t):
        """(offset in px or None for the resting, cut-short line, alpha) t seconds in."""
        over = self.view.text_overflow(text, field)
        if over <= 0:
            return None, 1.0
        glide = over / (SCROLL_PX_S * self.view.k)
        for length, at in ((SCROLL_HOLD_S, lambda u: (0.0, 1.0)),
                           (glide, lambda u: (round(over * u * u * (3 - 2 * u), 1), 1.0)),
                           (SCROLL_HOLD_S, lambda u: (over, 1.0)),
                           (SCROLL_FADE_S, lambda u: (over, round(1 - u, 3))),
                           (SCROLL_FADE_S, lambda u: (None, round(u, 3)))):
            if t < length:
                return at(t / length)
            t -= length
        return None, 1.0

    @property
    def busy(self):
        """True while something is still moving: the camera, a fade, a scrolling line, or the
        on-air symbol's few seconds. (The tuning symbol's waves don't count: they keep going
        until a station plays.)"""
        text, alpha = self._toast
        return (self._moving or self._fade is not None or bool(text) and alpha < 1
                or not self._top_settled or self._cross != self._cross_want
                or self._flight is not None or self._level != self._level_goal
                or self._spin_since is not None
                or bool(self._shown and self._shown[3].scroll))


def _arc_deg(lat1, lon1, lat2, lon2):
    """Degrees of arc between two points on the globe."""
    a = unit_vectors(np.array([lat1, lat2], dtype=np.float64), np.array([lon1, lon2], dtype=np.float64))
    return math.degrees(math.acos(max(-1.0, min(1.0, float(a[0].astype(np.float64) @ a[1].astype(np.float64))))))


def spin_offset(elapsed):
    """How many degrees the idle globe has turned `elapsed` seconds after it started: easing in
    over SPIN_RAMP_S, then SPIN_DEG_S, in steps of 1/SPIN_FPS s."""
    t = math.floor(elapsed * SPIN_FPS) / SPIN_FPS
    if t < SPIN_RAMP_S:
        return SPIN_DEG_S * t * t / (2 * SPIN_RAMP_S)
    return SPIN_DEG_S * (t - SPIN_RAMP_S / 2)


def drift_offset(elapsed, width_deg):
    """How far (dlat, dlon) a game world's idle map has drifted `elapsed` seconds in: a slow loop,
    DRIFT_SHARE of the view's width either way, easing in over DRIFT_RAMP_S, in steps of 1/SPIN_FPS s."""
    t = math.floor(elapsed * SPIN_FPS) / SPIN_FPS
    u = min(1.0, t / DRIFT_RAMP_S)
    a = DRIFT_SHARE * width_deg * u * u * (3 - 2 * u)
    return (round(a * math.sin(2 * math.pi * t / DRIFT_PERIODS_S[0]), 6),
            round(a * math.sin(2 * math.pi * t / DRIFT_PERIODS_S[1]), 6))


def signal_phase(now):
    """Where the tuning symbol's waves are, from the clock alone, so every screen agrees."""
    return round((now % TUNE_CYCLE_S) / TUNE_CYCLE_S, 2)


def signal_scroll(now):
    """How far round a world's scrolling tuning symbol is (0-1), from the clock alone."""
    return round((now % WAVE_CYCLE_S) / WAVE_CYCLE_S, 3)


# --- command line: still images and the frame-time benchmark -------------------

def to_rgb565(img):
    """RGB888 -> big-endian RGB565 bytes, the format SPI screens like the ST7796S take."""
    a = np.asarray(img, dtype=np.uint16)
    v = ((a[..., 0] >> 3) << 11) | ((a[..., 1] >> 2) << 5) | (a[..., 2] >> 3)
    return v.astype(">u2").tobytes()


def benchmark(stations, size=SIZE, frames=200):
    from .dial import Dial

    view = MapView(stations, size)
    dial = Dial(stations, player=None)
    region_r = radius_for_view(math.degrees(2000 / EARTH_KM), size)
    close_r = radius_for_view(math.degrees(500 / EARTH_KM), size)
    runs = {
        # Pan east across Europe (the densest part of the map), at each zoom.
        "2,000 km view, panning across Europe": (region_r, lambda i: (50.0, -10.0 + 0.25 * i)),
        "500 km view, panning across Europe": (close_r, lambda i: (50.0, 0.0 + 0.0625 * i)),
        # Spin the whole globe.
        "world view, spinning": (radius_for_view(180, size), lambda i: (30.0, -180.0 + 1.8 * i)),
    }
    report = {}
    for name, (radius, path) in runs.items():
        totals, stages = [], {}
        for i in range(frames + 10):
            lat, lon = path(i)
            near = dial.nearest(lat, lon)[0]
            hud = Hud(title=near["name"], subtitle=place(near), tone="live",
                      detail=f"{_fmt_km(haversine_km(lat, lon, near['geo_lat'], near['geo_long']))} · on air",
                      coords=_fmt_coords(lat, lon), marker=(near["geo_lat"], near["geo_long"]))
            t = {}
            t0 = time.perf_counter()
            view.render(lat, lon, radius, hud, timings=t)
            dt = time.perf_counter() - t0
            if i >= 10:  # skip warm-up frames
                totals.append(dt)
                for k, v in t.items():
                    stages.setdefault(k, []).append(v)
        report[name] = (totals, {k: statistics.median(v) for k, v in stages.items()})

    img = view.render(50, 10, region_r)
    lookups, conversions = [], []
    for i in range(frames):
        t0 = time.perf_counter()
        dial.nearest(50.0, -10.0 + 0.25 * i)
        lookups.append(time.perf_counter() - t0)
        t0 = time.perf_counter()
        to_rgb565(img)
        conversions.append(time.perf_counter() - t0)
    return report, statistics.median(lookups), statistics.median(conversions)


def _main():
    from . import stations as station_db
    from .dial import Dial

    ap = argparse.ArgumentParser(description="Render the round map without a window.")
    ap.add_argument("--png", help="save one frame to this file")
    ap.add_argument("--at", default="51.51,-0.13", help="lat,lon for --png")
    ap.add_argument("--km", type=float, default=2000, help="km across the window for --png")
    ap.add_argument("--world", action="store_true", help="whole-world view for --png")
    ap.add_argument("--size", type=int,
                    help=f"pixels across: {SIZE} (the face, default) or 240 (fallback screen); "
                         "--bench does both")
    ap.add_argument("--scale", type=int, default=2, help="enlarge the --png this many times")
    ap.add_argument("--theme", default=THEMES[0].name, help="color theme for --png")
    ap.add_argument("--sheet", help="save every theme side by side to this file")
    ap.add_argument("--bench", action="store_true", help="measure frame times")
    ap.add_argument("--frames", type=int, default=200)
    args = ap.parse_args()
    stations = station_db.load()

    def demo_hud(lat, lon, toast=""):
        """What the screen would show at (lat, lon) with the nearest station playing, now."""
        dial = Dial(stations, player=None, lat=lat, lon=lon)
        dial.state.status = "playing"
        dial.state.playing = dial.state.nearest
        return make_hud(dial.state, toast=toast)

    if args.png:
        lat, lon = (float(v) for v in args.at.split(","))
        width = 180 if args.world else math.degrees(args.km / EARTH_KM)
        size = args.size or SIZE
        img = MapView(stations, size, get_theme(args.theme)).render(
            lat, lon, radius_for_view(width, size), demo_hud(lat, lon))
        if args.scale > 1:
            img = img.resize((img.width * args.scale,) * 2, Image.NEAREST)
        img.save(args.png)
        print(f"Saved {args.png}")

    if args.sheet:
        size = args.size or 320
        view = MapView(stations, size)
        spots = [(51.51, -0.13, math.degrees(2000 / EARTH_KM)), (30.0, 10.0, 180)]
        sheet = Image.new("RGB", (size * len(THEMES), size * len(spots)))
        for col, theme in enumerate(THEMES):
            view.set_theme(theme)
            for row, (lat, lon, width) in enumerate(spots):
                img = view.render(lat, lon, radius_for_view(width, size), demo_hud(lat, lon, theme.name.title()))
                sheet.paste(img, (col * size, row * size))
        sheet.save(args.sheet)
        print(f"Saved {args.sheet}")

    if args.bench:
        for size in [args.size] if args.size else [SIZE, 240]:
            print(f"--- {size}x{size} ---")
            report, lookup, conversion = benchmark(stations, size, args.frames)
            for name, (totals, stages) in report.items():
                ms = sorted(t * 1000 for t in totals)
                med = statistics.median(ms)
                print(f"{name}: median {med:.1f} ms, p90 {ms[int(len(ms) * 0.9)]:.1f} ms, "
                      f"max {ms[-1]:.1f} ms over {len(ms)} frames")
                print("    " + ", ".join(f"{k} {v * 1000:.2f}" for k, v in stages.items())
                      + " (ms, medians)")
                # Rule of thumb: a Pi Zero 2 W runs this kind of code 6-10x slower than a laptop.
                print(f"    Pi Zero 2 W guess (6-10x slower): {med * 6:.0f}-{med * 10:.0f} ms, "
                      f"{1000 / (med * 10):.0f}-{1000 / (med * 6):.0f} fps")
            print(f"nearest-station lookup (dial.py, per move): {lookup * 1000:.2f} ms")
            print(f"RGB565 conversion for an SPI screen: {conversion * 1000:.2f} ms")


if __name__ == "__main__":
    _main()
