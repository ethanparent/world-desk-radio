"""Keeping the radio's software up to date from GitHub.

The radio runs from a git checkout of the project. Every CHECK_S it asks GitHub whether main has
moved on (git fetch). If it has, the phone page says so and lists what's new, with an Install
button. With automatic updates on (the Pi's default), the radio also installs it by itself at
night (NIGHT_HOURS) while it's in standby or sitting quiet. Installing is:

    git merge --ff-only origin/main         the new version, exactly as it is on GitHub
    pip install -r requirements.txt         only when that file changed
    a test start                            python -c "import radio, radio_map" must work,
                                            or it goes back to the version it had
    restart                                 the main loop sees restart_due and starts again

It never touches a checkout with changes of its own (edits not committed, commits not on
GitHub, another branch checked out): that's someone working on it, as on the laptop.

    updater = Updater(root, auto=True)
    updater.start()             # checks now, then every CHECK_S, on a background thread
    updater.status()            # for the phone page: {"state": ..., "version": ..., "new": [...]}
    updater.check()             # look again now
    updater.install()           # install now
    updater.tick(quiet)         # main loop, every frame: quiet = in standby, or idle and silent
    updater.restart_due         # True once the new version is in place
"""

import os
import subprocess
import sys
import threading
import time
from datetime import date
from pathlib import Path

CHECK_S = 6 * 3600
NIGHT_HOURS = range(2, 5)        # automatic installs happen between 2 and 5 am
REMOTE, BRANCH = "origin", "main"
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

# What each state means on the phone page
STATES = {
    "checking": "Checking for updates…",
    "current": "Up to date",
    "ready": "An update is ready",
    "installing": "Installing the update…",
    "installed": "Updated: restarting…",
    "own": "This copy has changes of its own, so it doesn't update itself",
    "offline": "Couldn't reach GitHub to check for updates",
    "failed": "Couldn't install the update",
    "off": "Updates need a git checkout of the project",
}


class UpdateError(Exception):
    pass


