"""What the phone page sees and asks for, handed safely between the web server's thread and the
radio's main loop. The loop publishes a snapshot every frame and carries out the page's requests;
the page never touches the radio directly.

    companion.publish({...})        # main loop, every frame
    for kind, args in companion.requests():
        ...                         # main loop: "alarm", "timer", "tour", "unlike"
    companion.snapshot()            # web server: what /api/radio returns
    companion.request("timer", minutes=30)   # web server
"""

import queue
import threading


class Companion:
    def __init__(self):
        self._lock = threading.Lock()
        self._snapshot = {}
        self._requests = queue.Queue()

    def publish(self, snapshot):
        with self._lock:
            self._snapshot = snapshot

    def snapshot(self):
        with self._lock:
            return dict(self._snapshot)

    def request(self, kind, **args):
        self._requests.put((kind, args))

    def requests(self):
        out = []
        while True:
            try:
                out.append(self._requests.get_nowait())
            except queue.Empty:
                return out
