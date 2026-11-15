"""The Pi's own parts, without the Pi: the trackball and knobs (knobs.py: the encoder decoding,
button presses, holds and the two-button chord, and, if gpiozero is installed, the whole thing on
gpiozero's pretend pins), the system mixer (mixer.py), and real Bluetooth and Wi-Fi
(connectivity.py) answering to made-up bluetoothctl and nmcli output.

    python tests/check_pi.py

Stops at the first check that fails, with exit code 1.
"""

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from terradio import knobs as kn                                         # noqa: E402
from terradio.connectivity import BluezBluetooth, NetworkManagerWiFi, _split_terse  # noqa: E402
from terradio.mixer import SystemMixer                                   # noqa: E402
from terradio.knobs import Debounced, Presses, Quadrature, ball_directions  # noqa: E402

FORWARD = [(0, 1), (0, 0), (1, 0), (1, 1)]          # one detent forward, from rest at 11
BACK = [(1, 0), (0, 0), (0, 1), (1, 1)]


def turn(q, seq):
    return sum(q.update(a, b) for a, b in seq)


def check(ok, what):
    if not ok:
        print(f"FAILED: {what}")
        sys.exit(1)
    print(f"ok  {what}")


def check_quadrature():
    q = Quadrature()
    check(turn(q, FORWARD) == 1, "a clean step forward is one click")
    check(turn(q, BACK) == -1, "a clean step back is minus one")
    check(turn(q, FORWARD * 5) == 5, "five detents are five clicks")
    bouncy = [(0, 1), (1, 1), (0, 1), (1, 1), (0, 1), (0, 0), (0, 1), (0, 0), (1, 0), (1, 1), (1, 0), (1, 1)]
    check(turn(q, bouncy) == 1, "a step with heavy bounce on both contacts is still one click")
    check(turn(q, [(0, 1), (1, 1), (0, 1), (1, 1)]) == 0, "chatter on one contact at rest is nothing")
    check(turn(q, [(0, 1), (0, 0), (0, 1), (1, 1)]) == 0, "half a step and back is nothing")
    q = Quadrature()
    check(turn(q, [(0, 1), (0, 0), (1, 1)]) == 1, "a quick step missing its last change still counts")
    half = Quadrature(steps_per_click=2)
    check(turn(half, [(0, 1), (0, 0)]) == 1 and turn(half, [(1, 0), (1, 1)]) == 1,
          "half-step encoders click at 00 and at 11")


def check_debounce():
    d = Debounced()
    for i, level in enumerate([1, 0, 1, 0, 1]):            # 5 ms of bounce on the way down
        d.feed(bool(level), 1.000 + i * 0.001)
    check(d.poll(1.010) == [], "a bouncing press isn't counted until it settles")
    out = d.poll(1.030)
    check(out == [(True, 1.004)], f"then it's one press, dated when it settled ({out})")
    d.feed(False, 1.200)
    d.feed(True, 1.201)
    d.feed(False, 1.203)
    check(d.poll(1.300) == [(False, 1.203)], "a bouncing release is one release")
    late = Debounced()
    late.feed(True, 2.0)
    late.feed(False, 2.1)
    check(late.poll(3.0) == [(True, 2.0), (False, 2.1)], "a tap read late still comes out as press and release")


def check_presses():
    p = Presses()
    check(p.change("ball", True, 0.0) == [] and p.change("ball", False, 0.2) == ["ball-press"],
          "a short ball press is ball-press")
    p.change("ball", True, 1.0)
    check(p.tick(1.5) == [] and p.tick(1.61) == ["ball-hold"], "held 0.6 s it's ball-hold, sent once")
    check(p.tick(2.0) == [] and p.change("ball", False, 2.5) == [], "and letting go adds nothing")
    p.change("tune", True, 3.0)
    p.change("volume", True, 3.1)
    check(p.change("volume", False, 3.3) == [] and p.change("tune", False, 3.35) == ["both-tap"],
          "tune and volume together, let go: both-tap, no single presses")
    p.change("tune", True, 4.0)
    p.change("volume", True, 4.05)
    check(p.tick(4.5) == [] and p.tick(4.7) == ["both-hold"], "held together: both-hold")
    check(p.tick(5.5) == [] and p.change("tune", False, 5.6) == [] and p.change("volume", False, 5.7) == [],
          "no tune-hold or volume-hold after it, and nothing on letting go")
    p.change("volume", True, 6.0)
    check(p.tick(6.7) == ["volume-hold"], "volume held alone: volume-hold (standby)")
    p.change("volume", False, 7.0)
    check(p.change("tune", True, 8.0) == [] and p.change("tune", False, 8.1) == ["tune-press"],
          "tune tapped alone: tune-press")


