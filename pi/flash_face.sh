#!/bin/sh
# Flashes the face (the ESP32-S3 round screen) from the Pi, over their USB cable, so new face
# firmware never needs the radio opened up or plugged into the laptop. tools/flash_face.py on the
# laptop builds the firmware, copies it and this script to the Pi, and runs it as:
#
#     sh flash_face.sh <folder> -fm dio -ff 80m -fs 16MB 0x0 face.ino.bootloader.bin 0x8000 ...
#
# the folder holding the .bin files, then esptool's write_flash options and (offset, file) pairs.
# It stops the radio (it holds the face's port), flashes, and starts the radio again, even if the
# flashing fails. A copy of the radio run by hand over SSH is left alone: quit that first.
# esptool comes with requirements.txt; if the Pi's .venv doesn't have it yet, it's added here.

set -eu
DIR=${1:?Usage: sh flash_face.sh <folder> <write_flash arguments>}
shift
PORT=${FACE_PORT:-/dev/ttyACM0}
PY=$HOME/world-desk-radio/.venv/bin/python

if [ ! -e "$PORT" ]; then
    echo "No face on $PORT: is its USB cable plugged into the Pi?" >&2
    exit 1
fi
if ! "$PY" -m esptool version >/dev/null 2>&1; then
    echo "Adding esptool to the radio's Python (.venv) ..."
    "$PY" -m pip install --quiet "esptool>=4.7"
fi

WAS_RUNNING=no
if systemctl --user is-active --quiet worlddeskradio; then
    WAS_RUNNING=yes
fi
start_again() {
    if [ "$WAS_RUNNING" = yes ]; then
        echo "Starting the radio again ..."
        systemctl --user start worlddeskradio
    fi
}
trap start_again EXIT

if [ "$WAS_RUNNING" = yes ]; then
    echo "Stopping the radio (it holds the face's port) ..."
    systemctl --user stop worlddeskradio
fi
if pgrep -f radio.py >/dev/null; then
    echo "Another copy of the radio is running (run by hand, or the monthly station refresh;" >&2
    echo "pgrep -af radio.py shows it): quit it or let it finish, then try again." >&2
    exit 1
fi

SINCE=$(date '+%Y-%m-%d %H:%M:%S')
cd "$DIR"
# The face's USB is the S3's own (USB-Serial/JTAG): esptool resets it into its bootloader and back
# by itself, no buttons. write_flash checks each part after writing it.
"$PY" -m esptool --chip esp32s3 --port "$PORT" --baud 921600 \
    --before default_reset --after hard_reset write_flash "$@"
echo "Flashed."

trap - EXIT
start_again
if [ "$WAS_RUNNING" = yes ]; then
    # Wait for the radio to find the face again, and show what it says about it.
    i=0
    while [ $i -lt 30 ]; do
        if journalctl --user-unit worlddeskradio --since "$SINCE" -o cat 2>/dev/null | grep -q "Face: connected"; then
            break
        fi
        sleep 1
        i=$((i + 1))
    done
    sleep 2
    journalctl --user-unit worlddeskradio --since "$SINCE" -o cat 2>/dev/null | grep "Face" || \
        echo "The radio hasn't mentioned the face yet: journalctl --user-unit worlddeskradio -f"
fi
