"""Pack the map data for the ESP32-S3 face into C headers that face.ino compiles in.

    python tools/face_data.py                     # writes face/data_*.h and prints their sizes
    python tools/face_data.py --res 110m,50m      # only some of the coastlines
    python tools/face_data.py --simplify 10m=250  # thin the 10m coastline to within 250 m (smaller)

What it writes (all generated, so they're not in git; run this again after a station refresh):

    data_coast_110m.h, data_coast_50m.h, data_coast_10m.h
    data_border_110m.h, data_border_50m.h, data_border_10m.h
        Natural Earth coastlines and country borders (terradio/coastlines.py; the borders are
        drawn only when the state message says "borders"), each point a unit vector (x, y, z)
        times 32767 in int16. The face turns the globe with one 3x3 multiply per point and needs
        no sin or cos. Each coastline is cut into runs of up to 32 segments, and each run has a
        bounding circle so the face can skip runs that are off screen without looking at their
        points, like coastlines.Polylines.gather. Runs of one line share their joining point,
        so each run is drawn as a polyline of its own.
    data_stations.h
        Station positions as int16 (lat, lon) in 1/180ths of a degree: the same format as the
        stations message (terradio/face_link.py). The face can draw the dots before the Pi has
        booted; the Pi sends a fresh list when it connects.
    data_themes.h
        The color themes from map_view.THEMES.
    data_fonts.h
        The preview's fonts (map_view.FONT_FILES: Segoe UI on Windows, DejaVu on the Pi) at the
        sizes the face uses, as 4-bit glyphs: Latin, Greek, Cyrillic and every other letter in the
        station list, plus Korean, Japanese kana and about 3,400 common Chinese characters at the
        sizes that show station names and songs.
    data_icons.h
        The crosshair, the status symbol, the weather symbols, the stars, the heart and the
        Bluetooth rune, drawn by map_view's own code at 480 px, as 8-bit masks.
    data_cities.h
        Natural Earth's major cities (coastlines.load_cities) for the city labels.
    (These three only for face/ itself; face/bench doesn't use them.)
"""

import argparse
import dataclasses
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from terradio import coastlines                                                  # noqa: E402
from terradio import stations as station_db                                      # noqa: E402
from terradio.face_link import POS_SCALE, pack_positions                         # noqa: E402
from terradio.map_view import SIZE, THEMES, radius_for_view, width_for_zoom     # noqa: E402

Q = 32767                 # int16 scale for unit vectors
RUN = 32                  # segments per run
EARTH_M = 6_371_000
FLASH = 16 * 1024 * 1024  # the board: ESP32-S3 with 16 MB flash and 8 MB PSRAM
APP_3MB = 3 * 1024 * 1024 # program space in Arduino's "16M Flash (3MB APP/9.9MB FATFS)" scheme
# Thin a coastline with Douglas-Peucker to within this many meters before packing (0 = keep every
# point, the default). Natural Earth is already generalized, so it doesn't save much: the 10m
# coastline (drawn at about 1 km per pixel) shrinks 19% at 150 m, 31% at 250 m, 44% at 400 m.
SIMPLIFY_M = {"110m": 0, "50m": 0, "10m": 0}
# Which view draws which coastline (as map_view.MapView.render picks them), for the work estimate.
VIEWS = {"110m": ("whole world", 0), "50m": ("2,000 km", 2000), "10m": ("500 km", 500)}


# --- coastlines ------------------------------------------------------------------

def _lines(poly):
    starts = np.flatnonzero(poly.starts)
    ends = np.append(starts[1:], len(poly.points))
    return [poly.points[a:b].astype(np.float64) for a, b in zip(starts, ends)]


