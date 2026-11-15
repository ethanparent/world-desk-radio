"""Terradio with the round map: the finished radio's screen, in a window on the laptop.

Start it with:  python radio.py --map      (same options as radio.py, plus --scale,
                --theme, and --size 240 to preview the 1.28" fallback screen)

The mouse stands in for the trackball, the keyboard for the knobs (and the ball):
    drag the map           roll the trackball: move the globe
    click                  trackball press: zoom (2,000 km -> 500 km -> whole world -> ...)
    double click or R      trackball double press: surprise me (a random station, flown to)
    long click / right     trackball long press: next color theme
    arrows or W / T        the same with the keyboard: nudge, zoom, theme
    wheel or [ / ]         Tune knob: next / previous station around where you stopped
    F / Shift+F            Tune knob press: next favorite / long press: save this spot
    + / -                  Volume knob
    M                      Volume knob press: mute / unmute
    Z                      Volume knob long press: standby (any key or click wakes it)
    H                      both knob buttons tapped: like the song playing
    B                      both knob buttons: the Bluetooth menu (pretend devices on the laptop)
    N                      Wi-Fi setup, as when the radio finds no network (pretend networks; the
                           phone page is at the address printed in the console)
    P                      the phone remote's QR code
    Q / Esc                quit

It starts where you left it (settings.json), unless you pass --start or --theme. The small
dot in the bottom-right corner is the trackball's light. The phone page (a remote control, the
alarm, sleep timer, world tour, liked songs, settings and updates) is at the address printed
when it starts; with --lan, a phone on the same Wi-Fi can open it too.

The weather at the station comes from Open-Meteo, and new versions of the radio's software from
GitHub (updater.py: checked every few hours, installed from the phone page, or by itself at
night on the Pi).

The face (Stage 2):
    --face COM5            also drive the real round face over its USB cable (/dev/ttyACM0 on the Pi)
    --virtual-face         draw the window only from the messages the face would get, as the face does
    --compare              the normal preview and the virtual face side by side

On the Pi (Stage 3), --headless runs the same radio with no window and without the map at all,
since the face draws its own: the terminal's keys (the same ones) are the controls, and it
prints a line when something changes. Stage 4's trackball and knobs call Controls the same way.
"""

import math
import os
import sys
import time
from pathlib import Path

from terradio.ball_light import BallLight
from terradio.clockradio import (ALARM_FADE_S, SNOOZE_FADE_S, SNOOZE_MIN, STANDBY_FADE_S, Alarm, SleepTimer,
                                  next_alarm)
from terradio.companion import Companion
from terradio.connectivity import BluezBluetooth, FakeBluetooth, FakeWiFi, NetworkManagerWiFi
from terradio import stations as station_db
from terradio.controls import BALL_STEP, ZOOMS_KM, Controls
from terradio.dial import Dial
from terradio.face_link import FaceLink, cut, face_state, loopback
from terradio.favorites import Favorites
from terradio.health import StationHealth
from terradio.liked import LikedSongs, spotify_url
from terradio.mixer import SystemMixer
from terradio.panels import AlarmCard, BluetoothMenu, PhoneRemote, SettingsMenu, WifiSetup, WorldMenu
from terradio.player import Player
from terradio.settings import Settings
from terradio.setup_page import SetupServer
from terradio.stations import StationSearch, place
from terradio.themes import THEMES, get_theme
from terradio.updater import Updater
from terradio.weather import SKIES, Weather
from terradio.worldtime import fmt_time
from terradio import worlds as world_db

ROOT = Path(__file__).resolve().parent
FAVORITES_PATH = ROOT / "favorites.json"
SETTINGS_PATH = ROOT / "settings.json"
LIKED_PATH = ROOT / "liked.json"
HEALTH_PATH = ROOT / "health.json"
START = (51.51, -0.13)      # London, the first time
FPS = 60                    # the map window
HEADLESS_FPS = 30           # headless: the face gets at most 30 state messages a second anyway
RESTART = 75                # what run() returns when a new version is installed: start again
NEW_WORLD = 76              # ... and when you pick another world in the World menu: start again in it
ON_PI = sys.platform.startswith("linux")
WIFI_SETUP_AFTER_S = 45     # the Pi with no Wi-Fi this long after starting (or losing it) opens Wi-Fi setup
BOOT_RETRY_S = 10           # no signal in the first minutes (Wi-Fi still coming up): try again this often
BOOT_S = 180
WORLD_SETTLE_S = 0.25       # a game world: seconds of stillness before tuning (Earth: --settle, 0.4)


def _hex(rgb):
    return "#%02x%02x%02x" % tuple(rgb[:3])


# The themes for the phone page's Map card: each one's name and a few of its colors, for a small
# picture of the globe on its button (the space round it, the globe, a coastline, a lit station).
THEME_SWATCHES = [{"name": t.name, "space": _hex(t.space), "globe": _hex(t.globe), "coast": _hex(t.coast),
                   "live": _hex(t.live)} for t in THEMES]


