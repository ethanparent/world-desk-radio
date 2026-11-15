"""The radio's features, checked with stand-ins: no sound, no network, a fake clock where it
matters, and a temporary folder for every file the radio writes (your favorites, settings and
liked songs are never touched).

    python tests/check_features.py

Stops at the first check that fails, with exit code 1. Needs the station list (python
radio.py --update); the first run downloads the Natural Earth coastlines and borders.
"""

import atexit
import dataclasses
import json
import random
import re
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import radio_map                                                                     # noqa: E402
from terradio import map_view as mv                                                 # noqa: E402
from terradio import stations as db                                                 # noqa: E402
from terradio import weather as weather_mod                                         # noqa: E402
from terradio.ball_light import BallLight                                           # noqa: E402
from terradio.clockradio import SNOOZE_FADE_S, Alarm, SleepTimer, next_alarm        # noqa: E402
from terradio.companion import Companion                                            # noqa: E402
from terradio.controls import DOUBLE_S, Controls                                    # noqa: E402
from terradio.dial import Dial                                                      # noqa: E402
from terradio.face_link import FaceState, decode, face_state, state_from_message, state_message  # noqa: E402
from terradio.favorites import Favorites                                            # noqa: E402
from terradio.health import StationHealth                                           # noqa: E402
from terradio.liked import LikedSongs, search_query, spotify_url                    # noqa: E402
from terradio.map_view import (FaceScreen, Hud, MapView, face_hud, fmt_date,        # noqa: E402
                                radius_for_view, spin_offset, width_for_zoom)
from terradio.player import Player                                                  # noqa: E402
from terradio.settings import Settings                                              # noqa: E402
from terradio.setup_page import SetupServer, lan_address                            # noqa: E402
from terradio.weather import Report, Weather, sky                                   # noqa: E402

if not db.CLEAN_PATH.exists():
    print("The station list isn't downloaded yet: run  python radio.py --update  first.")
    sys.exit(2)

TMP = Path(tempfile.mkdtemp(prefix="terradio-checks-"))
atexit.register(shutil.rmtree, TMP, ignore_errors=True)
ok_count = 0


def check(label, cond):
    global ok_count
    print(("  ok   " if cond else "  FAIL ") + label)
    ok_count += bool(cond)
    if not cond:
        raise SystemExit(1)


class FakePlayer:
    """Stations whose url is in `dead` never start."""
    volume, stream_volume, title, on_title, backend = 80, 80, "", None, "ffplay"

    def __init__(self):
        self.dead, self.plays, self.static_on = set(), [], False

    def stop_stream(self): pass
    def start_static(self): self.static_on = True
    def stop_static(self): self.static_on = False
    def fade_out_static(self): self.static_on = False
    def set_volume(self, v): self.volume = v
    def fade_out(self, s): self.faded = s

    def play_stream(self, url, timeout=5, canceled=lambda: False, fade_s=None, cue=None):
        self.plays.append((url, fade_s))
        return url not in self.dead

    def stream_alive(self): return True


class NoFavs:
    items = []
    def find(self, s): return None


def settle(dial, seconds=2):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        dial.tick()
        time.sleep(0.01)
        if dial.state.status in ("playing", "no signal"):
            return


stations = db.load()
print("Dead-station skipping")
player = FakePlayer()
health = StationHealth(TMP / "health.json")
dial = Dial(stations, player, lat=48.85, lon=2.35, settle_s=0.05, health=health)
first = dial.nearest(48.85, 2.35, 3)
player.dead = {first[0]["url_resolved"]}                       # the nearest one is dead
for attempt in range(2):                                        # stop at the same spot twice
    dial.set_position(48.85, 2.35); settle(dial)
check("the dead station is passed over for the next nearest", dial.state.playing is first[1])
check("after 2 failed tries it's skipped from every search", dial.nearest(48.85, 2.35)[0] is not first[0]
      and dial.state.skipped == 1)
check("the record survives a restart (health.json)",
      first[0]["stationuuid"] in StationHealth(TMP / "health.json").skipped())
player.dead = {s["url_resolved"] for s in stations}            # the internet is down
before, plays = json.loads((TMP / "health.json").read_text()), len(player.plays)
dial.set_position(48.85, 2.35); settle(dial, 5)
check("when nothing starts at all (internet down?) nobody is blamed",
      json.loads((TMP / "health.json").read_text()) == before and dial.state.status == "no signal")
check("...and the search gives up after a dozen tries", len(player.plays) - plays == 12)

print("A whole broadcaster down: reaching further out")


def made_up(name, host, lat, lon):
    return {"name": name, "stationuuid": name, "url_resolved": f"https://{host}/{name}",
            "geo_lat": lat, "geo_long": lon, "country": "Germany", "countrycode": "DE"}


town = ([made_up(f"big-{i}", "down.example", 50.5, 8.26) for i in range(20)]      # one company, all down
        + [made_up("other-1", "also-down.example", 50.6, 8.3), made_up("other-2", "gone.example", 50.7, 8.3)]
        + [made_up("further", "fine.example", 51.5, 9.0), made_up("further-still", "fine.example", 52.5, 13.4)])