def check_directions():
    check(ball_directions(0) == {"up": "up", "right": "right", "down": "down", "left": "left"},
          "no turn: each pin is its own way")
    check(ball_directions(90)["up"] == "right" and ball_directions(90)["left"] == "up",
          "a quarter turn clockwise: the board's up pin is really right")
    check(ball_directions(270)["up"] == "left", "three quarters: up is left")
    check(ball_directions(0, mirror=True)["left"] == "right", "mirror swaps left and right")


def glide(r, steps, start, frames=30):
    """All the movement from these steps, frame by frame at 30 a second: [(east, north)]."""
    moves = [r.total(steps, start)]
    for i in range(1, frames):
        moves.append(r.total([], start + i / 30))
    return moves


def check_rolls():
    r = kn.Rolls()
    moves = glide(r, [("right", 0.0)], 0.0)
    east = sum(m[0] for m in moves)
    check(abs(east + 1) < 0.02 and all(m[1] == 0 for m in moves),
          f"one slow step right moves one step in all (the crosshair west): {east:.2f}")
    check(-0.6 < moves[0][0] < 0, f"...gliding in over a few frames, not all at once ({moves[0][0]:.2f} first)")
    r = kn.Rolls()
    moves = [r.total([("right", 2.0)], 2.0), r.total([("up", 2.04)], 2.04), r.total([("right", 2.07)], 2.07),
             r.total([("up", 2.1)], 2.1)] + glide(r, [], 2.13)[1:]
    mixed = sum(1 for e, n in moves[1:6] if e and n)
    check(mixed >= 4, f"right, up, right, up: the frames move both ways at once, a diagonal ({mixed} of 5)")
    r = kn.Rolls()
    steps = [("up", 1.0 + i * 0.01) for i in range(12)]           # 100 steps a second: a flick
    north = sum(m[1] for m in glide(r, steps, 1.12))
    check(north < -12 * 1.5, f"a flick moves further than its steps ({north:.1f})")


def check_on_mock_pins():
    try:
        from gpiozero.pins.mock import MockFactory, MockPWMPin
    except ImportError:
        print("--  gpiozero isn't installed here: skipping the pretend-pin check")
        return
    import time as _time
    now = [100.0]
    factory = MockFactory(pin_class=MockPWMPin)
    k = kn.Knobs(factory=factory, clock=lambda: now[0], log=lambda *a: None)
    pins = {n: factory.pin(n) for n in (5, 6, 13, 16, 17, 22, 23, 24, 25, 26, 27)}
    check(all(p.state for p in pins.values()), "inputs rest high on their pull-ups")

    def encoder(a, b, seq):
        for la, lb in seq:
            (pins[a].drive_high if la else pins[a].drive_low)()
            (pins[b].drive_high if lb else pins[b].drive_low)()

    # Drive A then B for each Gray step: only one of them actually changes each time.
    encoder(5, 6, FORWARD * 3)
    encoder(17, 27, BACK * 2)
    events = k.events()
    # On the real knobs A-then-B is counter-clockwise (bench, 2026-11-14), so it counts down.
    check(("tune", -3) in events and ("volume", 2) in events, f"encoders on pins: tune -3, volume +2 ({events})")

    pins[24].drive_low()
    pins[24].drive_high()
    events = k.events()
    check(events and events[0][0] == "roll" and events[0][1] < 0, f"ball right pin flips: a roll ({events})")
    for _ in range(10):                  # let that roll glide to its end
        now[0] += 0.1
        k.events()

    pins[25].drive_low()
    now[0] += 0.1
    k.events()
    pins[24].drive_low()                 # the finger wobbles the ball while pressing it
    pins[25].drive_high()
    now[0] += 0.1
    check(k.events() == ["ball-press"], "ball button tapped: ball-press, and the wobble isn't a roll")
    now[0] += 0.5
    pins[24].drive_high()
    check(k.events()[0][0] == "roll", "a moment after letting go, rolling counts again")

    pins[13].drive_low()
    pins[22].drive_low()
    now[0] += 0.1
    k.events()
    now[0] += 0.7
    check(k.events() == ["both-hold"], "both knob buttons held: both-hold")
    pins[13].drive_high()
    pins[22].drive_high()
    now[0] += 0.1
    check(k.events() == [], "letting go after the hold adds nothing")

    lights = {name: factory.pin(n) for name, n in kn.LIGHT_PINS.items()}
    k.set_light((200, 0, 0, 0))
    check(lights["red"].pull == "up" and lights["green"].pull == "down" and lights["red"].function == "input",
          "pull-up lights: red lit by its pull-up, the pin stays an input")
    k.set_light((0, 0, 0, 0))
    check(lights["red"].pull == "down", "and dark again by the pull-down")
    k.close()
    _time.sleep(0)


