"""What the controls do, whatever is driving them: mouse and keyboard on the laptop,
the trackball and two knobs on the Pi.

Follows the Controls table in PLAN.md:

    trackball     roll: move the globe        press: zoom (2,000 km -> 500 km -> whole world -> ...)
                                              double press: surprise me (a random station)
                                              long press: next color theme
    Tune knob     turn: step station by station outward from where you rolled to
                  press: next favorite        long press: save this spot as a favorite
    Volume knob   turn: volume                press: mute / unmute
                                              long press: standby (any control wakes it)
    both buttons  tap: like the song playing  hold: the settings menu (Bluetooth, phone remote,
                                              Wi-Fi setup): turn Tune to choose, press it to open

Rolling moves the globe the way rolling the ball would. A keyboard nudge moves a
fixed fraction of the visible map, so it's small zoomed in and big in world view,
and nudges in quick succession speed up.
"""

import functools
import math
import random
import time

from .stations import place

EARTH_KM = 6371.0
# Zoom levels: how many km the round window spans edge to edge (None = the whole globe).
# Pressing the trackball zooms in one level, and after the closest jumps back out to the world.
ZOOMS_KM = (None, 2000, 500)
START_ZOOM = 1               # start at 2,000 km across
BALL_STEP = 1 / 30           # one trackball step moves this much of the visible width
NUDGE_STEP = 1 / 40          # one arrow-key press moves this much of the visible width
FAST_CLICK_S = 0.08          # presses closer together than this count as moving quickly
SPEEDUP, MAX_SPEEDUP = 1.1, 3.0
TUNE_STATIONS = 60           # how far the Tune knob reaches: the 60 nearest stations
VOLUME_STEP = 5
TOAST_S = 1.8                # how long short messages stay on screen
VOLUME_SHOWN_S = 1.5         # how long the volume ring stays after the last change
IDLE_S = 120                 # untouched this long, the face shows its clock
DOUBLE_S = 0.4               # a second ball press this soon makes a double press (0.3 was too quick for the real ball)
TOUR_S = 180                 # the world tour moves on this often


def _input(method):
    """Marks a control: using one wakes the face from its idle clock, and the radio from
    standby (that first touch does nothing else)."""
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        self._last_input = self.clock()
        if self.asleep and method.__name__ != "long_press_volume":
            self.asleep = False
            return None
        return method(self, *args, **kwargs)
    return wrapper


def _ordinal(n):
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