class Updater:
    def __init__(self, root, *, auto=False, remote=REMOTE, branch=BRANCH, clock=time.monotonic,
                 local_hour=lambda: time.localtime().tm_hour, pip=None):
        """pip: runs the requirements install (for tests); the default calls pip."""
        self.root = Path(root)
        self.auto = auto
        self.remote, self.branch = remote, branch
        self.clock, self.local_hour = clock, local_hour
        self._pip = pip or self._pip_install
        self.restart_due = False
        self._lock = threading.Lock()
        self._state, self._detail = "checking", ""
        self._version = self._latest = None       # ("7938d5b", "2026-10-21")
        self._new = []                            # what's new: commit subjects, newest first
        self._checked = None                      # clock() of the last check
        self._jobs = []
        self._wake = threading.Event()
        self._thread = None
        self._env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never")

    # --- for the main loop and the phone page -----------------------------------------

    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        self.check()

    def check(self):
        self._ask("check")

    def install(self):
        self._ask("install")

    def tick(self, quiet):
        """Every frame. Checks every CHECK_S, and installs a waiting update at night when the
        radio is quiet and automatic updates are on."""
        with self._lock:
            state, checked, busy = self._state, self._checked, bool(self._jobs)
        if busy or state in ("checking", "installing", "installed"):
            return
        if checked is not None and self.clock() - checked >= CHECK_S:
            self.check()
        elif state == "ready" and self.auto and quiet and self.local_hour() in NIGHT_HOURS:
            self.install()

    def status(self):
        with self._lock:
            return {"state": self._state, "words": STATES[self._state], "detail": self._detail,
                    "version": _shown(self._version), "latest": _shown(self._latest),
                    "new": list(self._new), "auto": self.auto}

    @property
    def busy(self):
        with self._lock:
            return bool(self._jobs) or self._state in ("checking", "installing")

    # --- the background thread ----------------------------------------------------

    def _ask(self, job):
        with self._lock:
            if job not in self._jobs:
                self._jobs.append(job)
            if job == "check" and self._state not in ("installing", "installed"):
                self._state = "checking"
        self._wake.set()

    def _run(self):
        while True:
            self._wake.wait()
            with self._lock:
                job = self._jobs[0] if self._jobs else None
                if job is None:
                    self._wake.clear()
            if job is None:
                continue
            try:
                self._install() if job == "install" else self._check()
            except Exception as e:                  # never let a surprise stop the radio
                self._set("failed" if job == "install" else "offline", str(e)[:200])
            with self._lock:
                self._jobs.remove(job)

    def _set(self, state, detail=""):
        with self._lock:
            self._state, self._detail = state, detail
            if state not in ("checking", "installing"):
                self._checked = self.clock()

    def _git(self, *args, timeout=30):
        try:
            r = subprocess.run(["git", "-C", str(self.root), *args], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=timeout, env=self._env,
                               stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise UpdateError(f"git {args[0]}: {e}") from e
        if r.returncode:
            lines = (r.stderr or r.stdout).strip().splitlines()
            raise UpdateError(lines[-1] if lines else f"git {args[0]} failed")
        return r.stdout.strip()

    def _version_of(self, ref):
        h, day = self._git("log", "-1", "--format=%h %cs", ref).split()
        return h, day

    def _own_changes(self):
        """Why this checkout shouldn't be updated, or "" if it can be."""
        branch = self._git("rev-parse", "--abbrev-ref", "HEAD")
        if branch != self.branch:
            return f"{branch} is checked out, not {self.branch}"
        if self._git("status", "--porcelain", "--untracked-files=no"):
            return "files have been edited"
        ahead = int(self._git("rev-list", "--count", f"{self.remote}/{self.branch}..HEAD"))
        if ahead:
            return f"{ahead} change{'s' if ahead != 1 else ''} not on GitHub yet"
        return ""

    def _check(self):
        try:
            self._git("rev-parse", "--git-dir")
        except UpdateError:
            self._set("off")
            return
        with self._lock:
            self._version = self._version_of("HEAD")
        try:
            self._git("fetch", "--quiet", "--no-tags", self.remote, self.branch, timeout=90)
        except UpdateError as e:
            self._set("offline", str(e)[:200])
            return
        upstream = f"{self.remote}/{self.branch}"
        behind = int(self._git("rev-list", "--count", f"HEAD..{upstream}"))
        new = self._git("log", "--format=%s", "-n", "8", f"HEAD..{upstream}").splitlines() if behind else []
        with self._lock:
            self._latest = self._version_of(upstream)
            self._new = new
        reason = self._own_changes()
        if reason:
            self._set("own", reason)
        else:
            self._set("ready" if behind else "current",
                      f"{behind} change{'s' if behind != 1 else ''}" if behind else "")

    def _install(self):
        with self._lock:
            if self._state != "ready":
                return
            self._state, self._detail = "installing", ""
        reason = self._own_changes()
        if reason:
            self._set("own", reason)
            return
        old = self._git("rev-parse", "HEAD")
        try:
            self._git("merge", "--ff-only", "--quiet", f"{self.remote}/{self.branch}", timeout=120)
        except UpdateError as e:
            self._set("failed", str(e)[:200])
            return
        try:
            if "requirements.txt" in self._git("diff", "--name-only", old, "HEAD").splitlines():
                self._pip()
            self._test_start()
        except UpdateError as e:
            self._git("reset", "--keep", old)       # back to the version that worked
            with self._lock:
                self._version = self._version_of("HEAD")
            self._set("failed", str(e)[:200])
            return
        with self._lock:
            self._version, self._new = self._version_of("HEAD"), []
        self._set("installed")
        self.restart_due = True

    def _run_python(self, args, timeout, what):
        try:
            r = subprocess.run([sys.executable, *args], cwd=self.root, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=timeout,
                               stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise UpdateError(f"{what}: {e}") from e
        if r.returncode:
            last = (r.stderr or r.stdout).strip().splitlines()
            raise UpdateError(f"{what}: {last[-1] if last else 'failed'}")

    def _pip_install(self):
        self._run_python(["-m", "pip", "install", "--quiet", "-r", "requirements.txt"], 900,
                         "installing its new packages")

    def _test_start(self):
        self._run_python(["-c", "import radio, radio_map"], 120, "the new version wouldn't start")


def _shown(version):
    """("7938d5b", "2026-10-21") -> "7938d5b, Oct 21"."""
    if not version:
        return None
    h, day = version
    try:
        d = date.fromisoformat(day)
        return f"{h}, {d:%b} {d.day}" + (f", {d.year}" if d.year != date.today().year else "")
    except ValueError:
        return h
