"""What time it is at a station, and here.

    station_minutes(station, when)     -> minutes since midnight where the station is (or None)
    local_minutes(when)                -> minutes since midnight here, on the radio's own clock
    local_date(when)                   -> today's date here, "2026-10-21"
    fmt_time(minutes)                  -> "3:12 am"

`when` is a Unix time (seconds), default now. Time zones come from the IANA table of zones
(zone1970.tab: each zone's country codes and its main city), from the tzdata package on
Windows or /usr/share/zoneinfo on the Pi. A station gets the zone of its country whose main
city is nearest, which is right except close to a border between two zones of one country.
"""

import math
import re
import time
import zoneinfo
from datetime import datetime
from functools import lru_cache
from pathlib import Path


def local_minutes(when=None):
    t = time.localtime(time.time() if when is None else when)
    return t.tm_hour * 60 + t.tm_min


def local_date(when=None):
    return time.strftime("%Y-%m-%d", time.localtime(time.time() if when is None else when))


def fmt_time(minutes):
    """Minutes after midnight -> "3:12 am"."""
    h, m = divmod(minutes, 60)
    return f"{(h - 1) % 12 + 1}:{m:02d} {'am' if h < 12 else 'pm'}"


def station_minutes(station, when=None):
    """Minutes since midnight at the station, or None if its time zone can't be found."""
    name = zone_name(station["geo_lat"], station["geo_long"], station.get("countrycode") or "")
    if not name:
        return None
    local = datetime.fromtimestamp(time.time() if when is None else when, _zone(name))
    return local.hour * 60 + local.minute


@lru_cache(maxsize=4096)
def zone_name(lat, lon, countrycode=""):
    """IANA zone name for a spot: the zone of that country whose main city is nearest."""
    zones = _zones()
    if not zones:
        return None
    pool = [z for z in zones if countrycode.upper() in z[0]] or zones
    p = _unit(lat, lon)
    return max(pool, key=lambda z: sum(a * b for a, b in zip(p, z[1])))[2]


@lru_cache(maxsize=512)
def _zone(name):
    return zoneinfo.ZoneInfo(name)


def _unit(lat, lon):
    la, lo = math.radians(lat), math.radians(lon)
    return (math.cos(la) * math.cos(lo), math.cos(la) * math.sin(lo), math.sin(la))


def _degrees(text, whole):
    """'+3733' or '+373300' style (whole = digits of whole degrees) -> degrees."""
    sign = -1 if text[0] == "-" else 1
    digits = text[1:]
    value = int(digits[:whole]) + int(digits[whole:whole + 2]) / 60
    if len(digits) > whole + 2:
        value += int(digits[whole + 2:]) / 3600
    return sign * value


@lru_cache(maxsize=1)
def _zones():
    """[(country codes, unit vector of the main city, zone name)] from zone1970.tab."""
    text = _read_table()
    zones = []
    for line in (text or "").splitlines():
        if not line or line.startswith("#"):
            continue
        codes, coords, name = line.split("\t")[:3]
        m = re.fullmatch(r"([+-]\d+)([+-]\d+)", coords)
        if not m:
            continue
        lat_text, lon_text = m.groups()
        lat = _degrees(lat_text, 2)
        lon = _degrees(lon_text, 3)
        zones.append((frozenset(codes.split(",")), _unit(lat, lon), name))
    return zones


def _read_table():
    try:
        from importlib.resources import files
        return files("tzdata").joinpath("zoneinfo", "zone1970.tab").read_text(encoding="utf-8")
    except (ImportError, OSError):
        pass
    for folder in zoneinfo.TZPATH:
        path = Path(folder) / "zone1970.tab"
        if path.exists():
            return path.read_text(encoding="utf-8")
    return None