far_player = FakePlayer()
far_player.dead = {s["url_resolved"] for s in town[:22]}
far = Dial(town, far_player, lat=50.5, lon=8.26, settle_s=0.05, health=StationHealth(TMP / "health-far.json"))
far.set_position(50.5, 8.26); settle(far)
check("nothing nearby starts: the nearest station that does is found",
      far.state.status == "playing" and far.state.playing["name"] == "further")
check("...and the dial moves there", (far.state.lat, far.state.lon) == (51.5, 9.0))
check("after two failures from one server, the rest of its stations are passed over",
      sum("down.example/" in url and "also" not in url for url, _ in far_player.plays) == 2
      and len(far_player.plays) == 5)

print("Standby, waking and the alarm's slow fade")
player.dead = set()
dial.turn(); settle(dial)
dial.sleep()
check("standby: silent, status off", dial.state.status == "off" and not player.static_on)
dial.wake(); settle(dial)
check("wake: tunes again", dial.state.status == "playing")
dial.sleep()
s = dial.nearest(35.68, 139.69)[0]
dial.set_position(s["geo_lat"], s["geo_long"], station=s, fade_s=60)
check("alarm: no burst of static before the station", not player.static_on)
settle(dial)
check("alarm: the station fades in over 60 s", player.plays[-1] == (s["url_resolved"], 60))

print("Static volume")
quiet_start = Player("ffplay", "ffplay", TMP / "static.wav", volume=10)
check("the static is a share of the volume from the start", quiet_start.static_volume == 4)
quiet_start.set_volume(80)
check("the share stays the same whatever volume the radio started at",
      quiet_start.static_volume == Player("ffplay", "ffplay", TMP / "static.wav", volume=80).static_volume == 28)

print("Controls: double press, standby, liked songs, world tour")
clock = [100.0]
c = Controls(dial, NoFavs(), themes=["night", "radar"], clock=lambda: clock[0])
c.liked = LikedSongs(TMP / "liked.json")
z = c.zoom
c.press_ball(); clock[0] += DOUBLE_S + 0.01; c.tick()
check("a single ball press zooms (a moment later)", c.zoom == (z + 1) % 3)
spot = (dial.state.lat, dial.state.lon)
c.press_ball(); clock[0] += 0.1; c.press_ball(); clock[0] += 1; c.tick()
check("a double press flies somewhere new instead of zooming", c.zoom == (z + 1) % 3
      and (dial.state.lat, dial.state.lon) != spot and c.toast() == "Surprise")
countries = {dial.random_station(random.Random(i)).get("countrycode") for i in range(60)}
check(f"surprises are spread over the world ({len(countries)} countries in 60 tries)", len(countries) > 30)
c.long_press_volume()
check("Volume long press: standby", c.asleep)
c.turn_tune(1)
check("any control wakes it, and does nothing else", not c.asleep and c._tune_index == -1)
settle(dial)
dial.state.song = ""
c.tap_both()
check("no song title: says so", c.toast() == "No song title to save")
dial.state.song = "Clair de Lune - Debussy"
dial.state.status = "playing"
c.tap_both()
check("tap both buttons: the song is saved", c.toast() == "Song saved" and c.liked.items()[0]["song"] == "Clair de Lune - Debussy")
c.tap_both()
check("...once", c.toast() == "Already saved" and len(c.liked.items()) == 1)
fs = face_state(dial.snapshot(), c)
check("the face is told it's liked", fs.liked and face_hud(fs).liked)
c.liked.delete(c.liked.items()[0]["id"])
check("deleted from the phone page", c.liked.items() == [] and json.loads((TMP / "liked.json").read_text()) == [])
c.set_world_tour(True)
spot = (dial.state.lat, dial.state.lon)
clock[0] += 181; c.tick()
check("world tour: somewhere new every 3 minutes", (dial.state.lat, dial.state.lon) != spot)
c.roll(0.1, 0)
check("rolling the ball ends the tour", not c.world_tour)

print("Alarm and sleep timer")
alarm = Alarm(on=True, hour=7, minute=30)
t730 = time.struct_time((2026, 10, 22, 7, 30, 5, 3, 295, 0))
check("rings at its minute", alarm.due(t730))
check("...once", not alarm.due(time.struct_time((2026, 10, 22, 7, 30, 40, 3, 295, 0))))
check("...and again the next day", alarm.due(time.struct_time((2026, 10, 23, 7, 30, 0, 4, 296, 0))))
check("not when off", not Alarm(on=False, hour=7, minute=30).due(t730))
check("settings round trip", Alarm.from_dict(alarm.to_dict()).to_dict() == alarm.to_dict())
timer = SleepTimer()
timer.set(30, 0)
check("timer running, 30 min left", timer.phase(0) == "running" and timer.minutes_left(0) == 30)
check("fading in its last minute", timer.phase(30 * 60 - 59) == "fading" and timer.minutes_left(30 * 60 - 59) == 1)
check("done", timer.phase(30 * 60) == "done")

