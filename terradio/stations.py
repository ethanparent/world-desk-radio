"""Download, clean and load the Radio Browser station list.

Radio Browser (https://www.radio-browser.info) is a free, community-run
database of internet radio stations. We download the whole list once, keep
only stations that are known to work and have map coordinates, and drop
duplicates. Hand-added stations from my-stations.json are merged on top.

Run on its own:  python -m terradio.stations            (download or resume)
                 python -m terradio.stations --fresh    (start over)
"""

import json
import math
import random
import re
import socket
import sys
import unicodedata
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RAW_PATH = DATA_DIR / "stations-raw.jsonl"      # one downloaded page per line
DONE_PATH = DATA_DIR / "stations-raw.complete"  # exists once the last page is in
CLEAN_PATH = DATA_DIR / "stations.json"
PAGE_SIZE = 500
MY_STATIONS_PATH = ROOT / "my-stations.json"

USER_AGENT = "Terradio/0.1 (hobby world-map radio prototype)"
PRIMARY_SERVER = "de1.api.radio-browser.info"
DNS_ALL = "all.api.radio-browser.info"

# Two stations with the same name closer than this are treated as duplicates.
DUPLICATE_KM = 2.0

# Fields we keep from each station. The full records carry ~40 fields.
KEEP_FIELDS = (
    "stationuuid", "name", "url_resolved", "homepage", "country", "countrycode",
    "state", "language", "tags", "codec", "bitrate", "geo_lat", "geo_long",
    "votes", "clickcount",
)


def _mirror_hosts():
    """Resolve the Radio Browser mirrors, as their docs recommend.

    all.api.radio-browser.info has one A record per server; a reverse lookup of
    each IP gives the server's proper hostname (needed for HTTPS).
    """
    hosts = set()
    try:
        for info in socket.getaddrinfo(DNS_ALL, 443, proto=socket.IPPROTO_TCP):
            ip = info[4][0]
            try:
                hosts.add(socket.gethostbyaddr(ip)[0])
            except OSError:
                pass
    except OSError:
        pass
    hosts.discard(PRIMARY_SERVER)
    hosts = list(hosts)
    random.shuffle(hosts)
    return hosts


def _get_json(url, timeout=180):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


class _Servers:
    """de1 first; if it fails, the mirrors behind all.api.radio-browser.info."""

    def __init__(self):
        self.hosts = [PRIMARY_SERVER]
        self.resolved = False

    def get_json(self, path, log):
        errors = []
        to_try = list(self.hosts)
        while to_try:
            host = to_try.pop(0)
            try:
                data = _get_json(f"https://{host}{path}")
            except Exception as exc:  # network errors, bad JSON, HTTP errors
                errors.append(f"{host}: {exc}")
                log(f"  {host} failed ({exc})")
                if not to_try and not self.resolved:
                    self.resolved = True
                    to_try = [h for h in _mirror_hosts() if h not in self.hosts]
                    self.hosts += to_try
                continue
            # Remember the host that worked so the next page starts there.
            self.hosts.remove(host)
            self.hosts.insert(0, host)
            return data
        raise RuntimeError("No Radio Browser server answered:\n  " + "\n  ".join(errors))


def _read_pages(raw_path=None):
    raw_path = raw_path or RAW_PATH
    if not raw_path.exists():
        return []
    stations = []
    good = []
    for line in raw_path.read_text(encoding="utf-8").splitlines():
        try:
            stations.extend(json.loads(line))
            good.append(line)
        except ValueError:
            # A page cut off mid-write (process killed); drop it and refetch.
            raw_path.write_text("".join(l + "\n" for l in good), encoding="utf-8")
            break
    return stations


def download(fresh=False, log=print, raw_path=None, done_path=None):
    """Fetch the station list into data/stations-raw.jsonl, one page per line.

    The Radio Browser servers can be slow (tens of KB/s), so the download is
    paged and resumable: re-running continues where it stopped, and Ctrl+C
    stops early and keeps what has arrived. Pages are ordered by station uuid,
    which is effectively random, so a partial download is still a worldwide
    sample. Returns (stations, complete).
    """
    raw_path, done_path = raw_path or RAW_PATH, done_path or DONE_PATH
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if fresh:
        raw_path.unlink(missing_ok=True)
        done_path.unlink(missing_ok=True)
    stations = _read_pages(raw_path)
    if done_path.exists():
        return stations, True

    servers = _Servers()
    log(f"Downloading stations from Radio Browser ({'resuming at ' + format(len(stations), ',') if stations else 'first run'}).")
    log("  This can take a while; press Ctrl+C to stop early and use what has arrived.")
    try:
        while True:
            # hidebroken drops stations whose last check failed and has_geo_info
            # skips ones with no coordinates; clean() re-checks both anyway.
            page = servers.get_json(
                "/json/stations/search?hidebroken=true&has_geo_info=true"
                f"&order=stationuuid&limit={PAGE_SIZE}&offset={len(stations)}", log)
            with raw_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(page) + "\n")
            stations.extend(page)
            log(f"  {len(stations):,} stations so far")
            if len(page) < PAGE_SIZE:
                done_path.touch()
                return stations, True
    except KeyboardInterrupt:
        log(f"\n  Stopped early with {len(stations):,} stations. Run again to resume.")
        return stations, False


