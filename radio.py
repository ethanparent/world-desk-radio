"""World Desk Radio, laptop prototype: arrow keys stand in for the two knobs.

    python radio.py               # run (downloads stations on first run)
    python radio.py --update      # finish / refresh the station download first
    python radio.py --start 37.57,126.98
    python radio.py --map         # the round map in a window (see radio_map.py for its keys)
    python radio.py --face COM5   # ...and drive the real round face over USB too
    python radio.py --headless --face /dev/ttyACM0   # the Pi: no window, the face draws the map
                                                          # (and the trackball and knobs drive it)
    python radio.py --refresh-stations   # a fresh station list, swapped in when complete

Keys:  left/right = longitude   up/down = latitude   +/- = step size
       Q or Esc = quit
"""

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

from worlddeskradio import stations as station_db
from worlddeskradio.dial import Dial
from worlddeskradio.player import Player, find_backend
from worlddeskradio import static

ROOT = Path(__file__).resolve().parent
STATIC_WAV = ROOT / "static.wav"
STEPS = [0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0]


# --- keyboard ------------------------------------------------------------

class Keys:
    """Non-blocking key reader returning 'up', 'down', 'left', 'right', 'esc' or a character."""

    ARROWS = {"A": "up", "B": "down", "C": "right", "D": "left"}

    def __enter__(self):
        if sys.platform != "win32":
            import termios
            import tty
            self._fd = sys.stdin.fileno()
            self._saved = termios.tcgetattr(self._fd)
            tty.setcbreak(self._fd)
            self._typed = ""
        return self

    def __exit__(self, *exc):
        if sys.platform != "win32":
            import termios
            termios.tcsetattr(self._fd, termios.TCSADRAIN, self._saved)

    def read(self):
        if sys.platform == "win32":
            import msvcrt
            if not msvcrt.kbhit():
                return None
            ch = msvcrt.getwch()
            if ch in ("\x00", "\xe0"):
                return {"H": "up", "P": "down", "K": "left", "M": "right"}.get(msvcrt.getwch())
            return "esc" if ch == "\x1b" else ch
        # Straight from the terminal, not through sys.stdin: its buffer would take a whole arrow
        # key ("\x1b[A") at once, and select() would then see nothing left, so it read as Esc.
        import select
        if not self._typed:
            if not select.select([self._fd], [], [], 0)[0]:
                return None
            self._typed = os.read(self._fd, 64).decode("utf-8", "replace")
            if not self._typed:           # the terminal has gone (the SSH connection closed)
                return None
        if self._typed == "\x1b" and select.select([self._fd], [], [], 0.02)[0]:
            self._typed += os.read(self._fd, 64).decode("utf-8", "replace")   # the rest of an arrow
        if self._typed[:2] in ("\x1b[", "\x1bO") and len(self._typed) >= 3:
            key, self._typed = self.ARROWS.get(self._typed[2]), self._typed[3:]
            return key
        ch, self._typed = self._typed[0], self._typed[1:]
        return "esc" if ch == "\x1b" else ch


# --- screen --------------------------------------------------------------

def fmt_lat(lat):
    return f"{abs(lat):6.2f}°{'N' if lat >= 0 else 'S'}"


def fmt_lon(lon):
    return f"{abs(lon):6.2f}°{'E' if lon >= 0 else 'W'}"


def place(s):
    return station_db.place(s) or "unknown location"