print("Face: messages, heart, reminder, flight, wake fade")
fs = FaceState(lat=1.0, lon=2.0, status="playing", name="X", place="Y", song="A - B", liked=True, alarm=450, timer=23,
               clock=1300, idle=True)
check("new fields survive encode/decode", state_from_message(decode(state_message(fs))) == fs)
hud = face_hud(fs)
check("idle clock line: sleep timer and alarm", hud.reminder == "Sleep in 23 min · Alarm 7:30 am")
scr = FaceScreen([], 480)
a = FaceState(lat=51.5, lon=-0.1, status="playing", name="London", place="UK", slat=51.5, slon=-0.1)
b = dataclasses.replace(a, lat=-33.9, lon=151.2, slat=-33.9, slon=151.2, name="Sydney")
t = 0.0
scr.frame(a, t)
radii = []
for i in range(90):                     # 3 s at 30 fps
    t += 1 / 30
    scr.frame(b, t)
    radii.append(scr.camera.radius)
check("a long jump is flown: the camera backs out towards the whole world on the way",
      min(radii) < radii[-1] / 5)
check("...and lands exactly on the new spot", (scr.camera.lat, scr.camera.lon) == (b.lat, b.lon))
for i in range(30):
    t += 1 / 30
    scr.frame(b, t)
check("...and then settles (nothing left moving)", not scr.busy)
bright = lambda img: sum(img.convert("L").get_flattened_data())
full = bright(scr.frame(b, t + 0.1) or scr._last)
scr.fade_out(t + 0.1, 1.5)
mid = bright(scr.frame(b, t + 0.85))
dark = bright(scr.frame(b, t + 1.7) or scr._last)
check(f"standby: the screen fades out ({full} -> {mid} -> {dark})", full > mid > dark == 0)
scr.wake(t + 2.0, 1.5)
up = bright(scr.frame(b, t + 2.75))
back = bright(scr.frame(b, t + 3.6) or scr._last)
check(f"waking: it fades back in ({dark} -> {up} -> {back})", 0 < up < back == full)
scr.fade_out(t + 4.0, 1.5)
half = bright(scr.frame(b, t + 4.75))
scr.wake(t + 4.75, 1.5)
turned = bright(scr.frame(b, t + 4.9))
check("woken halfway through the fade: it turns around, no jump to black", 0 < half <= turned < full)

print("Standby steps in the main loop (radio_map.Extras)")


class StubPlayer:
    def fade_out(self, s): pass


class StubDial:
    player = StubPlayer()
    def snapshot(self): return None


class StubSettings:
    def get(self, key, default=None): return default
    def update(self, **kw): pass


class StubControls:
    asleep, world_tour, liked = False, False, None
    def close_panel(self): pass
    def say(self, text): pass


ctl = StubControls()
ex = radio_map.Extras(StubDial(), ctl, StubSettings(), Companion(), [])
steps = []
ctl.asleep = True
steps.append(ex.tick(10.0)); steps.append(ex.tick(10.5)); steps.append(ex.tick(11.6))
ctl.asleep = False
steps.append(ex.tick(20.0))
check(f"Volume long press: dim for 1.5 s, then dark; a touch wakes it fading in ({steps})",
      steps == [("dim", 1.5), None, "sleep", ("wake", 1.5, False)])
ctl.asleep = True
ex.tick(30.0)
ctl.asleep = False
check("woken during the fade: wake at once and restart the sound", ex.tick(30.5) == ("wake", 1.5, True))
print("Weather at the station")
check("weather codes -> symbols", [sky(0), sky(0, False), sky(2), sky(2, False), sky(3), sky(45), sky(61), sky(81),
                                   sky(73), sky(95)] ==
      ["sun", "moon", "sun-cloud", "moon-cloud", "cloud", "fog", "rain", "rain", "snow", "storm"])
r = Report("sun-cloud", 18.4)
check(f"°F by default, °C on request ({r.temp()}°F, {r.temp('C')}°C; '{r.words()}')",
      r.temp() == 65 and r.temp("C") == 18 and r.words() == "Partly cloudy, 65°F")
wclock = [0.0]
calls = []


def fake_fetch(lat, lon):
    calls.append((lat, lon))
    if lat > 80:
        raise OSError("offline")
    return Report("rain", -3.6)


def wait_weather(w):
    end = time.time() + 3
    while w.busy and time.time() < end:
        time.sleep(0.01)


w = Weather(fetch=fake_fetch, clock=lambda: wclock[0])
check("not known yet: nothing (and it's looked up in the background)", w.get(48.86, 2.35) is None)
wait_weather(w)
check("then it's there", w.get(48.86, 2.35) == Report("rain", -3.6) and calls == [(48.9, 2.4)])
w.get(48.87, 2.38); wait_weather(w)
check("a station in the same city shares the lookup", len(calls) == 1)
check("rolling past an unknown place: no lookup", w.get(10.0, 10.0, fetch=False) is None and len(calls) == 1)
wclock[0] += weather_mod.FRESH_S + 1
check("after 30 minutes: still shown while it's looked up again", w.get(48.86, 2.35) is not None)
wait_weather(w)
check("...and looked up again", len(calls) == 2)
wclock[0] += weather_mod.KEEP_S + 1
check("too old to show (after 2 hours)", w.get(48.86, 2.35, fetch=False) is None)
w.get(85.0, 0.0); wait_weather(w)
w.get(85.0, 0.0); wait_weather(w)
check("offline: no retry for 5 minutes", calls.count((85.0, 0.0)) == 1)
wclock[0] += weather_mod.RETRY_S + 1
w.get(85.0, 0.0); wait_weather(w)
check("...then it tries again", calls.count((85.0, 0.0)) == 2)

