"""Game worlds (worlds.py, PLAN.md Stage 7), checked on a pretend world made here: an island,
three stations of test tones, and a stream. Nothing of yours is touched (a temporary folder for
the world and every file the radio writes); the tones play muted.

    python tests/check_worlds.py            # a picture of the pretend world goes to a temp folder
    python tests/check_worlds.py OUT        # ...or to OUT

Needs ffmpeg and ffprobe (the tones and their lengths) and ffplay or mpv.
"""

import atexit
import json
import math
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import radio                                                                    # noqa: E402
import radio_map                                                                     # noqa: E402
from worlddeskradio import worlds                                                         # noqa: E402
from worlddeskradio.dial import Dial                                                      # noqa: E402
from worlddeskradio.face_link import (FaceLink, FaceState, decode, face_state, loopback,  # noqa: E402
                                 state_from_message, state_message)
from worlddeskradio.map_view import FaceScreen, MapView, face_hud, radius_for_view       # noqa: E402
from worlddeskradio.panels import WorldMenu                                               # noqa: E402
from worlddeskradio.player import Player, find_backend                                    # noqa: E402
from worlddeskradio.settings import Settings                                              # noqa: E402
from worlddeskradio.virtual_face import VirtualFace                                       # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="worlddeskradio-worlds-"))
atexit.register(shutil.rmtree, TMP, ignore_errors=True)
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else TMP
OUT.mkdir(parents=True, exist_ok=True)
ok_count = 0


def check(label, cond):
    global ok_count
    print(("  ok   " if cond else "  FAIL ") + label)
    ok_count += bool(cond)
    if not cond:
        sys.exit(1)


for tool in ("ffmpeg", "ffprobe"):
    if not shutil.which(tool):
        print(f"{tool} isn't installed (winget install Gyan.FFmpeg): can't make the test tones.")
        sys.exit(2)


# --- a pretend world ------------------------------------------------------------

def island(cx, cy, r, n=90):
    """A wobbly island outline, closed, as [[lat, lon], ...]."""
    pts = []
    for i in range(n + 1):
        a = 2 * math.pi * i / n
        k = r * (1 + 0.18 * math.sin(3 * a) + 0.08 * math.cos(7 * a))
        pts.append([round(cy + k * math.sin(a), 4), round(cx + k * math.cos(a), 4)])
    return pts


def make_world(folder):
    """worlds/testland: a 1,500 km island, two stations of tones and one stream."""
    world = folder / "testland"
    stations = [("north", "North Radio", 2.0, 0.5, (300, 400, 500)), ("south", "South Radio", -3.0, -1.0, (600, 700))]
    for sid, _, _, _, tones in stations:
        audio = world / "stations" / sid
        audio.mkdir(parents=True)
        names = []
        for i, hz in enumerate(tones):
            name = f"{i:04d}.opus"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency={hz}:duration={4 + i}",
                            "-ac", "2", "-ar", "48000", "-c:a", "libopus", "-b:a", "48k", str(audio / name)], check=True)
            names.append((name, f"Tone {hz} Hz" if i % 2 == 0 else ""))
        worlds.write_playlist(audio, names)
    # Its own signal symbols: a solid block on air, a strip (a ramp, so where it's scrolled to
    # shows) while tuning, and one that's missing.
    from PIL import Image
    (world / "icons").mkdir()
    Image.new("L", (10, 8), 255).save(world / "icons" / "playing.png")
    Image.frombytes("L", (28, 6), bytes((x * 9) % 256 for _ in range(6) for x in range(28))).save(world / "icons" / "tuning.png")
    (world / "world.json").write_text(json.dumps({
        "title": "Testland", "theme": "radar",
        "icons": {"playing": {"file": "icons/playing.png"}, "tuning": {"file": "icons/tuning.png", "window": 8},
                  "no signal": {"file": "icons/gone.png"}, "dancing": {"file": "icons/playing.png"}},
        "coast": [island(0, 0, 6.5)],
        "borders": [[[6, -2], [0, 0], [-6, 2]]],
        "places": [{"name": "Village", "lat": 3, "lon": 2.5, "zoom": 7.0}, {"name": "Capital", "lat": -1, "lon": -3, "zoom": 4.0}],
        "stations": [{"id": sid, "name": name, "place": "Somewhere", "lat": lat, "lon": lon}
                     for sid, name, lat, lon, _ in stations]
        + [{"id": "stream", "name": "A Stream", "lat": 0.5, "lon": 4.0, "url": "http://example.invalid/stream"},
           {"id": "broken", "name": "No Audio", "lat": 1, "lon": 1}],
    }), encoding="utf-8")
    return world