def haversine_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(min(1.0, a)))


# Countries as people say them, by ISO code. Radio Browser gives many in their official long
# form ("The Russian Federation"), and a few garbled ("Coted Ivoire").
COUNTRY_NAMES = {
    "AE": "United Arab Emirates", "AX": "Åland Islands", "CD": "DR Congo", "CI": "Côte d'Ivoire",
    "DO": "Dominican Republic", "FK": "Falkland Islands", "GB": "United Kingdom", "IR": "Iran",
    "KP": "North Korea", "KR": "South Korea", "MD": "Moldova", "MF": "Saint Martin",
    "MK": "North Macedonia", "NL": "Netherlands", "PH": "Philippines", "PS": "Palestine",
    "RE": "Réunion", "RU": "Russia", "SH": "Saint Helena", "SX": "Sint Maarten", "SY": "Syria",
    "TW": "Taiwan", "TZ": "Tanzania", "UM": "U.S. Minor Outlying Islands", "US": "United States",
    "VA": "Vatican City", "VE": "Venezuela",
}
_SMALL_WORDS = re.compile(r"(?<= )(Of|And|The|Da)(?= )")         # "Isle Of Man" -> "Isle of Man"
_PLACEHOLDER = re.compile(r"\b(select|choose|please)\b|izberite", re.IGNORECASE)   # "Please Select"


def country_name(s):
    """A station's country as people say it: "Russia", not "The Russian Federation"."""
    code = (s.get("countrycode") or "").upper()
    if code in COUNTRY_NAMES:
        return COUNTRY_NAMES[code]
    name = (s.get("country") or "").strip() or code
    if name.startswith("The "):
        name = name[4:]
    return _SMALL_WORDS.sub(lambda m: m.group(1).lower(), name)


def place(s):
    """'Region, Country' for a station, from whichever of the two it has. Leaves out regions
    that are a website's leftover prompt ("Please Select"), no word at all, or the country again."""
    country = country_name(s)
    region = (s.get("state") or "").strip()
    if (len(region) < 2 or not re.search(r"[^\W\d_]", region) or _PLACEHOLDER.search(region)
            or region.lower() in (country.lower(), (s.get("country") or "").strip().lower())):
        region = ""
    return ", ".join(p for p in (region, country) if p)


def _fold(text):
    """Lower case without accents, so "sao paulo" finds "São Paulo"."""
    text = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in text if not unicodedata.combining(c)).casefold()


class StationSearch:
    """Finds stations by name, place or style (their tags), for the phone page's search box.

        search = StationSearch(stations)
        for i in search.find("jazz paris"):
            search.stations[i]
    """

    def __init__(self, stations):
        self.stations = stations
        self._index = None          # built on the first search, so it doesn't slow the radio's start


    def find(self, query, limit=20):
        """Indexes into self.stations of the stations matching every word of the query: names
        that are the query or start with it first, then names with every word, then the rest
        (a match on place or style), the most popular first within each."""
        words = _fold(query).split()
        if not words:
            return []
        if self._index is None:
            self._index = [(_fold(s.get("name")),
                            _fold(" ".join((place(s), s.get("country") or "", s.get("tags") or ""))))
                           for s in self.stations]
        whole = " ".join(words)
        hits = []
        for i, (name, rest) in enumerate(self._index):
            if not all(w in name or w in rest for w in words):
                continue
            rank = (0 if name == whole else 1 if name.startswith(whole)
                    else 2 if all(w in name for w in words) else 3)
            hits.append((rank, -self.stations[i].get("votes", 0), i))
        hits.sort()
        return [i for _, _, i in hits[:limit]]


def _has_coords(s):
    lat, lon = s.get("geo_lat"), s.get("geo_long")
    if lat is None or lon is None:
        return False
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return False
    # 0,0 ("Null Island") is almost always a placeholder, not a real location.
    if lat == 0 and lon == 0:
        return False
    return -90 <= lat <= 90 and -180 <= lon <= 180


def _slim(s):
    out = {k: s.get(k) for k in KEEP_FIELDS}
    out["url_resolved"] = out["url_resolved"] or s.get("url")
    out["geo_lat"] = float(out["geo_lat"])
    out["geo_long"] = float(out["geo_long"])
    out["bitrate"] = int(out["bitrate"] or 0)
    out["votes"] = int(out["votes"] or 0)
    out["clickcount"] = int(out["clickcount"] or 0)
    out["name"] = (out["name"] or "").strip()
    out["language"] = (out["language"] or "").lower()
    return out


def _rank(s):
    """Higher is better when choosing between duplicates."""
    return (s["bitrate"], s["votes"], s["clickcount"])


def _dedupe(stations):
    """Drop same-name stations at (nearly) the same spot, keeping the best bitrate."""
    by_name = {}
    for s in stations:
        by_name.setdefault(s["name"].casefold(), []).append(s)

    kept = []
    for group in by_name.values():
        group.sort(key=_rank, reverse=True)
        chosen = []
        for s in group:
            if not any(haversine_km(s["geo_lat"], s["geo_long"], c["geo_lat"], c["geo_long"])
                       < DUPLICATE_KM for c in chosen):
                chosen.append(s)
        kept.extend(chosen)
    return kept