fs = FaceState(lat=1.0, lon=2.0, status="playing", name="X", place="Y", local=912, sky="snow", temp=-4)
check("sky and temp survive encode/decode", state_from_message(decode(state_message(fs))) == fs)
check("the face shows a real minus sign", face_hud(fs).weather == ("snow", "−4°"))
check("no weather, no symbol", face_hud(dataclasses.replace(fs, sky=None, temp=None)).weather is None)
check("weather alone (time unknown) still shows", face_hud(dataclasses.replace(fs, local=None)).local_alpha == 1.0)

print("The phone remote, settings and updates (radio_map.Extras)")


class FakeUpdater:
    auto, restart_due = False, False

    def __init__(self):
        self.asked = []

    def install(self): self.asked.append("install")
    def check(self): self.asked.append("check")
    def status(self): return {"state": "ready", "new": ["Phone remote"], "auto": self.auto}


player.dead = set()
favs = Favorites(TMP / "favorites.json")
rc_clock = [500.0]
rc = Controls(dial, favs, themes=["night", "radar"], clock=lambda: rc_clock[0])
rc.liked = LikedSongs(TMP / "liked2.json")
settings = Settings(TMP / "settings.json")
companion = Companion()
upd = FakeUpdater()
w = Weather(fetch=lambda lat, lon: Report("sun", 21.0))
ex = radio_map.Extras(dial, rc, settings, companion, stations, weather=w, updater=upd)
dial.set_position(48.85, 2.35); settle(dial)


def press(do, value=None):
    companion.request("remote", do=do, value=value)
    ex.tick(time.monotonic())


on_air = dial.state.playing
press("next")
check(f"Next: on to the next station out, not the one already on ({rc.toast()!r})",
      rc.toast() == "2nd nearest" and dial.state.nearest is not on_air)
press("next")
check(f"...and the one after ({rc.toast()!r})", rc.toast() == "3rd nearest")
press("prev")
press("prev")
check("Previous: back to the nearest", rc.toast() == "Nearest")
press("prev")
check("...and no further (it stays on)", rc.toast() == "Nearest")
press("volume", 35)
check("the volume slider sets the volume and shows the ring", dial.state.volume == 35 and rc.volume_shown())
press("mute")
check("mute", rc.muted and dial.state.volume == 0)
press("mute")
check("...and back to where it was", not rc.muted and dial.state.volume == 35)
settle(dial)
press("save")
check(f"★ saves the station as a favorite ({rc.toast()!r})", len(favs) == 1 and rc.toast() == "Saved as favorite 1")
press("next"); settle(dial)
press("favorite", 0)
check("tapping a favorite tunes it", rc.toast() == "Favorite 1 of 1"
      and abs(dial.state.lat - favs.items[0]["lat"]) + abs(dial.state.lon - favs.items[0]["lon"]) < 1e-9)
press("favorite", 7)
check("a favorite that's gone: nothing happens", rc.toast() == "Favorite 1 of 1")
press("theme")
check("theme", rc.theme == "radar")
press("power")
check("power: standby", rc.asleep)
rc_clock[0] += 5                        # the last message has gone
press("next")
check("any button wakes it (and does nothing else)", not rc.asleep and rc.toast() == "")
settle(dial)
ex.publish(dial.snapshot(), face_state(dial.snapshot(), rc, weather=lambda s: ex.weather_at(s, "playing")), 0)
time.sleep(0.2)
st = dial.snapshot()
state = face_state(st, rc, weather=lambda s: ex.weather_at(s, st.status))
ex._published = -1e9
ex.publish(st, state, 1.0)
snap = companion.snapshot()
check(f"the phone page sees the weather ({snap['now']['weather']!r}), volume, favorites, updates",
      snap["now"]["weather"] == "Clear, 70°F" and snap["now"]["sky"] == "sun" and snap["volume"] == 35
      and snap["favorites"][0]["name"] and snap["update"]["state"] == "ready")
companion.request("settings", units="C"); ex.tick(time.monotonic())
check("°C from the phone: the face's temperature follows", ex.weather_at(st.playing, "playing") == ("sun", 21)
      and settings.get("units") == "C")
check(f"the phone page's Map card is told the themes and which one is on ({snap['theme']!r})",
      snap["theme"] == rc.theme and [t["name"] for t in snap["themes"]] == [t.name for t in radio_map.THEMES]
      and all(re.fullmatch(r"#[0-9a-f]{6}", t[k]) for t in snap["themes"] for k in ("space", "globe", "coast", "live")))
