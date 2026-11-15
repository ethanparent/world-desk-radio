"""Bluetooth and Wi-Fi: what the radio's menus need from them.

    bluetooth.start_scan() / stop_scan()
    bluetooth.devices()            -> [Device]: nearby and paired headphones and speakers
    bluetooth.connect(address) / disconnect(address)
    bluetooth.connected            -> the Device playing the sound, or None

    wifi.scan()                    -> [Network], strongest first
    wifi.connect(ssid, password)   (takes a few seconds: watch wifi.status())
    wifi.status()                  -> (phase, ssid): idle | connecting | connected | failed
    wifi.start_hotspot(name, password) / stop_hotspot()

On the Pi, BluezBluetooth and NetworkManagerWiFi talk to BlueZ (through bluetoothctl) and
NetworkManager (through nmcli). On the laptop, FakeBluetooth and FakeWiFi stand in: made-up
headphones and networks that behave like real ones (they take a moment to turn up, and to connect).

The Pi's Wi-Fi also has:
    wifi.online                    -> True / False once known: joined to a network?
"""

import shutil
import subprocess
import threading
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class Device:
    address: str
    name: str
    kind: str                 # headphones | speaker
    paired: bool = False
    state: str = ""           # "" | connecting | connected | disconnecting | failed


@dataclass(frozen=True)
class Network:
    ssid: str
    signal: int               # 0-100
    secure: bool              # needs a password


class FakeBluetooth:
    """Pretend Bluetooth: devices turn up one by one while searching, connecting takes 2 s,
    and one of them never manages it (to show what failing looks like)."""

    DEVICES = (("Sony WH-1000XM5", "headphones"), ("JBL Flip 6", "speaker"),
               ("AirPods Pro", "headphones"), ("Bose QuietComfort 45", "headphones"),
               ("UE Boom 3", "speaker"))
    FAILS = "Bose QuietComfort 45"
    FOUND_EVERY_S = 0.8
    CONNECT_S = 2.0

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self._found = {}          # address -> when it turned up
        self._paired = set()
        self._connected = None
        self._busy = None         # (address, "connecting" | "disconnecting", until)
        self._failed = set()
        self._scan_from = None

    def _address(self, i):
        return f"58:2A:F7:{i:02X}:3C:91"

    def start_scan(self):
        self._scan_from = self.clock()

    def stop_scan(self):
        self._scan_from = None

    def _advance(self):
        now = self.clock()
        if self._scan_from is not None:
            for i in range(len(self.DEVICES)):
                if now >= self._scan_from + (i + 1) * self.FOUND_EVERY_S:
                    self._found.setdefault(self._address(i), now)
        if self._busy and now >= self._busy[2]:
            address, action, _ = self._busy
            self._busy = None
            if action == "disconnecting":
                self._connected = None
            elif self._name(address) == self.FAILS:
                self._failed.add(address)
            else:
                self._paired.add(address)
                self._connected = address

    def _name(self, address):
        return self.DEVICES[int(address.split(":")[3], 16)][0]

    def devices(self):
        self._advance()
        out = []
        for i, (name, kind) in enumerate(self.DEVICES):
            address = self._address(i)
            if address not in self._found and address not in self._paired:
                continue
            if self._busy and self._busy[0] == address:
                state = self._busy[1]
            elif address == self._connected:
                state = "connected"
            elif address in self._failed:
                state = "failed"
            else:
                state = ""
            out.append(Device(address, name, kind, address in self._paired, state))
        return out

    def connect(self, address):
        self._advance()
        self._failed.discard(address)
        if self._connected and self._connected != address:
            self._connected = None            # one at a time, like the real radio
        self._busy = (address, "connecting", self.clock() + self.CONNECT_S)

    def disconnect(self, address):
        self._advance()
        self._busy = (address, "disconnecting", self.clock() + 0.6)

    @property
    def connected(self):
        return next((d for d in self.devices() if d.state == "connected"), None)


