"""The whole radio, end to end: radio_map.run --compare in a hidden window, driven by a script of
mouse moves, keys and phone-page requests (about 2.5 minutes, sound muted, pretend weather and a
pretend update, temporary copies of every file the radio writes).

    python tests/check_face.py              # screenshots of each checkpoint go to a temp folder
    python tests/check_face.py OUT_FOLDER   # ...or to OUT_FOLDER

At each checkpoint, once both screens have settled, the virtual face (right pane, drawn only from
the messages a real face would get) must match, pixel for pixel, the same drawing made with the
station positions as the face receives them. Also checked: every state message survives encode and
decode unchanged, standby and snooze go dark, the idle spin agrees on both screens, the phone's
requests all work, message rates, and that "Install now" restarts the radio.

Needs pygame, the station list (python radio.py --update) and ffplay or mpv (it plays muted).
"""
import dataclasses
import json
import sys
import tempfile
import threading
import time
import types
import urllib.request
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp(prefix="terradio-face-"))
OUT.mkdir(parents=True, exist_ok=True)

import pygame                                                                        # noqa: E402
from PIL import Image                                                                # noqa: E402

import radio                                                                    # noqa: E402
import radio_map                                                                     # noqa: E402
from terradio import controls as controls_mod                                       # noqa: E402
from terradio import face_link                                                      # noqa: E402
from terradio import map_view as map_view_mod                                       # noqa: E402
from terradio import player as player_mod                                           # noqa: E402
from terradio import screen as screen_mod                                           # noqa: E402
from terradio import static                                                         # noqa: E402
from terradio import stations as db                                                 # noqa: E402
from terradio import updater as updater_mod                                         # noqa: E402
from terradio import virtual_face as virtual_face_mod                               # noqa: E402
from terradio.map_view import MapView, face_view, get_theme, signal_phase           # noqa: E402
from terradio.screen import PygameWindow                                            # noqa: E402
from terradio.weather import SKIES, Report, Weather                                 # noqa: E402

if not db.CLEAN_PATH.exists():
    print("The station list isn't downloaded yet: run  python radio.py --update  first.")
    sys.exit(2)
BACKEND = player_mod.find_backend()
if not BACKEND:
    print("No audio player (ffplay or mpv) found: the radio can't run without one.")
    sys.exit(2)
if static.needs_update(radio.STATIC_WAV):
    static.generate(radio.STATIC_WAV)

# --- silence -----------------------------------------------------------------
o1, o2 = player_mod.Player._stream_cmd, player_mod.Player._static_cmd


def mute(cmd):
    cmd = list(cmd)
    if "-volume" in cmd:
        cmd[cmd.index("-volume") + 1] = "0"
    return [c if not c.startswith("--volume=") else "--volume=0" for c in cmd]


player_mod.Player._stream_cmd = lambda self, url, fade_s=None, cue=None: mute(o1(self, url, fade_s, cue))
player_mod.Player._static_cmd = lambda self: mute(o2(self))

# --- taps --------------------------------------------------------------------
REF = {"fs": None}
last_show = [0.0]           # when either screen last drew something new
T0 = None
roundtrip = {"checked": 0, "mismatch": 0}
real_face_state = radio_map.face_state


def tapped_face_state(st, controls, **kw):
    fs = real_face_state(st, controls, **kw)
    back = face_link.state_from_message(face_link.decode(face_link.state_message(fs)))
    roundtrip["checked"] += 1
    if back != fs:
        roundtrip["mismatch"] += 1
        if roundtrip["mismatch"] < 3:
            print("ROUND TRIP MISMATCH", fs, back)
    REF["fs"] = fs
    REF["controls"] = controls
    return fs


radio_map.face_state = tapped_face_state

cable = []                     # (seconds since start, type, bytes) for every line the radio sent


class RecordingEnd:
    def __init__(self, end):
        self.end = end
        self._partial = b""

    def write(self, data):
        self._partial += data
        while b"\n" in self._partial:
            line, self._partial = self._partial.split(b"\n", 1)
            cable.append((time.monotonic() - T0, json.loads(line)["t"], len(line) + 1, line))
        return self.end.write(data)

    @property
    def in_waiting(self):
        return self.end.in_waiting

    def read(self, n=1):
        return self.end.read(n)