class Controls:
    def __init__(self, dial, favorites, *, themes=("night",), theme=None, zooms=ZOOMS_KM, clock=time.monotonic):
        """zooms: the zoom levels, km across (None: the whole world). A game world (worlds.py) has
        its own, the first showing the whole map."""
        self.dial = dial
        self.favorites = favorites
        self.clock = clock
        self.zooms = tuple(zooms)
        self.zoom = START_ZOOM if zooms[0] is None else 0     # index into self.zooms
        self.themes = tuple(themes)  # theme names, in the order the long press steps through them
        self.theme = theme or self.themes[0]
        self._toast, self._toast_until = "", 0.0
        self._volume_until = 0.0
        self._last_input = clock()
        self.panel = None            # the card open over the map (panels.py), if any
        self.bluetooth_menu = None   # set by the front end: panels.BluetoothMenu
        self.settings_menu = None    # set by the front end: what both knob buttons open (panels.SettingsMenu)
        self.liked = None            # set by the front end: liked.LikedSongs
        self.asleep = False          # standby (the front end silences the radio and the face)
        self.world_tour = False      # a new random station every TOUR_S
        self._tour_next = 0.0
        self._ball_pressed = None    # a ball press waiting to see if a second one follows
        self.rng = random.Random()
        self._unmute_volume = None
        self._speed = {}             # key -> (time of last press, current speed-up)
        self._tune_list = []         # stations nearest the spot the Tune knob started from
        self._tune_index = -1        # which of them is tuned (-1: none yet)
        self._tune_at = None         # where the dial was after the last Tune step

    # --- what the screen needs ---------------------------------------------

    @property
    def view_width_deg(self):
        """How much of the globe the window spans, in degrees of arc (180 = the whole disc)."""
        km = self.zooms[self.zoom]
        return 180.0 if km is None else math.degrees(km / EARTH_KM)

    @property
    def zoom_km(self):
        """How many km the window spans, 0 for the whole world (as the face is told)."""
        return self.zooms[self.zoom] or 0

    @property
    def muted(self):
        return self._unmute_volume is not None

    @property
    def idle(self):
        return self.clock() - self._last_input >= IDLE_S

    def toast(self):
        return self._toast if self.clock() < self._toast_until else ""

    def volume_shown(self):
        return self.clock() < self._volume_until

    def is_favorite(self, station):
        return self.favorites.find(station) is not None

    def _say(self, text):
        self._toast, self._toast_until = text, self.clock() + TOAST_S

    def say(self, text):
        """Show a short message (for the front end: the alarm, the phone page's changes)."""
        self._say(text)

    # --- trackball ---------------------------------------------------------

    @_input
    def roll(self, east, north):
        """Move the crosshair east and north, measured in visible widths (1.0 = the whole view).

        The laptop sends mouse drags here. On the Pi each trackball step is BALL_STEP.
        """
        if not (east or north):
            return
        self.close_panel()           # rolling the ball puts any card away
        self._tour_off()
        width = self.view_width_deg
        # A degree of longitude shrinks towards the poles; scale it up so a roll
        # covers the same distance on screen wherever you are.
        shrink = max(0.1, math.cos(math.radians(self.dial.state.lat)))
        self.dial.turn(dlat=north * width, dlon=east * width / shrink)

    @_input
    def nudge(self, east, north):
        """Arrow keys: a small roll, which speeds up when the key is held."""
        now = self.clock()
        key = "east" if east else "north"
        last, speed = self._speed.get(key, (-1.0, 1.0))
        speed = min(MAX_SPEEDUP, speed * SPEEDUP) if now - last < FAST_CLICK_S else 1.0
        self._speed[key] = (now, speed)
        self.roll(east * NUDGE_STEP * speed, north * NUDGE_STEP * speed)

    @_input
    def press_ball(self):
        """Zoom, a moment later: a second press within DOUBLE_S makes it "surprise me" instead."""
        if self.panel is not None:
            self.close_panel()
            return
        now = self.clock()
        if self._ball_pressed is not None and now - self._ball_pressed < DOUBLE_S:
            self._ball_pressed = None
            self._surprise()
        else:
            self._ball_pressed = now

    @_input
    def zoom_in(self):
        """Zoom straight away (the keyboard's W; the ball waits to rule out a double press)."""
        if self.panel is not None:
            self.close_panel()
            return
        self._zoom()

    def _zoom(self):
        self.zoom = (self.zoom + 1) % len(self.zooms)
        self._say(self.zoom_label(self.zoom, toast=True))

    def zoom_label(self, index, toast=False):
        """What zoom level `index` is called: "2,000 km" (the toast: "2,000 km across"), or in a
        game world, whose pretend km mean nothing, in words."""
        km = self.zooms[index]
        if self.zooms[0] is not None:
            return ("Whole map", "Closer", "Close up")[min(index, 2)]
        if km is None:
            return "Whole world"
        return f"{km:,} km across" if toast else f"{km:,} km"

    @_input
    def set_zoom(self, index):
        """Straight to one zoom level (the phone page's Map card)."""
        if 0 <= index < len(self.zooms) and index != self.zoom:
            self.zoom = index
            self._say(self.zoom_label(index, toast=True))

    @_input
    def surprise(self):
        """A random station somewhere in the world (the ball's double press; the keyboard's R)."""
        self._surprise()

    def _surprise(self):
        station = self.dial.random_station(self.rng)
        if station is None:
            return
        self.close_panel()
        self.dial.set_position(station["geo_lat"], station["geo_long"], station=station)
        self._tune_at = None
        self._say("Surprise")

    @_input
    def long_press_ball(self):
        if self.panel is not None:
            self.close_panel()
            return
        self.theme = self.themes[(self.themes.index(self.theme) + 1) % len(self.themes)]
        self._say(f"{self.theme.title()} theme")

    @_input
    def set_theme(self, name):
        """Straight to one theme (the phone page's Map card). An unknown name does nothing."""
        if name in self.themes and name != self.theme:
            self.theme = name
            self._say(f"{name.title()} theme")

    # --- Tune knob ---------------------------------------------------------

    @_input
    def turn_tune(self, clicks):
        """Step to the next-nearest (or, turning back, the previous) station around the spot
        where you stopped rolling, like sweeping a radio's dial through the band."""
        if self.panel is not None:
            self.panel.move(clicks)
            return
        self._tour_off()
        st = self.dial.state
        if (st.lat, st.lon) != self._tune_at:
            # Moved since the last Tune step: start a fresh list from here. The station already
            # on (usually the nearest) counts as the current step, so the first click moves on.
            self._tune_list = self.dial.nearest(st.lat, st.lon, TUNE_STATIONS)
            on = (st.playing or st.nearest) if st.status in ("tuning", "playing") else None
            uuids = [s.get("stationuuid") for s in self._tune_list]
            self._tune_index = uuids.index(on.get("stationuuid")) if on and on.get("stationuuid") in uuids else -1
        if not self._tune_list:
            return
        i = min(len(self._tune_list) - 1, max(0, self._tune_index + clicks))
        if i != self._tune_index:         # (at either end of the list, the station stays on)
            self._tune_index = i
            station = self._tune_list[i]
            self.dial.set_position(station["geo_lat"], station["geo_long"], station=station)
        self._tune_at = (self.dial.state.lat, self.dial.state.lon)
        self._say("Nearest" if self._tune_index == 0 else f"{_ordinal(self._tune_index + 1)} nearest")

    @_input
    def press_tune(self):
        if self.panel is not None:
            self.panel.press()
            return
        found = self.favorites.next()
        if not found:
            self._say("No favorites saved yet")
            return
        i, fav = found
        self.dial.set_position(fav["lat"], fav["lon"])
        self._say(f"Favorite {i + 1} of {len(self.favorites)}")

    @_input
    def go_to_favorite(self, i):
        """The phone page's list of favorites: tune straight to number i (from 0)."""
        found = self.favorites.recall(i)
        if found is None:
            return
        self._tour_off()
        self.close_panel()
        self.dial.set_position(found["lat"], found["lon"])
        self._say(f"Favorite {i + 1} of {len(self.favorites)}")

    @_input
    def tune_to(self, station):
        """The phone page's search: fly straight to a station and play it."""
        self._tour_off()
        self.close_panel()
        self.dial.set_position(station["geo_lat"], station["geo_long"], station=station)
        self._tune_at = None
        self._say("Found it")

    @_input
    def long_press_tune(self):
        if self.panel is not None:
            return
        st = self.dial.snapshot()
        station = st.playing if st.status == "playing" and st.playing else st.nearest
        i, added = self.favorites.add(st.lat, st.lon, station, place(station) if station else "")
        self._say(f"Saved as favorite {i + 1}" if added else f"Already favorite {i + 1}")

    # --- Volume knob -------------------------------------------------------

    @_input
    def turn_volume(self, clicks):
        self.dial.set_volume(self.dial.state.volume + clicks * VOLUME_STEP)
        self._unmute_volume = None
        self._volume_until = self.clock() + VOLUME_SHOWN_S

    @_input
    def set_volume(self, volume):
        """The phone page's volume slider: straight to a level, 0-100."""
        self.dial.set_volume(max(0, min(100, int(volume))))
        self._unmute_volume = None
        self._volume_until = self.clock() + VOLUME_SHOWN_S

    @_input
    def long_press_volume(self):
        """Standby: the screen goes dark and the sound stops; any control wakes it again."""
        self.asleep = not self.asleep
        if self.asleep:
            self.close_panel()
            self.world_tour = False

    @_input
    def press_volume(self):
        if self.panel is not None:
            back = getattr(self.panel, "back", None)
            if back is not None:
                back()                   # the alarm card: snooze
            self.close_panel()           # on a card, the Volume button means "back"
            return
        volume = self.dial.state.volume
        if volume > 0:
            self._unmute_volume = volume
            self.dial.set_volume(0)
            self._say("Muted")
        else:
            self.dial.set_volume(self._unmute_volume or 50)
            self._unmute_volume = None
            self._say("Sound on")
        self._volume_until = self.clock() + VOLUME_SHOWN_S

    # --- cards over the map: Bluetooth, Wi-Fi setup ------------------------

    @_input
    def press_both(self):
        """Both knob buttons held together: the settings menu (Bluetooth, phone remote, Wi-Fi
        setup), or straight to Bluetooth where there's no settings menu."""
        if self.settings_menu is not None:
            self.open_panel(self.settings_menu)
        else:
            self.open_bluetooth()

    @_input
    def open_bluetooth(self):
        """The Bluetooth menu. (B on the laptop; on the radio, from the settings menu.)"""
        if self.bluetooth_menu is not None:
            self.open_panel(self.bluetooth_menu)

    def open_panel(self, panel):
        if self.panel is not None and self.panel is not panel:
            self.panel.close()
        self.panel = panel
        panel.open()
        self._last_input = self.clock()

    def close_panel(self):
        if self.panel is not None:
            self.panel.close()
            self.panel = None

    def panel_view(self):
        """What the open card shows (a face_link.Panel), or None."""
        if self.panel is not None and self.panel.done:
            self.panel = None
        return self.panel.view() if self.panel is not None else None

    # --- both knob buttons: like the song --------------------------------------

    @_input
    def tap_both(self):
        """Both knob buttons tapped together: save the song that's playing. (H on the laptop.)"""
        st = self.dial.snapshot()
        if st.status != "playing" or not st.song or self.liked is None:
            self._say("No song title to save")
        elif self.liked.add(st.song, st.playing):
            self._say("Song saved")
        else:
            self._say("Already saved")

    def is_liked(self, song, station):
        return self.liked is not None and self.liked.has(song, station)

    # --- the world tour, and anything else that runs on time ----------------------

    def set_world_tour(self, on):
        self.world_tour = bool(on)
        self._tour_next = self.clock() + TOUR_S
        self._say("World tour on" if on else "World tour off")

    def _tour_off(self):
        if self.world_tour:
            self.world_tour = False
            self._say("World tour off")

    def tick(self):
        """Call every frame: carries out a single ball press once it can't be a double one, and
        moves the world tour along."""
        now = self.clock()
        if self._ball_pressed is not None and now - self._ball_pressed >= DOUBLE_S:
            self._ball_pressed = None
            self._zoom()
        if self.world_tour and not self.asleep and now >= self._tour_next:
            self._tour_next = now + TOUR_S
            self._surprise()
