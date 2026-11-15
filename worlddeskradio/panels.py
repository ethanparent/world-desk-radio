"""Cards the radio shows over the map: the settings menu (holding both knob buttons), the
Bluetooth menu, Wi-Fi setup, the phone remote's QR code, and the alarm going off.

Each one is driven by Controls while it's open:

    panel.open() / close()
    panel.move(clicks)     the Tune knob turns
    panel.press()          the Tune knob is pressed
    panel.done             True once it has closed itself
    panel.view()           -> face_link.Panel: what the face draws

The Volume knob's press closes a panel (calling its back() first, if it has one), and so does
rolling or pressing the ball.
"""

import random
import threading
import time

from .face_link import Panel, cut

DETAILS = {"connected": "connected", "connecting": "connecting…", "disconnecting": "disconnecting…",
           "failed": "couldn't connect"}


class BluetoothMenu:
    """Nearby headphones and speakers. Turn Tune to choose, press it to connect (or, on the one
    playing, to disconnect). Closes itself after IDLE_S untouched."""

    IDLE_S = 30

    def __init__(self, bluetooth, clock=time.monotonic):
        self.bluetooth = bluetooth
        self.clock = clock
        self.is_open = False
        self._chosen = None       # address of the highlighted device (rows move as devices turn up)
        self._touched = 0.0

    def open(self):
        self.bluetooth.start_scan()
        self.is_open, self._chosen, self._touched = True, None, self.clock()

    def close(self):
        self.bluetooth.stop_scan()
        self.is_open = False

    @property
    def done(self):
        if self.is_open and self.clock() - self._touched > self.IDLE_S:
            self.close()
        return not self.is_open

    def _rows(self):
        """Connected first, then paired, then the rest in the order they turned up."""
        rank = {"connected": 0, "connecting": 0, "disconnecting": 0}
        return sorted(self.bluetooth.devices(), key=lambda d: rank.get(d.state, 1 if d.paired else 2))

    def _index(self, rows):
        for i, d in enumerate(rows):
            if d.address == self._chosen:
                return i
        return 0 if rows else -1

    def move(self, clicks):
        self._touched = self.clock()
        rows = self._rows()
        if rows:
            i = min(len(rows) - 1, max(0, self._index(rows) + clicks))
            self._chosen = rows[i].address

    def press(self):
        self._touched = self.clock()
        rows = self._rows()
        i = self._index(rows)
        if i < 0:
            return
        device = rows[i]
        self._chosen = device.address
        if device.state == "connected":
            self.bluetooth.disconnect(device.address)
        elif device.state not in ("connecting", "disconnecting"):
            self.bluetooth.connect(device.address)

    def view(self):
        rows = self._rows()
        if not rows:
            return Panel("Bluetooth", lines=("Looking for headphones", "and speakers…"),
                         notes=("Turn them on and make them", "ready to pair"))
        i = self._index(rows)
        items = tuple((cut(d.name), DETAILS.get(d.state) or ("paired" if d.paired else d.kind))
                      for d in rows)
        action = "disconnect" if rows[i].state == "connected" else "connect"
        return Panel("Bluetooth", items=items, sel=i,
                     notes=(f"Turn Tune to choose, press to {action}", "Press Volume to close"))


