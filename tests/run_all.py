"""Run every check, one after another, and say which passed:

    python tests/run_all.py            # about 3 minutes
    python tests/run_all.py --quick    # skips the end-to-end face test: about 30 seconds

    check_features.py   the radio's features with stand-ins (no sound, no network)
    check_pi.py         the Pi's own parts (knobs, mixer, Bluetooth, Wi-Fi) without the Pi
    check_updater.py    software updates, against throwaway git repos
    check_worlds.py     game worlds, on a pretend world of test tones (needs ffmpeg)
    check_face.py       the whole radio in a hidden window, the virtual face against the preview

They need the station list (python radio.py --update); the face test also needs pygame and
ffplay or mpv (it plays muted). Nothing touches your favorites, settings or liked songs: each check
works on its own copies in a temporary folder, so it's fine to run them while the radio plays.
Exit code 0 when everything passed.
"""

import os
import subprocess
import sys
import time
from pathlib import Path

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")     # pygame's hello line, here and in the checks

HERE = Path(__file__).resolve().parent
CHECKS = [("check_features.py", "features"), ("check_pi.py", "the Pi's own parts"),
          ("check_updater.py", "software updates"), ("check_worlds.py", "game worlds"),
          ("check_face.py", "the whole radio, end to end")]


def main():
    sys.stdout.reconfigure(line_buffering=True)      # show progress as it happens, even in a log
    quick = "--quick" in sys.argv[1:]
    failed = []
    for name, what in CHECKS:
        if quick and name == "check_face.py":
            print(f"skipped     {what} ({name}: --quick)")
            continue
        if name == "check_face.py" and not _has("pygame"):
            print(f"skipped     {what} ({name}: needs pygame)")
            continue
        start = time.monotonic()
        print(f"running     {what} ({name}) ...", flush=True)
        run = subprocess.run([sys.executable, str(HERE / name)], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", cwd=HERE.parent,
                             env={**os.environ, "PYTHONIOENCODING": "utf-8"})   # (a ★ in a check's name)
        took = time.monotonic() - start
        lines = run.stdout.strip().splitlines()
        summary = lines[-1] if lines else ""
        if run.returncode == 0:
            print(f"  passed    {summary} ({took:.0f} s)")
        elif run.returncode == 2:
            print(f"  couldn't run: {summary}")
            failed.append(name)
        else:
            failed.append(name)
            print(f"  FAILED    ({took:.0f} s). The end of its output:")
            for line in (lines + run.stderr.strip().splitlines())[-25:]:
                print("            " + line)
    print()
    if failed:
        print(f"Not everything passed: {', '.join(failed)}. Run one on its own for all its output, e.g.")
        print(f"    python tests/{failed[0]}")
        return 1
    print("Everything passed.")
    return 0


def _has(module):
    try:
        __import__(module)
        return True
    except ImportError:
        return False


if __name__ == "__main__":
    sys.exit(main())