print("A pretend world")
make_world(TMP / "worlds")
notes = []
w = worlds.load("testland", TMP / "worlds", log=notes.append)
check("it loads: title, theme, its lines", w.title == "Testland" and w.theme == "radar"
      and len(w.coast) == 1 and len(w.borders) == 1)
check("a station with no audio is left out, with a note", len(w.stations) == 3 and any("broken" in n for n in notes))
check("its own signal symbols load; a missing one is left out with a note, an unknown one ignored",
      set(w.icons) == {"playing", "tuning"} and w.icons["tuning"].window == 8 and w.icons["playing"].w == 10
      and len(w.icons["playing"].mask) == 80 and any("no signal" in n for n in notes))
north = next(s for s in w.stations if s["name"] == "North Radio")
stream = next(s for s in w.stations if s["name"] == "A Stream")
check("its stations look like Earth's: place, ids", worlds.is_world_station(north)
      and north["stationuuid"] == "world:testland:north" and north["country"] == "Testland"
      and stream["url_resolved"].startswith("http") and "tracks" not in stream)
check("the World menu lists Earth first, then the world",
      worlds.available(TMP / "worlds") == [("earth", "Earth"), ("testland", "Testland")])

print("Live by the clock")
tr = north["tracks"]
check("the list's length is the tracks' lengths added up", abs(tr.total - sum(tr.seconds)) < 1e-6 and 14 < tr.total < 16)
t0 = worlds.EPOCH                             # where every list starts
check("at the top of the list: track 0, 0 s in", tr.at(t0) == (0, 0.0))
i, off = tr.at(t0 + tr.seconds[0] + 1.5)
check("1.5 s into the second track", i == 1 and abs(off - 1.5) < 1e-6)
check("songs by the clock (and \"\" for the DJ)", tr.title_at(t0 + 1) == "Tone 300 Hz" and tr.title_at(t0 + tr.seconds[0] + 1) == "")
cue = tr.cue(t0 + 2.0, span_s=60)
check("cue: the track on air from part-way, then the rest round and round",
      cue[0][1] == 2.0 and cue[0][0].name == "0000.opus" and [p.name for p, _ in cue[1:4]] == ["0001.opus", "0002.opus", "0000.opus"]
      and all(skip == 0 for _, skip in cue[1:]) and sum(tr.seconds[int(p.stem)] for p, _ in cue) - 2.0 >= 60)
cue = tr.cue(t0 + tr.seconds[0] - 1.0)
check("under 2 s left of a track: start the next one instead", cue[0] == (tr.folder / "0001.opus", 0.0))
check("a cue is never more than CUE_FILES files (a short loop over 12 hours would be thousands)",
      len(tr.cue()) == worlds.CUE_FILES and len(tr.cue(max_files=5)) == 5)
from worlddeskradio.health import StationHealth                                           # noqa: E402
health = StationHealth()
for _ in range(3):
    health.record("http-ok", ["world:testland:north", "http-dead"])
check("a world's stations are never marked as off the air (a stream is)",
      health.skipped() == {"http-dead"})


print("The dial in a world")