def draw(state, step, backend):
    out = ["\x1b[H\x1b[J",
           "WORLD DESK RADIO  ·  laptop prototype\n",
           "─" * 56 + "\n",
           f"  Dial      {fmt_lat(state.lat)}  {fmt_lon(state.lon)}    step {step:g}°\n",
           f"  Stations  {state.station_count:,}\n\n"]
    s = state.nearest
    if s:
        out.append(f"  Nearest   {s['name'][:44]}\n")
        out.append(f"            {place(s)}  ·  {state.distance_km:,.0f} km away\n")
        out.append(f"            {s.get('codec') or '?'} {s.get('bitrate') or '?'} kbps"
                   f"  ·  {s.get('language') or 'language unknown'}\n")
    else:
        out.append("  Nearest   (none)\n\n\n")
    out.append("\n")
    status = state.status.upper()
    if state.status == "playing" and state.playing:
        p = state.playing
        note = "" if s and p["stationuuid"] == s["stationuuid"] else "  (fallback)"
        out.append(f"  ▶ ON AIR  {p['name'][:40]}{note}\n")
        out.append(f"            {place(p)}\n")
    elif state.status == "turning":
        out.append("  ~~~ static ~~~\n\n")
    else:
        out.append(f"  {status}\n\n")
    out.append(f"  {state.message[:54]}\n" if state.message else "\n")
    if state.tried and state.status != "playing":
        out.append(f"  no answer: {', '.join(state.tried)[:44]}\n")
    out.append("\n" + "─" * 56 + "\n")
    out.append(f"  ←/→ longitude  ↑/↓ latitude  +/- step  Q quit   [{backend}]\n")
    sys.stdout.write("".join(out))
    sys.stdout.flush()


# --- main ----------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="World Desk Radio laptop prototype")
    ap.add_argument("--update", action="store_true",
                    help="finish a partial station download, or re-download a complete one")
    ap.add_argument("--start",
                    help="starting lat,lon (default: London; with --map or --headless, where you left off)")
    ap.add_argument("--settle", type=float, default=0.4, help="seconds of stillness before tuning")
    ap.add_argument("--timeout", type=float, default=5.0, help="seconds to wait for a stream")
    ap.add_argument("--map", action="store_true",
                    help="show the round map in a window instead of the text screen")
    ap.add_argument("--scale", type=int,
                    help="with --map: enlarge the window this many times (default: about 720 px wide)")
    ap.add_argument("--size", type=int, default=480,
                    help="with --map: pixels across: 480 (the round face) or 240 (the 1.28\" fallback)")
    ap.add_argument("--theme",
                    help="with --map or --headless: color theme to start with: night, radar, ice, paper "
                         "or mono (default: the last one used)")
    ap.add_argument("--face", metavar="PORT",
                    help="also drive the round face over USB serial, e.g. COM5 or /dev/ttyACM0 "
                         "(opens the map, unless --headless)")
    ap.add_argument("--headless", action="store_true",
                    help="the whole radio with no window, and without drawing the map (the face draws it): "
                         "for the Pi. Keys from this terminal; add --face for the face")
    ap.add_argument("--virtual-face", action="store_true",
                    help="draw the map window only from the messages the face would get (opens the map)")
    ap.add_argument("--compare", action="store_true",
                    help="the map preview and the virtual face side by side (opens the map)")
    ap.add_argument("--lan", action="store_true",
                    help="with --map or --headless: serve the phone page on the home network, so a phone "
                         "on the same Wi-Fi can be the remote (anyone on that Wi-Fi can use it)")
    ap.add_argument("--no-knobs", action="store_true",
                    help="with --headless: leave the trackball and knobs alone (keys and the phone page only)")
    ap.add_argument("--ball-turn", type=int, choices=(0, 90, 180, 270),
                    help="with --headless: how far the trackball board is turned in the case, clockwise "
                         "seen from above, so rolling it moves the globe the same way (remembered)")
    ap.add_argument("--ball-mirror", action=argparse.BooleanOptionalAction,
                    help="with --headless: swap the ball's left and right as well (remembered)")
    ap.add_argument("--debug-knobs", action="store_true",
                    help="with --headless: print every trackball step, knob click and button press")
    ap.add_argument("--refresh-stations", action="store_true",
                    help="download a fresh station list and swap it in once it's complete, then exit "
                         "(the monthly timer on the Pi; a running radio picks it up when it's quiet)")
    args = ap.parse_args()
    if args.refresh_stations:
        return 0 if station_db.refresh() else 1
    if args.headless and (args.virtual_face or args.compare):
        ap.error("--virtual-face and --compare draw in a window, so they don't go with --headless")
    if args.face or args.virtual_face or args.compare:
        args.map = True

    if sys.platform != "win32":
        # A stop (systemd, kill) ends the radio the way Q does, so its players stop with it and
        # favorites and settings are saved, instead of mpv being left playing on its own.
        import signal
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    if sys.platform == "win32":
        os.system("")  # turns on ANSI escape handling in the Windows console
        sys.stdout.reconfigure(encoding="utf-8")

    backend = find_backend()
    if not backend:
        print("No audio player found. Install mpv (recommended):\n\n"
              "    winget install shinchiro.mpv\n\n"
              "then open a NEW terminal so it's on PATH. (ffplay from FFmpeg also works.)")
        return 1

    if args.update:
        # Finish a partial download, or refresh a complete one.
        station_db.update(fresh=station_db.download_complete())
    elif not station_db.CLEAN_PATH.exists():
        station_db.update()
    elif not station_db.download_complete():
        print("Note: the station list is only partly downloaded. "
              "Run  python radio.py --update  to fetch the rest.")
        time.sleep(1.5)
    stations = station_db.load()
    if static.needs_update(STATIC_WAV):
        static.generate(STATIC_WAV)

    lat, lon = (float(v) for v in args.start.split(",")) if args.start else (None, None)
    if args.map or args.headless:
        import radio_map
        return radio_map.run(args, stations, backend, STATIC_WAV, lat, lon)
    if lat is None:
        lat, lon = 51.51, -0.13   # London

    step_i = STEPS.index(1.0)
    player = Player(*backend, STATIC_WAV)

    dirty = True

    def on_change(_state):
        nonlocal dirty
        dirty = True

    dial = Dial(stations, player, lat=lat, lon=lon, settle_s=args.settle,
                start_timeout_s=args.timeout, on_change=on_change)

    moves = {"up": (1, 0), "down": (-1, 0), "left": (0, -1), "right": (0, 1)}
    sys.stdout.write("\x1b[?25l")  # hide cursor
    try:
        with Keys() as keys:
            dial.turn()  # start with static, then tune the starting spot
            while True:
                key = keys.read()
                while key:
                    step = STEPS[step_i]
                    if key in moves:
                        dlat, dlon = moves[key]
                        dial.turn(dlat * step, dlon * step)
                    elif key in "+=":
                        step_i = min(step_i + 1, len(STEPS) - 1)
                        dirty = True
                    elif key in "-_":
                        step_i = max(step_i - 1, 0)
                        dirty = True
                    elif key.lower() == "q" or key in ("esc", "\x03"):
                        return 0
                    key = keys.read()
                dial.tick()
                if dirty:
                    dirty = False
                    draw(dial.state, STEPS[step_i], backend[0])
                time.sleep(0.03)
    except KeyboardInterrupt:
        return 0
    finally:
        dial.close()
        sys.stdout.write("\x1b[?25h\n")


