#!/bin/sh
# Makes the Pi a Terradio appliance (Stage 5). Run it on the Pi, from the checkout, as yourself
# (not with sudo; it asks for sudo where it needs it):
#
#     cd ~/terradio && sh pi/install.sh
#
# It's safe to run again. What it does:
#   1. installs what the radio needs from Raspberry Pi OS (mpv, PipeWire and its Bluetooth part,
#      BlueZ, avahi, lgpio for the pins), if missing
#   2. sets up .venv with the Python packages (requirements.txt), reaching the system's lgpio
#   3. adds you to the groups for the pins (gpio), the face's USB port (dialout) and Bluetooth
#   4. installs the radio as a user service that starts at boot (pi/terradio.service), and the
#      monthly station-list refresh (pi/terradio-stations.timer), and turns on "lingering" so
#      they run without anyone logging in
#   5. announces the phone page as http://<hostname>.local:8737 (avahi)
#   6. Wi-Fi setup: while the radio's own network is up, every web address leads to the setup
#      page, so it pops up on a phone by itself
# It doesn't start the radio (it may already be running by hand) and doesn't touch the
# read-only overlay (that's pi/overlay.sh, later).

set -eu
cd "$(dirname "$0")/.."
HERE=$(pwd)
ME=$(id -un)
PORT=8737

if [ "$(id -u)" = 0 ]; then
    echo "Run this as yourself, not with sudo: it sets up your own user service." >&2
    exit 1
fi

echo "== 1. Packages"
NEED=""
for pkg in mpv pipewire pipewire-pulse wireplumber libspa-0.2-bluetooth bluez avahi-daemon \
           python3-venv python3-lgpio network-manager nftables; do
    dpkg -s "$pkg" >/dev/null 2>&1 || NEED="$NEED $pkg"
done
if [ -n "$NEED" ]; then
    echo "Installing:$NEED"
    sudo apt-get update
    # shellcheck disable=SC2086
    sudo apt-get install -y $NEED
else
    echo "All there."
fi

echo "== 2. Python packages (.venv)"
# --system-site-packages lets the venv use the system's lgpio (apt's python3-lgpio), which
# gpiozero needs for the pins; everything else comes from requirements.txt as usual.
if [ ! -x .venv/bin/python ]; then
    python3 -m venv --system-site-packages .venv
elif ! grep -q "include-system-site-packages = true" .venv/pyvenv.cfg; then
    sed -i 's/include-system-site-packages = false/include-system-site-packages = true/' .venv/pyvenv.cfg
fi
.venv/bin/python -m pip install --quiet -r requirements.txt
.venv/bin/python -c "import gpiozero, lgpio" && echo "gpiozero and lgpio: ok"

echo "== 3. Groups"
for group in gpio dialout bluetooth netdev; do
    if getent group "$group" >/dev/null && ! id -nG "$ME" | tr ' ' '\n' | grep -qx "$group"; then
        sudo usermod -aG "$group" "$ME"
        echo "Added you to $group (takes effect after a reboot)."
    fi
done

echo "== 4. The service and the monthly refresh"
UNITS="$HOME/.config/systemd/user"
mkdir -p "$UNITS"
for unit in terradio.service terradio-stations.service terradio-stations.timer; do
    cp "pi/$unit" "$UNITS/$unit"
done
systemctl --user daemon-reload
systemctl --user enable terradio.service
systemctl --user enable --now terradio-stations.timer
sudo loginctl enable-linger "$ME"
# PipeWire is a user service too: make sure it starts at boot along with the radio.
systemctl --user enable pipewire.socket pipewire-pulse.socket wireplumber.service 2>/dev/null || true

echo "== 5. terradio.local"
sudo tee /etc/avahi/services/terradio.service >/dev/null <<EOF
<?xml version="1.0" standalone='no'?>
<!DOCTYPE service-group SYSTEM "avahi-service.dtd">
<service-group>
  <name replace-wildcards="yes">Terradio on %h</name>
  <service>
    <type>_http._tcp</type>
    <port>$PORT</port>
  </service>
</service-group>
EOF
sudo systemctl enable --now avahi-daemon >/dev/null 2>&1 || true
echo "Phone page: http://$(hostname).local:$PORT"

echo "== 6. Wi-Fi setup page pops up on phones"
# Every name looked up on the radio's own network answers with the radio (10.42.0.1) ...
sudo mkdir -p /etc/NetworkManager/dnsmasq-shared.d
echo "address=/#/10.42.0.1" | sudo tee /etc/NetworkManager/dnsmasq-shared.d/terradio-captive.conf >/dev/null
# ... and while that network is up, web requests to port 80 go to the page on port $PORT.
sudo tee /etc/NetworkManager/dispatcher.d/90-terradio-captive >/dev/null <<EOF
#!/bin/sh
# Terradio's Wi-Fi setup (installed by pi/install.sh): send the hotspot's web traffic to the page.
[ "\$CONNECTION_ID" = "terradio-setup" ] || exit 0
case "\$2" in
    up)
        nft add table ip terradio 2>/dev/null
        nft 'add chain ip terradio pre { type nat hook prerouting priority -100; }' 2>/dev/null
        nft flush chain ip terradio pre
        nft add rule ip terradio pre iifname "\$1" tcp dport 80 redirect to :$PORT
        ;;
    down)
        nft delete table ip terradio 2>/dev/null || true
        ;;
esac
EOF
sudo chmod 755 /etc/NetworkManager/dispatcher.d/90-terradio-captive

echo
echo "Done. The radio starts by itself at the next boot. To start it now instead:"
echo "    systemctl --user start terradio"
echo "(stop any copy you're running by hand first: pgrep -af radio.py)"
echo "Its lines:  journalctl --user-unit terradio -f"