class SettingsMenu:
    """What holding both knob buttons opens: a short list of the radio's settings cards
    (Bluetooth, the phone remote, Wi-Fi setup). Turn Tune to choose, press it to open that card
    in this one's place. Closes itself after IDLE_S untouched.

    entries: (label, open) pairs, or (label, open, detail) where detail() gives the row's
    right-hand text ("" for none)."""

    IDLE_S = 30

    def __init__(self, entries, clock=time.monotonic):
        self.entries = [tuple(e) + (None,) * (3 - len(e)) for e in entries]
        self.clock = clock
        self.is_open = False
        self.sel = 0
        self._touched = 0.0

    def open(self):
        self.is_open, self.sel, self._touched = True, 0, self.clock()

    def close(self):
        self.is_open = False

    @property
    def done(self):
        if self.is_open and self.clock() - self._touched > self.IDLE_S:
            self.close()
        return not self.is_open

    def move(self, clicks):
        self._touched = self.clock()
        self.sel = min(len(self.entries) - 1, max(0, self.sel + clicks))

    def press(self):
        """Open the chosen card. It takes this one's place (Controls.open_panel closes this)."""
        self._touched = self.clock()
        if self.entries:
            self.entries[self.sel][1]()

    def view(self):
        items = tuple((cut(label), (detail() if detail else "") or "") for label, _, detail in self.entries)
        return Panel("Settings", items=items, sel=self.sel,
                     notes=("Turn Tune to choose, press to open", "Press Volume to close"))


class WorldMenu:
    """The World card, from the settings menu: Earth and the game worlds installed (worlds.py).
    Turn Tune to choose, press it to go there (the radio starts again in that world). Closes
    itself after IDLE_S untouched.

    worlds: [(id, title)]; current: the id of the world the radio is in; on_choose(id)."""

    IDLE_S = 30

    def __init__(self, worlds, current, on_choose, clock=time.monotonic):
        self.worlds, self.current, self.on_choose = list(worlds), current, on_choose
        self.clock = clock
        self.is_open = False
        self.sel = 0
        self._touched = 0.0

    def open(self):
        ids = [w for w, _ in self.worlds]
        self.is_open, self._touched = True, self.clock()
        self.sel = ids.index(self.current) if self.current in ids else 0

    def close(self):
        self.is_open = False

    @property
    def done(self):
        if self.is_open and self.clock() - self._touched > self.IDLE_S:
            self.close()
        return not self.is_open

    def move(self, clicks):
        self._touched = self.clock()
        self.sel = min(len(self.worlds) - 1, max(0, self.sel + clicks))

    def press(self):
        self._touched = self.clock()
        if self.worlds:
            self.on_choose(self.worlds[self.sel][0])

    def view(self):
        items = tuple((cut(title), "here" if w == self.current else "") for w, title in self.worlds)
        return Panel("World", items=items, sel=self.sel,
                     notes=("Turn Tune to choose, press to go", "Press Volume to close"))


class AlarmCard:
    """The alarm going off. The Volume button snoozes it (back()); the Tune button turns it off, and
    so does anything else that closes the card. After RING_S untouched it closes by itself, and
    the station plays on."""

    RING_S = 30 * 60

    def __init__(self, on_snooze, on_stop, clock=time.monotonic):
        self.on_snooze, self.on_stop = on_snooze, on_stop
        self.clock = clock
        self.label = ""               # the alarm's time, "7:00 am"
        self.is_open = False
        self._opened, self._snoozed = 0.0, False

    def open(self):
        self.is_open, self._opened, self._snoozed = True, self.clock(), False

    def close(self):
        if self.is_open:
            self.is_open = False
            if not self._snoozed:
                self.on_stop()

    def back(self):
        self._snoozed = True
        self.on_snooze()

    @property
    def done(self):
        if self.is_open and self.clock() - self._opened > self.RING_S:
            self.close()
        return not self.is_open

    def move(self, clicks):
        pass

    def press(self):
        self.close()

    def view(self):
        return Panel("Good morning", lines=(self.label,),
                     notes=("Press Volume to snooze 9 minutes", "Press Tune to turn it off"))