companion.request("theme", name="night"); ex.tick(time.monotonic())
check("a theme tapped on the phone goes straight there", rc.theme == "night" and rc.toast() == "Night theme")
companion.request("theme", name="plaid"); ex.tick(time.monotonic())
check("...and a theme that isn't there changes nothing", rc.theme == "night")
check(f"the Map card is told the zoom levels and which one it's at ({snap['zooms']}, {snap['zoom']})",
      snap["zooms"] == ["Whole world", "2,000 km", "500 km"] and snap["zoom"] == rc.zoom)
companion.request("zoom", index=2); ex.tick(time.monotonic())
check("a zoom tapped on the phone goes straight there", rc.zoom == 2 and rc.toast() == "500 km across")
companion.request("zoom", index=7); ex.tick(time.monotonic())
check("...and one that isn't there changes nothing", rc.zoom == 2)
world_rc = Controls(dial, favs, zooms=(1700, 600, 200))
check("a game world's zoom levels are named in words",
      [world_rc.zoom_label(i) for i in range(3)] == ["Whole map", "Closer", "Close up"])
companion.request("settings", auto_update=True); ex.tick(time.monotonic())
check("automatic updates from the phone", upd.auto and settings.get("auto_update") is True)
companion.request("update", do="install"); companion.request("update", do="check"); ex.tick(time.monotonic())
check("Install now / Check now reach the updater", upd.asked == ["install", "check"])
press("unfavorite", 0)
check("removing a favorite from the phone", len(favs) == 0
      and json.loads((TMP / "favorites.json").read_text()) == [])

print("Idle spin, country borders and cities")
check("spin: still at the start, easing in, then 2° a second",
      spin_offset(0) == 0 and 0 < spin_offset(2) < 2 * 2 / 2 and abs(spin_offset(10) - 2 * (10 - 2)) < 1e-9)
check("...in steps of 1/15 s (light on the face)", spin_offset(4.01) == spin_offset(4.0) and spin_offset(4.07) > spin_offset(4.0))
idle = FaceState(lat=40.0, lon=10.0, status="playing", name="X", place="Y", slat=40.0, slon=10.0, idle=True, spin=True,
                 borders=True, cities=True)
check("borders, cities and spin survive encode/decode", state_from_message(decode(state_message(idle))) == idle)
fs_plain = face_state(dial.snapshot(), rc, spin=True)
check("spin is only sent while idle", not fs_plain.spin and fs_plain.borders and fs_plain.cities)
scr = FaceScreen([], 480)
t = 0.0
awake = dataclasses.replace(idle, idle=False, spin=False)
for _ in range(30):
    scr.frame(awake, t); t += 1 / 30
lons = []
for _ in range(int(12 * 30)):                  # 12 s idle
    scr.frame(idle, t); t += 1 / 30
    lons.append(scr.camera.lon)
turned = (10.0 - lons[-1]) % 360
check(f"idle: the globe turns west to east, the land drifting right ({turned:.1f}° in 12 s)",
      abs(turned - spin_offset(12 - 1 / 30)) < 0.6 and scr.camera.lat == 40.0 and scr.busy)
steps = len(set(round(l, 6) for l in lons[-60:]))
check(f"...stepping about 15 times a second ({steps} positions in 2 s)", 25 <= steps <= 32)
for _ in range(120):
    scr.frame(awake, t); t += 1 / 30
check("a touch brings it back to the station and it settles",
      (round(scr.camera.lat, 6), round(scr.camera.lon, 6)) == (40.0, 10.0) and not scr.busy)
scr3 = FaceScreen([], 480)
t = 0.0
for _ in range(30):
    scr3.frame(awake, t); t += 1 / 30
for _ in range(60 * 30):                 # a minute of spin: over 100 degrees round
    scr3.frame(idle, t); t += 1 / 30
world_r = mv.radius_for_view(180, 480)
radii = []
for _ in range(int((mv.FLY_S + 0.3) * 30)):
    scr3.frame(awake, t); t += 1 / 30
    radii.append(scr3.camera.radius)
check(f"waking a spun globe: it turns back at whole-world size, never smaller ({min(radii) / world_r:.2f} x), "
      f"then dives in to the station", min(radii) >= world_r * 0.999 and abs(scr3.camera.lon - 10.0) < 1e-6
      and abs(radii[-1] - mv.radius_for_view(width_for_zoom(2000), 480)) < 1e-6)
scr2 = FaceScreen([], 480)
off = dataclasses.replace(idle, spin=False)
t = 0.0
for _ in range(150):                    # 5 s: past the on-air symbol's 3 s
    scr2.frame(off, t); t += 1 / 30
check("spin off: the idle globe stays put", scr2.camera.lon == 10.0 and not scr2.busy)
view = MapView(stations, 480)
r = radius_for_view(width_for_zoom(2000), 480)
plain = view.render(48.5, 10.0, r, Hud())
with_borders = view.render(48.5, 10.0, r, Hud(borders=True))
changed = sum(1 for a, b in zip(plain.get_flattened_data(), with_borders.get_flattened_data()) if a != b)
check(f"borders draw on the map ({changed:,} pixels change over central Europe)", changed > 500)
check("face_hud carries the setting", face_hud(dataclasses.replace(awake, borders=True)).borders
      and not face_hud(dataclasses.replace(awake, borders=False)).borders)