def _simplify(line, tol):
    """Douglas-Peucker on the unit sphere (straight chords; fine at these distances).
    Returns which points to keep."""
    keep = np.zeros(len(line), dtype=bool)
    keep[[0, -1]] = True
    todo = [(0, len(line) - 1)]
    while todo:
        a, b = todo.pop()
        if b - a < 2:
            continue
        p, d = line[a], line[b] - line[a]
        rel = line[a + 1:b] - p
        length2 = d @ d
        t = np.clip(rel @ d / length2, 0, 1) if length2 else np.zeros(len(rel))
        dist = np.linalg.norm(rel - t[:, None] * d, axis=1)
        i = int(np.argmax(dist))
        if dist[i] > tol:
            m = a + 1 + i
            keep[m] = True
            todo += [(a, m), (m, b)]
    return keep


def _thinning_error(line, keep):
    """Furthest any point of the line lies from the thinned line (radians)."""
    kept = np.flatnonzero(keep)
    j = np.clip(np.searchsorted(kept, np.arange(len(line)), side="right") - 1, 0, len(kept) - 2)
    a, b = line[kept[j]], line[kept[j + 1]]
    d = b - a
    length2 = np.einsum("ij,ij->i", d, d)
    t = np.clip(np.einsum("ij,ij->i", line - a, d) / np.where(length2, length2, 1), 0, 1)
    return float(np.linalg.norm(line - (a + t[:, None] * d), axis=1).max())


def _circle(run):
    """Bounding circle of a run of int16 points: its center (int16) and cos/sin of its radius,
    rounded so the circle is never too small."""
    v = run.astype(np.float64)
    v /= np.linalg.norm(v, axis=1, keepdims=True)
    center = np.round(v.sum(axis=0) / np.linalg.norm(v.sum(axis=0)) * Q).astype(np.int16)
    c = center / np.linalg.norm(center.astype(np.float64))
    r = math.acos(float(np.clip(v @ c, -1, 1).min())) + 1e-4    # plus a hair for rounding
    return center, math.floor(math.cos(r) * Q), min(Q, math.ceil(math.sin(r) * Q))


def pack_coast(res, simplify_m, layer=coastlines.COAST):
    """-> dict with points (N,3) int16, runs (center (K,3), cos_r, sin_r, count, first) and stats.
    layer: coastlines.COAST or coastlines.BORDERS."""
    poly = coastlines.load(res, layer)
    tol = simplify_m / EARTH_M
    points, centers, cos_r, sin_r, counts, firsts = [], [], [], [], [], []
    n = 0
    worst = 0.0
    for line in _lines(poly):
        error = 0.0
        if tol:
            keep = _simplify(line, tol)
            error = _thinning_error(line, keep)
            line = line[keep]
        q = np.round(line * Q).astype(np.int16)
        back = q / np.linalg.norm(q.astype(np.float64), axis=1, keepdims=True)
        worst = max(worst, error + float(np.linalg.norm(back - line, axis=1).max()))
        q = q[np.r_[True, np.any(q[1:] != q[:-1], axis=1)]]     # rounding can make repeats
        if len(q) < 2:
            continue
        for a in range(0, len(q) - 1, RUN):
            run = q[a:a + RUN + 1]
            center, cr, sr = _circle(run)
            points.append(run)
            centers.append(center)
            cos_r.append(cr)
            sin_r.append(sr)
            counts.append(len(run))
            firsts.append(n)
            n += len(run)
    return {
        "res": res, "layer": layer, "source_points": len(poly.points), "simplify_m": simplify_m,
        "points": np.concatenate(points), "centers": np.array(centers, dtype=np.int16),
        "cos_r": np.array(cos_r, dtype=np.int16), "sin_r": np.array(sin_r, dtype=np.int16),
        "counts": np.array(counts, dtype=np.uint16), "firsts": np.array(firsts, dtype=np.uint32),
        "max_error_m": worst * EARTH_M,
    }


def coast_flash_bytes(pack):
    return pack["points"].nbytes + len(pack["counts"]) * 16      # CoastRun is 16 bytes


