"""The trackball and the two knobs on the Pi (Stage 4), turned into the same calls the laptop's
mouse and keys make on controls.py.

    knobs = Knobs.start(settings)        # None on a computer without the Pi's pins
    for event in knobs.events():         # every frame, from the main loop
        ...                              # ("roll", east, north), ("tune", clicks), ("volume", clicks),
                                         # or "ball-press", "ball-hold", "tune-press", "tune-hold",
                                         # "volume-press", "volume-hold", "both-tap", "both-hold"
    knobs.set_light((r, g, b, w))        # the ball's four lights, from ball_light.py
    knobs.close()

The pins only note what happened, from gpiozero's own thread; the main loop collects it with
events() and makes the Controls calls itself, so nothing in controls.py runs on two threads.

    trackball    each flip of a direction pin is one step of roll(). Fast rolling (a flick)
                 moves further per step. Press: zoom; double press: surprise me; hold: next theme
                 Rolls while the ball is pressed (and just after) are the finger wobbling it: left out
    Tune knob    turn: turn_tune(); press: next favorite; hold: save this spot
    Volume knob  turn: volume; press: mute; hold: standby
    both buttons tap: like the song; hold: the settings menu (Bluetooth, phone remote, Wi-Fi setup)

The encoders bounce a lot, so they go through a quadrature state machine (Quadrature): a click
counts only when the knob reaches its next resting position (a detent) having passed through
the steps between, so bounce on one contact cancels itself out. The buttons count a change only
once it has held for DEBOUNCE_S (Debounced).

The ball sits a quarter turn round in the case, so which pin means which way is a setting in
settings.json: "ball_turn" (0, 90, 180 or 270: how far the board is turned, clockwise seen from
above) and "ball_mirror" (true if left and right still come out swapped). `radio.py
--ball-turn 90` sets it once and remembers. How far each step moves the globe is "ball_speed"
(1.0 as designed, 0.5 for half as far; radio_map.py reads it).

The ball's lights are active-high (each glows while its pin is high). Until their resistors are
checked with the meter they are lit only through the pin's weak internal pull-up (about 50 kΩ, so
a few tens of microamps): on or off, never driven. Once the resistors are confirmed, set
"ball_lights": "pwm" in settings.json for proper dimming.
"""

import collections
import math
import threading
import time

# Pins, BCM numbering (PLAN.md's pin map).
BALL_PINS = {"up": 16, "down": 23, "left": 26, "right": 24}
BALL_BUTTON = 25
TUNE_PINS, TUNE_BUTTON = (5, 6), 13
VOLUME_PINS, VOLUME_BUTTON = (17, 27), 22
LIGHT_PINS = {"white": 4, "red": 12, "green": 7, "blue": 8}

BALL_QUIET_S = 0.3           # rolling is ignored while the ball is pressed, and this long after
HOLD_S = 0.6                 # a press held this long is a long press (the laptop's long click too)
DEBOUNCE_S = 0.02            # a button's contact must stay put this long to count
STEPS_PER_CLICK = 4          # quadrature steps from one detent to the next: 4 for the EC11 (20 detents,
                             # 20 pulses); 2 for encoders that rest at both 00 and 11

# Rolling: one step moves controls.BALL_STEP of the view. A flick moves further: above FLICK_RATE
# steps a second, each step counts for more, up to MAX_GAIN times.
FLICK_RATE = 15.0
MAX_GAIN = 4.0
RATE_WINDOW_S = 0.12
GLIDE_S = 0.08               # each step glides in over about this long (a diagonal stays a diagonal)

# How each way the ball rolls moves the globe: grab-and-drag, like the mouse on the laptop.
# Rolling the top of the ball to the right turns the globe to the right (the crosshair goes west).
ROLL = {"up": (0, -1), "down": (0, +1), "left": (+1, 0), "right": (-1, 0)}
CLOCKWISE = ("up", "right", "down", "left")
LIGHT_ON = 40                # pull-up lights: a channel at this level or more (of 255) is on