def labeled(lat, lon, km):
    """How many pixels the city labels change at this spot and zoom."""
    radius = radius_for_view(width_for_zoom(km), 480)
    a, b = view.render(lat, lon, radius, Hud()), view.render(lat, lon, radius, Hud(cities=True))
    return sum(1 for p, q in zip(a.get_flattened_data(), b.get_flattened_data()) if p != q)


check(f"cities are labeled at 2,000 km ({labeled(48.5, 10.0, 2000):,} pixels over central Europe) "
      f"and 500 km ({labeled(-37.81, 144.96, 500):,} around Melbourne)",
      labeled(48.5, 10.0, 2000) > 500 and labeled(-37.81, 144.96, 500) > 200)
check("...but not in the whole-world view", labeled(20.0, 10.0, 0) == 0)
check("face_hud carries the cities setting", face_hud(dataclasses.replace(awake, cities=True)).cities
      and not face_hud(dataclasses.replace(awake, cities=False)).cities)
companion.request("settings", borders=False, cities=False, spin=False); ex.tick(time.monotonic())
ex._published = -1e9
ex.publish(dial.snapshot(), face_state(dial.snapshot(), rc), 2.0)
snap = companion.snapshot()
check("the phone's Display switches reach the settings and come back to the page",
      settings.get("borders") is False and settings.get("spin") is False and snap["borders"] is False
      and snap["spin"] is False and settings.get("cities") is False and snap["cities"] is False)

print("Two alarms with days, snooze, brightness, the date")
wed_742 = time.struct_time((2026, 10, 21, 7, 42, 0, 2, 294, 0))     # a Wednesday
wed_700 = time.struct_time((2026, 10, 21, 7, 0, 5, 2, 294, 0))
sat_700 = time.struct_time((2026, 10, 24, 7, 0, 5, 5, 297, 0))
weekdays, weekend = Alarm(on=True, hour=7, days=range(5)), Alarm(on=True, hour=9, days=(5, 6))
check("a weekday alarm rings on Wednesday, not on Saturday", weekdays.due(wed_700) and not weekdays.due(sat_700))
check(f"next alarm from Wednesday 7:42 am: Thursday 7:00 ({weekdays.minutes_until(wed_742)} min) before Saturday 9:00",
      next_alarm([weekend, weekdays], wed_742) is weekdays and weekdays.minutes_until(wed_742) == 1398
      and weekend.minutes_until(wed_742) == 3 * 1440 + 78)
check("nothing on: no next alarm", next_alarm([Alarm(), Alarm()], wed_742) is None)
check("days survive settings", Alarm.from_dict(weekend.to_dict()).days == [5, 6])
check(f"the date line: {fmt_date('2026-10-21')!r}, {fmt_date('2025-08-07')!r}", fmt_date("2026-10-21") == "Wed Oct 21" and fmt_date("2025-08-07") == "Thu Aug 7")
fs = FaceState(lat=1.0, lon=2.0, idle=True, clock=462, date="2026-10-21", bright=40)
check("date and brightness survive encode/decode", state_from_message(decode(state_message(fs))) == fs)
check("the idle clock shows the date", face_hud(fs).date == "Wed Oct 21")
scr = FaceScreen([], 480)
full = bright(scr.frame(dataclasses.replace(fs, bright=100), 0.0))
dim = bright(scr.frame(fs, 0.1))
check(f"40% brightness dims the screen ({full} -> {dim})", abs(dim / full - 0.4) < 0.03)   # (each pixel rounds down)
lit = BallLight().color(dataclasses.replace(fs, status="playing", idle=False, bright=100), 0)
half = BallLight().color(dataclasses.replace(fs, status="playing", idle=False, bright=50), 0)
check(f"...and the trackball's light ({lit} -> {half})", sum(half) * 2 in range(sum(lit) - 4, sum(lit) + 5))

old = Settings(TMP / "old-settings.json")
old.update(alarm={"on": True, "hour": 6, "minute": 30, "station": None})
migrated = radio_map.Extras(dial, rc, old, Companion(), stations)
check("an older settings.json's one alarm becomes the first of two",
      (migrated.alarms[0].on, migrated.alarms[0].hour, migrated.alarms[0].days) == (True, 6, list(range(7)))
      and migrated.alarms[1].days == [5, 6] and not migrated.alarms[1].on)

# Holding both knob buttons: the settings menu. Tune chooses, its press opens that card in the
# menu's place, and the Volume button closes whichever card is up.
from terradio.connectivity import FakeBluetooth                                     # noqa: E402
from terradio.panels import BluetoothMenu, PhoneRemote, SettingsMenu                # noqa: E402

rc.close_panel()
if rc.asleep:
    rc.long_press_volume()
remote = PhoneRemote("http://192.168.1.5:8737/")
rc.bluetooth_menu = BluetoothMenu(FakeBluetooth())
rc.settings_menu = SettingsMenu([("Bluetooth", rc.open_bluetooth),
                                 ("Phone remote", lambda: rc.open_panel(remote), lambda: "QR code"),
                                 ("Wi-Fi setup", lambda: None)], clock=lambda: rc_clock[0])