def points_per_frame(pack, centers_latlon, size=SIZE):
    """How many points the face projects per frame with this coastline, at the zoom that uses it,
    for each view center: the points of every run whose circle reaches the view."""
    zoom_km = VIEWS[pack["res"]][1]
    radius = radius_for_view(width_for_zoom(zoom_km), size)
    margin = 40                                     # as map_view._draw_lines
    half = math.asin(min(1.0, (size / 2 + margin) * math.sqrt(2) / radius))
    c = pack["centers"].astype(np.float64)
    c /= np.linalg.norm(c, axis=1, keepdims=True)
    limit = math.cos(half) * pack["cos_r"] / Q - math.sin(half) * pack["sin_r"] / Q
    views = coastlines.unit_vectors(centers_latlon[:, 0], centers_latlon[:, 1]).astype(np.float64)
    out = []
    for start in range(0, len(views), 512):
        keep = views[start:start + 512] @ c.T >= limit
        out.append(keep.astype(np.int64) @ pack["counts"].astype(np.int64))
    return np.concatenate(out)


# --- headers ---------------------------------------------------------------------

HEADER = "// Generated by tools/face_data.py{src}. Don't edit: run it again.\n#pragma once\n#include <stdint.h>\n\n"

RUN_STRUCT = """#ifndef FACE_COAST_RUN
#define FACE_COAST_RUN
// Up to 33 points (32 segments) along one coastline or border, drawn as one polyline. Runs of the same
// line share their joining point. Skip a run when its circle is off screen:
//   dot(center, view) / 32767^2 < cos(view_half_angle) * cos_r / 32767 - sin(view_half_angle) * sin_r / 32767
typedef struct {
  int16_t cx, cy, cz;      // center of a circle on the globe around every point (unit vector * 32767)
  int16_t cos_r, sin_r;    // the circle's angular radius r, as cos(r) * 32767 and sin(r) * 32767
  uint16_t count;          // points in the run
  uint32_t first;          // index of its first point
} CoastRun;
#endif

"""


def _rows(values, per_line, fmt):
    items = [fmt(v) for v in values]
    return ",\n".join("  " + ",".join(items[i:i + per_line]) for i in range(0, len(items), per_line))


def write_coast(pack, out_dir):
    border = pack["layer"] == coastlines.BORDERS
    name = f"{'BORDER' if border else 'COAST'}_{pack['res'].upper()}"
    note = f" (simplified to {pack['simplify_m']} m)" if pack["simplify_m"] else ""
    what = "country borders" if border else "coastlines"
    text = (HEADER.format(src=f" from Natural Earth {pack['res']} {what}{note}, public domain")
            + RUN_STRUCT
            + f"static const uint32_t {name}_POINT_COUNT = {len(pack['points'])};\n"
            + f"static const uint32_t {name}_RUN_COUNT = {len(pack['counts'])};\n\n"
            + "// Unit vectors * 32767: x towards 0N 0E, y towards 0N 90E, z towards the North Pole.\n"
            + f"static const int16_t {name}_POINTS[][3] = {{\n"
            + _rows(pack["points"].tolist(), 8, lambda p: "{%d,%d,%d}" % tuple(p)) + "\n};\n\n"
            + f"static const CoastRun {name}_RUNS[] = {{\n"
            + _rows(zip(pack["centers"].tolist(), pack["cos_r"].tolist(), pack["sin_r"].tolist(),
                        pack["counts"].tolist(), pack["firsts"].tolist()), 3,
                    lambda r: "{%d,%d,%d,%d,%d,%d,%d}" % (*r[0], r[1], r[2], r[3], r[4])) + "\n};\n")
    path = out_dir / f"data_{'border' if border else 'coast'}_{pack['res']}.h"
    path.write_text(text, encoding="ascii")
    return path


