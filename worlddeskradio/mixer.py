"""The Pi's system volume, through PipeWire's wpctl, so the Volume knob works the same for the
speaker and for Bluetooth headphones, and standby can fade everything out smoothly.

    mixer = SystemMixer.find()     # None where there's no wpctl (the laptop, or no PipeWire)
    mixer.set(60)                  # 0-100, straight away (a fade in progress stops)
    mixer.fade_out(1.5)            # down to silence over 1.5 s; set() brings it back
    mixer.refresh()                # apply it again, after the output changed (headphones)
    mixer.close()

wpctl takes a moment to run on a Pi Zero, so the calls go to a worker thread that always jumps
to the newest value: turning the knob quickly never builds up a queue.
"""

import shutil
import subprocess
import threading
import time

SINK = "@DEFAULT_AUDIO_SINK@"      # whatever is playing: the speaker, or the headphones once connected
FADE_STEPS_PER_S = 12


class SystemMixer:
    def __init__(self, wpctl="wpctl", run=None, clock=time.monotonic, sleep=time.sleep):
        self.wpctl = wpctl
        self._run = run or self._wpctl
        self.clock, self.sleep = clock, sleep
        self._cond = threading.Condition()
        self._target = None            # the level wanted, 0-100
        self._fade = None              # (start level, start time, seconds) while fading out
        self._applied = None
        self._closed = False
        self._thread = threading.Thread(target=self._work, name="mixer", daemon=True)
        self._thread.start()

    @classmethod
    def find(cls):
        """A mixer if wpctl is here and can see an audio output, else None."""
        path = shutil.which("wpctl")
        if not path:
            return None
        try:
            r = subprocess.run([path, "get-volume", SINK], capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            return None
        return cls(path) if r.returncode == 0 and "Volume" in r.stdout else None

    def _wpctl(self, level):
        try:
            subprocess.run([self.wpctl, "set-volume", SINK, f"{level / 100:.3f}"],
                           capture_output=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass

    def set(self, volume):
        with self._cond:
            self._target = max(0, min(100, int(volume)))
            self._fade = None
            self._cond.notify()

    def fade_out(self, seconds):
        with self._cond:
            # From where it is now: standby can start while the sleep timer's fade is under way.
            start = self._wanted()
            start = start if start is not None else 100
            self._fade = (start, self.clock(), max(0.05, seconds))
            self._cond.notify()

    def refresh(self):
        """Apply the level again (after the output changed: headphones connected or gone)."""
        with self._cond:
            self._applied = None
            self._cond.notify()

    def _wanted(self):
        """The level to apply now, or None if there's nothing to do."""
        if self._fade is not None:
            start, t0, seconds = self._fade
            left = max(0.0, 1 - (self.clock() - t0) / seconds)
            level = round(start * left)
            if left == 0:
                self._fade, self._target = None, 0     # stays silent until the next set()
            return level
        return self._target

    def _work(self):
        while True:
            with self._cond:
                while not self._closed and (self._wanted() is None or self._wanted() == self._applied):
                    self._cond.wait(1 / FADE_STEPS_PER_S if self._fade else None)
                if self._closed:
                    return
                level = self._wanted()
                fading = self._fade is not None
            self._run(level)
            self._applied = level
            if fading:
                self.sleep(1 / FADE_STEPS_PER_S)

    def close(self):
        with self._cond:
            self._closed = True
            self._cond.notify()