real_loopback = radio_map.loopback


def tapped_loopback():
    radio_end, face_end = real_loopback()
    return RecordingEnd(radio_end), face_end


radio_map.loopback = tapped_loopback


class TappedVirtualFace(virtual_face_mod.VirtualFace):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        REF["vface"] = self

    def frame(self, now=None):
        out = super().frame(now)
        if out is not None:
            last_show[0] = time.monotonic() - T0
        return out


class TappedFaceScreen(map_view_mod.FaceScreen):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        REF["preview"] = self

    def frame(self, fs, now):
        out = super().frame(fs, now)
        if out is not None:
            last_show[0] = time.monotonic() - T0
        return out


map_view_mod.FaceScreen = TappedFaceScreen


class TappedSetupServer(radio_map.SetupServer):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        REF["page"] = self


radio_map.SetupServer = TappedSetupServer
page_log = []


def phone(path, body=None):
    """What the phone's browser does on the setup page (in a thread, like a real phone)."""
    def go():
        url = REF["page"].url.rstrip("/") + path
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                reply = r.read()
                page_log.append((path, r.status, reply[:120]))
                if path == "/api/radio":
                    REF["radio_json"] = json.loads(reply)
        except Exception as e:
            page_log.append((path, "error", str(e)[:120]))
    threading.Thread(target=go, daemon=True).start()


# --- no network, no git: pretend weather and a pretend update waiting on GitHub ---------


def pretend_weather(lat, lon):
    time.sleep(0.05)
    return Report(list(SKIES)[int(abs(lat * 7 + lon * 3)) % len(SKIES)], (lat % 30) - 5)


def make_weather():
    REF["weather"] = Weather(fetch=pretend_weather)
    return REF["weather"]


class PretendUpdater:
    """An update waiting on GitHub; installing it asks the radio to restart."""
    busy = False

    def __init__(self, root, auto=False):
        self.auto, self.restart_due, self.state, self.quiet = auto, False, "ready", []
        REF["updater"] = self

    def start(self): pass
    def check(self): self.state = "ready"

    def install(self):
        self.state, self.restart_due = "installed", True

    def tick(self, quiet):
        self.quiet.append(quiet)

    def status(self):
        return {"state": self.state, "words": updater_mod.STATES[self.state], "detail": "2 changes",
                "version": "7938d5b, Oct 21", "latest": "a1b2c3d, Oct 22",
                "new": ["Weather at the station", "Phone remote"], "auto": self.auto}


class TappedExtras(radio_map.Extras):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        REF["extras"] = self
        self.snooze_s = 6                   # a 6-second snooze, for the test


radio_map.Extras = TappedExtras
radio_map.Weather = make_weather
radio_map.Updater = PretendUpdater
virtual_face_mod.VirtualFace = TappedVirtualFace
radio_map.FAVORITES_PATH = OUT / "favorites-face-test.json"
radio_map.SETTINGS_PATH = OUT / "settings-face-test.json"
radio_map.LIKED_PATH = OUT / "liked-face-test.json"
radio_map.HEALTH_PATH = OUT / "health-face-test.json"
for path in (radio_map.FAVORITES_PATH, radio_map.SETTINGS_PATH, radio_map.LIKED_PATH, radio_map.HEALTH_PATH):
    if path.exists():
        path.unlink()
radio_map.SETTINGS_PATH.write_text('{"spin": false}')   # the idle globe stays still until it's tested
controls_mod.IDLE_S = 1000          # no idle screen until the end of the script
# Long lines scroll fast here, so checkpoints don't wait 15 s for a song title to finish.
map_view_mod.SCROLL_HOLD_S, map_view_mod.SCROLL_PX_S, map_view_mod.SCROLL_FADE_S = 0.2, 2000, 0.05

# --- reference renderers -----------------------------------------------------
STATIONS = db.load()
rx = face_link.StationsReceiver()
got = None
for m in face_link.stations_messages(STATIONS):
    got = rx.add(face_link.decode(m)) or got