def write_stations(stations, out_dir):
    packed = np.frombuffer(pack_positions([s["geo_lat"] for s in stations],
                                          [s["geo_long"] for s in stations]), dtype="<i2").reshape(-1, 2)
    text = (HEADER.format(src=" from the Radio Browser station list")
            + f"static const uint16_t STATION_COUNT = {len(packed)};\n\n"
            + f"// (lat, lon) in 1/{POS_SCALE}ths of a degree, as in the stations message.\n"
            + "static const int16_t STATION_POS[][2] = {\n"
            + _rows(packed.tolist(), 10, lambda p: "{%d,%d}" % tuple(p)) + "\n};\n")
    path = out_dir / "data_stations.h"
    path.write_text(text, encoding="ascii")
    return path, packed.nbytes


def write_themes(out_dir):
    colors = [f.name for f in dataclasses.fields(THEMES[0]) if f.name != "name"]
    rows = []
    for t in THEMES:
        vals = [f"{{{c[0]},{c[1]},{c[2]}}}" for c in (getattr(t, name) for name in colors)]
        rows.append(f'  {{"{t.name}", {", ".join(vals)}, {t.crosshair[3]}}}')
    text = (HEADER.format(src=" from terradio/map_view.py THEMES")
            + "typedef struct { uint8_t r, g, b; } FaceColor;\n\n"
            + "// The same fields as map_view.Theme; crosshair's opacity is in crosshair_alpha.\n"
            + "typedef struct {\n  const char *name;\n"
            + "".join(f"  FaceColor {name};\n" for name in colors)
            + "  uint8_t crosshair_alpha;\n} FaceTheme;\n\n"
            + f"static const int FACE_THEME_COUNT = {len(THEMES)};\n"
            + "static const FaceTheme FACE_THEMES[] = {\n" + ",\n".join(rows) + "\n};\n")
    path = out_dir / "data_themes.h"
    path.write_text(text, encoding="ascii")
    return path


# --- fonts -----------------------------------------------------------------------

# The face's text sizes: map_view draws each role at max(MIN_TEXT_PX, round(size * 1.5)) px on the
# 480 px face. (name, role, px, extra): extra "cjk" adds Korean, kana and common Chinese characters
# (station names, places and songs), "clock" means only what the big idle clock needs.
FONT_SIZES = [
    ("R15", "regular", 15, None),     # city labels (10)
    ("R16", "regular", 16, None),     # coordinates, panel notes (11)
    ("R18", "regular", 18, "cjk"),    # the song or distance, the idle reminder, panel details (12)
    ("R20", "regular", 20, "cjk"),    # the place, the time slot, panel lines and items (13)
    ("R21", "regular", 21, None),     # the idle date (14)
    ("B20", "bold", 20, None),        # toasts (13)
    ("B26", "bold", 26, None),        # panel titles, the chosen item (17)
    ("B27", "bold", 27, "cjk"),       # the station name (18)
    ("B51", "bold", 51, "clock"),     # the idle clock (34)
]
PUNCTUATION = "‐‑‒–—―‘’‚‛“”„‟†‡•…‰′″‹›⁄€™←↑→↓−·°"


def _font_file(role):
    """The first font of map_view's list for this role that's on this computer."""
    from PIL import ImageFont
    from terradio.map_view import FONT_FILES
    for name in FONT_FILES[role] + (FONT_FILES["regular"] if role != "regular" else ()):
        try:
            return ImageFont.truetype(name, 10).path
        except OSError:
            continue
    raise SystemExit(f"No font found for {role}: map_view.FONT_FILES lists {FONT_FILES[role]}")


def _cmap(path):
    from fontTools.ttLib import TTFont
    return TTFont(path, fontNumber=0, lazy=True).getBestCmap()


def _double_byte(encoding, rows, cells):
    out = set()
    for a in range(rows[0], rows[1] + 1):
        for b in range(cells[0], cells[1] + 1):
            try:
                out.add(bytes([a, b]).decode(encoding))
            except UnicodeDecodeError:
                pass
    return out


def _is_cjk(ch):          # as map_view._is_cjk: these use the CJK font
    o = ord(ch)
    return 0x2E80 <= o <= 0x9FFF or 0xAC00 <= o <= 0xD7AF or 0xF900 <= o <= 0xFAFF or 0xFF00 <= o <= 0xFFEF