class FakeWiFi:
    """Pretend Wi-Fi: a few networks nearby. The ones with a password accept PASSWORD."""

    NETWORKS = (("Home Wi-Fi", 82, True), ("Home Wi-Fi 5G", 74, True), ("NETGEAR-2F7A", 51, True),
                ("TelstraA1B2", 38, True), ("Cafe Guest", 30, False))
    PASSWORD = "terradio"
    CONNECT_S = 2.5

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self._phase, self._ssid, self._until, self._ok = "idle", None, 0.0, False
        self.hotspot = None       # (name, password) while the radio's own network is up

    def scan(self):
        return [Network(*n) for n in self.NETWORKS]

    def connect(self, ssid, password):
        network = next((n for n in self.scan() if n.ssid == ssid), None)
        self._phase, self._ssid, self._until = "connecting", ssid, self.clock() + self.CONNECT_S
        self._ok = network is not None and (not network.secure or password == self.PASSWORD)

    def status(self):
        if self._phase == "connecting" and self.clock() >= self._until:
            self._phase = "connected" if self._ok else "failed"
        return self._phase, self._ssid

    def start_hotspot(self, name, password):
        self.hotspot = (name, password)

    def stop_hotspot(self):
        self.hotspot = None


# --- the real ones, on the Pi ------------------------------------------------------------------

def _run(args, timeout=20):
    """Run a command: (exit code, its output and errors together). 127 if it isn't there."""
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return 127, ""
    except subprocess.TimeoutExpired:
        return 124, "timed out"
    except OSError as e:
        return 126, str(e)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def _background(job):
    threading.Thread(target=job, daemon=True).start()


class BluezBluetooth:
    """Bluetooth through BlueZ's own command-line tool, bluetoothctl. Searching runs
    `bluetoothctl scan on` in the background while the menu is open; a background thread keeps a
    list of the audio devices (headphones, speakers) it has seen, so devices() and connected never
    wait. Connecting pairs (with no PIN, as headphones expect), trusts (so they reconnect by
    themselves next time) and connects. PipeWire then moves the sound to them, and back to the
    speaker when they go."""

    REFRESH_S = 1.5            # while searching or connecting
    QUIET_REFRESH_S = 6.0      # the rest of the time: is anything still connected?
    SCAN_S = 120

    def __init__(self, run=_run, start=True):
        self._run = run
        self._lock = threading.Lock()
        self._known = {}           # address -> (name, kind), for audio devices only
        self._others = set()       # addresses looked at that aren't audio devices
        self._paired, self._connected = set(), set()
        self._busy = {}            # address -> "connecting" | "disconnecting"
        self._failed = set()
        self._scan = None
        self._wake = threading.Event()
        if start:
            _background(self._watch)

    @classmethod
    def available(cls):
        return shutil.which("bluetoothctl") is not None

    # --- what the menu sees --------------------------------------------------

    def start_scan(self):
        self.stop_scan()
        try:
            self._scan = subprocess.Popen(["bluetoothctl", "--timeout", str(self.SCAN_S), "scan", "on"],
                                          stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                          stderr=subprocess.DEVNULL)
        except OSError:
            self._scan = None
        self._wake.set()

    def stop_scan(self):
        scan, self._scan = self._scan, None
        if scan and scan.poll() is None:
            scan.terminate()

    def devices(self):
        with self._lock:
            out = []
            for address, (name, kind) in self._known.items():
                paired = address in self._paired
                if address in self._busy:
                    state = self._busy[address]
                elif address in self._connected:
                    state = "connected"
                elif address in self._failed:
                    state = "failed"
                else:
                    state = ""
                if paired or state or self._scanning():
                    out.append(Device(address, name, kind, paired, state))
            return out

    @property
    def connected(self):
        return next((d for d in self.devices() if d.state == "connected"), None)

    def connect(self, address):
        with self._lock:
            self._failed.discard(address)
            self._busy[address] = "connecting"
            others = [a for a in self._connected if a != address]
        _background(lambda: self._connect(address, others))

    def disconnect(self, address):
        with self._lock:
            self._busy[address] = "disconnecting"
        _background(lambda: self._disconnect(address))

    # --- behind the scenes ---------------------------------------------------

    def _scanning(self):
        return self._scan is not None and self._scan.poll() is None

    def _connect(self, address, others):
        for other in others:                  # one at a time, as on the laptop
            self._run(["bluetoothctl", "disconnect", other], 15)
        ok = True
        if address not in self._paired:
            code, out = self._run(["bluetoothctl", "--agent", "NoInputNoOutput", "pair", address], 40)
            ok = code == 0 or "AlreadyExists" in out
            if ok:
                self._run(["bluetoothctl", "trust", address], 10)
        if ok:
            code, out = self._run(["bluetoothctl", "connect", address], 30)
            ok = code == 0 and "Failed" not in out
        self._refresh()
        with self._lock:
            self._busy.pop(address, None)
            if not ok or address not in self._connected:
                self._failed.add(address)

    def _disconnect(self, address):
        self._run(["bluetoothctl", "disconnect", address], 15)
        self._refresh()
        with self._lock:
            self._busy.pop(address, None)

    def _addresses(self, *which):
        code, out = self._run(["bluetoothctl", "devices", *which], 10)
        found = {}
        for line in out.splitlines():
            parts = line.strip().split(" ", 2)
            if len(parts) >= 2 and parts[0] == "Device":
                found[parts[1]] = parts[2] if len(parts) > 2 else parts[1]
        return code, found

    def _refresh(self):
        _, seen = self._addresses()
        code, paired = self._addresses("Paired")
        _, connected = self._addresses("Connected")
        filters = code == 0               # older bluetoothctl has no Paired / Connected: ask each one
        if not filters:
            paired, connected = {}, {}
        for address, name in seen.items():
            if address in self._others or (filters and address in self._known):
                continue
            info = self._run(["bluetoothctl", "info", address], 10)[1]
            kind = _audio_kind(info)
            if kind is None:
                if "Name:" in info:           # named and still not audio: never ask again
                    self._others.add(address)
                continue
            if name.replace("-", ":") == address:     # no name yet: wait for one
                continue
            with self._lock:
                self._known[address] = (name, kind)
            if not filters:
                if "Paired: yes" in info:
                    paired[address] = name
                if "Connected: yes" in info:
                    connected[address] = name
        with self._lock:
            self._paired, self._connected = set(paired), set(connected)

    def _watch(self):
        self._run(["bluetoothctl", "power", "on"], 10)
        while True:
            try:
                self._refresh()
            except Exception as e:       # never let the watcher die
                print(f"Bluetooth: {e}", flush=True)
            busy = self._scanning() or bool(self._busy)
            self._wake.wait(self.REFRESH_S if busy else self.QUIET_REFRESH_S)
            self._wake.clear()