def ball_directions(turn=0, mirror=False):
    """Which way the ball really rolled when each of the board's pins flips, with the board turned
    `turn` degrees clockwise in the case (seen from above), and left and right swapped if mirror."""
    quarter = (int(turn) // 90) % 4
    out = {}
    for i, pin_says in enumerate(CLOCKWISE):
        really = CLOCKWISE[(i + quarter) % 4]
        if mirror and really in ("left", "right"):
            really = "right" if really == "left" else "left"
        out[pin_says] = really
    return out


class Quadrature:
    """A rotary encoder's two contacts (A and B) -> clicks, ignoring bounce.

    Each change of A or B is a quarter step forward or back along the Gray code
    11 -> 01 -> 00 -> 10 -> 11 (forward) or the reverse; a jump straight to the opposite state
    (a missed change) counts as nothing. The steps add up, and only when the contacts reach a
    resting position (11, or 00 too for half-step encoders) does the total become a click: forward
    if it reached at least half a click's steps, back if at most minus half. So a contact
    chattering on its own goes back and forth and adds up to nothing, and a knob nudged without
    reaching the next detent does nothing."""

    FORWARD = {0b11: 0b01, 0b01: 0b00, 0b00: 0b10, 0b10: 0b11}
    BACK = {v: k for k, v in FORWARD.items()}

    def __init__(self, steps_per_click=STEPS_PER_CLICK, a=1, b=1):
        self.rests = {0b11} if steps_per_click >= 4 else {0b11, 0b00}
        self.need = max(1, steps_per_click // 2)
        self.state = (a << 1) | b
        self.total = 0

    def update(self, a, b):
        """The contacts are now a and b (1 = open, the pull-up's high). Returns +1, -1 or 0."""
        new = (int(bool(a)) << 1) | int(bool(b))
        if new == self.state:
            return 0
        if self.FORWARD[self.state] == new:
            self.total += 1
        elif self.BACK[self.state] == new:
            self.total -= 1
        self.state = new
        if new not in self.rests:
            return 0
        total, self.total = self.total, 0
        return 1 if total >= self.need else -1 if total <= -self.need else 0


class Debounced:
    """A button's raw contact changes (noted with their times, from any thread) -> clean presses
    and releases, each with the time it really happened. A change counts once the contact has
    stayed that way for DEBOUNCE_S. Fed late, it still works out the same answer."""

    def __init__(self, pressed=False, debounce_s=DEBOUNCE_S):
        self.debounce_s = debounce_s
        self.pressed = pressed           # the clean state
        self._raw = pressed
        self._since = 0.0
        self._lock = threading.Lock()
        self._changes = collections.deque()

    def feed(self, pressed, t):
        """The contact changed (gpiozero's thread)."""
        self._changes.append((bool(pressed), t))

    def poll(self, now):
        """[(pressed, t)], the clean changes since the last poll."""
        out = []
        with self._lock:
            while self._changes:
                pressed, t = self._changes.popleft()
                self._settle(t, out)
                if pressed != self._raw:
                    self._raw, self._since = pressed, t
            self._settle(now, out)
        return out

    def _settle(self, t, out):
        if self._raw != self.pressed and t - self._since >= self.debounce_s:
            self.pressed = self._raw
            out.append((self.pressed, self._since))


class Presses:
    """The three buttons' clean presses and releases -> what they mean (no GPIO here).

    Ball: released before HOLD_S is "ball-press" (controls.press_ball works out double presses),
    held that long is "ball-hold". Tune and Volume the same, "tune-press" / "tune-hold" and so on.
    Both knob buttons down together make a chord: "both-tap" when they're let go, or "both-hold"
    once both have been held HOLD_S; the single presses are then left out."""

    def __init__(self, hold_s=HOLD_S):
        self.hold_s = hold_s
        self._down = {}              # name -> when it went down
        self._held = set()           # long press already sent
        self._chord = None           # when both knob buttons were down together
        self._chord_held = False

    def change(self, name, pressed, t):
        out = []
        if pressed:
            self._down[name] = t
            other = {"tune": "volume", "volume": "tune"}.get(name)
            if other in self._down and other not in self._held and self._chord is None:
                self._chord, self._chord_held = t, False
            return out
        started = self._down.pop(name, None)
        if started is None:
            return out
        held = name in self._held
        self._held.discard(name)
        if self._chord is not None and name in ("tune", "volume"):
            if "tune" not in self._down and "volume" not in self._down:    # both let go
                if not self._chord_held:
                    out.append("both-tap")
                self._chord = None
            return out
        if not held:
            out.append(f"{name}-press")
        return out

    def tick(self, now):
        out = []
        if self._chord is not None:
            if not self._chord_held and "tune" in self._down and "volume" in self._down \
                    and now - self._chord >= self.hold_s:
                self._chord_held = True
                out.append("both-hold")
        for name, t in self._down.items():
            if name in self._held or (self._chord is not None and name in ("tune", "volume")):
                continue
            if now - t >= self.hold_s:
                self._held.add(name)
                out.append(f"{name}-hold")
        return out


class Rolls:
    """Trackball steps -> how far to roll this frame, faster for a flick, and smooth.

    The ball reports left-right and up-down as separate steps, so a diagonal roll arrives as
    right, up, right, up... Moving a whole step at once would make a staircase. Instead each step
    goes into a small store that drains GLIDE_S-smoothly over the next frames: a right step and an
    up step close together blend into one diagonal glide, and every roll looks smoother. The
    distance in all is the same as the steps."""

    def __init__(self):
        self._recent = collections.deque()    # times of recent steps
        self._east = self._north = 0.0        # still to move
        self._last = None

    @property
    def moving(self):
        return abs(self._east) + abs(self._north) > 0.01

    def total(self, steps, now):
        """steps: [(direction, t)] since the last frame. Returns (east, north) in ball steps to move
        this frame (call it every frame while moving, even with no new steps)."""
        east, north = self._add(steps)
        self._east += east
        self._north += north
        dt = 1 / 30 if self._last is None else max(0.0, now - self._last)
        self._last = now
        if not self.moving:
            self._east = self._north = 0.0
            return 0.0, 0.0
        share = 1 - math.exp(-dt / GLIDE_S)
        move = (self._east * share, self._north * share)
        self._east -= move[0]
        self._north -= move[1]
        return move

    def _add(self, steps):
        east = north = 0.0
        for direction, t in steps:
            self._recent.append(t)
            while self._recent and t - self._recent[0] > RATE_WINDOW_S:
                self._recent.popleft()
            rate = len(self._recent) / RATE_WINDOW_S
            gain = min(MAX_GAIN, max(1.0, rate / FLICK_RATE))
            de, dn = ROLL[direction]
            east += de * gain
            north += dn * gain
        return east, north


class Knobs:
    """The hardware: gpiozero pins in, events() out, set_light() to the ball."""

    def __init__(self, *, ball_turn=0, ball_mirror=False, lights="pullup", reverse_knobs=False,
                 steps_per_click=STEPS_PER_CLICK, factory=None, log=print, debug=False, clock=time.monotonic):
        from gpiozero import Device
        if factory is not None:
            Device.pin_factory = factory
        elif Device.pin_factory is None:
            Device.pin_factory = Device._default_pin_factory()   # noqa: SLF001 (gpiozero's pick: lgpio on the Pi)
        self.factory = Device.pin_factory
        self.clock, self.log, self.debug = clock, log, debug
        self._lock = threading.Lock()
        self._steps = []              # trackball (direction, t)
        self._clicks = {"tune": 0, "volume": 0}
        self._pins = []
        self._directions = ball_directions(ball_turn, ball_mirror)
        # The EC11s as wired in the pin map run B ahead of A when turned clockwise (bench, 2026-11-14),
        # so their clicks are flipped to make clockwise "up". knobs_reversed flips them back.
        self._sign = 1 if reverse_knobs else -1
        self._ball_quiet_until = 0.0  # pressing the ball wobbles it: no rolling around a press
        self._rolls = Rolls()
        self._presses = Presses()
        self._buttons = {}
        self._encoders = {}

        for pin_says, number in BALL_PINS.items():
            self._watch(number, self._ball_step(self._directions[pin_says]))
        for name, number in (("ball", BALL_BUTTON), ("tune", TUNE_BUTTON), ("volume", VOLUME_BUTTON)):
            button = Debounced()
            self._buttons[name] = button
            pin = self._watch(number, self._button_changed(button, ball=name == "ball"))
            button.pressed = button._raw = not pin.state       # noqa: SLF001 (pressed pulls it low)
        for name, (a, b) in (("tune", TUNE_PINS), ("volume", VOLUME_PINS)):
            self._watch_encoder(name, a, b, steps_per_click)
        self.lights = BallLights(self.factory, mode=lights, log=log)

    @classmethod
    def start(cls, settings, *, log=print, debug=False):
        """The knobs, set up from settings.json, or None (with a line saying why) where there are
        no Pi pins: on the laptop, or if gpiozero isn't installed."""
        try:
            import gpiozero  # noqa: F401
        except ImportError:
            log("Knobs: gpiozero isn't installed (pip install gpiozero lgpio), so no trackball or knobs.")
            return None
        try:
            return cls(ball_turn=settings.get("ball_turn", 0), ball_mirror=settings.get("ball_mirror", False),
                       lights=settings.get("ball_lights", "pullup"),
                       reverse_knobs=settings.get("knobs_reversed", False), log=log, debug=debug)
        except Exception as e:      # no GPIO here, or a pin in use: the radio still runs, from the keys
            log(f"Knobs: no trackball or knobs ({type(e).__name__}: {e}).")
            return None

    # --- pins (gpiozero's thread) -----------------------------------------------

    def _watch(self, number, on_change):
        """An input pin with its pull-up on; on_change(level, t) on every change, either way."""
        pin = self.factory.pin(number)
        pin.function = "input"
        pin.pull = "up"
        pin.bounce = None
        pin.edges = "both"
        clock = self.clock

        def changed(_ticks, state):
            on_change(pin.state if state is None else state, clock())
        pin.when_changed = changed
        # gpiozero keeps only a weak reference to the callback: without this one it would be
        # thrown away at once, and the pin would quietly do nothing.
        self._pins.append((pin, changed))
        return pin

    def _ball_step(self, direction):
        last = {}

        def step(level, t):
            if last.get("level") == level:
                return                      # the same level again: not a flip
            last["level"] = level
            with self._lock:
                if t < self._ball_quiet_until:
                    return                  # the finger pressing the ball, not a roll
                self._steps.append((direction, t))
        return step

    def _button_changed(self, button, ball=False):
        def changed(level, t):
            if ball:
                with self._lock:
                    # Held down: no rolling until let go (+inf); then a moment more for the finger.
                    self._ball_quiet_until = float("inf") if not level else t + BALL_QUIET_S
            button.feed(not level, t)
        return changed

    def _watch_encoder(self, name, a, b, steps_per_click):
        levels = {}
        decoder = None

        def contact(which):
            def changed(level, _t):
                with self._lock:
                    levels[which] = 1 if level else 0
                    click = decoder.update(levels["a"], levels["b"])
                    if click:
                        self._clicks[name] += click * self._sign
            return changed
        pin_a = self._watch(a, contact("a"))
        pin_b = self._watch(b, contact("b"))
        levels["a"], levels["b"] = int(pin_a.state), int(pin_b.state)
        decoder = Quadrature(steps_per_click, levels["a"], levels["b"])
        self._encoders[name] = decoder

    # --- the main loop's side ------------------------------------------------------

    def events(self):
        now = self.clock()
        with self._lock:
            steps, self._steps = self._steps, []
            clicks = dict(self._clicks)
            self._clicks = {"tune": 0, "volume": 0}
        out = []
        if steps or self._rolls.moving:
            east, north = self._rolls.total(steps, now)
            if east or north:
                out.append(("roll", east, north))
        for name in ("tune", "volume"):
            if clicks[name]:
                out.append((name, clicks[name]))
        for name, button in self._buttons.items():
            for pressed, t in button.poll(now):
                out += self._presses.change(name, pressed, t)
        out += self._presses.tick(now)
        if self.debug and out:
            for event in out:
                self.log(f"  knobs: {event if isinstance(event, str) else ' '.join(map(str, event))}")
            if steps:
                self.log("  ball: " + " ".join(d for d, _ in steps))
        return out

    def set_light(self, rgbw):
        self.lights.set(rgbw)

    def close(self):
        self.lights.close()
        for pin, _callback in self._pins:
            try:
                pin.when_changed = None
                pin.close()
            except Exception:
                pass


class BallLights:
    """The ball's white, red, green and blue LEDs (active-high).

    mode "pullup": each lit only through its pin's weak internal pull-up, on or off (safe without
                   knowing the LEDs' resistors; the pin is never driven).
    mode "pwm":    driven, dimmable (gpiozero PWMLED). Only once the resistors are confirmed.
    mode "off":    left dark."""

    ORDER = ("red", "green", "blue", "white")       # ball_light's (r, g, b, w)

    def __init__(self, factory, mode="pullup", log=print):
        self.mode = mode if mode in ("pullup", "pwm", "off") else "pullup"
        self._shown = None
        self._pins, self._leds = {}, {}
        try:
            if self.mode == "pwm":
                from gpiozero import PWMLED
                self._leds = {name: PWMLED(LIGHT_PINS[name], frequency=200) for name in self.ORDER}
            else:
                for name in self.ORDER:
                    pin = factory.pin(LIGHT_PINS[name])
                    pin.function = "input"            # never an output in this mode
                    pin.pull = "down"
                    self._pins[name] = pin
        except Exception as e:
            log(f"Ball lights: off ({type(e).__name__}: {e}).")
            self.mode, self._pins, self._leds = "off", {}, {}

    def set(self, rgbw):
        if self.mode == "off" or rgbw == self._shown:
            return
        if self.mode == "pullup":
            on = tuple(v >= LIGHT_ON for v in rgbw)
            if self._shown is not None and on == tuple(v >= LIGHT_ON for v in self._shown):
                self._shown = rgbw
                return
            for name, lit in zip(self.ORDER, on):
                self._pins[name].pull = "up" if lit else "down"
        else:
            for name, v in zip(self.ORDER, rgbw):
                self._leds[name].value = max(0.0, min(1.0, v / 255))
        self._shown = rgbw

    def close(self):
        for pin in self._pins.values():
            try:
                pin.pull = "down"
                pin.close()
            except Exception:
                pass
        for led in self._leds.values():
            try:
                led.close()
            except Exception:
                pass