rc.press_both()
v = rc.panel_view()
check("holding both knob buttons opens the settings menu: Bluetooth, Phone remote, Wi-Fi setup",
      rc.panel is rc.settings_menu and v.title == "Settings" and v.sel == 0
      and v.items == (("Bluetooth", ""), ("Phone remote", "QR code"), ("Wi-Fi setup", "")))
rc.turn_tune(5)
at_end = rc.settings_menu.sel
rc.turn_tune(-1)
rc.press_tune()
v = rc.panel_view()
check("turning Tune to Phone remote and pressing it shows the phone page's QR code in the menu's place",
      at_end == 2 and rc.panel is remote and v.qr == remote.url and not rc.settings_menu.is_open)
rc.press_volume()
check("the Volume button closes the QR code", rc.panel is None)
rc.press_both()
rc.press_tune()
check("pressing Tune on the first row opens the Bluetooth menu", rc.panel is rc.bluetooth_menu)
rc.close_panel()
rc.settings_menu = rc.bluetooth_menu = None

rclock = [1000.0]
ex.clock = lambda: rclock[0]
ex.snooze_s = 9 * 60
rc.close_panel()
if rc.asleep:
    rc.long_press_volume()
for _ in range(3):
    ex.tick(time.monotonic())
settle(dial)
ex._ring(ex.alarms[0])
check("ringing: the alarm card is up", rc.panel is ex.alarm_card and rc.panel_view().title == "Good morning"
      and ex.ringing is ex.alarms[0])
rc.press_volume()
check("Volume on the card: snooze (the radio goes quiet, the card closes)",
      rc.asleep and rc.panel is None and ex.ringing is ex.alarms[0])
steps = [ex.tick(time.monotonic())]
time.sleep(1.6)
steps.append(ex.tick(time.monotonic()))
check(f"...fading out like standby ({steps})", steps == [("dim", 1.5), "sleep"])
rclock[0] += 9 * 60 + 1
back = ex.tick(time.monotonic())
check(f"9 minutes later it rings again, fading in over {SNOOZE_FADE_S} s ({back})",
      back == ("wake", SNOOZE_FADE_S, False) and rc.panel is ex.alarm_card and not rc.asleep)
rc_clock[0] += 5
rc.press_tune()
rc.panel_view()
check(f"Tune on the card: off, and the station plays on ({rc.toast()!r})",
      rc.panel is None and ex.ringing is None and rc.toast() == "Good morning" and not rc.asleep)
ex._ring(ex.alarms[1])
companion.request("ring", do="snooze")
ex.tick(time.monotonic())
ex._published = -1e9
ex.publish(dial.snapshot(), face_state(dial.snapshot(), rc), 3.0)
snap = companion.snapshot()
check(f"Snooze from the phone ({snap['ringing']})", rc.asleep and snap["ringing"]["snoozed"] is not None)
companion.request("ring", do="stop")
ex.tick(time.monotonic())
check("Turn off from the phone while snoozed: no more today", ex.ringing is None and ex._snooze_until is None)
rc.long_press_volume()                  # back on
for _ in range(2):
    ex.tick(time.monotonic())
ex._ring(ex.alarms[0])
rc.press_volume()                       # snooze
ex.tick(time.monotonic())
rc.turn_tune(1)                         # then wake it by hand
ex.tick(time.monotonic())
check("woken by hand while snoozing: the alarm is done", ex.ringing is None and ex._snooze_until is None)
companion.request("alarm", index=1, on=True, hour=6, minute=15, days=[0, 2, 4], use_current=False)
ex.tick(time.monotonic())
check("the phone sets the second alarm and its days",
      (ex.alarms[1].on, ex.alarms[1].hour, ex.alarms[1].minute, ex.alarms[1].days) == (True, 6, 15, [0, 2, 4])
      and settings.get("alarms")[1]["days"] == [0, 2, 4])

print("Saved songs: Find on Spotify")
cases = {"Daughtry - Every Time You Turn Around | Album: Leave This Town": "Daughtry Every Time You Turn Around",
         "Now Playing: U2 - Beautiful Day": "U2 Beautiful Day", "Warfaze \u2013 Tomake": "Warfaze Tomake",
         "Joel Corry x Jem Cooke - Daydream (Official Video)": "Joel Corry x Jem Cooke Daydream",
         "Adele - Hello [Live]": "Adele Hello", "Daft Punk - One More Time (Radio Edit)": "Daft Punk One More Time (Radio Edit)"}
check("station titles tidied for a search (album tags, 'Now Playing:', dashes, '[Live]', '(Official Video)')",
      all(search_query(k) == v for k, v in cases.items()))
check(f"the link: {spotify_url('Warfaze - Tomake')}", spotify_url("Warfaze - Tomake")
      == "https://open.spotify.com/search/Warfaze%20Tomake" and "%26" in spotify_url("Simon & Garfunkel - Mrs. Robinson"))