QUANTIZED = [{"geo_lat": a, "geo_long": b} for a, b in zip(got[0].tolist(), got[1].tolist())]
VIEW_A = MapView(STATIONS, 480)
VIEW_C = MapView(QUANTIZED, 480)
yy, xx = np.mgrid[0:480, 0:480] + 0.5
SCREEN = (np.hypot(xx - 240, yy - 240) < 239)[..., None]

# --- script ------------------------------------------------------------------
P = pygame
script = []


def at(t, *evs):
    script.append([t, list(evs)])


def ev(kind, **kw):
    return ("post", kind, kw)


def key(k, ch, mod=0):
    return [ev(P.KEYDOWN, key=k, unicode=ch, mod=mod, scancode=0), ev(P.KEYUP, key=k, unicode=ch, mod=mod, scancode=0)]


CX, CY = 240, 240               # middle of the left pane
at(5.0, ("cp", "start: London, 2,000 km"))
# A long steady drag (2 s): the map moves every frame, so state messages should hit ~30/s.
at(6.0, ev(P.MOUSEBUTTONDOWN, button=1, pos=(CX, CY)))
for i in range(200):
    at(6.01 + i * 0.01, ev(P.MOUSEMOTION, pos=(CX - 2 * (i + 1), CY), rel=(-2, 0), buttons=(1, 0, 0)))
at(8.05, ev(P.MOUSEBUTTONUP, button=1, pos=(CX - 400, CY)))
at(9.2, ("cp", "after a drag east"))
at(9.5, ev(P.MOUSEBUTTONDOWN, button=1, pos=(CX, CY)), ev(P.MOUSEBUTTONUP, button=1, pos=(CX, CY)))
at(10.4, ("cp", "zoom 500 km, toast"))
for i in range(3):
    at(11.0 + i * 0.15, ev(P.MOUSEWHEEL, x=0, y=1, flipped=False))
at(12.0, ("cp", "Tune: three steps out"))
at(17.0, ("cp", "tuned, settled"))
at(17.2, ev(P.MOUSEBUTTONDOWN, button=3, pos=(CX, CY)), ev(P.MOUSEBUTTONUP, button=3, pos=(CX, CY)))
at(18.0, ("cp", "radar theme"))
at(18.5, *key(P.K_EQUALS, "+"))
at(19.0, ("cp", "volume ring"))
at(19.5, *key(P.K_m, "m"))
at(20.0, ("cp", "muted"))
at(20.6, *key(P.K_m, "m"))
at(21.5, *key(P.K_f, "F", P.KMOD_SHIFT))
at(22.2, ("cp", "saved favorite: star"))
at(24.0, ev(P.MOUSEBUTTONDOWN, button=1, pos=(CX, CY)), ev(P.MOUSEBUTTONUP, button=1, pos=(CX, CY)))
at(25.0, ("cp", "whole world"))
at(25.4, *key(P.K_t, "t"))
at(25.8, *key(P.K_t, "t"))
at(26.6, ("cp", "paper theme, world"))
at(26.8, *key(P.K_t, "t"))
at(27.0, ev(P.MOUSEBUTTONDOWN, button=1, pos=(CX, CY)), ev(P.MOUSEBUTTONUP, button=1, pos=(CX, CY)))
at(28.0, ("cp", "mono theme, 2,000 km"))
# Bluetooth: both knob buttons (B), pick the 2nd device with the Tune knob, press to connect.
at(28.5, *key(P.K_b, "b"))
at(32.5, ("cp", "Bluetooth: devices found"))
at(33.0, ev(P.MOUSEWHEEL, x=0, y=1, flipped=False))
at(33.4, *key(P.K_f, "f"))
at(34.0, ("cp", "Bluetooth: connecting"))
at(36.0, ("cp", "Bluetooth: connected"))
at(36.5, *key(P.K_m, "m"))                       # Volume button: close
at(37.5, ("cp", "map with the Bluetooth symbol"))
# Wi-Fi setup (N), then the phone: the page, a wrong password, then the right one.
at(38.0, *key(P.K_n, "n"))
at(38.5, ("call", lambda: phone("/")), ("call", lambda: phone("/api/networks")),
   ("call", lambda: phone("/generate_204")))