class FakePlayer:
    volume, stream_volume, title, on_title, backend = 80, 80, "", None, "ffplay"

    def __init__(self):
        self.plays, self.alive = [], True

    def stop_stream(self): pass
    def start_static(self): pass
    def stop_static(self): pass
    def fade_out_static(self): pass
    def set_volume(self, v): self.volume = v

    def play_stream(self, url, timeout=5, canceled=lambda: False, fade_s=None, cue=None):
        self.plays.append((url, cue))
        self.alive = True
        return not url.startswith("http")

    def stream_alive(self): return self.alive


def settle(dial, seconds=2):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        dial.tick()
        time.sleep(0.01)
        if dial.state.status in ("playing", "no signal"):
            return


clicks = []
radio_map.station_db.report_click = clicks.append      # (the Dial imported its own copy: patched below too)
import worlddeskradio.dial as dial_mod                                                    # noqa: E402
dial_mod.report_click = clicks.append
player = FakePlayer()
dial = Dial(w.stations, player, lat=2.0, lon=0.5, settle_s=0.05)
dial.turn(); settle(dial)
url, cue = player.plays[-1]
check("a world station plays from its files, cued to the clock", dial.state.playing is north and cue
      and cue[0][0] == tr.cue()[0][0])
check("its song comes from the clock", dial.state.song == tr.title_at())
time.sleep(0.3)
check("and Radio Browser isn't told (it isn't theirs)", clicks == [])
player.alive = False                         # the player ran out (after half a day)
plays = len(player.plays)
dial.tick(); time.sleep(0.2); dial.tick()
check("a player that ends picks up again where the clock is", len(player.plays) == plays + 1
      and dial.state.status == "playing" and dial.state.playing is north)

south = next(s for s in w.stations if s["name"] == "South Radio")
plays = len(player.plays)
dial.turn(dlat=-1.0, dlon=0.3)
settle(dial, 0.3)
check("rolling a little, the station on air keeps playing: nothing stops or retunes",
      dial.state.status == "playing" and dial.state.playing is north and len(player.plays) == plays
      and abs(dial.state.lat - 1.0) + abs(dial.state.lon - 0.8) < 1e-9)
dial.set_position(-2.5, -0.8)
check("rolled to where another station is clearly nearer: it tunes that one",
      dial.state.status == "turning")
settle(dial)
check("...and locks on to it", dial.state.status == "playing" and dial.state.playing is south)
from worlddeskradio.stations import haversine_km                                          # noqa: E402
mid = (-0.25, -0.15)                 # a bit nearer North, but not under HOLD times as far
d_n, d_s = haversine_km(*mid, 2.0, 0.5), haversine_km(*mid, -3.0, -1.0)
plays = len(player.plays)
dial.set_position(*mid)
settle(dial, 0.3)
check(f"halfway, with North only a little nearer ({d_n:.0f} vs {d_s:.0f} km), South stays on",
      dial_mod.HOLD * d_s <= d_n < d_s and dial.state.playing is south and len(player.plays) == plays)
dial.set_position(2.0, 0.5, station=north)
settle(dial)
check("picking a station (the Tune knob, a favorite) still goes straight to it", dial.state.playing is north)

controls = radio_map.Controls(dial, radio_map.Favorites(TMP / "favs.json"), themes=["night", "radar"], theme="radar")
fs = face_state(dial.snapshot(), controls, when=time.time())
check("the face gets no distance (-1) and your own time as the station's",
      fs.km == -1 and fs.local == fs.clock and fs.sky is None)
back = state_from_message(decode(state_message(fs)))
check("km -1 survives the cable", back.km == -1)
check("the preview leaves the distance line empty", face_hud(FaceState(lat=0, lon=0, name="X", km=-1, status="turning")).detail == "")
extras = radio_map.Extras(dial, controls, Settings(TMP / "s.json"), radio_map.Companion(), w.stations,
                          weather=object(), world=w)