def wait_for(condition, seconds=2.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.01)
    return condition()


def check_mixer():
    applied = []

    def slow_wpctl(level):
        time.sleep(0.05)                 # wpctl is slow on a Pi Zero
        applied.append(level)
    m = SystemMixer(run=slow_wpctl)
    for v in range(40, 80, 2):           # the knob turned quickly
        m.set(v)
    check(wait_for(lambda: applied and applied[-1] == 78), f"the mixer ends at the last level ({applied[-1:]})")
    check(len(applied) < 10, f"...without running wpctl for every click ({len(applied)} runs for 20 clicks)")
    applied.clear()
    m.fade_out(0.4)
    check(wait_for(lambda: applied and applied[-1] == 0), "fade_out ends silent")
    check(len(applied) >= 3 and applied == sorted(applied, reverse=True),
          f"...stepping down on the way ({applied})")
    m.set(80)
    check(wait_for(lambda: applied[-1] == 80), "set() brings it back")
    applied.clear()
    m.fade_out(1.0)                      # the sleep timer's long fade ...
    time.sleep(0.5)
    m.fade_out(0.3)                      # ... then standby's short one, halfway through
    check(wait_for(lambda: applied and applied[-1] == 0) and max(applied[1:] or [0]) <= applied[0] <= 80
          and applied == sorted(applied, reverse=True),
          f"a second fade carries on down from where the first had got to ({applied})")
    m.set(55)
    check(wait_for(lambda: applied[-1] == 55), "and set() brings it back")
    n = len(applied)
    m.refresh()
    check(wait_for(lambda: len(applied) > n) and applied[-1] == 55, "refresh() applies it again (headphones came)")
    m.close()


class FakeCommands:
    """Answers commands from a table of (first words) -> (exit code, output), and notes them."""

    def __init__(self, answers):
        self.answers, self.ran = answers, []

    def __call__(self, args, timeout=20):
        self.ran.append(args)
        for words, answer in self.answers.items():
            if tuple(args[:len(words)]) == words:
                return answer() if callable(answer) else answer
        return 0, ""