at(39.0, ("cp", "Wi-Fi setup: QR code"))
at(39.5, ("call", lambda: phone("/api/connect", {"ssid": "Home Wi-Fi", "password": "wrong-one"})))
at(40.2, ("cp", "Wi-Fi: connecting"))
at(42.6, ("cp", "Wi-Fi: wrong password"), ("call", lambda: phone("/api/status")))
at(47.0, ("call", lambda: phone("/api/connect", {"ssid": "Home Wi-Fi", "password": "terradio"})))
at(50.0, ("cp", "Wi-Fi: connected"), ("call", lambda: phone("/api/status")))
at(53.5, ("cp", "back to the map"))
# Like the song (both knob buttons tapped: H), then surprise me (R) and its flight.
at(54.0, *key(P.K_h, "h"))
at(54.6, ("cp", "like the song"))
at(56.0, *key(P.K_r, "r"))
at(60.0, ("cp", "surprise: flown somewhere new"))
# Standby (Volume long press: Z), then any key wakes it.
at(66.0, *key(P.K_z, "z"))
at(66.7, ("dark", "standby: halfway through the fade"))
at(68.0, ("dark", "standby: screen dark"))
at(69.0, *key(P.K_m, "m"))
at(73.0, ("cp", "awake again"))
# The phone page: an alarm (waking to the station playing now) and a 30-minute sleep timer.
at(73.0, ("call", lambda: phone("/api/alarm", {"on": True, "time": "07:00", "use_current": True})),
   ("call", lambda: phone("/api/timer", {"minutes": 30})))
at(74.0, ("call", lambda: phone("/api/radio")))
at(74.5, ("mark", "quiet start"))      # no input from here: only toasts expiring and heartbeats
at(74.6, ("call", lambda: setattr(controls_mod, "IDLE_S", 2)))   # idle 2 s after the last input
at(80.5, ("call", lambda: phone("/api/radio")))
at(81.0, ("mark", "quiet end"), ("cp", "idle: clock with the alarm and sleep timer"))
# The phone as a remote: next station, the volume slider, the remote's QR code, a favorite,
# °C, and finally Install now, which restarts the radio on the new version.
at(81.2, ("call", lambda: setattr(controls_mod, "IDLE_S", 1000)))
at(81.5, ("call", lambda: phone("/api/remote", {"do": "next"})))
at(82.0, ("call", lambda: phone("/api/remote", {"do": "volume", "value": 55})))
at(86.0, ("cp", "phone: next station, volume 55, weather"))
at(86.5, *key(P.K_p, "p"))
at(87.5, ("cp", "phone remote: QR code"))
at(88.0, *key(P.K_m, "m"))
at(88.5, ("call", lambda: phone("/api/remote", {"do": "favorite", "value": 0})))
at(93.0, ("cp", "phone: back to favorite 1"))
at(93.2, ("call", lambda: phone("/api/settings", {"units": "C"})))
at(94.0, ("cp", "phone: degrees C"), ("call", lambda: phone("/api/radio")))
# The idle spin (paused while the sleep timer runs) and borders off, from the phone.
spin_log = []


def spin_probe(label):
    fs, pre, vf = REF["fs"], REF["preview"].camera, REF["vface"].screen.camera
    spin_log.append((label, round(time.monotonic() - T0, 2), fs.idle, fs.spin, fs.borders,
                     pre.lat, pre.lon, vf.lat, vf.lon))


at(97.5, ("call", lambda: phone("/api/settings", {"spin": True, "borders": False})))
at(98.0, ("call", lambda: setattr(controls_mod, "IDLE_S", 2)))
at(99.5, ("call", lambda: spin_probe("idle, sleep timer on")))
at(99.6, ("call", lambda: phone("/api/timer", {"minutes": 0})))
at(101.5, ("call", lambda: spin_probe("spinning, 2 s in")))
at(105.5, ("call", lambda: spin_probe("spinning, 6 s in")))
at(106.0, *key(P.K_EQUALS, "+"))
at(106.1, ("call", lambda: setattr(controls_mod, "IDLE_S", 1000)))
at(110.0, ("cp", "back from the spin, borders off"))
# The alarm going off: its card, Volume to snooze (dark), back again, Tune to turn it off.
at(111.0, ("call", lambda: REF["extras"]._ring(REF["extras"].alarms[0])))
at(113.5, ("cp", "alarm: the card"))
at(114.0, *key(P.K_m, "m"))
at(118.6, ("dark", "alarm snoozed: dark"))
at(129.5, ("cp", "snooze over: the card again"))
at(130.0, *key(P.K_f, "f"))
at(130.2, ("call", lambda: phone("/api/settings", {"brightness": 40})))
at(132.0, ("cp", "alarm off: good morning, at 40% brightness"))
at(133.0, ("call", lambda: phone("/api/update", {"do": "install"})))
at(150.0, *key(P.K_q, "q"))                       # only if the restart never came
for s in script:
    flat = []
    for e in s[1]:
        flat += e if isinstance(e, list) else [e]
    s[1] = flat