check("no weather looked up for a world station", extras.weather_at(north, "playing") is None)


print("Zooms, the map's edge, and drifting")
check("a world's zooms are worked out from its map: the whole map, closer, close up",
      len(w.zooms) == 3 and w.zooms[0] > w.zooms[1] > w.zooms[2] and 1400 <= w.zooms[0] <= 1900
      and w.home[2] == w.zooms[0])
edge = Dial(w.stations, FakePlayer(), lat=0, lon=0, settle_s=0.05, bounds=w.bounds)
edge.turn(dlat=50, dlon=50)
check("rolling stops at the edge of the map", (edge.state.lat, edge.state.lon) == (w.bounds[1], w.bounds[3]))
zc = radio_map.Controls(edge, radio_map.Favorites(TMP / "favs2.json"), zooms=w.zooms)
check("a world starts at its whole map", zc.zoom_km == w.zooms[0])
zc.zoom_in()
check("zooming in says it in words, not the pretend km", zc.toast() == "Closer" and zc.zoom_km == w.zooms[1])
zc.zoom_in(), zc.zoom_in()
check("and goes round: close up, then the whole map again", zc.zoom_km == w.zooms[0] and zc.toast() == "Whole map")
from worlddeskradio.map_view import drift_offset, width_for_zoom                          # noqa: E402
width = width_for_zoom(w.home[2])
path = [drift_offset(t, width) for t in range(0, 600, 5)]
check("idle, the map drifts in a slow loop, never far (under a tenth of the view)",
      drift_offset(0, width) == (0.0, 0.0) and max(max(abs(a), abs(b)) for a, b in path) <= 0.081 * width
      and max(abs(a) for a, _ in path) > 0.05 * width)
fs_idle = FaceState(lat=2.0, lon=0.5, zoom=w.zooms[2], name="North Radio", slat=2.0, slon=0.5, km=-1,
                    status="playing", idle=True, spin=True, clock=600)
scr = FaceScreen(w.stations)
scr.view.set_world(w.coast, w.borders)
scr.home = w.home
for k in range(60):
    scr.frame(fs_idle, 1000.0 + k * 0.5)
cam = (scr.camera.lat, scr.camera.lon, scr.camera.radius)
check("idle in a world: out to the whole map, around its middle (not spinning the globe)",
      abs(cam[0] - w.home[0]) < 0.1 * width and abs(cam[1] - w.home[1]) < 0.1 * width
      and abs(cam[2] - radius_for_view(width)) < 1)
scr.frame(fs_idle, 1100.0)
moved = (scr.camera.lat, scr.camera.lon) != cam[:2]
check("...and drifting", moved)
far = FaceState(lat=-5.0, lon=-3.0, zoom=w.zooms[2], name="South Radio", slat=-5.0, slon=-3.0, km=-1, status="playing")
near = FaceState(lat=5.0, lon=3.0, zoom=w.zooms[2], name="North Radio", slat=5.0, slon=3.0, km=-1, status="playing")
scr2 = FaceScreen(w.stations)
scr2.home = w.home
scr2.frame(near, 0.0)
scr2.frame(far, 0.1)
check("a jump across the map is flown, backing out no further than the whole map",
      scr2._flight is not None and abs(math.exp(scr2._flight["world"]) - radius_for_view(width)) < 1e-6)

print("Switching worlds")
settings = Settings(TMP / "settings.json")
settings.update(lat=51.5, lon=-0.1, zoom=2000, theme="night")
earth, there = radio_map.WorldSpot(settings), radio_map.WorldSpot(settings, w)
there.update(lat=2.0, lon=0.5, zoom=500, theme="radar")
check("each world keeps its own spot, zoom and theme; Earth's stay where they were",
      earth.get("lat") == 51.5 and earth.get("theme") == "night" and there.get("zoom") == 500
      and settings.get("worlds") == {"testland": {"lat": 2.0, "lon": 0.5, "zoom": 500, "theme": "radar"}})
