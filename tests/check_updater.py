"""Software updates (worlddeskradio/updater.py) against throwaway git repos in a temporary folder: a
bare "GitHub", the radio's checkout, and a developer's checkout that pushes new versions.
Nothing touches this project's own repo or the real GitHub.

    python tests/check_updater.py
"""
import atexit
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from worlddeskradio.updater import Updater                                                # noqa: E402

BASE = Path(tempfile.mkdtemp(prefix="worlddeskradio-updater-"))
atexit.register(shutil.rmtree, BASE, ignore_errors=True)
results = []


def check(label, ok, detail=""):
    results.append(ok)
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f"   ({detail})" if detail else ""))


def git(cwd, *args):
    r = subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", *args],
                       cwd=cwd, capture_output=True, text=True)
    assert r.returncode == 0, (args, r.stderr)
    return r.stdout.strip()


def commit(repo, message, files):
    for name, text in files.items():
        (repo / name).write_text(text, encoding="utf-8")
    git(repo, "add", *files)
    git(repo, "commit", "-q", "-m", message)


def push(repo):
    git(repo, "push", "-q", "origin", "main")


def wait(u, timeout=60):
    end = time.time() + timeout
    while u.busy and time.time() < end:
        time.sleep(0.05)
    return u.status()


hub = BASE / "github.git"
git(BASE, "init", "-q", "--bare", "-b", "main", str(hub))
dev = BASE / "dev"
git(BASE, "clone", "-q", str(hub), str(dev))
git(dev, "checkout", "-q", "-b", "main")
commit(dev, "First version", {"radio.py": "VERSION = 1\n", "radio_map.py": "", "requirements.txt": "numpy\n",
                              ".gitignore": "settings.json\n"})
push(dev)
radio = BASE / "radio"
git(BASE, "clone", "-q", str(hub), str(radio))
(radio / "settings.json").write_text("{}")             # the radio's own data, ignored by git

pip_calls = []
u = Updater(radio, pip=lambda: pip_calls.append(1))
u.start()
s = wait(u)
check("fresh checkout: up to date", s["state"] == "current", f"{s['state']}, version {s['version']}")

commit(dev, "Weather at the station", {"radio.py": "VERSION = 2\n"})
commit(dev, "Phone remote", {"radio_map.py": "REMOTE = True\n"})
push(dev)
u.check()
s = wait(u)
check("two new versions on GitHub: ready, listing what's new",
      s["state"] == "ready" and s["new"] == ["Phone remote", "Weather at the station"], f"{s['detail']}: {s['new']}")
u.install()
s = wait(u)
check("install: fast-forwarded, restart due",
      s["state"] == "installed" and u.restart_due and (radio / "radio.py").read_text() == "VERSION = 2\n",
      s["version"])
check("the radio's own files are left alone", (radio / "settings.json").read_text() == "{}")
check("no pip run when requirements.txt didn't change", not pip_calls)

# A new package
u = Updater(radio, pip=lambda: pip_calls.append(1))
commit(dev, "Needs segno", {"requirements.txt": "numpy\nsegno\n"})
push(dev)
u.start()
wait(u)
u.install()
s = wait(u)
check("requirements.txt changed: pip runs", s["state"] == "installed" and pip_calls == [1])

# A broken version goes back
u = Updater(radio, pip=lambda: None)
good = git(radio, "rev-parse", "HEAD")
commit(dev, "Oops", {"radio_map.py": "def broken(:\n"})
push(dev)
u.start()
wait(u)
u.install()
s = wait(u)
check("a version that won't start: back to the old one",
      s["state"] == "failed" and git(radio, "rev-parse", "HEAD") == good
      and (radio / "radio_map.py").read_text() == "REMOTE = True\n" and not u.restart_due, s["detail"])
commit(dev, "Fix", {"radio_map.py": "REMOTE = True\nFIXED = True\n"})
push(dev)

# Changes of its own
(radio / "radio.py").write_text("VERSION = 'mine'\n")
u = Updater(radio)
u.start()
s = wait(u)
check("edited files: it won't update", s["state"] == "own", s["detail"])
u.install()
s = wait(u)
check("...even when asked", s["state"] == "own" and (radio / "radio.py").read_text() == "VERSION = 'mine'\n")
git(radio, "checkout", "--", "radio.py")
git(radio, "merge", "-q", "--ff-only", "origin/main")
commit(radio, "Local experiment", {"radio.py": "VERSION = 'local'\n"})
u = Updater(radio)
u.start()
s = wait(u)
check("a commit not on GitHub: it won't update", s["state"] == "own", s["detail"])
git(radio, "reset", "-q", "--hard", "origin/main")

# Night installs
hour = [14]
u = Updater(radio, auto=True, local_hour=lambda: hour[0], pip=lambda: None)
commit(dev, "Night update", {"radio.py": "VERSION = 3\n"})
push(dev)
u.start()
wait(u)
u.tick(quiet=True)
s = wait(u)
check("automatic, but daytime: waits", s["state"] == "ready")
hour[0] = 3
u.tick(quiet=False)
s = wait(u)
check("3 am but someone's listening: waits", s["state"] == "ready")
u.tick(quiet=True)
time.sleep(0.2)
s = wait(u)
check("3 am and quiet: installs", s["state"] == "installed" and u.restart_due)
u = Updater(radio, auto=False, local_hour=lambda: 3)
commit(dev, "Another", {"radio.py": "VERSION = 4\n"})
push(dev)
u.start()
wait(u)
u.tick(quiet=True)
time.sleep(0.2)
s = wait(u)
check("automatic updates off: never by itself", s["state"] == "ready")

# No network, no checkout
git(radio, "remote", "set-url", "origin", str(BASE / "nowhere.git"))
u = Updater(radio)
u.start()
s = wait(u)
check("GitHub unreachable: says so", s["state"] == "offline", s["detail"][:60])
plain = BASE / "plain"
plain.mkdir()
u = Updater(plain)
u.start()
s = wait(u)
check("not a git checkout: off", s["state"] == "off")

if all(results):
    print(f"\nall {len(results)} updater checks passed")
else:
    print(f"\n{results.count(False)} of {len(results)} updater checks FAILED")
    sys.exit(1)