def run(args, stations, backend, static_wav, lat=None, lon=None):
    settings = Settings(SETTINGS_PATH)
    # A game world (worlds.py, picked in the World menu) instead of Earth: its stations and map.
    world = _load_world(settings)
    if world is not None:
        stations = world.stations
        print(f"In the world {world.title}: {len(stations)} station{'s' * (len(stations) != 1)}.")
    spot = WorldSpot(settings, world)
    if lat is None:
        first = (stations[0]["geo_lat"], stations[0]["geo_long"]) if world is not None else START
        lat, lon = spot.get("lat", first[0]), spot.get("lon", first[1])
    try:
        default = (world.theme if world is not None else None) or THEMES[0].name
        theme = get_theme(getattr(args, "theme", None) or spot.get("theme", default)).name
    except ValueError:
        theme = THEMES[0].name
    # On the Pi the volume goes through the system mixer (PipeWire), for the speaker and Bluetooth alike.
    mixer = SystemMixer.find() if ON_PI else None
    player = Player(*backend, static_wav, volume=settings.get("volume", 80), mixer=mixer)
    # A game world locks on sooner: its few stations are far apart, so a pause means "this one".
    settle = args.settle if world is None else min(args.settle, WORLD_SETTLE_S)
    dial = Dial(stations, player, lat=lat, lon=lon, settle_s=settle,
                start_timeout_s=args.timeout, health=StationHealth(HEALTH_PATH),
                bounds=world.bounds if world is not None else None)
    favorites = Favorites(FAVORITES_PATH if world is None else FAVORITES_PATH.with_name(f"favorites-{world.id}.json"))
    controls = Controls(dial, favorites, themes=[t.name for t in THEMES], theme=theme,
                        zooms=world.zooms if world is not None else ZOOMS_KM)
    controls.liked = LikedSongs(LIKED_PATH)
    companion = Companion()
    # Automatic updates are for the radio itself (the Pi); on the laptop you choose on the phone page.
    updater = Updater(ROOT, auto=settings.get("auto_update", sys.platform.startswith("linux")))
    updater.start()
    extras = Extras(dial, controls, settings, companion, stations, weather=Weather(), updater=updater,
                    world=world)
    if os.environ.pop("TERRADIO_UPDATED", None):
        controls.say("Software updated")
    if os.environ.pop("TERRADIO_NEW_WORLD", None):
        controls.say(world.title if world is not None else "Earth")
    if spot.get("zoom") in [z or 0 for z in controls.zooms]:
        controls.zoom = [z or 0 for z in controls.zooms].index(spot.get("zoom"))
    ball = BallLight()
    # Bluetooth and Wi-Fi: real ones on the Pi (BlueZ, NetworkManager), pretend ones on the laptop.
    bluetooth = BluezBluetooth() if ON_PI and BluezBluetooth.available() else FakeBluetooth()
    wifi = NetworkManagerWiFi() if ON_PI and NetworkManagerWiFi.available() else FakeWiFi()
    fake_wifi = isinstance(wifi, FakeWiFi)
    controls.bluetooth_menu = BluetoothMenu(bluetooth)
    wifi_setup = WifiSetup(wifi)
    setup_page = SetupServer(wifi_setup, companion, host="0.0.0.0" if getattr(args, "lan", False) else "127.0.0.1",
                             demo_password=FakeWiFi.PASSWORD if fake_wifi else None,
                             search=StationSearch(stations))
    setup_page.start()
    print(f"Phone page: {setup_page.url}"
          + (f"  (or {setup_page.mdns_url})" if setup_page.mdns_url else ""), flush=True)
    phone_remote = PhoneRemote(setup_page.url)

    def start_wifi_setup():
        controls.open_panel(wifi_setup)
        if fake_wifi:
            print(f"Wi-Fi setup: open {setup_page.url} (the pretend networks' password is {FakeWiFi.PASSWORD})")
        else:
            if not getattr(args, "lan", False):
                print("Wi-Fi setup: without --lan the setup page only answers on the radio itself, "
                      "so a phone can't reach it.", flush=True)
            print(f"Wi-Fi setup: the radio's own network is \"{wifi_setup.HOTSPOT}\", "
                  f"password {wifi_setup.password}", flush=True)

    def open_phone_remote():
        phone_remote.url = setup_page.current_url()     # the radio may have changed networks since
        controls.open_panel(phone_remote)

    def connected_name():
        device = bluetooth.connected
        return cut(device.name) if device is not None else ""
    # The World card: Earth and the game worlds installed. Picking another starts the radio again in it.
    here = world.id if world is not None else world_db.EARTH
    choices = extras.worlds
    moving_to = []

    def go_to_world(world_id):
        controls.close_panel()
        if world_id != here:
            settings.update(world=world_id)
            moving_to.append(world_id)
    world_menu = WorldMenu(choices, here, go_to_world)
    extras.go_to_world = go_to_world          # the phone page's World card

    # Holding both knob buttons: the settings cards.
    entries = [("Bluetooth", controls.open_bluetooth, connected_name),
               ("Phone remote", open_phone_remote, lambda: "QR code"),
               ("Wi-Fi setup", start_wifi_setup)]
    if len(choices) > 1:            # only once there's a game world to go to
        entries.append(("World", lambda: controls.open_panel(world_menu), lambda: dict(choices).get(here, "")))
    controls.settings_menu = SettingsMenu(entries)
    links = [FaceLink(args.face, stations, world=world)] if getattr(args, "face", None) else []
    # The window draws the map; headless, only the face does. On the Pi, the trackball and knobs.
    if getattr(args, "headless", False):
        knobs = None
        if ON_PI and not getattr(args, "no_knobs", False):
            from terradio.knobs import Knobs
            if getattr(args, "ball_turn", None) is not None:
                settings.update(ball_turn=args.ball_turn)
            if getattr(args, "ball_mirror", None) is not None:
                settings.update(ball_mirror=args.ball_mirror)
            knobs = Knobs.start(settings, debug=getattr(args, "debug_knobs", False))
        front = Headless(knobs)
    else:
        front = MapWindow(args, stations, world)
    links += front.links
    actions = {
        "left": lambda: controls.nudge(-1, 0),
        "right": lambda: controls.nudge(+1, 0),
        "up": lambda: controls.nudge(0, +1),
        "down": lambda: controls.nudge(0, -1),
        "click": controls.press_ball,
        "w": controls.zoom_in,
        "W": controls.zoom_in,
        "r": controls.surprise,
        "R": controls.surprise,
        "long-click": controls.long_press_ball,
        "t": controls.long_press_ball,
        "T": controls.long_press_ball,
        "[": lambda: controls.turn_tune(-1),
        "]": lambda: controls.turn_tune(+1),
        "f": controls.press_tune,
        "F": controls.long_press_tune,
        "+": lambda: controls.turn_volume(+1),
        "-": lambda: controls.turn_volume(-1),
        "m": controls.press_volume,
        "M": controls.press_volume,
        "z": controls.long_press_volume,
        "Z": controls.long_press_volume,
        "h": controls.tap_both,
        "H": controls.tap_both,
        "b": controls.open_bluetooth,
        "B": controls.open_bluetooth,
        "s": controls.press_both,           # the settings menu, as holding both knob buttons
        "S": controls.press_both,
        "n": start_wifi_setup,
        "N": start_wifi_setup,
        "p": open_phone_remote,
        "P": open_phone_remote,
        # The trackball's and knobs' buttons (knobs.py); their turns come as ("roll" | "tune" | "volume", ...)
        "ball-press": controls.press_ball,
        "ball-hold": controls.long_press_ball,
        "tune-press": controls.press_tune,
        "tune-hold": controls.long_press_tune,
        "volume-press": controls.press_volume,
        "volume-hold": controls.long_press_volume,
        "both-tap": controls.tap_both,
        "both-hold": controls.press_both,
    }
    front.print_help()

    # How far one trackball step moves the globe, as a share of the usual (settings.json "ball_speed").
    try:
        ball_step = BALL_STEP * max(0.1, min(4.0, float(settings.get("ball_speed", 1.0))))
    except (TypeError, ValueError):
        ball_step = BALL_STEP
    list_mtime, next_list_check = _mtime(station_db.CLEAN_PATH), 0.0
    started = time.monotonic()
    retry_at = 0.0                  # no signal at boot: when to try again
    played = False                  # ...until something has played
    offline_since = None            # the Pi's Wi-Fi: since when it hasn't been joined to a network
    setup_offered = False           # ...and Wi-Fi setup has opened for it (once each time it's lost)
    was_online = None
    bt_address = None
    try:
        dial.turn()  # start with static, then tune the starting spot
        while True:
            for event in front.events():
                if event in ("quit", "esc", "q", "Q"):
                    return 0
                if isinstance(event, tuple):
                    kind, *values = event
                    if kind == "drag":
                        # Grab-and-drag the globe: dragging right brings the west into view.
                        dx, dy = values
                        controls.roll(-dx, dy)
                    elif kind == "wheel":
                        controls.turn_tune(values[0])
                    elif kind == "roll":          # the trackball, in steps
                        controls.roll(values[0] * ball_step, values[1] * ball_step)
                    elif kind == "tune":
                        controls.turn_tune(values[0])
                    elif kind == "volume":
                        controls.turn_volume(values[0])
                elif event in actions:
                    actions[event]()
            if moving_to:
                print(f"Going to {dict(choices).get(moving_to[0], moving_to[0])}: starting again.", flush=True)
                return NEW_WORLD
            dial.tick()
            controls.tick()

            now = time.monotonic()
            change = extras.tick(now)
            if change == "sleep":             # the fade to dark is over: stop the sound
                dial.sleep()
            elif change and change[0] == "dim":
                dial.player.fade_out(change[1])
                for link in links:
                    link.sleep(change[1])
                front.fade_out(now, change[1])
            elif change and change[0] == "wake":
                if change[2]:                 # woken while still fading out: start the sound afresh
                    dial.sleep()
                dial.wake(change[1])
                for link in links:
                    link.wake(change[1])
                front.wake(now, change[1])
            st = dial.snapshot()
            # Headphones came or went: the sound moved, so set the volume on where it is now.
            address = bluetooth.connected.address if bluetooth.connected else None
            if address != bt_address:
                bt_address = address
                if mixer is not None:
                    mixer.refresh()
            # No Wi-Fi on the Pi: after a while, open Wi-Fi setup (once each time it's lost).
            online = getattr(wifi, "online", None)
            if online is False:
                offline_since = offline_since or now
                if not setup_offered and now - offline_since >= WIFI_SETUP_AFTER_S and controls.panel is None \
                        and not controls.asleep:
                    setup_offered = True
                    print("No Wi-Fi: opening Wi-Fi setup.", flush=True)
                    start_wifi_setup()
            elif online:
                offline_since, setup_offered = None, False
            # Nothing would play just after starting (Wi-Fi still coming up), or the Wi-Fi is back:
            # try the spot again. Only while nobody's using the radio: not around the alarm,
            # standby or a card, and at boot only until something has played.
            played = played or st.status == "playing"
            hands_off = not (controls.asleep or extras.sleeping or extras.dimming or controls.panel is not None
                             or extras.ringing is not None)
            if st.status == "no signal" and hands_off and (
                    (not played and now - started < BOOT_S and now >= retry_at) or (online and was_online is False)):
                retry_at = now + BOOT_RETRY_S
                dial.set_position(st.lat, st.lon, station=st.nearest)
            was_online = online if online is not None else was_online
            state = face_state(st, controls, bt=bluetooth.connected is not None,
                               alarm=extras.next_alarm_minutes(), timer=extras.timer.minutes_left(now),
                               weather=lambda station: extras.weather_at(station, st.status),
                               borders=settings.get("borders", True),
                               cities=settings.get("cities", True),     # (a game world: its places)
                               # The idle globe turns, unless the sleep timer is running: falling
                               # asleep is easier without motion in the corner of your eye.
                               spin=settings.get("spin", True) and extras.timer.left(now) is None,
                               bright=settings.get("brightness", 100))
            extras.publish(st, state, now)
            spots = [(f["lat"], f["lon"]) for f in favorites.items]
            for link in links:
                link.set_favorites(spots)
                link.update(state, now)
            light = ball.color(state, now, asleep=extras.sleeping or extras.dimming)
            front.show(state, now, spots=spots, light=light, dark=extras.sleeping)
            spot.update(lat=round(st.lat, 4), lon=round(st.lon, 4), zoom=controls.zoom_km, theme=controls.theme)
            if st.volume > 0:       # muted: come back at the volume before
                settings.update(volume=st.volume)
            settings.tick()
            # Updates install at night only while nobody's listening: in standby, or idle and silent.
            quiet = extras.sleeping or (controls.idle and st.status != "playing")
            updater.tick(quiet=quiet)
            if updater.restart_due:
                print("A new version is installed: starting again.")
                return RESTART
            if quiet and now >= next_list_check:
                # The monthly refresh (radio.py --refresh-stations) swapped in a new station
                # list: start again on it, which also sends the face its new dots.
                next_list_check = now + 60
                if _mtime(station_db.CLEAN_PATH) != list_mtime:
                    print("A new station list is in: starting again.")
                    return RESTART
            time.sleep(max(0.0, 1 / front.fps - (time.monotonic() - now)))
    except KeyboardInterrupt:
        return 0
    finally:
        settings.save()
        setup_page.stop()
        front.close()
        dial.close()
        for link in links:
            link.close()


