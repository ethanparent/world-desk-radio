"""The radio's page for your phone: Wi-Fi setup, and the rest of the time a remote control, station
search, the map's world and colors, the alarm, the sleep timer, the world tour, the songs you liked,
settings and software updates.

While Wi-Fi setup is on (panels.WifiSetup), the radio runs its own network. A phone that joins
it gets this page (on the Pi every web address will lead here, like hotel Wi-Fi). You pick your
network, type its password, and the radio connects. Once the radio is on your Wi-Fi, the same
page is its companion (companion.py carries requests to the radio's main loop).

    server = SetupServer(wifi_setup, companion)   # 127.0.0.1 on the laptop
    server.start()
    print(server.url)
    ...
    server.stop()

On the laptop it serves the pretend networks of connectivity.FakeWiFi: open server.url in a
browser. With host="0.0.0.0" (radio.py --lan) it's on the home network, so a phone on the
same Wi-Fi can open it; anyone on that network can use it, like most home speakers. The page
itself is setup_page.html, with nothing loaded from the internet (the phone has none while it's
on the radio's network).
"""

import json
import re
import socket
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .stations import place

PAGE = Path(__file__).with_name("setup_page.html")
PORT = 8737
# Addresses phones and computers fetch when they join a network, to see if it has internet.
# Sending them to the page is what makes it pop up on its own.
CAPTIVE_CHECKS = {"/generate_204", "/gen_204", "/hotspot-detect.html", "/library/test/success.html",
                  "/ncsi.txt", "/connecttest.txt", "/redirect", "/success.txt", "/canonical.html"}


# The phone remote's buttons: what each one does is up to the radio's main loop (radio_map.py)
REMOTE = {"prev", "next", "volume", "mute", "power", "favorite", "unfavorite", "save", "like", "surprise",
          "theme", "zoom"}