script.sort(key=lambda s: s[0])

results, marks, unsettled = [], {}, []


def checkpoint(label, now):
    """Returns False to try again next frame."""
    vf = REF["vface"]
    fs = REF["fs"]
    # Wait for both screens to settle (camera, fades, scrolling) on the radio's latest state.
    if REF["preview"].busy or vf.screen.busy or vf.state != fs or REF["weather"].busy:
        return False
    surf = pygame.display.get_surface()
    img = Image.frombytes("RGB", surf.get_size(), pygame.image.tobytes(surf, "RGB"))
    left, right = img.crop((0, 0, 480, 480)), img.crop((480, 0, 960, 480))
    radius, hud = face_view(fs, 480)
    # The crosshair fades by itself while a station plays: use the level both screens are at.
    assert REF["preview"].crosshair_level == vf.screen.crosshair_level
    hud = dataclasses.replace(hud, crosshair=vf.screen.crosshair_level)
    if hud.signal == "tuning":       # the waves move with the clock: use the frame's own moment
        assert REF["preview"]._time == vf.screen._time
        hud = dataclasses.replace(hud, signal_phase=signal_phase(vf.screen._time))
    for v in (VIEW_A, VIEW_C):
        if v.theme.name != fs.theme:
            v.set_theme(get_theme(fs.theme))
    favs = [(f["lat"], f["lon"]) for f in json.loads(radio_map.FAVORITES_PATH.read_text())]         if radio_map.FAVORITES_PATH.exists() else []
    a_ref = VIEW_A.render(fs.lat, fs.lon, radius, hud, favorites=favs)
    c_ref = VIEW_C.render(fs.lat, fs.lon, radius, hud, favorites=vf.screen.favorites)
    if fs.bright < 100:
        black = Image.new("RGB", a_ref.size)
        a_ref, c_ref = (Image.blend(black, x, fs.bright / 100) for x in (a_ref, c_ref))
    # Only the round screen counts: the window's corners show the trackball's light.
    L, R, A, C = (np.asarray(x).astype(int) * SCREEN for x in (left, right, a_ref, c_ref))
    diff = np.abs(L - R).max(axis=2)
    res = dict(t=round(now, 2), label=label, left_is_preview=bool(np.array_equal(L, A)),
               right_is_exact=bool(np.array_equal(R, C)), same_pct=100 * float((diff == 0).mean()),
               over8_pct=100 * float((diff > 8).mean()), max_diff=int(diff.max()),
               status=fs.status, name=(fs.name or "")[:28], toast=fs.toast, theme=fs.theme, zoom=fs.zoom,
               ring=fs.ring, mute=fs.mute, fav=fs.fav, idle=fs.idle, song=fs.song[:30], local=fs.local,
               reminder=hud.reminder, liked=fs.liked, bt=fs.bt, panel=(fs.panel.title + ": " + " / ".join(fs.panel.lines + tuple(
                   f"[{l}]" if i == fs.panel.sel else l for i, (l, _) in enumerate(fs.panel.items)))[:60]) if fs.panel else "")
    results.append(res)
    sheet = Image.new("RGB", (1440, 480))
    sheet.paste(left, (0, 0))
    sheet.paste(right, (480, 0))
    sheet.paste(Image.fromarray(np.clip(diff * 8, 0, 255).astype(np.uint8)).convert("RGB"), (960, 0))
    sheet.save(OUT / f"cp{len(results):02d}.png")
    return True