def check_bluetooth():
    state = {"connected": ""}

    def connect():
        time.sleep(0.2)                  # connecting takes a moment
        state["connected"] = "Device AA:BB:CC:DD:EE:01 Flip 6\n"
        return 0, "Connection successful"
    run = FakeCommands({
        ("bluetoothctl", "devices", "Paired"): (0, "Device 11:22:33:44:55:66 WH-1000XM5\n"),
        ("bluetoothctl", "devices", "Connected"): lambda: (0, state["connected"]),
        ("bluetoothctl", "devices"): (0, "Device 11:22:33:44:55:66 WH-1000XM5\nDevice AA:BB:CC:DD:EE:01 Flip 6\n"
                                         "Device AA:BB:CC:DD:EE:02 Pixel 8\nDevice AA:BB:CC:DD:EE:03 AA-BB-CC-DD-EE-03\n"),
        ("bluetoothctl", "info", "11:22:33:44:55:66"): (0, "Name: WH-1000XM5\n\tIcon: audio-headset\n\tPaired: yes\n"),
        ("bluetoothctl", "info", "AA:BB:CC:DD:EE:01"): (0, "Name: Flip 6\n\tIcon: audio-card\n\tPaired: no\n"),
        ("bluetoothctl", "info", "AA:BB:CC:DD:EE:02"): (0, "Name: Pixel 8\n\tIcon: phone\n"),
        ("bluetoothctl", "info", "AA:BB:CC:DD:EE:03"): (0, "\tIcon: audio-card\n"),
        ("bluetoothctl", "connect"): connect,
    })
    bt = BluezBluetooth(run=run, start=False)
    bt._refresh()                                              # noqa: SLF001
    names = {d.name: d for d in bt.devices()}
    check(set(names) == {"WH-1000XM5"}, f"not searching: only the paired headphones show ({sorted(names)})")
    bt._scan = type("Scan", (), {"poll": lambda self: None})()  # noqa: SLF001 (searching)
    names = {d.name: d for d in bt.devices()}
    check(set(names) == {"WH-1000XM5", "Flip 6"},
          f"searching: audio devices only, no phone, none without a name ({sorted(names)})")
    check(names["WH-1000XM5"].kind == "headphones" and names["Flip 6"].kind == "speaker"
          and names["WH-1000XM5"].paired, "headphones and speakers told apart, paired marked")
    bt.connect("AA:BB:CC:DD:EE:01")
    check(next(d for d in bt.devices() if d.name == "Flip 6").state == "connecting", "connecting... shows at once")
    check(wait_for(lambda: bt.connected is not None and bt.connected.name == "Flip 6"), "then connected")
    ran = [" ".join(a[1:3]) for a in run.ran]
    check("--agent NoInputNoOutput" in ran and "trust AA:BB:CC:DD:EE:01" in ran,
          "a new speaker is paired (no PIN) and trusted before connecting")
    bt._scan = None                                             # noqa: SLF001


def check_wifi():
    tries = []

    def connect():
        time.sleep(0.2)
        tries.append(1)
        return (10, "Error: Secrets were required") if len(tries) == 1 else (0, "successfully activated")
    run = FakeCommands({
        ("nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY"): (0, "Home:82:WPA2\nHome:40:WPA2\nCafe\\:Guest:30:\n:20:WPA2\n"),
        ("nmcli", "-t", "-f", "NAME"): (0, "preconfigured\n"),
        ("nmcli", "device", "wifi", "connect"): connect,
        ("nmcli", "device", "wifi", "hotspot"): (4, "Error: Not authorized to control networking."),
        ("sudo", "-n", "nmcli", "device", "wifi", "hotspot"): (0, "Device 'wlan0' successfully activated"),
    })
    check(_split_terse("Cafe\\:Guest:30:") == ["Cafe:Guest", "30", ""], "nmcli's escaped colons are read right")
    w = NetworkManagerWiFi(run=run, start=False)
    nets = w.scan()
    check([n.ssid for n in nets] == ["Home", "Cafe:Guest"] and nets[0].signal == 82 and not nets[1].secure,
          f"networks: strongest of each, no hidden ones, open ones marked ({nets})")
    w.start_hotspot("Terradio setup", "12345678")
    check(wait_for(lambda: any(a[:3] == ["sudo", "-n", "nmcli"] for a in run.ran)),
          "the hotspot goes through sudo when nmcli isn't allowed")
    before = len(run.ran)
    check(w.scan() == nets and len(run.ran) == before, "while the hotspot is up, the list is the one from before")
    w.connect("Home", "wrong")
    check(w.status() == ("connecting", "Home"), "connecting...")
    check(wait_for(lambda: w.status()[0] == "failed"), "a wrong password fails")
    check(wait_for(lambda: any("delete" in a for a in run.ran)),
          "...its new profile is deleted, so the wrong password isn't kept")
    check(wait_for(lambda: sum("hotspot" in a for a in run.ran) >= 4), "...and the hotspot comes back for another try")
    w.connect("Home", "right")
    check(wait_for(lambda: w.status() == ("connected", "Home")) and w.hotspot is None, "the right one connects")


def main():
    check_quadrature()
    check_debounce()
    check_presses()
    check_directions()
    check_rolls()
    check_on_mock_pins()
    check_mixer()
    check_bluetooth()
    check_wifi()
    print("All Pi checks passed.")


if __name__ == "__main__":
    main()