def lan_address():
    """This computer's address on the home network (finding it sends nothing)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"


class _Server(ThreadingHTTPServer):
    # On Windows, "reuse address" lets a second server bind a port that's already taken and
    # quietly steal its requests; better to be refused and pick another port.
    allow_reuse_address = sys.platform != "win32"
    daemon_threads = True


class SetupServer:
    def __init__(self, setup, companion=None, host="127.0.0.1", port=PORT, demo_password=None, search=None):
        """demo_password: shown on the page when the networks are pretend ones. search: a
        stations.StationSearch, for the page's search box."""
        page = PAGE.read_bytes()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass                      # keep the radio's console quiet

            def _send(self, code, body, kind="application/json"):
                data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", f"{kind}; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                path = self.path.split("?", 1)[0]
                if path in ("/", "/index.html"):
                    self._send(200, page, "text/html")
                elif path == "/api/networks":
                    networks = [{"ssid": n.ssid, "signal": n.signal, "secure": n.secure}
                                for n in setup.networks()] if setup.is_open else []
                    self._send(200, {"active": setup.is_open, "networks": networks,
                                     "demo": demo_password})
                elif path == "/api/status":
                    self._send(200, setup.status())
                elif path == "/api/radio" and companion is not None:
                    self._send(200, companion.snapshot())
                elif path == "/api/search" and search is not None:
                    query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("q", [""])[0]
                    found = [{"id": i, "name": search.stations[i].get("name", ""),
                              "place": place(search.stations[i])} for i in search.find(query[:80])]
                    self._send(200, {"results": found})
                elif path in CAPTIVE_CHECKS:
                    self.send_response(302)
                    self.send_header("Location", "/")
                    self.end_headers()
                else:
                    self._send(404, {"error": "not found"})

            def do_POST(self):
                try:
                    size = int(self.headers.get("Content-Length") or 0)
                    if size > 2048:
                        raise ValueError("too long")
                    body = json.loads(self.rfile.read(size) or b"{}")
                    if not isinstance(body, dict):
                        raise ValueError("expected an object")
                    if self.path == "/api/connect":
                        ssid, password = str(body.get("ssid", "")), str(body.get("password", ""))
                        if not ssid or len(ssid) > 64 or len(password) > 63:
                            raise ValueError("bad network or password")
                        ok = setup.connect(ssid, password)
                        self._send(200 if ok else 409, {"ok": ok})
                        return
                    if companion is None:
                        raise LookupError
                    if self.path == "/api/alarm":
                        m = re.fullmatch(r"(\d{1,2}):(\d{2})", str(body.get("time", "")))
                        if not m or int(m[1]) > 23 or int(m[2]) > 59:
                            raise ValueError("time should be HH:MM")
                        index = int(body.get("index", 0))
                        days = body.get("days")
                        if index not in (0, 1):
                            raise ValueError("index should be 0 or 1")
                        if days is not None and (not isinstance(days, list) or not days
                                                 or not all(isinstance(d, int) and 0 <= d <= 6 for d in days)):
                            raise ValueError("days should be a list of 0 (Monday) to 6 (Sunday)")
                        companion.request("alarm", index=index, on=bool(body.get("on")), hour=int(m[1]),
                                          minute=int(m[2]), days=days, use_current=bool(body.get("use_current")))
                    elif self.path == "/api/ring":
                        if body.get("do") not in ("snooze", "stop"):
                            raise ValueError("do should be snooze or stop")
                        companion.request("ring", do=body["do"])
                    elif self.path == "/api/timer":
                        minutes = int(body.get("minutes", 0))
                        if not 0 <= minutes <= 240:
                            raise ValueError("minutes should be 0-240")
                        companion.request("timer", minutes=minutes)
                    elif self.path == "/api/tour":
                        companion.request("tour", on=bool(body.get("on")))
                    elif self.path == "/api/play" and search is not None:
                        index = int(body.get("id"))
                        if not 0 <= index < len(search.stations):
                            raise ValueError("no such station")
                        companion.request("play", id=index)
                    elif self.path == "/api/unlike":
                        companion.request("unlike", id=int(body.get("id")))
                    elif self.path == "/api/remote":
                        do = str(body.get("do", ""))
                        if do not in REMOTE:
                            raise ValueError("unknown button")
                        value = body.get("value")
                        if do in ("volume", "favorite", "unfavorite"):
                            value = int(value)
                            if not 0 <= value <= (100 if do == "volume" else 999):
                                raise ValueError("value out of range")
                        companion.request("remote", do=do, value=value)
                    elif self.path == "/api/settings":
                        changes = {}
                        if "units" in body:
                            if body["units"] not in ("C", "F"):
                                raise ValueError("units should be C or F")
                            changes["units"] = body["units"]
                        for key in ("auto_update", "borders", "cities", "spin"):
                            if key in body:
                                changes[key] = bool(body[key])
                        if "brightness" in body:
                            changes["brightness"] = int(body["brightness"])
                            if not 10 <= changes["brightness"] <= 100:
                                raise ValueError("brightness should be 10-100")
                        companion.request("settings", **changes)
                    elif self.path == "/api/world":
                        world_id = str(body.get("id", ""))
                        if not world_id or len(world_id) > 64:
                            raise ValueError("id should name a world")
                        companion.request("world", id=world_id)
                    elif self.path == "/api/theme":
                        name = str(body.get("name", ""))
                        if not re.fullmatch(r"[a-z]{1,32}", name):
                            raise ValueError("name should name a theme")
                        companion.request("theme", name=name)
                    elif self.path == "/api/zoom":
                        index = body.get("index")
                        if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index <= 9:
                            raise ValueError("index should be a zoom level, 0-9")
                        companion.request("zoom", index=index)
                    elif self.path == "/api/update":
                        if body.get("do") not in ("check", "install"):
                            raise ValueError("do should be check or install")
                        companion.request("update", do=body["do"])
                    else:
                        raise LookupError
                except LookupError:
                    self._send(404, {"error": "not found"})
                    return
                except (ValueError, TypeError) as e:
                    self._send(400, {"ok": False, "error": str(e)})
                    return
                self._send(200, {"ok": True})

        try:
            self.httpd = _Server((host, port), Handler)
        except OSError:                   # that port is taken: any free one will do
            self.httpd = _Server((host, 0), Handler)
        self._host = host
        self.url = self.current_url()
        self._thread = None

    def current_url(self):
        """The page's address now: on the home network (--lan) this computer's address there,
        which changes when the radio joins another network or opens its own."""
        shown = lan_address() if self._host in ("0.0.0.0", "") else self._host
        self.url = f"http://{shown}:{self.httpd.server_address[1]}/"
        return self.url

    @property
    def mdns_url(self):
        """http://terradio.local:8737/ and the like (avahi on the Pi announces the name), or None
        when the page is only on this computer."""
        if self._host not in ("0.0.0.0", "") or sys.platform == "win32":
            return None
        return f"http://{socket.gethostname()}.local:{self.httpd.server_address[1]}/"

    def start(self):
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()
