#!/bin/sh
# Protects the SD card from sudden unplugging (Stage 5): Raspberry Pi OS's read-only overlay keeps
# the system unchanged (writes go to memory and vanish at the next boot), and the few things that
# must last go to a small writable spot: a disk image on the boot partition, mounted at boot.
# What lives there:
#     ~/world-desk-radio                   the checkout: code, .venv, favorites, settings, liked
#                                          songs, the station list (so software updates still work)
#     /var/lib/bluetooth                   paired headphones and speakers
#     /etc/NetworkManager/system-connections   Wi-Fi networks joined from the setup page
#
#     sh pi/overlay.sh prepare     make the writable spot, move those there, reboot (overlay still off)
#     sh pi/overlay.sh on          turn the read-only overlay on, reboot
#     sh pi/overlay.sh off         turn it off again (for apt upgrades, or changes to the system), reboot
#     sh pi/overlay.sh status
#
# Run prepare first and check the radio still works after its reboot before running on.

set -eu
IMG=/boot/firmware/worlddeskradio-data.img
SPOT=/mnt/worlddeskradio-data
ME=$(id -un)
CHECKOUT=$(cd "$(dirname "$0")/.." && pwd)
KEEP_FREE_MB=64              # left free on the boot partition for kernel updates

overlay_on() {
    grep -Eq "overlayroot=tmpfs|boot=overlay" /boot/firmware/cmdline.txt 2>/dev/null || [ "$(findmnt -no FSTYPE /)" = overlay ]
}

case "${1:-status}" in
status)
    if overlay_on; then echo "Read-only overlay: on"; else echo "Read-only overlay: off"; fi
    if findmnt "$SPOT" >/dev/null; then
        echo "Writable spot: $IMG at $SPOT"
        df -h "$SPOT" | tail -1
    else
        echo "Writable spot: not set up (sh pi/overlay.sh prepare)"
    fi
    ;;

prepare)
    if findmnt "$SPOT" >/dev/null; then
        echo "The writable spot is already set up."; exit 0
    fi
    if overlay_on; then
        echo "Turn the overlay off first: sh pi/overlay.sh off" >&2; exit 1
    fi
    if pgrep -f radio.py >/dev/null; then
        echo "The radio is running: stop it first (systemctl --user stop worlddeskradio, or Q)." >&2; exit 1
    fi
    FREE_MB=$(df -m --output=avail /boot/firmware | tail -1 | tr -d ' ')
    NEED_MB=$(du -sm "$CHECKOUT" | cut -f1)
    SIZE_MB=$((FREE_MB - KEEP_FREE_MB))
    echo "Boot partition: ${FREE_MB} MB free. The checkout takes ${NEED_MB} MB."
    if [ "$SIZE_MB" -lt $((NEED_MB + 64)) ]; then
        echo "Not enough room on the boot partition for the writable spot." >&2; exit 1
    fi
    echo "Making a ${SIZE_MB} MB writable spot at $IMG ..."
    sudo fallocate -l "${SIZE_MB}M" "$IMG"
    sudo mkfs.ext4 -q -L worlddeskradio "$IMG"
    sudo mkdir -p "$SPOT"
    sudo mount -o loop,noatime "$IMG" "$SPOT"
    sudo mkdir -p "$SPOT/worlddeskradio" "$SPOT/bluetooth" "$SPOT/system-connections"
    sudo chown "$ME:$ME" "$SPOT/worlddeskradio"
    echo "Copying the checkout, Bluetooth pairings and Wi-Fi networks ..."
    cp -a "$CHECKOUT/." "$SPOT/worlddeskradio/"
    sudo cp -a /var/lib/bluetooth/. "$SPOT/bluetooth/" 2>/dev/null || true
    sudo cp -a /etc/NetworkManager/system-connections/. "$SPOT/system-connections/"
    sudo chmod 700 "$SPOT/bluetooth" "$SPOT/system-connections"
    sudo umount "$SPOT"
    sudo cp /etc/fstab /etc/fstab.before-worlddeskradio
    sudo tee -a /etc/fstab >/dev/null <<EOF
# World Desk Radio's writable spot (pi/overlay.sh), kept when the rest is read-only
$IMG $SPOT ext4 loop,noatime,commit=30,nofail,x-systemd.requires-mounts-for=/boot/firmware 0 2
$SPOT/worlddeskradio $CHECKOUT none bind,nofail,x-systemd.requires-mounts-for=$SPOT 0 0
$SPOT/bluetooth /var/lib/bluetooth none bind,nofail,x-systemd.requires-mounts-for=$SPOT 0 0
$SPOT/system-connections /etc/NetworkManager/system-connections none bind,nofail,x-systemd.requires-mounts-for=$SPOT 0 0
EOF
    echo "Done. Rebooting so everything comes up from the writable spot (the overlay is still off)."
    echo "After it's back, check the radio plays, then: sh pi/overlay.sh on"
    sudo reboot
    ;;

on)
    if ! findmnt "$SPOT" >/dev/null; then
        echo "Set up the writable spot first: sh pi/overlay.sh prepare" >&2; exit 1
    fi
    echo "Turning the read-only overlay on (the boot partition stays writable), then rebooting ..."
    sudo raspi-config nonint do_overlayfs 0
    sudo reboot
    ;;

off)
    echo "Turning the read-only overlay off, then rebooting ..."
    sudo raspi-config nonint do_overlayfs 1
    sudo reboot
    ;;

*)
    echo "Usage: sh pi/overlay.sh [status|prepare|on|off]" >&2; exit 2
    ;;
esac