class MapWindow:
    """The laptop's front end: the round map in a window, the mouse and keyboard as the controls,
    and the trackball's light as a dot in the corner. The normal preview draws straight from the
    radio's state. The virtual face draws only from the messages a real face would get, through
    a pretend cable. --compare shows both."""

    fps = FPS

    def __init__(self, args, stations, world=None):
        # Only the window draws the map, so only it loads the drawing code and the coastlines.
        from PIL import Image

        from terradio.map_view import FaceScreen
        from terradio.screen import PygameWindow
        from terradio.virtual_face import VirtualFace

        self.size = size = args.size
        compare = getattr(args, "compare", False)
        virtual = compare or getattr(args, "virtual_face", False)
        print("Loading the map ...")
        self.preview = FaceScreen(stations, size) if compare or not virtual else None
        if self.preview and world is not None:
            self.preview.view.set_world(world.coast, world.borders, world.places, icons=world.icons)
            self.preview.home = world.home
        self.face, self.links = None, []
        if virtual:
            radio_end, face_end = loopback()
            self.face = VirtualFace(face_end, size)
            self.links.append(FaceLink(radio_end, stations, world=world))
        self.panes = 2 if compare else 1
        try:
            self.display = PygameWindow(size=(size * self.panes, size),
                                        scale=args.scale or max(1, 720 // size))
        except ImportError:
            raise SystemExit("The map window needs pygame:  python -m pip install pygame\n"
                             "(On the Pi, start the radio with --headless: the face draws the map.)"
                             ) from None
        self._shown = [None] * self.panes
        self._shown_light = None
        self._dark = Image.new("RGB", (size, size))
        self._preview_dark = False

    def print_help(self):
        print("Map window open. Drag to roll the globe, click to zoom, right-click for themes, "
              "mouse wheel or [ ] to tune, +/- volume, M mute, F / Shift+F favorites, "
              "double-click or R surprise, H like the song, Z standby, S settings, B Bluetooth, N Wi-Fi setup, "
              "P phone remote, Q quits.")

    def events(self):
        """PygameWindow's events. Its drags are fractions of the window, which --compare makes
        twice as wide: here they're fractions of one map."""
        return [("drag", e[1] * self.panes, e[2]) if isinstance(e, tuple) and e[0] == "drag" else e
                for e in self.display.events()]

    def fade_out(self, now, fade_s):
        if self.preview:
            self.preview.fade_out(now, fade_s)

    def wake(self, now, fade_s):
        if self.preview:
            self.preview.wake(now, fade_s)

    def show(self, state, now, *, spots, light, dark):
        """Draw the frame for this FaceState, if anything on screen (or the ball) has changed.
        dark: standby, after the fade."""
        frames = []
        if self.preview and dark:           # standby: the screen goes dark
            frames.append(None if self._preview_dark else self._dark)
            self._preview_dark = True
        elif self.preview:
            self._preview_dark = False
            self.preview.set_favorites(spots)
            frames.append(self.preview.frame(state, now))
        if self.face:
            self.face.poll()
            frames.append(self.face.frame(now))
        if any(f is not None for f in frames) or light != self._shown_light:
            self._shown = [f if f is not None else old for f, old in zip(frames, self._shown)]
            image = self._shown[0] if self.panes == 1 else _side_by_side(self._shown, self.size)
            self.display.show(_with_ball(image, light))
            self._shown_light = light

    def close(self):
        self.display.close()


class Headless:
    """The Pi's front end (--headless): no window, and no map, since the face draws its own from
    the messages FaceLink sends. The trackball and knobs (knobs.py) are the controls, and so are
    this terminal's keys (the same keys as the window's) when there is one; a line of plain text
    says what changed. With no terminal (as a service), the knobs and the phone page are the remote."""

    fps = HEADLESS_FPS
    links = ()
    NAMES = {"=": "+", "_": "-", "\x03": "quit"}          # keys named as the window names them
    STATUS = {"tuning": "Tuning", "playing": "On air", "no signal": "No signal"}

    def __init__(self, knobs=None):
        from radio import Keys
        self._keys = Keys().__enter__() if sys.stdin.isatty() else None
        self.knobs = knobs
        self._said = {}
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(errors="replace")   # a name the terminal can't show won't stop the radio

    def print_help(self):
        if self._keys:
            print("No window (--headless). Arrows roll the globe, W zoom, T theme, R surprise, [ ] tune, "
                  "F / Shift+F favorites, +/- volume, M mute, Z standby, H like the song, S settings, B Bluetooth, "
                  "N Wi-Fi setup, P phone remote, Q quits.", flush=True)
        else:
            print("No window (--headless) and no terminal to read keys from: the "
                  + ("trackball, knobs and phone page are the remote." if self.knobs else "phone page is the remote."),
                  flush=True)
        if self.knobs:
            print("Trackball and knobs ready.", flush=True)

    def events(self):
        keys = self.knobs.events() if self.knobs else []
        key = self._keys.read() if self._keys else None
        while key:
            keys.append(self.NAMES.get(key, key))
            key = self._keys.read()
        return keys

    def fade_out(self, now, fade_s):
        pass

    def wake(self, now, fade_s):
        pass

    def show(self, fs, now, *, spots, light, dark):
        """Print what changed in this FaceState: the station, the song, a message, the volume, a
        card. light is the trackball's (r, g, b, w), for its four LEDs."""
        if self.knobs:
            self.knobs.set_light(light)
        if fs.status == "off":
            station = "Standby"
        elif fs.name is None:
            station = "No stations"
        elif fs.status == "turning":
            station = "Static"              # rolling: the nearest station changes too fast to list
        else:
            station = (f"{self.STATUS.get(fs.status, fs.status.capitalize())}: {fs.name}"
                       + (f" · {fs.place}" if fs.place else ""))
        self._say("station", station)
        self._say("song", fs.song if fs.status == "playing" else "", "  ")
        self._say("toast", fs.toast, "  ")
        self._say("volume", f"Volume {fs.vol}" if fs.ring and not fs.mute else "", "  ")
        card = _card_text(fs.panel) if fs.panel else ""
        if not card and self._said.get("card"):
            print("  Card closed", flush=True)
        self._say("card", card)

    def _say(self, kind, text, indent=""):
        if text != self._said.get(kind):
            self._said[kind] = text
            if text:
                print(indent + text, flush=True)

    def close(self):
        if self._keys:
            self._keys.__exit__(None, None, None)
        if self.knobs:
            self.knobs.close()


class Extras:
    """The alarm, the sleep timer, standby, the weather and the phone page's requests (the remote,
    settings, updates), run from the main loop."""

    def __init__(self, dial, controls, settings, companion, stations, weather=None, updater=None,
                 clock=time.monotonic, world=None):
        self.dial, self.controls, self.settings, self.companion = dial, controls, settings, companion
        self.world = world              # the game world the radio is in (worlds.World), None on Earth
        self.worlds = world_db.available()  # Earth and the worlds installed, for the phone page
        self.go_to_world = None         # set by run(): start again in another world (the World card's way)
        self.weather, self.updater, self.clock = weather, updater, clock
        # Two alarms. Their first defaults: 7:00 on weekdays, 9:00 at the weekend (both off).
        # (An older settings.json has one "alarm": it becomes the first.)
        saved = settings.get("alarms") or [settings.get("alarm")]
        defaults = (Alarm(hour=7, days=range(5)), Alarm(hour=9, days=(5, 6)))
        self.alarms = [Alarm.from_dict(saved[i] if i < len(saved) else None, defaults[i]) for i in range(2)]
        self.alarm_card = AlarmCard(self._snooze, self._stop_ringing)
        self.ringing = None             # the alarm going off: its card is up, or it's snoozed
        self._snooze_until = None
        self.snooze_s = SNOOZE_MIN * 60
        self.timer = SleepTimer()
        self.timer_set = 0              # the minutes chosen, for the phone page's buttons
        self.sleeping = False             # standby, after the fade to dark
        self._dark_at = None              # standby: when the fade to dark ends
        self._fading = False
        self._wake_fade = 0
        self._published = -math.inf
        self._stations = {s.get("stationuuid"): s for s in stations}
        self._station_list = stations

    @property
    def dimming(self):
        return self._dark_at is not None

    def tick(self, now):
        """What standby should do this frame: ("dim", seconds) to start fading out, "sleep" once
        it's dark, ("wake", seconds to fade in, start the sound afresh?), or None."""
        for kind, args in self.companion.requests():
            self._request(kind, args, now)
        local = time.localtime()
        for alarm in self.alarms:
            if alarm.due(local):
                self._ring(alarm)
        if self._snooze_until is not None:
            if not self.controls.asleep:            # woken by hand while snoozing: they're up
                self._stop_ringing()
            elif self.clock() >= self._snooze_until:
                self._snooze_until = None
                self._ring(self.ringing, SNOOZE_FADE_S)
        phase = self.timer.phase(now)
        if phase == "fading" and not self._fading:
            self._fading = True
            self.dial.player.fade_out(self.timer.left(now))
        elif phase == "done":
            self.timer.set(0, now)
            self._fading = False
            self.controls.close_panel()
            self.controls.asleep = True
        asleep = self.controls.asleep
        if self._dark_at is not None:               # fading out
            if not asleep:
                self._dark_at = None
                return ("wake", STANDBY_FADE_S, True)
            if now >= self._dark_at:
                self._dark_at, self.sleeping = None, True
                return "sleep"
            return None
        if asleep and not self.sleeping:
            self.timer.set(0, now)                  # standby by hand cancels the sleep timer
            self._fading = False
            self._dark_at = now + STANDBY_FADE_S
            return ("dim", STANDBY_FADE_S)
        if self.sleeping and not asleep:
            self.sleeping = False
            fade, self._wake_fade = self._wake_fade or STANDBY_FADE_S, 0
            return ("wake", fade, False)
        return None

    @property
    def units(self):
        return self.settings.get("units", "F")

    def weather_at(self, station, status):
        """(sky, temperature) at a station, or None. Looked up once the dial stops on it; while
        rolling past, only places already looked up show their weather."""
        if self.weather is None or station.get("world"):      # a game world has no weather service
            return None
        report = self.weather.get(station["geo_lat"], station["geo_long"], fetch=status in ("tuning", "playing"))
        return (report.sky, report.temp(self.units)) if report else None

    def next_alarm_minutes(self):
        """When the next alarm rings (minutes after midnight), for the idle clock; None if none is on."""
        alarm = next_alarm(self.alarms, time.localtime())
        return alarm.minutes if alarm else None

    def _ring(self, alarm, fade=ALARM_FADE_S):
        """Wake to the alarm's station, fading in slowly with the screen, and show its card."""
        controls, dial = self.controls, self.dial
        controls.close_panel()
        controls.world_tour = False
        if dial.state.volume == 0:
            dial.set_volume(40)               # muted at bedtime shouldn't mean a silent alarm
        saved = alarm.station
        station = self._stations.get(saved.get("stationuuid")) if saved else None
        if station is None and saved and self.world is None and not world_db.is_world_station(saved):
            station = saved                 # gone from the station list since: try it anyway
        # (An alarm set in another world rings on the spot the dial is on.)
        st = dial.state
        if station:
            dial.set_position(station["geo_lat"], station["geo_long"], station=station, fade_s=fade)
        else:
            dial.set_position(st.lat, st.lon, fade_s=fade)
        self._wake_fade = fade if self.sleeping or self.dimming else 0
        controls.asleep = False
        self.ringing = alarm
        self.alarm_card.label = fmt_time(alarm.hour * 60 + alarm.minute)
        controls.open_panel(self.alarm_card)

    def _snooze(self):
        """The alarm card's Volume press (or Snooze on the phone): quiet and dark for SNOOZE_MIN,
        then the alarm again."""
        self._snooze_until = self.clock() + self.snooze_s
        self.controls.asleep = True

    def _stop_ringing(self):
        """Turned off (Tune on the card, or Turn off on the phone): the station plays on."""
        was_snoozing = self._snooze_until is not None
        self.ringing = self._snooze_until = None
        if not was_snoozing:
            self.controls.say("Good morning")

    def _request(self, kind, args, now):
        controls = self.controls
        if kind == "alarm":
            alarm = self.alarms[args["index"]]
            alarm.on, alarm.hour, alarm.minute = args["on"], args["hour"], args["minute"]
            if args.get("days") is not None:
                alarm.days = sorted(set(args["days"]))
            if args.get("use_current") or alarm.station is None:
                st = self.dial.snapshot()
                s = st.playing or st.nearest
                if s:
                    alarm.station = {k: s.get(k) for k in ("name", "stationuuid", "geo_lat", "geo_long",
                                                           "url_resolved", "state", "country", "countrycode")}
            self.settings.update(alarms=[a.to_dict() for a in self.alarms])
            controls.say(f"Alarm {fmt_time(alarm.minutes)}" if alarm.on else "Alarm off")
        elif kind == "ring":
            if args["do"] == "snooze" and self.alarm_card.is_open:
                self.alarm_card.back()
                controls.close_panel()
            elif args["do"] == "stop":
                if self.alarm_card.is_open:
                    controls.close_panel()          # the card's close turns it off
                elif self.ringing is not None:
                    self._stop_ringing()            # snoozed: no more today
        elif kind == "timer":
            self.timer.set(args["minutes"], now)
            self.timer_set, self._fading = args["minutes"], False
            controls.say(f"Sleep in {args['minutes']} min" if args["minutes"] else "Sleep timer off")
        elif kind == "tour":
            controls.set_world_tour(args["on"])
        elif kind == "play":
            controls.tune_to(self._station_list[args["id"]])
        elif kind == "unlike" and controls.liked is not None:
            controls.liked.delete(args["id"])
        elif kind == "remote":
            self._remote(args["do"], args["value"])
        elif kind == "settings":
            self.settings.update(**args)
            if "auto_update" in args and self.updater:
                self.updater.auto = args["auto_update"]
        elif kind == "theme":
            controls.set_theme(args["name"])
        elif kind == "zoom":
            controls.set_zoom(args["index"])
        elif kind == "world" and self.go_to_world is not None:
            if args["id"] in dict(self.worlds):
                self.go_to_world(args["id"])
        elif kind == "update" and self.updater:
            self.updater.install() if args["do"] == "install" else self.updater.check()

    def _remote(self, do, value):
        """The phone remote's buttons: the same as the knobs and the ball."""
        c = self.controls
        if do == "volume":
            c.set_volume(value)
        elif do == "favorite":
            c.go_to_favorite(value)
        elif do == "unfavorite":
            c.favorites.remove(value)
        elif do == "power":
            c.long_press_volume()
        else:
            {"prev": lambda: c.turn_tune(-1), "next": lambda: c.turn_tune(+1), "mute": c.press_volume,
             "save": c.long_press_tune, "like": c.tap_both, "surprise": c.surprise,
             "theme": c.long_press_ball, "zoom": c.zoom_in}[do]()

    def publish(self, st, state, now):
        """What the phone page shows, a few times a second."""
        if now - self._published < 0.25:
            return
        self._published = now
        shown = st.playing if st.status == "playing" and st.playing else st.nearest
        controls, off = self.controls, st.status == "off"
        favorites = controls.favorites.items
        self.companion.publish({
            "now": {"station": "Standby" if off else shown["name"] if shown else "",
                    "place": place(shown) if shown and not off else "",
                    "song": st.song if st.status == "playing" else "", "liked": state.liked,
                    "status": st.status,
                    "local": fmt_time(state.local) if state.local is not None and not off else "",
                    "weather": f"{SKIES[state.sky]}, {state.temp}°{self.units}" if state.sky and not off else "",
                    "sky": state.sky if not off else None},
            "volume": st.volume, "muted": controls.muted, "asleep": controls.asleep,
            "favorites": [{"name": f.get("name", ""), "place": f.get("place", "")} for f in favorites],
            "favorite": controls.favorites.find(shown) if shown and not off else None,
            "units": self.units,
            "borders": self.settings.get("borders", True), "cities": self.settings.get("cities", True),
            "spin": self.settings.get("spin", True),
            "update": self.updater.status() if self.updater else None,
            "brightness": self.settings.get("brightness", 100),
            "theme": controls.theme,
            "themes": THEME_SWATCHES,
            "zoom": controls.zoom,
            "zooms": [controls.zoom_label(i) for i in range(len(controls.zooms))],
            "worlds": [{"id": w, "title": title} for w, title in self.worlds],
            "world": self.world.id if self.world is not None else world_db.EARTH,
            "alarms": [{"on": a.on, "time": f"{a.hour:02d}:{a.minute:02d}", "days": a.days,
                        "station": a.station.get("name") if a.station else None} for a in self.alarms],
            "ringing": None if self.ringing is None else {
                "time": fmt_time(self.ringing.hour * 60 + self.ringing.minute),
                "snoozed": None if self._snooze_until is None else fmt_time(
                    _minutes(time.time() + max(0.0, self._snooze_until - self.clock())))},
            "timer": self.timer.minutes_left(now), "timer_set": self.timer_set,
            "tour": self.controls.world_tour,
            "liked": [dict(item, spotify=spotify_url(item["song"]))
                      for item in (self.controls.liked.items()[:100] if self.controls.liked else [])],
            "skipped": st.skipped,
        })


class WorldSpot:
    """Where the dial was, at what zoom and in what theme, kept for each world: Earth's at the
    top of settings.json as before, each game world's under "worlds"."""

    def __init__(self, settings, world=None):
        self.settings, self.id = settings, world.id if world is not None else None

    def get(self, key, default=None):
        if self.id is None:
            return self.settings.get(key, default)
        return ((self.settings.get("worlds") or {}).get(self.id) or {}).get(key, default)

    def update(self, **values):
        if self.id is None:
            self.settings.update(**values)
            return
        everywhere = dict(self.settings.get("worlds") or {})
        mine = dict(everywhere.get(self.id) or {})
        mine.update(values)
        everywhere[self.id] = mine
        self.settings.update(worlds=everywhere)


def _load_world(settings):
    """The game world settings.json says the radio is in, or None for Earth. One that won't load
    (deleted, or broken) sends it back to Earth, with a note why."""
    world_id = settings.get("world") or world_db.EARTH
    if world_id == world_db.EARTH:
        return None
    try:
        return world_db.load(world_id)
    except world_db.WorldError as e:
        print(f"{e}: back to Earth.", flush=True)
        settings.update(world=world_db.EARTH)
        return None


def _mtime(path):
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _minutes(when):
    t = time.localtime(when)
    return t.tm_hour * 60 + t.tm_min


def _card_text(panel):
    """A card over the map (a face_link.Panel) as a few lines of text, for --headless."""
    lines = [f"[{panel.title}]", *panel.lines]
    lines += [f"{'>' if i == panel.sel else ' '} {label}" + (f"  ({detail})" if detail else "")
              for i, (label, detail) in enumerate(panel.items)]
    lines += panel.notes
    return "\n".join("  " + line for line in lines)


def _side_by_side(images, size):
    from PIL import Image
    out = Image.new("RGB", (size * len(images), size))
    for i, img in enumerate(images):
        if img is not None:
            out.paste(img, (i * size, 0))
    return out


def _with_ball(image, rgbw):
    """The trackball's light as a dot in the window's bottom-right corner, outside the round
    screen (the face's corners are hidden behind the front panel anyway)."""
    from PIL import ImageDraw
    image = image.copy()
    r, g, b, w = rgbw
    color = tuple(min(255, c + w) for c in (r, g, b))
    x, y, rad = image.width - 26, image.height - 26, 13
    d = ImageDraw.Draw(image)
    d.ellipse([x - rad, y - rad, x + rad, y + rad], fill=color, outline=(60, 60, 60))
    return image


if __name__ == "__main__":
    import sys

    import radio
    if "--map" not in sys.argv:
        sys.argv.insert(1, "--map")
    sys.exit(radio.restart_if_updated(radio.main()))