chosen = []
menu = WorldMenu(worlds.available(TMP / "worlds"), "earth", chosen.append)
menu.open()
check("the World card opens on the world you're in", menu.sel == 0 and menu.view().items[0] == ("Earth", "here"))
menu.move(+1); menu.press()
check("Tune picks the next one, press goes there", chosen == ["testland"])
went = []
extras.worlds, extras.go_to_world = worlds.available(TMP / "worlds"), went.append
extras._request("world", {"id": "nowhere"}, 0)
extras._request("world", {"id": "earth"}, 0)
check("the phone page's World card goes to a world that's there, and only one",
      went == ["earth"])
published = []
extras.companion.publish = published.append
extras.publish(dial.snapshot(), fs, 1e9)
check("the phone page is told the worlds and which one it's in",
      published and published[-1]["world"] == "testland"
      and published[-1]["worlds"] == [{"id": "earth", "title": "Earth"}, {"id": "testland", "title": "Testland"}])
worlds.WORLDS_DIR = TMP / "worlds"
settings.update(world="testland")
check("settings.json's world is loaded at start", radio_map._load_world(settings).title == "Testland")
settings.update(world="gone")
check("a world that's gone sends the radio back to Earth", radio_map._load_world(settings) is None
      and settings.get("world") == "earth")
check("starting again in another world drops --start and --theme",
      radio._without(["radio.py", "--map", "--start", "1,2", "--theme=ice", "--face", "COM5"],
                          ("--start", "--theme")) == ["radio.py", "--map", "--face", "COM5"])


print("The world on the face")
radio_end, face_end = loopback()
face = VirtualFace(face_end)
link = FaceLink(radio_end, w.stations, world=w)
link.update(fs, 0.0)
face.poll()
link.update(fs, 0.1)
face.poll()
lines = face.screen.view._world
check("...and its whole-map view, for idle and long jumps", face.screen.home == w.home)
got_places = [c for c in face.screen.view._world["places"].names]
check("...and its places, to label as Earth's cities (the ones shown first, first)",
      got_places == ["Capital", "Village"] and [p[0] for p in w.places] == ["Capital", "Village"])
check("the virtual face gets the world's lines from the messages",
      lines is not None and lines["coast"] is not None and lines["border"] is not None)
got = face._world_lines["coast"][0]
sent = w.coast[0]
check("...as sent, to 1/180 of a degree", len(got[0]) == len(sent[0])
      and max(abs(a - b) for a, b in zip(got[0], sent[0])) <= 0.5 / 180 + 1e-9)
check("...and its own signal symbols, put back together from the icon messages",
      face._icons == w.icons and face.screen.view._icons.keys() == w.icons.keys())
radio_end2, face_end2 = loopback()
face.port = face_end2
earth_link = FaceLink(radio_end2, w.stations)       # a radio on Earth: the face drops the world
earth_link.update(fs, 0.0)
face.poll()
check("a radio on Earth sends the face back to Earth's lines", face.screen.view._world is None)

print("The world on the map")
view = MapView(w.stations)
view.set_world(w.coast, w.borders)
img = view.render(0.0, 0.0, radius_for_view(math.degrees(2000 / 6371)))
img.save(OUT / "testland.png")
# The coast is the theme's coast color: count those pixels along a row through the island.
row = [img.getpixel((x, 240)) for x in range(480)]
coast = view.theme.coast
check("the island's coast is drawn, at the 2,000 km zoom", sum(1 for p in row if p == coast) >= 2)
from worlddeskradio.map_view import SIGNAL_Y, Hud                                         # noqa: E402
view.set_world(w.coast, w.borders, icons=w.icons)
cy = round(SIGNAL_Y * view.k)
r = radius_for_view(math.degrees(2000 / 6371))
on_air = view.render(0.0, 0.0, r, Hud(signal="playing"))
check("on air, the world's own symbol fills the signal slot in the live color",
      on_air.getpixel((240, cy)) == view.theme.live)