def restart_if_updated(code):
    """After the updater has installed a new version (radio_map.RESTART), start the radio again,
    with the same options, on the new code. On the Pi the process is replaced; on Windows a fresh
    one runs in this console until it ends."""
    import radio_map
    if code not in (radio_map.RESTART, radio_map.NEW_WORLD):
        return code
    args = sys.argv
    if code == radio_map.RESTART:
        os.environ["WORLDDESKRADIO_UPDATED"] = "1"      # so it says "Software updated" when it's back
    else:
        # Another world (the World menu): it says the world's name when it's there, and starts
        # where you last were in it, so a --start or --theme given for the old one is dropped.
        os.environ["WORLDDESKRADIO_NEW_WORLD"] = "1"
        args = _without(args, ("--start", "--theme"))
    argv = [sys.executable, *args]
    if sys.platform == "win32":
        return subprocess.call(argv)
    os.execv(sys.executable, argv)


def _without(argv, options):
    """argv with these options (and their values, "--start 1,2" or "--start=1,2") taken out."""
    out, skip = [], False
    for a in argv:
        if skip:
            skip = False
        elif a in options:
            skip = True
        elif not a.startswith(tuple(o + "=" for o in options)):
            out.append(a)
    return out


if __name__ == "__main__":
    sys.exit(restart_if_updated(main()))
