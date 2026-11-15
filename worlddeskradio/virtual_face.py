"""A stand-in for the ESP32-S3 face, on the laptop. It draws the round screen from nothing but
the messages it receives over a pretend USB cable, as the real face will:

    radio_end, face_end = face_link.loopback()
    face = VirtualFace(face_end)               # says hello, like a face that has just booted
    link = FaceLink(radio_end, stations)       # the radio's end
    ... every frame:
        link.update(face_state(dial.snapshot(), controls))
        face.poll()
        image = face.frame(now)                # None when nothing on screen changed

It shares the drawing code with the laptop preview (map_view.FaceScreen) but never sees the
radio's station list or its state, only the stations and state messages. So if the messages left
something out, the virtual face would look different from the preview.
"""

import base64
import math
import time

import numpy as np

from PIL import Image

from .face_link import (POS_SCALE, IconReceiver, StationsReceiver, decode, favorites_from_message,
                        hello_message, lines_from_points, state_from_message)
from .map_view import SIZE, FaceScreen, radius_for_view

WAIT_SPIN_DEG_S = 6     # how fast the globe turns while waiting for the radio
QUIET_S = 3.0           # nothing heard for this long (the Pi sends a state every second): radio gone


class VirtualFace:
    def __init__(self, port, size=SIZE, clock=time.monotonic):
        self.port = port
        self.size = size
        self.clock = clock
        self.screen = FaceScreen([], size)    # no dots until the stations message arrives
        self.state = None                     # the last FaceState received
        self.asleep = False
        self.bad_lines = 0
        self._stations = StationsReceiver()
        self._layers = {}                     # a game world's lines on their way: layer -> receiver
        self._world_lines = {}
        self._places = []
        self._icons = {}                      # a world's own signal symbols: state -> worlds.Icon
        self._icon_rx = IconReceiver()
        self._inbox = bytearray()
        self._heard = -math.inf               # when the last message arrived
        self._mode = None                     # what's on screen: "state", "waiting" or "dark"
        self._dark_at = None                  # standby: when the fade to black is over
        port.write(hello_message())

    def poll(self):
        """Read and act on every message that has arrived."""
        waiting = self.port.in_waiting
        if waiting:
            self._inbox += self.port.read(waiting)
        while b"\n" in self._inbox:
            line, _, rest = self._inbox.partition(b"\n")
            self._inbox = bytearray(rest)
            self._handle(line)

    def _handle(self, line):
        try:
            msg = decode(line)
            kind = msg.get("t")
            if kind == "state":
                self.state = state_from_message(msg)
            elif kind == "stations":
                done = self._stations.add(msg)
                if done is not None:
                    lats, lons = done
                    self.screen.set_stations([{"geo_lat": a, "geo_long": b}
                                              for a, b in zip(lats.tolist(), lons.tolist())])
            elif kind == "world":
                # A game world's map (its lines follow), or Earth again. A layer with no lines
                # is complete as soon as it's announced.
                if msg.get("name"):
                    self._layers = {layer: StationsReceiver() for layer in ("coast", "border")
                                    if int(msg.get(layer) or 0)}
                    self._world_lines, self._places = {}, [None] * int(msg.get("places") or 0)
                    self._icons, self._icon_rx = {}, IconReceiver()
                    self.screen.view.set_world()
                    self.screen.home = (float(msg["lat"]), float(msg["lon"]), int(msg["zoom"]))
                else:
                    self._layers, self._icons = {}, {}
                    self.screen.view.set_world(earth=True)
                    self.screen.home = None
                self.screen.redraw()
            elif kind == "lines":
                layer = msg["layer"]
                done = self._layers[layer].add(msg) if layer in self._layers else None
                if done is not None:
                    self._world_lines[layer] = lines_from_points(*(a.tolist() for a in done))
                    self._show_world()
            elif kind == "places":
                i = int(msg["i"])
                q = np.frombuffer(base64.b64decode(msg["pos"]), dtype="<i2").reshape(-1, 2)
                for k, (name, zoom, (a, b)) in enumerate(zip(msg["names"], msg["zoom"], q.tolist())):
                    if 0 <= i + k < len(self._places):
                        self._places[i + k] = (name, a / POS_SCALE, b / POS_SCALE, zoom / 100)
                if all(p is not None for p in self._places):
                    self._show_world()
            elif kind == "icon":
                done = self._icon_rx.add(msg)
                if done is not None:
                    self._icons[done[0]] = done[1]
                    self._show_world()
            elif kind == "favs":
                self.screen.set_favorites(favorites_from_message(msg))
            elif kind == "sleep":
                fade = float(msg.get("fade") or 0)
                self.screen.fade_out(self.clock(), fade)
                if fade:
                    self._dark_at = self.clock() + fade     # fade first, then the backlight goes off
                else:
                    self.asleep = True
            elif kind == "wake":
                self.asleep, self._dark_at = False, None
                self.screen.wake(self.clock(), float(msg.get("fade") or 0))
        except (ValueError, KeyError, TypeError):
            self.bad_lines += 1       # garbled line: skip it, the next state will put things right
            return
        self._heard = self.clock()

    def _show_world(self):
        self.screen.view.set_world(self._world_lines.get("coast"), self._world_lines.get("border"),
                                   [p for p in self._places if p is not None], icons=self._icons)
        self.screen.redraw()

    def frame(self, now=None):
        """The picture on the face now, or None if it hasn't changed."""
        now = self.clock() if now is None else now
        if self._dark_at is not None and now >= self._dark_at:
            self.asleep, self._dark_at = True, None
        if self.asleep:
            # Standby: the real face turns its backlight off.
            if self._mode == "dark":
                return None
            self._mode = "dark"
            return Image.new("RGB", (self.size, self.size))
        if self.state is None or now - self._heard > QUIET_S:
            # Before the radio has started (or after it has gone quiet), the face shows a slowly
            # turning globe and nothing else.
            self._mode = "waiting"
            lon = (now * WAIT_SPIN_DEG_S) % 360 - 180
            return self.screen.view.render(20.0, lon, radius_for_view(180, self.size))
        if self._mode != "state":
            self._mode = "state"
            self.screen.redraw()
        return self.screen.frame(self.state, now)