def _skip_ranges():
    """BMP characters map_view._printable drops (symbols and emoji, but not ° © ®), as ranges."""
    import unicodedata
    skip = [o for o in range(0x10000) if unicodedata.category(chr(o)) == "So" and chr(o) not in "°©®"]
    ranges = []
    for o in skip:
        if ranges and ranges[-1][1] == o - 1:
            ranges[-1][1] = o
        else:
            ranges.append([o, o])
    return ranges


def _glyphs(path, px, chars):
    """4-bit glyphs: (codepoint, w, h, x, y, advance in 1/16 px, packed bytes), and the font's
    middle (what anchor "m" centers on) above the baseline."""
    from PIL import Image, ImageDraw, ImageFont
    font = ImageFont.truetype(path, px)
    out = []
    for ch in sorted(chars):
        l, t, r, b = font.getbbox(ch, anchor="ls")
        w, h = max(0, r - l), max(0, b - t)
        data = b""
        if w and h:
            im = Image.new("L", (w, h))
            ImageDraw.Draw(im).text((-l, -t), ch, font=font, fill=255, anchor="ls")
            q = (np.asarray(im, dtype=np.int32) + 8) // 17
            if not q.any():
                w = h = 0
            else:
                if w % 2:
                    q = np.pad(q, ((0, 0), (0, 1)))
                data = (q[:, 0::2] << 4 | q[:, 1::2]).astype(np.uint8).tobytes()
        if not (w and h):
            l = t = 0
        out.append((ord(ch), w, h, l, t, round(font.getlength(ch) * 16), data))
    ascent, descent = font.getmetrics()
    return out, (ascent - descent) / 2


def _c_bytes(data):
    """Bytes as C string literals (smaller source than a list of numbers)."""
    lines, cur = [], []
    for byte in data:
        ch = chr(byte)
        if byte in (34, 92, 63) or not 32 <= byte < 127:
            cur.append("\\%03o" % byte)     # always 3 digits, so a digit after it can't join it
        else:
            cur.append(ch)
        if len(cur) >= 100:
            lines.append('  "' + "".join(cur) + '"')
            cur = []
    if cur:
        lines.append('  "' + "".join(cur) + '"')
    return "\n".join(lines) or '  ""'


