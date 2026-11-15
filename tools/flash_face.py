"""Update the face's firmware over Wi-Fi: build face/ on the laptop, copy it to the Pi, and have the
Pi flash the face over their USB cable (pi/flash_face.sh). Nothing to unplug or open up.

    python tools/flash_face.py                     # build, copy, flash
    python tools/flash_face.py --no-build          # flash what's already in face/build
    python tools/flash_face.py --host you@192.168.1.50       # another user name, or if terradio.local doesn't resolve
    python tools/flash_face.py --build-only        # just build (same as the IDE's Export Compiled Binary)

It builds with the arduino-cli that comes inside the Arduino IDE, with the IDE's own settings, and
the board options face/ needs (FQBN below: Tools > Partition Scheme > Custom and the rest). The
face can't update itself over the air: its firmware (about 10 MB, with the map data) leaves no room
in the 16 MB flash for a second copy (face/partitions.csv), so the Pi does it, as it's on the cable.

The laptop and the Pi need to be on the same network.
Without an SSH key, it asks for the Pi's password twice (copying, then flashing).
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKETCH = ROOT / "face"
BUILD = SKETCH / "build" / "esp32.esp32.esp32s3"
FQBN = ("esp32:esp32:esp32s3:FlashSize=16M,PSRAM=opi,PartitionScheme=custom,CDCOnBoot=cdc,"
        "CPUFreq=240,FlashMode=qio")
HOST = "pi@terradio.local"           # the user and hostname you set in Raspberry Pi Imager
REMOTE_DIR = "/tmp/terradio-face"
ARDUINO_CLI = [Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Arduino IDE" / "resources" / "app" / "lib"
               / "backend" / "resources" / "arduino-cli.exe"]
IDE_CONFIG = Path.home() / ".arduinoIDE" / "arduino-cli.yaml"
# esptool's short names for write_flash's flash options (the same in esptool 4 and 5).
SHORT = {"--flash-mode": "-fm", "--flash_mode": "-fm", "--flash-freq": "-ff", "--flash_freq": "-ff",
         "--flash-size": "-fs", "--flash_size": "-fs"}


def find_cli():
    found = shutil.which("arduino-cli")
    if found:
        return found
    for path in ARDUINO_CLI:
        if path.exists():
            return str(path)
    sys.exit("No arduino-cli: install the Arduino IDE (it comes inside), or build in the IDE with\n"
             "Sketch > Export Compiled Binary and run this again with --no-build.")


def build():
    cmd = [find_cli(), "compile", "--fqbn", FQBN, "--export-binaries", str(SKETCH)]
    if IDE_CONFIG.exists():                  # the IDE's folders (its libraries, its ESP32 package)
        cmd[1:1] = ["--config-file", str(IDE_CONFIG)]
    print("Building face/ (a few minutes: the map data is big) ...", flush=True)
    started = time.monotonic()
    if subprocess.run(cmd).returncode != 0:
        sys.exit("The build failed (above).")
    print(f"Built in {time.monotonic() - started:.0f} s.")


def newest_source():
    files = [p for p in SKETCH.iterdir() if p.suffix in (".ino", ".h", ".cpp", ".c", ".csv")]
    return max(files, key=lambda p: p.stat().st_mtime)


def flash_plan():
    """write_flash's arguments from the build's flash_args: [options..., offset, file, ...], and the files."""
    args_file = BUILD / "flash_args"
    if not args_file.exists():
        sys.exit(f"No {args_file.relative_to(ROOT)}: build first (leave out --no-build).")
    lines = [line.split() for line in args_file.read_text().splitlines() if line.strip()]
    options, parts = [], []
    for words in lines:
        if words[0].startswith("-"):
            for i in range(0, len(words) - 1, 2):
                options += [SHORT.get(words[i], words[i]), words[i + 1]]
        else:
            parts.append((words[0], words[1]))
    files = [BUILD / name for _, name in parts]
    missing = [f.name for f in files if not f.exists()]
    if missing:
        sys.exit(f"The build is missing {', '.join(missing)}: build again.")
    return options + [x for part in parts for x in part], files


def run(cmd, what):
    if subprocess.run(cmd).returncode != 0:
        sys.exit(f"{what} failed (above). Is the Pi on and on this network? Try --host <user>@<its address>.")


def main():
    ap = argparse.ArgumentParser(description="Build the face's firmware and flash it from the Pi, over Wi-Fi.")
    ap.add_argument("--host", default=HOST, help=f"the Pi, as for ssh (default {HOST})")
    ap.add_argument("--no-build", action="store_true", help="flash what's in face/build as it is")
    ap.add_argument("--build-only", action="store_true", help="build, but don't flash")
    args = ap.parse_args()

    if not args.no_build:
        build()
    if args.build_only:
        return 0
    write_args, files = flash_plan()
    source = newest_source()
    built = (BUILD / "face.ino.bin").stat().st_mtime
    if source.stat().st_mtime > built:
        print(f"Note: face/{source.name} changed after this build, so the face gets the older firmware.\n"
              "      Leave out --no-build to build it first.")
    for tool in ("ssh", "scp"):
        if not shutil.which(tool):
            sys.exit(f"No {tool}: turn on Windows' OpenSSH Client (Settings > System > Optional features).")

    size = sum(f.stat().st_size for f in files) / 1e6
    print(f"Copying the firmware ({size:.1f} MB) to {args.host} ...", flush=True)
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp) / Path(REMOTE_DIR).name         # copied whole, as REMOTE_DIR
        folder.mkdir()
        for f in files:
            shutil.copy2(f, folder / f.name)
        # The Pi's shell needs Unix line ends, whatever git did to the file on Windows.
        (folder / "flash_face.sh").write_bytes((ROOT / "pi" / "flash_face.sh").read_bytes().replace(b"\r\n", b"\n"))
        run(["scp", "-q", "-r", str(folder), f"{args.host}:{Path(REMOTE_DIR).parent.as_posix()}/"],
            "Copying to the Pi")
    print("Flashing the face from the Pi ...", flush=True)
    run(["ssh", args.host, f"sh {REMOTE_DIR}/flash_face.sh {REMOTE_DIR} {' '.join(write_args)}; "
                           f"status=$?; rm -rf {REMOTE_DIR}; exit $status"], "Flashing")
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