settle(dial)
dial.state.status, dial.state.song = "playing", "Adele - Hello [Live]"
rc.liked.add("Adele - Hello [Live]", dial.state.playing)
ex._published = -1e9
ex.publish(dial.snapshot(), face_state(dial.snapshot(), rc), 4.0)
check("the phone page gets a Spotify link for each saved song",
      companion.snapshot()["liked"][0]["spotify"] == "https://open.spotify.com/search/Adele%20Hello")

print("Station search")
search = db.StationSearch(stations)
first = search.find("bbc radio 1")
check(f"a name finds that station first ({stations[first[0]]['name'] if first else None})",
      first and stations[first[0]]["name"].lower().startswith("bbc radio 1"))
paulo = search.find("sao paulo", limit=50)
check("accents don't matter (\"sao paulo\" finds São Paulo)",
      paulo and any("são paulo" in (stations[i]["name"] + db.place(stations[i])).lower() for i in paulo))
check("every word has to match, and an empty search finds nothing",
      search.find("zzzqqq jazz") == [] and search.find("   ") == [] and len(search.find("jazz", limit=7)) == 7)

print("The phone page's server")


class NoSetup:
    is_open = False
    def status(self): return {"phase": "off"}


srv = SetupServer(NoSetup(), companion, port=0, search=search)
srv.start()


def post_json(path, body):
    req = urllib.request.Request(srv.url.rstrip("/") + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


companion.requests()
codes = [post_json("/api/remote", {"do": "next"}), post_json("/api/remote", {"do": "volume", "value": 60}),
         post_json("/api/remote", {"do": "explode"}), post_json("/api/remote", {"do": "volume", "value": 400}),
         post_json("/api/settings", {"units": "K"}), post_json("/api/settings", {"units": "F"}),
         post_json("/api/update", {"do": "install"}), post_json("/api/update", {"do": "delete"})]
got = companion.requests()
check(f"good requests go through, bad ones are refused ({codes})", codes == [200, 200, 400, 400, 400, 200, 200, 400]
      and got == [("remote", {"do": "next", "value": None}), ("remote", {"do": "volume", "value": 60}),
                  ("settings", {"units": "F"}), ("update", {"do": "install"})])
companion.requests()
codes = [post_json("/api/alarm", {"index": 1, "on": True, "time": "06:15", "days": [5, 6]}),
         post_json("/api/alarm", {"index": 2, "on": True, "time": "06:15"}),
         post_json("/api/alarm", {"index": 0, "on": True, "time": "06:15", "days": []}),
         post_json("/api/alarm", {"index": 0, "on": True, "time": "06:15", "days": [0, 7]}),
         post_json("/api/ring", {"do": "snooze"}), post_json("/api/ring", {"do": "later"}),
         post_json("/api/settings", {"brightness": 40}), post_json("/api/settings", {"brightness": 5})]
got = companion.requests()
check(f"alarms, ring and brightness requests are checked ({codes})",
      codes == [200, 400, 400, 400, 200, 400, 200, 400] and [k for k, _ in got] == ["alarm", "ring", "settings"]
      and got[0][1]["days"] == [5, 6] and got[2][1] == {"brightness": 40})
companion.requests()
codes = [post_json("/api/theme", {"name": "radar"}), post_json("/api/theme", {"name": ""}),
         post_json("/api/theme", {"name": "x" * 40}), post_json("/api/theme", {"name": "Radar!"})]
got = companion.requests()
check(f"the Map card's theme requests are checked ({codes})",
      codes == [200, 400, 400, 400] and got == [("theme", {"name": "radar"})])
companion.requests()
codes = [post_json("/api/zoom", {"index": 0}), post_json("/api/zoom", {"index": -1}),
         post_json("/api/zoom", {"index": "2"}), post_json("/api/zoom", {"index": True})]
got = companion.requests()
check(f"the Map card's zoom requests are checked ({codes})",
      codes == [200, 400, 400, 400] and got == [("zoom", {"index": 0})])
companion.requests()
with urllib.request.urlopen(srv.url + "api/search?q=" + urllib.request.quote("triple j"), timeout=5) as r:
    found = json.load(r)["results"]
codes = [post_json("/api/play", {"id": found[0]["id"]}), post_json("/api/play", {"id": len(stations)}),
         post_json("/api/play", {"id": "x"})]
got = companion.requests()
check(f"the search box finds stations, and tapping one asks the radio to play it ({codes})",
      found and "triple j" in found[0]["name"].lower() and found[0]["place"]
      and codes == [200, 400, 400] and got == [("play", {"id": found[0]["id"]})])
rc.asleep = False
ex._request("play", got[0][1], 0.0)
picked = stations[found[0]["id"]]
check("the radio flies to the station found and tunes that one",
      abs(dial.state.lat - picked["geo_lat"]) < 1e-9 and abs(dial.state.lon - picked["geo_long"]) < 1e-9
      and dial._chosen is picked
      and rc.toast() == "Found it")
srv.stop()
check(f"--lan shows the computer's address on the home network ({lan_address()})",
      re.fullmatch(r"\d+\.\d+\.\d+\.\d+", lan_address()) is not None)
print(f"\nall {ok_count} checks passed")