strip = [view.render(0.0, 0.0, r, Hud(signal="tuning", signal_scroll=s)) for s in (0.0, 0.5)]
check("tuning, a window slides along its strip as it scrolls",
      [strip[0].getpixel((x, cy)) for x in range(236, 244)] != [strip[1].getpixel((x, cy)) for x in range(236, 244)])
usual = view.render(0.0, 0.0, r, Hud(signal="no signal"))
check("no symbol of its own for no signal: the usual one (in the warning color)",
      any(usual.getpixel((x, y)) == view.theme.warn for x in range(210, 270) for y in range(cy - 20, cy + 20)))
view.set_world(earth=True)
check("set_world(earth=True) is Earth again", view._world is None and view._icons == {})


print("The whole radio in a world (headless, muted)")
backend = find_backend()
if not backend:
    print("  skip  no ffplay or mpv")
else:
    import argparse
    radio_map.SETTINGS_PATH = TMP / "settings-run.json"
    radio_map.FAVORITES_PATH = TMP / "favorites-run.json"
    radio_map.LIKED_PATH, radio_map.HEALTH_PATH = TMP / "liked-run.json", TMP / "health-run.json"
    radio_map.SETTINGS_PATH.write_text(json.dumps({"world": "testland", "volume": 0, "auto_update": False}))
    radio_map.Updater = type("NoUpdates", (), {"__init__": lambda self, *a, **k: None, "start": lambda self: None,
                                                "tick": lambda self, quiet: None, "restart_due": False,
                                                "status": lambda self: None, "auto": False})
    seen = []
    real_face_state = radio_map.face_state
    radio_map.face_state = lambda st, *a, **k: seen.append(real_face_state(st, *a, **k)) or seen[-1]
    # Hold both knob buttons for the settings menu, turn Tune to World, open it, turn to Earth, press.
    script = [(4.0, "s"), (4.3, "]"), (4.4, "]"), (4.5, "]"), (4.7, "f"), (5.0, "["), (5.2, "f")]
    t0 = time.monotonic()

    def events(self):
        due = [key for at, key in script if at <= time.monotonic() - t0]
        del script[:len(due)]
        return due if time.monotonic() - t0 < 20 else ["quit"]
    radio_map.Headless.events = events
    args = argparse.Namespace(settle=0.3, timeout=5.0, headless=True, face=None, theme=None, lan=False,
                              no_knobs=True, ball_turn=None, ball_mirror=None, debug_knobs=False)
    code = radio_map.run(args, [], backend, ROOT / "static.wav")
    playing = [fs for fs in seen if fs.status == "playing"]
    check("it starts in the world it was in, and a station there plays",
          playing and playing[0].name in ("North Radio", "South Radio", "A Stream") and playing[0].km == -1)
    panels = [fs.panel.title for fs in seen if fs.panel is not None]
    check("Settings has a World entry, which opens the World card", "Settings" in panels and "World" in panels
          and any(fs.panel.items[-1][0] == "World" for fs in seen if fs.panel and fs.panel.title == "Settings"))
    after = json.loads(radio_map.SETTINGS_PATH.read_text())
    check("picking Earth starts the radio again (NEW_WORLD), set to Earth, with this world's spot kept",
          code == radio_map.NEW_WORLD and after["world"] == "earth" and "testland" in after.get("worlds", {}))

print("Playing the tones (muted)")
backend = find_backend()
if not backend:
    print("  skip  no ffplay or mpv")
else:
    real = Player(*backend, ROOT / "static.wav", volume=0)
    started = real.play_stream("world:testland:north", 6, cue=tr.cue())
    check(f"{backend[0]} plays the cue", started and real.stream_alive())
    real.close()
    check("and stops", not real.stream_alive())

print(f"\nAll {ok_count} checks passed.")