class Scripted(PygameWindow):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        pygame.display.set_mode(self._window.get_size(), pygame.HIDDEN)


    def events(self):
        now = time.monotonic() - T0
        while script and script[0][0] <= now:
            t, evs = script[0]
            pending = []
            for e in evs:
                if e[0] == "post":
                    pygame.event.post(pygame.event.Event(e[1], **e[2]))
                elif e[0] == "mark":
                    marks[e[1]] = now
                elif e[0] == "call":
                    e[1]()
                elif e[0] == "dark":
                    surf = pygame.display.get_surface()
                    img = np.asarray(Image.frombytes("RGB", surf.get_size(), pygame.image.tobytes(surf, "RGB")))
                    inside = np.concatenate([img[:, :480][SCREEN[..., 0]], img[:, 480:][SCREEN[..., 0]]])
                    results.append(dict(t=round(now, 2), label=e[1], dark=int(inside.max()),
                                        asleep=REF["vface"].asleep))
                elif e[0] == "cp":
                    if not checkpoint(e[1], now):
                        if now - t < 8:
                            pending.append(e)
                        else:
                            print("checkpoint never settled:", e[1])
                            unsettled.append(e[1])
            if pending:
                script[0][1] = pending
                break
            script.pop(0)
        return super().events()


screen_mod.PygameWindow = Scripted
args = types.SimpleNamespace(settle=0.4, timeout=5.0, scale=1, size=480, theme="night",
                             face=None, virtual_face=False, compare=True)
T0 = time.monotonic()
rc = radio_map.run(args, STATIONS, BACKEND, radio.STATIC_WAV, 51.51, -0.13)
print("rc", rc, "(75: restart on the new version)" if rc == radio_map.RESTART else "")
print("\nIdle spin (label, t, idle, spin, borders, preview lat/lon, virtual face lat/lon):")
for row in spin_log:
    print("  ", row[0], row[1], row[2:5], f"preview {row[5]:.3f},{row[6]:.3f}", f"face {row[7]:.3f},{row[8]:.3f}")
print("updater told quiet:", sum(REF["updater"].quiet), "of", len(REF["updater"].quiet), "frames")
print("the phone's last /api/radio:", json.dumps({k: REF["radio_json"][k] for k in ("now", "volume", "favorites", "favorite", "units", "update")}, ensure_ascii=False)[:600])

print("\nCheckpoints (left = normal preview, right = virtual face):")
for r in results:
    if "dark" in r:
        print(f"  {r['t']:6.2f}s {r['label']:<28} brightest pixel inside the screens: {r['dark']} "
              f"(0 = black); virtual face asleep: {r['asleep']}")
        continue
    print(f"  {r['t']:6.2f}s {r['label']:<28} right==face-precision preview: {r['right_is_exact']}  "
          f"left==settled preview: {r['left_is_preview']}  identical px {r['same_pct']:6.2f}%  "
          f">8 levels {r['over8_pct']:5.2f}%  max {r['max_diff']:3d}   "
          f"[{r['status']}, {r['theme']}, {r['zoom']} km, toast={r['toast']!r}, ring={r['ring']}, "
          f"mute={r['mute']}, fav={r['fav']}, idle={r['idle']}, local={r['local']}, bt={r['bt']}, "
          f"panel={r['panel']!r}, reminder={r['reminder']!r}, liked={r['liked']}, "
          f"{r['name']!r}, song={r['song']!r}]")
print(f"\nencode->decode round trips: {roundtrip['checked']:,} FaceStates, {roundtrip['mismatch']} changed")
print("virtual face bad lines:", REF["vface"].bad_lines)
print("\nThe phone's requests to the setup page:")
for path, status, body in page_log:
    print(f"  {path:16} {status}  {body!r}"[:150])

# --- sizes and rates ---------------------------------------------------------
by = {}
for t, kind, n, _ in cable:
    by.setdefault(kind, []).append(n)
print("\nMessages sent over the pretend cable:")
for kind, sizes in by.items():
    s = sorted(sizes)
    print(f"  {kind:<9} {len(s):5d} lines, {sum(s):8,} bytes, size min {s[0]} / median {s[len(s) // 2]} / max {s[-1]}")
states = [(t, n) for t, kind, n, _ in cable if kind == "state"]
drag = [(t, n) for t, n in states if 6.1 <= t <= 8.0]
dur = 8.0 - 6.1
print(f"  while dragging ({dur:.1f} s): {len(drag) / dur:.1f} state messages/s, "
      f"{sum(n for _, n in drag) / dur:,.0f} bytes/s")