def write_fonts(stations, out_dir):
    from terradio.stations import place
    station_chars = set()
    for s in stations:
        station_chars |= set(s["name"] + place(s))
    city_chars = set("".join(coastlines.load_cities().names))
    latin = set(map(chr, range(0x20, 0x7F))) | set(map(chr, range(0xA0, 0x250))) \
        | set(map(chr, range(0x370, 0x500))) | set(PUNCTUATION) | {"�"}
    hangul = {c for c in _double_byte("cp949", (0xB0, 0xC8), (0xA1, 0xFE)) if 0xAC00 <= ord(c) <= 0xD7AF}
    hanzi = _double_byte("gb2312", (0xB0, 0xD7), (0xA1, 0xFE)) \
        | {c for c in _double_byte("shift_jis", (0x88, 0x98), (0x40, 0xFC)) if 0x4E00 <= ord(c) <= 0x9FFF}
    cjk_extra = hangul | hanzi | set(map(chr, range(0x3000, 0x3100))) | set(map(chr, range(0x3130, 0x3190))) \
        | set(map(chr, range(0xFF00, 0xFFF0)))
    paths = {role: _font_file(role) for role in ("regular", "bold", "cjk", "cjk-bold")}
    cmaps = {role: _cmap(p) for role, p in paths.items()}

    text = [HEADER.format(src=" from the fonts in map_view.FONT_FILES ("
                          + ", ".join(Path(p).name for p in dict.fromkeys(paths.values())) + ")"),
            "typedef struct {\n  uint32_t off;        // first byte in the font's bits\n"
            "  uint16_t cp;         // Unicode code point\n  uint16_t adv16;      // advance, 1/16 px\n"
            "  uint8_t w, h;        // bitmap size (4 bits a pixel, rows padded to whole bytes)\n"
            "  int8_t x, y;         // bitmap's top left from the pen, on the baseline\n} FaceGlyph;\n\n"
            "typedef struct {\n  const FaceGlyph *glyphs;\n  const uint8_t *bits;\n  uint16_t count;\n"
            "  uint8_t px;\n  float mid;            // anchor \"m\": this far above the baseline\n} FaceFont;\n\n"]
    report = []
    for name, role, px, extra in FONT_SIZES:
        for cjk in (False, True) if extra == "cjk" else (False,):
            frole = ("cjk-bold" if role == "bold" else "cjk") if cjk else role
            if cjk:
                chars = {c for c in cjk_extra | station_chars if _is_cjk(c)}
            elif extra == "clock":
                chars = set("0123456789: apm")
            else:
                chars = {c for c in latin | station_chars | (city_chars if px == 15 else set())
                         if not _is_cjk(c) and ord(c) < 0x10000}
            chars = {c for c in chars if ord(c) in cmaps[frole] or c == " "}
            glyphs, mid = _glyphs(paths[frole], px, chars)
            ident = name + ("_CJK" if cjk else "")
            blob, rows, off = bytearray(), [], 0
            for cp, w, h, x, y, adv, data in glyphs:
                rows.append("{%d,%d,%d,%d,%d,%d,%d}" % (off, cp, adv, w, h, x, y))
                blob += data
                off += len(data)
            text.append(f"static const FaceGlyph FONT_{ident}_GLYPHS[] = {{\n"
                        + ",\n".join("  " + ",".join(rows[i:i + 6]) for i in range(0, len(rows), 6))
                        + "\n};\n")
            text.append(f"static const uint8_t FONT_{ident}_BITS[] =\n{_c_bytes(bytes(blob))};\n")
            text.append(f"static const FaceFont FONT_{ident} = {{FONT_{ident}_GLYPHS, FONT_{ident}_BITS, "
                        f"{len(glyphs)}, {px}, {mid:.2f}f}};\n\n")
            report.append((ident, len(glyphs), len(blob) + 12 * len(glyphs)))
    ranges = _skip_ranges()
    text.append("// Symbols and emoji map_view leaves out of text (all but the degree, copyright and registered signs), as [first, last] ranges.\n"
                f"static const uint16_t FACE_SKIP_COUNT = {len(ranges)};\n"
                "static const uint16_t FACE_SKIP[][2] = {\n"
                + _rows(ranges, 10, lambda r: "{%d,%d}" % tuple(r)) + "\n};\n")
    path = out_dir / "data_fonts.h"
    path.write_text("".join(text), encoding="ascii")
    return path, report


# --- icons and cities ---------------------------------------------------------------

