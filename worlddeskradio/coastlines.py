"""World coastlines, country borders and major cities from Natural Earth
(https://www.naturalearthdata.com, public domain).

Three levels of detail: 110m (coarse, ~5k points) for the whole-world view,
50m (~60k points) for the 2,000 km view and 10m (finest) for the 500 km view.
Each is downloaded once
as GeoJSON into data/naturalearth/ and converted to a small .npz of 3D unit
vectors, which is what the map renderer wants and loads in milliseconds.

    load("50m")                  # coastlines
    load("50m", BORDERS)         # borders between countries, on land
    load_cities()                # cities to label, biggest first

Run on its own to fetch them all:  python -m worlddeskradio.coastlines
"""

import json
import urllib.request

import numpy as np

from .stations import DATA_DIR, USER_AGENT

NE_DIR = DATA_DIR / "naturalearth"
# The official Natural Earth repository, pinned to a release so the data never shifts.
NE_URL = ("https://raw.githubusercontent.com/nvkelso/natural-earth-vector/"
          "v5.1.2/geojson/ne_{res}_{layer}.geojson")
RESOLUTIONS = ("110m", "50m", "10m")
COAST, BORDERS = "coastline", "admin_0_boundary_lines_land"     # Natural Earth's names for them
_SHORT = {COAST: "coastline", BORDERS: "borders"}
CITIES = "populated_places_simple"
# Natural Earth gives each city the web-map zoom level where it first earns a label (1.7 for
# London or Tokyo, 7 for a town). Past this one they're too small for the closest view.
CITY_MAX_ZOOM = 6.1


def unit_vectors(lat, lon):
    """Degrees -> (N, 3) float32 points on the unit sphere (x towards 0,0; z towards the north pole)."""
    la, lo = np.radians(lat), np.radians(lon)
    cl = np.cos(la)
    return np.stack([cl * np.cos(lo), cl * np.sin(lo), np.sin(la)], axis=-1).astype(np.float32)


class Polylines:
    """Many lines packed into one array: points (N, 3) plus a flag marking each line's first point.

    The points are also grouped into short chunks, each with a bounding circle,
    so gather() can skip whole chunks that are nowhere near the view. Zoomed in,
    that's over 95% of the 50m coastline.
    """

    CHUNK = 32

    def __init__(self, points, starts):
        self.points = points
        self.starts = starts
        n = len(points)
        line_starts = np.flatnonzero(starts)
        line_ends = np.append(line_starts[1:], n)
        firsts = np.concatenate([np.arange(a, b, self.CHUNK) for a, b in zip(line_starts, line_ends)])
        self.chunk_first = firsts
        self.chunk_len = np.diff(np.append(firsts, n))
        sums = np.add.reduceat(points.astype(np.float64), firsts, axis=0)
        centers = sums / np.linalg.norm(sums, axis=1, keepdims=True)
        cos_to_center = np.einsum("ij,ij->i", points, np.repeat(centers, self.chunk_len, axis=0))
        self.chunk_radius = np.arccos(np.clip(np.minimum.reduceat(cos_to_center, firsts), -1, 1))
        self.chunk_center = centers.astype(np.float32)

    def gather(self, center, half_angle):
        """Points of every chunk that may lie within half_angle (radians) of center, plus line-start
        flags for them. A chunk whose neighbor was skipped starts a new line."""
        limit = np.cos(np.minimum(np.pi, half_angle + self.chunk_radius))
        keep = self.chunk_center @ center >= limit
        ids = np.flatnonzero(keep)
        lens = self.chunk_len[ids]
        offsets = np.cumsum(lens) - lens
        idx = np.arange(lens.sum()) + np.repeat(self.chunk_first[ids] - offsets, lens)
        starts = self.starts[idx]
        prev_kept = keep[ids - 1] & (ids > 0)
        starts[offsets[~prev_kept]] = True
        return self.points[idx], starts

    @classmethod
    def from_lines(cls, lines):
        """lines: iterable of (lat_array, lon_array) pairs."""
        lats, lons, starts = [], [], []
        for lat, lon in lines:
            if len(lat) < 2:
                continue
            lats.append(np.asarray(lat, dtype=np.float64))
            lons.append(np.asarray(lon, dtype=np.float64))
            s = np.zeros(len(lat), dtype=bool)
            s[0] = True
            starts.append(s)
        return cls(unit_vectors(np.concatenate(lats), np.concatenate(lons)), np.concatenate(starts))


