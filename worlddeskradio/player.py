"""Audio playback through an external player process (mpv preferred, ffplay as a fallback).

Two independent channels: a looping static hiss, and the live station stream.
Each is its own subprocess, so starting one never blocks the other. The static starts at a
random point in its loop each time (ffplay; mpv in Stage 3), so no two turns hiss alike.

With ffplay (the laptop) a station fades in over FADE_S while the static fades out, and the
song title comes from the stream: ffplay prints it once at the start, and fetch_stream_title()
asks again every TITLE_POLL_S. mpv prints every new title itself.

With mpv on the Pi (anywhere with Unix sockets), each mpv gets a control socket (MpvControl): a
station starts silent and its volume rises over FADE_S while the static's falls, the static starts
at a random point in its loop, and the volume changes without restarting anything. With a system
mixer (mixer.SystemMixer: PipeWire's wpctl) the players stay at full and the mixer sets the
volume and does standby's fade, for the speaker and Bluetooth alike.
"""

import functools
import http.client
import itertools
import json
import os
import random
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import wave

from .stations import USER_AGENT

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
FADE_S = 1.5                  # station fades in, static fades out
STATIC_SHARE = 0.35           # the static's volume, as a share of the station's
TITLE_POLL_S = 30             # ffplay only: how often to ask the stream for its song title
# A song title in the player's output: ffplay's "StreamTitle     : ..." or mpv's " icy-title: ..."
_IPC = os.name == "posix" and hasattr(socket, "AF_UNIX")     # mpv's control socket (not on Windows)
RAMP_STEPS_PER_S = 20
# mpv's extras a radio never uses (the youtube-dl hook, on-screen controls, key bindings, stats):
# left off, every station starts about 0.1 s sooner on the Pi (1.35 s, not 1.45).
MPV_LEAN = ["--ytdl=no", "--load-scripts=no", "--osc=no", "--input-default-bindings=no", "--audio-display=no",
            "--load-stats-overlay=no"]
_TITLE_LINE = re.compile(r"^\s*(?:StreamTitle\s*|icy-title):\s?(.*?)\s*$")


@functools.lru_cache(maxsize=1)
def _pycaw():
    """(comtypes, pycaw's AudioUtilities) on Windows when pycaw is installed, else None. It sets
    each program's own volume in the Windows mixer, which is how the laptop fades a playing
    station (ffplay itself can't). On the Pi the system mixer will do this (Stage 3)."""
    if sys.platform != "win32":
        return None
    try:
        import comtypes
        from pycaw.pycaw import AudioUtilities
    except ImportError:
        return None
    return comtypes, AudioUtilities


def _mixer_volumes(audio, pids, wait_s):
    """The Windows mixer's volume controls for these processes. A process shows up there once it
    makes sound, so wait up to wait_s for at least one."""
    end = time.monotonic() + wait_s
    while True:
        found = [s.SimpleAudioVolume for s in audio.GetAllSessions() if s.Process and s.Process.pid in pids]
        if found or time.monotonic() >= end:
            return found
        time.sleep(0.05)


def _in_mixer_thread(job):
    """Run job(AudioUtilities) in the background, with the COM setup Windows needs."""
    comtypes, audio = _pycaw()

    def run():
        comtypes.CoInitialize()
        try:
            job(audio)
        except (OSError, comtypes.COMError):
            pass                         # the process ended mid-way: nothing left to turn down
        finally:
            comtypes.CoUninitialize()
    threading.Thread(target=run, daemon=True).start()


def _wav_seconds(path):
    try:
        with wave.open(str(path)) as w:
            return w.getnframes() / w.getframerate()
    except (OSError, EOFError, wave.Error):
        return 0.0


def _looped_from(path, start):
    """An ffmpeg filter graph that plays the wav from `start` seconds to its end once, then loops
    it whole. (ffplay's own -loop jumps back to `start` each time, which breaks the seamless
    loop.) None if the path has a quote, which can't be written into a filter graph."""
    if "'" in path:
        return None
    name = path.replace("\\", "/").replace(":", r"\:").replace("=", r"\=")
    return (f"amovie='{name}':sp={start:.3f}[a];amovie='{name}':loop=0[b];"
            "[a][b]concat=n=2:v=0:a=1,asetpts=N/SR/TB")    # keep the clock counting up