def write_icons(out_dir):
    """map_view's symbols at 480 px, as masks: (name, mask, x, y), x and y where the mask's top
    left goes relative to the point it's drawn at (or on screen, for the crosshair and signal)."""
    from PIL import Image, ImageDraw
    from terradio.map_view import SIGNAL_Y, MapView
    from terradio.weather import SKIES
    view = MapView([], SIZE)
    k = view.k
    icons = []
    mask, x0, y0 = view._crosshair
    icons.append(("CROSSHAIR", mask, x0, y0))
    sig = view._signal
    sx, sy = round(SIZE / 2 - sig["size"][0] / 2), round(SIGNAL_Y * k - sig["size"][1] / 2)
    for part in ("dot", "ring", "near", "far", "slash"):
        icons.append(("SIGNAL_" + part.upper(), sig[part], sx, sy))
    for sky in SKIES:
        icons.append(("SKY_" + sky.upper().replace("-", "_"), view._weather_icon(sky), 0, 0))

    def shape(name, r, draw_it):
        o = int(math.ceil(r)) + 2
        im = Image.new("L", (2 * o + 1, 2 * o + 1))
        draw_it(ImageDraw.Draw(im), o)
        icons.append((name, im, -o, -o))

    shape("STAR_TITLE", 6 * k, lambda d, o: MapView._star(d, o, o, 6 * k, 255))
    shape("STAR_EDGE", 5.5 * k, lambda d, o: MapView._star(d, o, o, 5.5 * k, 255))
    shape("STAR_FAV", 4.5 * k, lambda d, o: MapView._star(d, o, o, 4.5 * k, 255))
    shape("HEART", 6 * k, lambda d, o: MapView._heart(d, o, o, 4.5 * k, 255))
    shape("BLUETOOTH", 7.5 * k, lambda d, o: view._bluetooth_symbol(d, o, o, 255))
    rr = 10 * k                     # the station's ring (MapView.render's marker)
    shape("MARKER", rr, lambda d, o: d.ellipse([o - rr, o - rr, o + rr, o + rr], outline=255,
                                               width=max(1, round(k))))
    cr = 2.2 * k                    # a city's ring (MapView._draw_cities)
    shape("CITY_RING", cr, lambda d, o: d.ellipse([o - cr, o - cr, o + cr, o + cr], outline=255,
                                                  width=max(1, round(0.7 * k))))
    text = [HEADER.format(src=" from terradio/map_view.py's symbols at 480 px"),
            "typedef struct {\n  const uint8_t *mask;   // 8-bit coverage, row by row\n  uint16_t w, h;\n"
            "  int16_t x, y;          // top left, from where it's drawn\n} FaceIcon;\n\n"]
    for name, mask, x, y in icons:
        text.append(f"static const uint8_t ICON_{name}_MASK[] =\n{_c_bytes(mask.tobytes())};\n"
                    f"static const FaceIcon ICON_{name} = {{ICON_{name}_MASK, {mask.width}, {mask.height}, {x}, {y}}};\n")
    text.append("static const char *const SKY_NAMES[] = {" + ", ".join(f'"{s}"' for s in SKIES) + "};\n"
                "static const FaceIcon *const SKY_ICONS[] = {"
                + ", ".join(f"&ICON_SKY_{s.upper().replace('-', '_')}" for s in SKIES) + "};\n"
                f"static const int SKY_COUNT = {len(SKIES)};\n")
    path = out_dir / "data_icons.h"
    path.write_text("".join(text), encoding="ascii")
    return path


def write_cities(out_dir):
    cities = coastlines.load_cities()
    names = bytearray()
    offs = []
    for n in cities.names:
        offs.append(len(names))
        names += n.encode("utf-8") + b"\0"
    xyz = np.round(cities.xyz.astype(np.float64) * Q).astype(np.int16)
    text = (HEADER.format(src=" from Natural Earth's populated places, public domain")
            + "typedef struct {\n  int16_t x, y, z;       // unit vector * 32767\n"
            + "  uint16_t zoom100;      // web-map zoom where it first gets a label, x 100\n"
            + "  uint32_t name;         // offset of its UTF-8 name in CITY_NAMES\n} FaceCity;\n\n"
            + f"static const uint16_t CITY_COUNT = {len(offs)};   // most important first\n"
            + "static const FaceCity CITIES[] = {\n"
            + _rows(zip(xyz.tolist(), cities.min_zoom.tolist(), offs), 6,
                    lambda c: "{%d,%d,%d,%d,%d}" % (*c[0], round(c[1] * 100), c[2])) + "\n};\n"
            + f"static const char CITY_NAMES[] =\n{_c_bytes(bytes(names))};\n")
    path = out_dir / "data_cities.h"
    path.write_text(text, encoding="ascii")
    return path, len(offs)


# --- main ------------------------------------------------------------------------

def _kb(n):
    return f"{n / 1024:,.0f} KB" if n < 1024 * 1024 else f"{n / 1024 / 1024:,.2f} MB"