def load_my_stations(path=MY_STATIONS_PATH):
    if not path.exists():
        return []
    entries = json.loads(path.read_text(encoding="utf-8") or "[]")
    out = []
    for i, s in enumerate(entries):
        if not _has_coords(s) or not (s.get("url_resolved") or s.get("url")):
            print(f"my-stations.json: skipping entry {i} ({s.get('name')!r}): "
                  "needs geo_lat, geo_long and url_resolved", file=sys.stderr)
            continue
        s = dict(s)
        s.setdefault("stationuuid", f"my-{i}")
        out.append(_slim(s))
    return out


def clean(raw, my_stations=None):
    """Working stations with coordinates, deduplicated, plus hand-added ones."""
    good = [_slim(s) for s in raw
            if s.get("lastcheckok") == 1 and _has_coords(s)
            and (s.get("url_resolved") or s.get("url")) and (s.get("name") or "").strip()]
    stations = _dedupe(good)

    # Hand-added stations win: they replace any Radio Browser entry with the
    # same uuid, and are exempt from deduplication.
    my_stations = my_stations or []
    mine = {s["stationuuid"] for s in my_stations}
    stations = [s for s in stations if s["stationuuid"] not in mine] + my_stations
    return stations


def update(fresh=False, log=print):
    """Download (or resume), clean and save data/stations.json. Returns the cleaned list."""
    raw, complete = download(fresh=fresh, log=log)
    # Paging can repeat a station if the list changed between resumed runs.
    raw = list({s["stationuuid"]: s for s in raw}.values())
    mine = load_my_stations()
    stations = clean(raw, mine)
    ok = sum(1 for s in raw if s.get("lastcheckok") == 1 and _has_coords(s))
    log(f"Cleaned: {len(raw):,} downloaded, {ok:,} working with coordinates, "
        f"{len(stations):,} after dedupe (incl. {len(mine)} from my-stations.json)"
        + ("" if complete else "  [partial list]"))
    _save_clean(stations)
    log(f"Saved {CLEAN_PATH.relative_to(ROOT)}")
    return stations


def _save_clean(stations):
    """Write data/stations.json whole or not at all: a radio starting (or a power cut) at the
    wrong moment never finds half a file."""
    tmp = CLEAN_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(stations, ensure_ascii=False), encoding="utf-8")
    tmp.replace(CLEAN_PATH)


REFRESH_RAW = DATA_DIR / "refresh-raw.jsonl"
REFRESH_DONE = DATA_DIR / "refresh-raw.complete"


def refresh(log=print, min_share=0.8):
    """A fresh station list, downloaded beside the one in use (resuming a refresh that was cut
    short) and swapped in only once it's complete and not much smaller than the old one (at least
    min_share of it), so a bad day at Radio Browser can't shrink the dial. The monthly timer on the
    Pi runs this (radio.py --refresh-stations); a running radio notices the new
    data/stations.json and restarts itself on it when nobody's listening. True if swapped in."""
    try:
        raw, complete = download(log=log, raw_path=REFRESH_RAW, done_path=REFRESH_DONE)
    except RuntimeError as e:
        log(f"Station refresh failed, keeping the old list: {e}")
        return False
    if not complete:
        log("Station refresh stopped early; it carries on next time.")
        return False
    raw = list({s["stationuuid"]: s for s in raw}.values())
    stations = clean(raw, load_my_stations())
    try:
        old = len(json.loads(CLEAN_PATH.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        old = 0
    if len(stations) < old * min_share:
        log(f"Station refresh found only {len(stations):,} stations (now {old:,}): keeping the old list.")
        REFRESH_RAW.unlink(missing_ok=True)
        REFRESH_DONE.unlink(missing_ok=True)
        return False
    _save_clean(stations)
    REFRESH_RAW.replace(RAW_PATH)          # the new download becomes the one --update resumes from
    DONE_PATH.touch()
    REFRESH_DONE.unlink(missing_ok=True)
    log(f"Station list refreshed: {len(stations):,} stations (was {old:,}).")
    return True


def download_complete():
    return DONE_PATH.exists()


def load(path=CLEAN_PATH):
    """Load the cleaned list, re-merging my-stations.json so edits apply without a re-download."""
    stations = json.loads(path.read_text(encoding="utf-8"))
    mine = load_my_stations()
    ids = {s["stationuuid"] for s in mine}
    return [s for s in stations if s["stationuuid"] not in ids] + mine


def report_click(stationuuid):
    """Tell Radio Browser a station was played (their API etiquette; feeds the popularity counts)."""
    if stationuuid.startswith(("my-", "world:")):
        return
    try:
        _get_json(f"https://{PRIMARY_SERVER}/json/url/{stationuuid}", timeout=5)
    except Exception:
        pass


if __name__ == "__main__":
    update(fresh="--fresh" in sys.argv)