q0, q1 = marks.get("quiet start"), marks.get("quiet end")
if q0 and q1:
    quiet = [(t, n) for t, n in states if q0 <= t <= q1]
    print(f"  idle ({q1 - q0:.1f} s, no input): {len(quiet)} state messages "
          f"({len(quiet) / (q1 - q0):.2f}/s), {sum(n for _, n in quiet) / (q1 - q0):,.0f} bytes/s")
gaps = np.diff([t for t, _ in drag])
print(f"  gaps while dragging: min {gaps.min() * 1000:.1f} ms, median {np.median(gaps) * 1000:.1f} ms, "
      f"max {gaps.max() * 1000:.1f} ms")
total_s = cable[-1][0] - cable[0][0]
print(f"  whole run: {len(states)} states in {total_s:.0f} s, {sum(n for _, n in states) / total_s:,.0f} bytes/s average")
examples = {}
for t, kind, n, line in cable:
    if kind == "state":
        examples[n] = line
print("\nlongest state line:", max(examples.items())[1].decode("utf-8"))
print("shortest state line:", min(examples.items())[1].decode("utf-8"))
json.dump(results, open(OUT / "results.json", "w"), indent=1)

# --- verdicts ----------------------------------------------------------------------
verdicts = []


def verdict(label, ok):
    verdicts.append(ok)
    print(("  ok   " if ok else "  FAIL ") + label)


print("\nVerdicts:")
cps = [r for r in results if "dark" not in r]
dark = {r["label"]: r for r in results if "dark" in r}
inexact = [r["label"] for r in cps if not r["right_is_exact"]]
verdict(f"{len(cps)} checkpoints settled, and at each the virtual face matched pixel for pixel"
        + (f" (not: {inexact})" if inexact else "") + (f" (never settled: {unsettled})" if unsettled else ""),
        not inexact and not unsettled and len(cps) >= 33)
verdict(f"every state message survived encode and decode ({roundtrip['checked']:,})",
        roundtrip["mismatch"] == 0 and roundtrip["checked"] > 1000)
verdict("the virtual face read every line", REF["vface"].bad_lines == 0)
half, gone, snoozed = (dark.get(k, {}) for k in ("standby: halfway through the fade", "standby: screen dark",
                                                 "alarm snoozed: dark"))
verdict(f"standby fades out and goes dark (brightest pixel {half.get('dark')} halfway, then {gone.get('dark')})",
        half.get("dark", 0) > 0 and gone.get("dark") == 0 and gone.get("asleep") is True)
verdict(f"a snoozed alarm goes dark (brightest pixel {snoozed.get('dark')})",
        snoozed.get("dark") == 0 and snoozed.get("asleep") is True)
probes = {row[0]: row for row in spin_log}
paused, early, later = (probes.get(k) for k in ("idle, sleep timer on", "spinning, 2 s in", "spinning, 6 s in"))
agree = all(p and abs(p[6] - p[8]) < 1e-6 and abs(p[5] - p[7]) < 1e-6 for p in (paused, early, later))
turned = (early[6] - later[6]) % 360 if early and later else 0
verdict(f"idle spin: paused by the sleep timer, then {turned:.1f}° west in 4 s, the same on both screens",
        bool(paused and early and later) and paused[2] and not paused[3] and early[3] and later[3]
        and agree and 3 < turned < 12)
errors = [(p, s) for p, s, _ in page_log if s not in (200,)]
verdict(f"the phone's {len(page_log)} requests all worked" + (f" (not: {errors})" if errors else ""),
        not errors and len(page_log) >= 15)
verdict(f"while dragging, {len(drag) / dur:.0f} state messages a second (about 30 wanted)", len(drag) / dur >= 20)
verdict("\"Install now\" restarted the radio on the new version", rc == radio_map.RESTART)
print(f"\nScreenshots of every checkpoint: {OUT}")
if all(verdicts):
    print(f"all {len(verdicts)} face checks passed")
else:
    print(f"{verdicts.count(False)} of {len(verdicts)} face checks FAILED")
    sys.exit(1)