def fetch_stream_title(url, timeout=6.0):
    """Ask an Icecast/SHOUTcast stream what's playing. Reads up to its first metadata block
    (usually 8-32 KB of audio) and hangs up. Returns the title, "" if the stream sends none,
    or None if it couldn't tell."""
    req = urllib.request.Request(url, headers={"Icy-MetaData": "1", "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            every = int(resp.headers.get("icy-metaint") or 0)
            if not every:
                return None
            resp.read(every)
            size = resp.read(1)
            if not size:
                return None
            block = resp.read(size[0] * 16).rstrip(b"\0")
    except (OSError, ValueError, http.client.HTTPException):
        return None
    try:
        text = block.decode("utf-8")
    except UnicodeDecodeError:
        text = block.decode("latin-1")
    m = re.search(r"StreamTitle='(.*?)';", text, re.S)
    return m.group(1).strip() if m else ""


class MpvControl:
    """One mpv's control socket (--input-ipc-server): set its volume, ramp it, seek."""

    _numbers = itertools.count()

    def __init__(self):
        folder = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
        self.path = os.path.join(folder, f"worlddeskradio-mpv-{os.getpid()}-{next(self._numbers)}.sock")
        self._sock = None
        self._lock = threading.Lock()
        self._ramp = 0                    # bumps with each ramp: an older one stops
        self.ramping = False

    @property
    def arg(self):
        return f"--input-ipc-server={self.path}"

    def _connect(self, wait_s, alive):
        end = time.monotonic() + wait_s
        while self._sock is None:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                s.connect(self.path)
            except OSError:
                s.close()
                if time.monotonic() >= end or not alive():
                    return False
                time.sleep(0.05)
                continue
            s.setblocking(False)
            self._sock = s
        return True

    def send(self, *command, wait_s=8.0, alive=lambda: True):
        """True if mpv got it. Waits up to wait_s for mpv to open its socket."""
        with self._lock:
            if not self._connect(wait_s, alive):
                return False
            try:
                try:
                    while self._sock.recv(65536):     # mpv's replies: not needed, just drained
                        pass
                except BlockingIOError:
                    pass
                self._sock.sendall(json.dumps({"command": list(command)}).encode() + b"\n")
                return True
            except OSError:
                return False

    def set_volume(self, volume, alive=lambda: True):
        """Straight to a volume, unless a fade is under way (it ends at the volume asked for when
        it finishes, so it picks this up itself)."""
        if self.ramping:
            return True
        return self.send("set_property", "volume", volume, wait_s=0.3, alive=alive)

    def ramp(self, start, end, seconds, alive, then=None):
        """Take the volume from start to end over seconds, in the background, then call then().
        end can be a function, asked at every step (a fade-in follows the knob)."""
        self._ramp += 1
        ramp = self._ramp
        target = end if callable(end) else (lambda: end)
        self.ramping = True

        def run():
            try:
                steps(ramp)
            finally:
                if ramp == self._ramp:
                    self.ramping = False

        def steps(ramp):
            n = max(1, round(seconds * RAMP_STEPS_PER_S))
            for i in range(n + 1):
                if ramp != self._ramp or not alive():
                    return
                if not self.send("set_property", "volume", round(start + (target() - start) * i / n, 1),
                                 alive=alive):
                    if alive():
                        print("mpv's control socket didn't answer: no fade this time", flush=True)
                    return
                if i < n:
                    time.sleep(seconds / n)
            if then is not None and ramp == self._ramp:
                then()
        threading.Thread(target=run, daemon=True).start()

    def close(self):
        self._ramp += 1
        with self._lock:
            if self._sock is not None:
                self._sock.close()
                self._sock = None
        try:
            os.unlink(self.path)
        except OSError:
            pass


def find_backend():
    """Return ("mpv", path) or ("ffplay", path), or None if neither is installed."""
    if sys.platform == "win32":
        # mpv.com is the console wrapper; it reliably writes to our pipes.
        for name in ("mpv.com", "mpv"):
            path = shutil.which(name)
            if path:
                com = os.path.splitext(path)[0] + ".com"
                return "mpv", com if os.path.exists(com) else path
    elif shutil.which("mpv"):
        return "mpv", shutil.which("mpv")
    if shutil.which("ffplay"):
        return "ffplay", shutil.which("ffplay")
    return None


class Player:
    READY_MARK = "WORLDDESKRADIO_PLAYING"

    def __init__(self, backend, path, static_wav, volume=80, mixer=None):
        self.backend = backend
        self.path = path
        self.static_wav = str(static_wav)
        self.volume = volume
        self.mixer = mixer            # mixer.SystemMixer: sets the volume for everything, mpv stays at full
        self._ipc = backend == "mpv" and _IPC
        self.static_volume = round(self._level() * STATIC_SHARE)
        self.stream_volume = None     # volume the current stream was started at
        if mixer is not None:
            mixer.set(volume)
        self._lock = threading.Lock()
        self._static = None
        self._static_tail = None      # a last bit of static fading out under a station
        self._stream = None
        self.title = ""               # song title from the current stream
        self._stream_url = None
        self.on_title = None          # called with the new title when it changes

    @property
    def live_volume(self):
        """True if set_volume() changes what's playing at once (the system mixer, or mpv's control
        socket), so the Dial needn't wait for the knob to rest, nor restart the station."""
        return self.mixer is not None or self._ipc

    def _level(self):
        """The players' own volume: full when the system mixer does the volume."""
        return 100 if self.mixer is not None else self.volume

    def set_volume(self, volume):
        """0-100. With the system mixer, or mpv's control socket, it changes what's playing at
        once. Otherwise it's for everything started from now on: the static restarts straight
        away (it's a local file), and a station already playing keeps its volume until restarted
        (the Dial does that, comparing stream_volume)."""
        self.volume = volume
        if self.mixer is not None:
            self.mixer.set(volume)
            self.stream_volume = volume
            return
        self.static_volume = round(volume * STATIC_SHARE)
        if self._ipc:
            with self._lock:
                playing = [(p, level) for p, level in ((self._stream, volume), (self._static, self.static_volume))
                           if p is not None and p.poll() is None]
            for proc, level in playing:
                proc.ctl.set_volume(level, alive=lambda p=proc: p.poll() is None)
            self.stream_volume = volume
            return
        with self._lock:
            static_on = self._static is not None and self._static.poll() is None
        if static_on:
            self.stop_static()
            self.start_static()

    # --- command lines -------------------------------------------------

    def _static_cmd(self):
        if self.backend == "mpv":
            # With the control socket it starts silent: start_static seeks to a random point, then
            # turns it up (so no two turns hiss alike, and the loop still goes back to the start).
            return [self.path, *MPV_LEAN, "--no-video", "--really-quiet", "--loop-file=inf",
                    f"--volume={0 if self._ipc else self.static_volume}", self.static_wav]
        graph = _looped_from(self.static_wav, random.uniform(0, _wav_seconds(self.static_wav)))
        source = ["-f", "lavfi", graph] if graph else ["-loop", "0", self.static_wav]
        return [self.path, "-nodisp", "-loglevel", "quiet", "-volume", str(self.static_volume), *source]

    def _stream_cmd(self, url, fade_s=None, cue=None):
        if cue:
            return self._cue_cmd(cue, fade_s)
        if self.backend == "mpv":
            # --term-playing-msg is printed (at info log level, so don't quieten
            # mpv) once the stream is open and playback starts.
            # With the control socket it starts silent and play_stream fades it in.
            return [self.path, *MPV_LEAN, "--no-video", "--no-config", f"--volume={0 if self._ipc else self.volume}",
                    f"--term-playing-msg={self.READY_MARK}", "--network-timeout=8", url]
        # ffplay prints "Input #0 ..." to stderr after it has probed real data,
        # or "<url>: <error>" if it couldn't open the stream. Keep its \r status
        # line on: on Windows the "Input #0" text only gets flushed alongside it.
        return [self.path, "-nodisp", "-autoexit", "-hide_banner",
                "-loglevel", "info", "-volume", str(self.volume),
                "-af", f"asetpts=PTS-STARTPTS,afade=t=in:d={fade_s or FADE_S}", url]

    def _cue_cmd(self, cue, fade_s=None):
        """A game world's station (worlds.py): local files, the first from part-way through.
        mpv takes them as a playlist, the first with its own start; ffplay plays one input, so
        they go in an ffconcat list, which joins them into one stream."""
        first, start = cue[0]
        if self.backend == "mpv":
            return [self.path, *MPV_LEAN, "--no-video", "--no-config", f"--volume={0 if self._ipc else self.volume}",
                    f"--term-playing-msg={self.READY_MARK}", "--gapless-audio=yes",
                    "--{", f"--start={start:.2f}", str(first), "--}", *(str(path) for path, _ in cue[1:])]
        lines = ["ffconcat version 1.0"]
        for path, skip in cue:
            lines.append("file '" + str(path).replace("\\", "/").replace("'", "'\\''") + "'")
            if skip:
                lines.append(f"inpoint {skip:.3f}")
        self._clear_cue_file()
        fd, self._cue_file = tempfile.mkstemp(prefix="worlddeskradio-", suffix=".ffconcat")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        return [self.path, "-nodisp", "-autoexit", "-hide_banner", "-loglevel", "info", "-volume", str(self.volume),
                "-af", f"asetpts=PTS-STARTPTS,afade=t=in:d={fade_s or FADE_S}",
                "-f", "concat", "-safe", "0", self._cue_file]

    def _clear_cue_file(self):
        path, self._cue_file = getattr(self, "_cue_file", None), None
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass        # the old player still has it open (Windows): it's in the temp folder anyway

    def _spawn(self, cmd, capture):
        ctl = None
        if self._ipc and cmd[0] == self.path:
            ctl = MpvControl()
            ctl.ramping = True        # it starts silent, and its fade-in will set the volume
            cmd = [cmd[0], ctl.arg, *cmd[1:]]
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
            stderr=subprocess.STDOUT if capture else subprocess.DEVNULL,
            creationflags=_NO_WINDOW,
        )
        proc.ctl = ctl
        if _pycaw():
            # Windows remembers each program's mixer level, so a fade to silence would carry over
            # to the next player started: put the new one back to full.
            _in_mixer_thread(lambda audio: [v.SetMasterVolume(1.0, None)
                                            for v in _mixer_volumes(audio, {proc.pid}, 3.0)])
        return proc

    @staticmethod
    def _kill(proc):
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
        if proc is not None and getattr(proc, "ctl", None) is not None:
            proc.ctl.close()

    # --- static ----------------------------------------------------------

    def start_static(self):
        if self.mixer is not None:
            self.mixer.set(self.volume)          # back from standby's fade
        started = None
        with self._lock:
            tail, self._static_tail = self._static_tail, None
            if not (self._static and self._static.poll() is None):
                self._static = started = self._spawn(self._static_cmd(), capture=False)
        self._kill(tail)
        if started is not None and started.ctl is not None:
            proc, length = started, _wav_seconds(self.static_wav)

            def begin():
                alive = lambda: proc.poll() is None
                if length and proc.ctl.send("seek", round(random.uniform(0, length), 2), "absolute", alive=alive):
                    time.sleep(0.05)
                proc.ctl.ramp(0, lambda: self.static_volume, 0.15, alive)
            threading.Thread(target=begin, daemon=True).start()

    def stop_static(self):
        with self._lock:
            procs = (self._static, self._static_tail)
            self._static = self._static_tail = None
        for proc in procs:
            self._kill(proc)

    def fade_out_static(self):
        """Stop the static gently, under a station that is fading in. Static is noise, so a
        fresh copy that fades out sounds the same as the old one turned down."""
        with self._lock:
            static = self._static
            hissing = static is not None and static.poll() is None
        if hissing and static.ctl is not None:
            # mpv: turn this one down through its socket, then stop it.
            with self._lock:
                old_tail, self._static, self._static_tail = self._static_tail, None, static
            self._kill(old_tail)
            static.ctl.ramp(self.static_volume, 0, FADE_S, lambda: static.poll() is None,
                            then=lambda: self._end_tail(static))
            return
        if self.backend != "ffplay" or not hissing:
            self.stop_static()
            return
        start = random.uniform(0, max(0.0, _wav_seconds(self.static_wav) - FADE_S - 0.2))
        tail = self._spawn([self.path, "-nodisp", "-autoexit", "-loglevel", "quiet",
                            "-volume", str(self.static_volume), "-ss", f"{start:.2f}", "-t", str(FADE_S + 0.1),
                            "-af", f"afade=t=out:st=0:d={FADE_S}", self.static_wav], capture=False)
        with self._lock:
            procs = (self._static, self._static_tail)
            self._static, self._static_tail = None, tail
        for proc in procs:
            self._kill(proc)

    def _end_tail(self, proc):
        with self._lock:
            if self._static_tail is proc:
                self._static_tail = None
        self._kill(proc)

    # --- stream ----------------------------------------------------------

    def stop_stream(self):
        with self._lock:
            proc, self._stream = self._stream, None
            self.title = ""
        self._kill(proc)

    def fade_out(self, seconds):
        """Turn everything playing down to silence over `seconds` (standby, the sleep timer's
        end); whoever called it then stops the players. On Windows the mixer does it smoothly
        (pycaw); on the Pi the system mixer will (Stage 3). Without either, a long fade starts the
        ffplay stream again fading out (a moment's gap), and a short one simply ends at the end."""
        if self.mixer is not None:
            self.mixer.fade_out(seconds)
            return
        procs = [p for p in (self._stream, self._static, self._static_tail) if p and p.poll() is None]
        if not procs:
            return
        if all(getattr(p, "ctl", None) is not None for p in procs):
            for p in procs:
                level = self.volume if p is self._stream else self.static_volume
                p.ctl.ramp(level, 0, seconds, lambda p=p: p.poll() is None)
            return
        if _pycaw():
            pids = {p.pid for p in procs}

            def ramp(audio):
                volumes = _mixer_volumes(audio, pids, 0.5)
                steps = max(1, round(seconds * 30))
                for i in range(1, steps + 1):
                    for v in volumes:
                        v.SetMasterVolume((1 - i / steps) ** 2, None)   # loudness falls evenly
                    time.sleep(seconds / steps)
            _in_mixer_thread(ramp)
            return
        url = self._stream_url
        if seconds < 10 or self.backend != "ffplay" or not url or self._stream is None:
            return
        proc = self._spawn([self.path, "-nodisp", "-autoexit", "-loglevel", "quiet",
                            "-volume", str(self.volume),
                            "-af", f"asetpts=PTS-STARTPTS,afade=t=out:st=0:d={seconds}", url], capture=False)
        with self._lock:
            old, self._stream = self._stream, proc
        self._kill(old)

    def _set_title(self, proc, title):
        title = title.strip()
        if not any(ch.isalnum() for ch in title):
            title = ""          # some stations send " - " when they have nothing to say
        with self._lock:
            if self._stream is not proc or title == self.title:
                return
            self.title = title
            callback = self.on_title
        if callback:
            callback(title)

    def _poll_titles(self, proc, url):
        """ffplay only prints the title once, so ask the stream again now and then."""
        while True:
            for _ in range(TITLE_POLL_S * 10):
                time.sleep(0.1)
                if self._stream is not proc or proc.poll() is not None:
                    return
            title = fetch_stream_title(url)
            if title is not None:
                self._set_title(proc, title)

    def stream_alive(self):
        """True while the stream's player is running."""
        with self._lock:
            return self._stream is not None and self._stream.poll() is None

    def play_stream(self, url, timeout=5.0, canceled=lambda: False, fade_s=None, cue=None):
        """Start a stream; return True once audio is flowing, False on failure/timeout/cancel.
        fade_s: how long it fades in (ffplay; default FADE_S). cue: a game world's station
        instead, [(path, seconds to skip)] from worlds.Tracks.cue(); url is then only its name.

        On success the stream keeps playing until stop_stream() is called.
        """
        self.stop_stream()
        if self.mixer is not None:
            self.mixer.set(self.volume)          # back from standby's fade
        proc = self._spawn(self._stream_cmd(url, fade_s, cue), capture=True)
        with self._lock:
            self._stream = proc
            self._stream_url = None if cue else url     # (fade_out restarts a stream by its url)
            self.stream_volume = self.volume
            self.title = ""

        ready = threading.Event()
        failed = threading.Event()

        def watch():
            # Read raw chunks, not lines (ffplay's status line ends in \r), and keep
            # draining until exit so the pipe never fills and blocks the player.
            tail = partial = ""
            while True:
                chunk = proc.stdout.read1(4096)
                if not chunk:
                    return
                text = chunk.decode("utf-8", "replace")
                tail = (tail + text)[-8192:]
                *lines, partial = re.split(r"[\r\n]", partial + text)
                partial = partial[-4096:]
                for line in lines:
                    m = _TITLE_LINE.match(line)
                    if m:
                        self._set_title(proc, m.group(1))
                if self.READY_MARK in tail or "Input #" in tail:
                    ready.set()
                elif f"{url}: " in tail:
                    failed.set()  # ffplay doesn't always exit when it can't open a URL

        threading.Thread(target=watch, daemon=True).start()

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if ready.wait(0.1):
                if self.backend == "ffplay":
                    # ffplay can open a URL then die on the first packet; give it a moment.
                    time.sleep(0.5)
                    if proc.poll() is not None:
                        break
                    if not cue:         # a world's station: the Dial knows its songs from the clock
                        threading.Thread(target=self._poll_titles, args=(proc, url), daemon=True).start()
                elif proc.ctl is not None:
                    # mpv started silent: fade it in (the alarm's fade_s is a whole minute).
                    proc.ctl.ramp(0, self._level, fade_s or FADE_S,
                                  lambda: proc.poll() is None and self._stream is proc)
                return True
            if canceled() or failed.is_set() or proc.poll() is not None:
                break

        with self._lock:
            if self._stream is proc:
                self._stream = None
        self._kill(proc)
        return False

    def close(self):
        self.stop_stream()
        self.stop_static()
        self._clear_cue_file()