def _audio_kind(info):
    """From `bluetoothctl info`: "headphones", "speaker", or None for anything that isn't audio."""
    icon = next((line.split(":", 1)[1].strip() for line in info.splitlines()
                 if line.strip().startswith("Icon:")), "")
    if icon in ("audio-headset", "audio-headphones"):
        return "headphones"
    if icon.startswith("audio") or "Audio Sink" in info:
        return "speaker"
    return None


def _split_terse(line):
    """nmcli -t fields: separated by ':', with a ':' inside a field written as backslash-colon."""
    fields, field, escaped = [], "", False
    for ch in line:
        if escaped:
            field, escaped = field + ch, False
        elif ch == "\\":
            escaped = True
        elif ch == ":":
            fields.append(field)
            field = ""
        else:
            field += ch
    fields.append(field)
    return fields


class NetworkManagerWiFi:
    """Wi-Fi through NetworkManager's nmcli. The Zero 2 W has one Wi-Fi radio, so while the setup
    hotspot is up it can't search: the list shown is the one found just before. Joining a network
    drops the hotspot; if joining fails, the hotspot comes back for another try.

    The hotspot gives the radio the address 10.42.0.1. For the page to pop up by itself on a
    phone, every name must lead there and port 80 must reach the page: pi/install.sh sets that up
    (a dnsmasq line and a NetworkManager dispatcher script)."""

    HOTSPOT = "terradio-setup"            # NetworkManager's name for the hotspot connection
    SCAN_S = 20                           # a list this fresh is good enough
    ONLINE_S = 10                         # how often to check for a connection

    def __init__(self, run=_run, clock=time.monotonic, iface="wlan0", start=True):
        self._run, self.clock, self.iface = run, clock, iface
        self._lock = threading.Lock()
        self._networks, self._scanned = [], -1e9
        self._phase, self._ssid = "idle", None
        self.hotspot = None
        self.online = None                 # True / False once known: joined to a network?
        if start:
            _background(self._watch)

    @classmethod
    def available(cls):
        return shutil.which("nmcli") is not None

    def _nmcli(self, *args, timeout=30):
        """nmcli, and again through sudo if it isn't allowed (the radio runs as a plain user)."""
        code, out = self._run(["nmcli", *args], timeout)
        if code != 0 and ("Not authorized" in out or "Insufficient privileges" in out):
            code, out = self._run(["sudo", "-n", "nmcli", *args], timeout)
        return code, out

    def scan(self):
        with self._lock:
            fresh = self.clock() - self._scanned < self.SCAN_S
            if fresh or self.hotspot is not None:
                return list(self._networks)
        return self._scan_now()

    def _scan_now(self):
        code, out = self._nmcli("-t", "-f", "SSID,SIGNAL,SECURITY", "device", "wifi", "list",
                                "ifname", self.iface, "--rescan", "yes", timeout=30)
        if code != 0:
            with self._lock:
                return list(self._networks)
        best = {}
        for line in out.splitlines():
            fields = _split_terse(line)
            if len(fields) < 3 or not fields[0]:
                continue
            ssid, signal, security = fields[0], fields[1], fields[2]
            try:
                signal = int(signal)
            except ValueError:
                signal = 0
            if ssid not in best or signal > best[ssid].signal:
                best[ssid] = Network(ssid, signal, security.strip() not in ("", "--"))
        networks = sorted(best.values(), key=lambda n: -n.signal)
        with self._lock:
            self._networks, self._scanned = networks, self.clock()
        return list(networks)

    def connect(self, ssid, password):
        with self._lock:
            self._phase, self._ssid = "connecting", ssid
        _background(lambda: self._connect(ssid, password))

    def _connect(self, ssid, password):
        _, names = self._nmcli("-t", "-f", "NAME", "connection", "show", timeout=10)
        had_profile = ssid in names.splitlines()
        args = ["device", "wifi", "connect", ssid, "ifname", self.iface]
        if password:
            args += ["password", password]
        code, _ = self._nmcli(*args, timeout=60)
        ok = code == 0
        if not ok and not had_profile:
            self._nmcli("connection", "delete", "id", ssid, timeout=10)     # don't keep a wrong password
        with self._lock:
            self._phase = "connected" if ok else "failed"
            hotspot = self.hotspot
            if ok:
                self.hotspot, self.online = None, True
        if not ok and hotspot:
            self._hotspot_up(*hotspot)      # trying dropped it: back up for another go

    def status(self):
        with self._lock:
            return self._phase, self._ssid

    def start_hotspot(self, name, password):
        with self._lock:
            self.hotspot = (name, password)
            self._phase, self._ssid = "idle", None

        def up():
            self._scan_now()                # the list to choose from, while it can still search
            if self.hotspot == (name, password):
                self._hotspot_up(name, password)
        _background(up)

    def _hotspot_up(self, name, password):
        code, out = self._nmcli("device", "wifi", "hotspot", "ifname", self.iface, "con-name", self.HOTSPOT,
                                "ssid", name, "password", password, timeout=30)
        if code != 0:
            print(f"Wi-Fi setup: the hotspot didn't start ({out.strip()[-200:]})", flush=True)
            return
        # Never back by itself after a restart: only when there's no Wi-Fi to join.
        self._nmcli("connection", "modify", self.HOTSPOT, "connection.autoconnect", "no", timeout=10)

    def stop_hotspot(self):
        with self._lock:
            was, self.hotspot = self.hotspot, None
        if was is not None:
            # Down, and NetworkManager goes back to a known network by itself.
            _background(lambda: self._nmcli("connection", "down", self.HOTSPOT, timeout=20))

    def _watch(self):
        while True:
            code, out = self._nmcli("-t", "-f", "STATE", "general", timeout=10)
            if code == 0:
                state = out.strip().splitlines()[0] if out.strip() else ""
                with self._lock:
                    self.online = state.startswith("connected") and self.hotspot is None
            time.sleep(self.ONLINE_S)
