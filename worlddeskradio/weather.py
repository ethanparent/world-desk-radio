"""The weather where the station is, from Open-Meteo (free, no account or key needed).

The face shows it after the time at the station, as a small symbol and a temperature
("3:12 pm  ☁ 18°"), and the phone page shows it in words. It's looked up when the dial stops
on a station, kept for FRESH_S, and shown for up to KEEP_S, so rolling back over a place you
visited shows its weather straight away without asking again. Offline, nothing is shown.

    weather = Weather()
    weather.get(lat, lon)              -> Report(sky="cloud", temp_c=18.4) or None (not known yet)
    report.temp()                      -> 65 (°F; "units" in settings.json can make it "C")
    weather.get(lat, lon, fetch=False) # only what's already known: while rolling past

Lookups run one at a time on a background thread, so the radio never waits for them.
"""

import json
import threading
import time
import urllib.request
from collections import OrderedDict
from dataclasses import dataclass

from .stations import USER_AGENT

URL = ("https://api.open-meteo.com/v1/forecast?latitude={lat:.2f}&longitude={lon:.2f}"
       "&current=temperature_2m,weather_code,is_day")
FRESH_S = 30 * 60             # look again after this long
KEEP_S = 2 * 3600             # don't show weather older than this
RETRY_S = 5 * 60              # after a failed lookup, wait this long before trying that place again
CACHE_PLACES = 300

# What the face draws, and what the phone page says
SKIES = {"sun": "Clear", "moon": "Clear", "sun-cloud": "Partly cloudy", "moon-cloud": "Partly cloudy",
         "cloud": "Cloudy", "fog": "Fog", "rain": "Rain", "snow": "Snow", "storm": "Thunderstorm"}


@dataclass(frozen=True)
class Report:
    sky: str                  # one of SKIES
    temp_c: float

    def temp(self, units="F"):
        """The temperature to show, rounded, in °F (or °C)."""
        return round(self.temp_c * 9 / 5 + 32 if units == "F" else self.temp_c)

    def words(self, units="F"):
        """For the phone page: "Partly cloudy, 64°F"."""
        return f"{SKIES[self.sky]}, {self.temp(units)}°{units}"


def sky(code, is_day=True):
    """A WMO weather code (what Open-Meteo reports) -> one of SKIES."""
    if code in (0, 1):
        return "sun" if is_day else "moon"
    if code == 2:
        return "sun-cloud" if is_day else "moon-cloud"
    if code in (45, 48):
        return "fog"
    if 51 <= code <= 67 or 80 <= code <= 82:
        return "rain"
    if 71 <= code <= 77 or code in (85, 86):
        return "snow"
    if code >= 95:
        return "storm"
    return "cloud"                # 3, overcast, and anything new


def fetch(lat, lon, timeout=8.0):
    """Ask Open-Meteo for the weather now at (lat, lon). Returns a Report; raises on failure."""
    req = urllib.request.Request(URL.format(lat=lat, lon=lon), headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        now = json.loads(r.read(65536))["current"]
    return Report(sky(int(now["weather_code"]), bool(now.get("is_day", 1))), float(now["temperature_2m"]))


class Weather:
    def __init__(self, fetch=fetch, clock=time.monotonic):
        self._fetch, self.clock = fetch, clock
        self._lock = threading.Lock()
        self._known = OrderedDict()   # place -> (Report, when it was looked up)
        self._failed = {}             # place -> when a lookup last failed
        self._wanted = None           # the place to look up next (only the latest one matters)
        self._busy = False
        self._wake = threading.Event()
        self._thread = None

    @staticmethod
    def _place(lat, lon):
        return round(lat, 1), round(lon, 1)       # about 10 km: a city's stations share one lookup

    def get(self, lat, lon, fetch=True):
        """The weather at (lat, lon) if it's known and recent enough, else None. With fetch,
        an unknown or old one is looked up in the background (ask again next frame)."""
        place, now = self._place(lat, lon), self.clock()
        with self._lock:
            known = self._known.get(place)
            stale = known is None or now - known[1] >= FRESH_S
            failed = self._failed.get(place)
            if fetch and stale and (failed is None or now - failed >= RETRY_S) and self._wanted != place:
                self._wanted = place
                self._start()
                self._wake.set()
            return known[0] if known and now - known[1] < KEEP_S else None

    @property
    def busy(self):
        with self._lock:
            return self._busy or self._wanted is not None

    def _start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

    def _run(self):
        while True:
            self._wake.wait()
            with self._lock:
                place, self._wanted = self._wanted, None
                self._wake.clear()
                self._busy = place is not None
            if place is None:
                continue
            try:
                report = self._fetch(*place)
            except Exception:             # offline, a timeout, an odd answer: try later
                report = None
            with self._lock:
                if report is None:
                    self._failed[place] = self.clock()
                else:
                    self._known[place] = (report, self.clock())
                    self._known.move_to_end(place)
                    self._failed.pop(place, None)
                    while len(self._known) > CACHE_PLACES:
                        self._known.popitem(last=False)
                self._busy = False