class PhoneRemote:
    """A QR code for the phone page, so a phone can be the radio's remote: point the camera at
    it and the page opens. Closes itself after IDLE_S."""

    IDLE_S = 60

    def __init__(self, url, clock=time.monotonic):
        self.url = url
        self.clock = clock
        self.is_open = False
        self._opened = 0.0

    def open(self):
        self.is_open, self._opened = True, self.clock()

    def close(self):
        self.is_open = False

    @property
    def done(self):
        if self.is_open and self.clock() - self._opened > self.IDLE_S:
            self.close()
        return not self.is_open

    def move(self, clicks):
        pass

    def press(self):
        pass

    def view(self):
        address = self.url.split("://", 1)[-1].rstrip("/")
        local = address.startswith(("127.", "localhost"))
        return Panel("Phone remote", lines=("Scan with your phone's camera",), qr=self.url,
                     notes=(address, "Laptop: start with --lan for a phone" if local
                            else "Your phone must be on the same Wi-Fi"))


class WifiSetup:
    """Wi-Fi from a phone. The radio opens its own network, HOTSPOT, with a fresh password,
    shows a QR code that joins it, and serves a page (setup_page.py) where you pick your Wi-Fi
    and type its password with the phone's keyboard. Thread-safe: the page calls in from the
    web server's thread."""

    HOTSPOT = "World Desk Radio setup"
    CONNECTED_S = 3.0         # "Connected" stays up this long, then the card closes
    FAILED_S = 4.0            # "Couldn't connect" stays up this long, then back to the QR code

    def __init__(self, wifi, clock=time.monotonic):
        self.wifi = wifi
        self.clock = clock
        self.is_open = False
        self.password = ""
        self._lock = threading.Lock()
        self._phase, self._ssid, self._since = "off", None, 0.0

    def open(self):
        with self._lock:
            self.password = "".join(random.choice("0123456789") for _ in range(8))
            self.wifi.start_hotspot(self.HOTSPOT, self.password)
            self.is_open, self._phase, self._ssid = True, "waiting", None

    def close(self):
        with self._lock:
            self.wifi.stop_hotspot()
            self.is_open, self._phase = False, "off"

    @property
    def done(self):
        self._advance()
        return not self.is_open

    def move(self, clicks):
        pass

    def press(self):
        pass

    # --- for the phone page ------------------------------------------------

    def networks(self):
        return self.wifi.scan()

    def connect(self, ssid, password):
        """Returns False if setup isn't running or the network isn't one we can see."""
        with self._lock:
            if not self.is_open or self._phase == "connecting":
                return False
            if not any(n.ssid == ssid for n in self.wifi.scan()):
                return False
            self.wifi.connect(ssid, password)
            self._phase, self._ssid, self._since = "connecting", ssid, self.clock()
            return True

    def status(self):
        self._advance()
        with self._lock:
            return {"phase": self._phase, "ssid": self._ssid}

    # --- the card on the face ----------------------------------------------

    def _advance(self):
        with self._lock:
            now = self.clock()
            if self._phase == "connecting":
                phase, _ = self.wifi.status()
                if phase in ("connected", "failed"):
                    self._phase, self._since = phase, now
            elif self._phase == "connected" and now - self._since >= self.CONNECTED_S:
                self.wifi.stop_hotspot()
                self.is_open, self._phase = False, "off"
            elif self._phase == "failed" and now - self._since >= self.FAILED_S:
                self._phase = "waiting"

    def qr_text(self):
        """A Wi-Fi QR code: phones join the network when their camera sees it."""
        return f"WIFI:T:WPA;S:{self.HOTSPOT};P:{self.password};;"

    def view(self):
        self._advance()
        ssid = cut(self._ssid or "")
        if self._phase == "connecting":
            return Panel("Wi-Fi", lines=("Connecting to", ssid), notes=("This takes a few seconds",))
        if self._phase == "connected":
            return Panel("Wi-Fi", lines=("Connected to", ssid), notes=("Back to the radio…",))
        if self._phase == "failed":
            return Panel("Wi-Fi", lines=("Couldn't connect to", ssid),
                         notes=("Check the password", "on your phone and try again"))
        return Panel("Wi-Fi setup", lines=("Scan with your phone's camera",), qr=self.qr_text(),
                     notes=(f"or join “{self.HOTSPOT}”",
                            f"password {self.password[:4]} {self.password[4:]}"))