def main():
    ap = argparse.ArgumentParser(description="Pack coastlines, stations and themes for the face.")
    ap.add_argument("--out", default=str(ROOT / "face"), help="folder for the headers (default: face/)")
    ap.add_argument("--res", default=",".join(coastlines.RESOLUTIONS),
                    help="coastlines to pack, e.g. 110m,50m (default: all three)")
    ap.add_argument("--simplify", action="append", default=[], metavar="RES=M",
                    help="thin a coastline to within M meters first, e.g. 10m=150 (0 keeps every point)")
    ap.add_argument("--no-text", action="store_true",
                    help="skip the fonts, icons and cities (always skipped for a folder other than face/)")
    args = ap.parse_args()
    simplify = dict(SIMPLIFY_M)
    for item in args.simplify:
        res, _, meters = item.partition("=")
        simplify[res] = float(meters)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    stations = station_db.load()
    rng = np.random.default_rng(1)
    sample = rng.choice(len(stations), size=min(3000, len(stations)), replace=False)
    spots = np.array([(stations[i]["geo_lat"], stations[i]["geo_long"]) for i in sample])

    print(f"Writing to {out_dir}\n")
    print(f"{'file':<20} {'points':>17} {'runs':>7} {'in flash':>10} {'header':>9} {'off by':>8}"
          f"   projected per frame (median / max)")
    total = 0
    for layer, res in [(layer, res) for layer in (coastlines.COAST, coastlines.BORDERS)
                       for res in args.res.split(",")]:
        pack = pack_coast(res, simplify.get(res, 0) if layer == coastlines.COAST else 0, layer)
        path = write_coast(pack, out_dir)
        flash = coast_flash_bytes(pack)
        total += flash
        work = points_per_frame(pack, spots)
        view = VIEWS[res][0]
        print(f"{path.name:<20} {pack['source_points']:>7,} -> {len(pack['points']):>6,} {len(pack['counts']):>7,} "
              f"{_kb(flash):>10} {_kb(path.stat().st_size):>9} {pack['max_error_m']:>6.0f} m"
              f"   {int(np.median(work)):,} / {int(work.max()):,} in the {view} view")
    path, flash = write_stations(stations, out_dir)
    total += flash
    print(f"{path.name:<20} {len(stations):>17,} {'':>7} {_kb(flash):>10} {_kb(path.stat().st_size):>9} "
          f"{0.5 / POS_SCALE * 111_195:>6.0f} m")
    path = write_themes(out_dir)
    print(f"{path.name:<20} {len(THEMES):>17} {'':>7} {'<1 KB':>10} {_kb(path.stat().st_size):>9}")
    # The fonts, symbols and cities are for face.ino only: face/bench (--out face/bench) doesn't use them.
    if not args.no_text and out_dir.resolve() == (ROOT / "face").resolve():
        path, report = write_fonts(stations, out_dir)
        fonts = sum(b for _, _, b in report)
        total += fonts
        print(f"{path.name:<20} {sum(n for _, n, _ in report):>11,} glyphs {'':>7} {_kb(fonts):>10} "
              f"{_kb(path.stat().st_size):>9}   " + ", ".join(f"{i} {n:,}" for i, n, _ in report))
        path = write_icons(out_dir)
        print(f"{path.name:<20} {'':>17} {'':>7} {'':>10} {_kb(path.stat().st_size):>9}")
        path, n = write_cities(out_dir)
        print(f"{path.name:<20} {n:>10,} cities {'':>7} {'':>10} {_kb(path.stat().st_size):>9}")
    print(f"\nTotal in flash: {_kb(total)}, {total / FLASH:.0%} of the board's 16 MB and "
          f"{total / APP_3MB:.0%} of the 3 MB program space in Arduino's usual 16 MB partition scheme.")
    print("'off by' is the most the packed coastline can stray from Natural Earth's "
          "(rounding to int16, plus any thinning).")
    print(f"'projected per frame' counts the points of every run near the view, over "
          f"{len(spots):,} station spots: the face's per-frame work for that layer.")


if __name__ == "__main__":
    main()