def _download(res, layer=COAST):
    NE_DIR.mkdir(parents=True, exist_ok=True)
    path = NE_DIR / f"ne_{res}_{layer}.geojson"
    if not path.exists():
        what = {COAST: "coastlines", BORDERS: "country borders", CITIES: "cities"}[layer]
        print(f"Downloading Natural Earth {res} {what} ...")
        req = urllib.request.Request(NE_URL.format(res=res, layer=layer), headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = resp.read()
        tmp = path.with_suffix(".part")
        tmp.write_bytes(body)
        tmp.replace(path)
    return path


def _convert(geojson_path, npz_path):
    data = json.loads(geojson_path.read_text(encoding="utf-8"))
    lines = []
    for feature in data["features"]:
        geom = feature["geometry"]
        parts = [geom["coordinates"]] if geom["type"] == "LineString" else geom["coordinates"]
        for coords in parts:
            arr = np.asarray(coords, dtype=np.float64)
            lines.append((arr[:, 1], arr[:, 0]))  # GeoJSON is [lon, lat]
    poly = Polylines.from_lines(lines)
    np.savez_compressed(npz_path, points=poly.points, starts=poly.starts)
    return poly


def load(res, layer=COAST):
    """Coastlines (or, with layer=BORDERS, country borders) at resolution '110m', '50m' or '10m',
    downloading them the first time."""
    npz_path = NE_DIR / f"{_SHORT[layer]}-{res}.npz"
    if npz_path.exists():
        with np.load(npz_path) as z:
            return Polylines(z["points"], z["starts"])
    return _convert(_download(res, layer), npz_path)


class Cities:
    """Places to label, most important first: names, unit vectors (N, 3), and min_zoom, the
    web-map zoom where each first gets a label (rising, so the cities for a zoom are a prefix)."""

    def __init__(self, names, xyz, min_zoom):
        self.names, self.xyz, self.min_zoom = names, xyz, min_zoom

    def upto(self, zoom):
        """How many of the cities are labeled by this zoom."""
        return int(np.searchsorted(self.min_zoom, zoom, side="right"))


def load_cities():
    """Cities worth a label on the map (about 3,000), downloading them the first time."""
    npz_path = NE_DIR / "cities.npz"
    if not npz_path.exists():
        data = json.loads(_download("10m", CITIES).read_text(encoding="utf-8"))
        places = [f["properties"] for f in data["features"]
                  if f["properties"]["min_zoom"] <= CITY_MAX_ZOOM]
        places.sort(key=lambda p: (p["min_zoom"], -p["pop_max"]))
        np.savez_compressed(npz_path, names=np.array([p["name"] for p in places]),
                            lat=np.array([p["latitude"] for p in places], dtype=np.float64),
                            lon=np.array([p["longitude"] for p in places], dtype=np.float64),
                            min_zoom=np.array([p["min_zoom"] for p in places], dtype=np.float64))
    with np.load(npz_path) as z:
        return Cities(z["names"].tolist(), unit_vectors(z["lat"], z["lon"]), z["min_zoom"])


def graticule(spacing_deg, sample_deg=1.0):
    """Faint latitude/longitude grid lines every spacing_deg degrees."""
    lines = []
    lon = np.arange(-180.0, 180.0 + sample_deg / 2, sample_deg)
    for lat in np.arange(-90 + spacing_deg, 90, spacing_deg):
        lines.append((np.full_like(lon, lat), lon))
    lat = np.arange(-80.0, 80.0 + sample_deg / 2, sample_deg)  # meridians stop short of the poles
    for lon_line in np.arange(-180.0, 180.0, spacing_deg):
        lines.append((lat, np.full_like(lat, lon_line)))
    return Polylines.from_lines(lines)


if __name__ == "__main__":
    for layer in (COAST, BORDERS):
        for r in RESOLUTIONS:
            p = load(r, layer)
            print(f"{_SHORT[layer]} {r}: {len(p.points):,} points in {int(p.starts.sum()):,} lines")
    print(f"cities: {len(load_cities().names):,}")
