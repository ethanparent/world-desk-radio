# World Desk Radio on a Raspberry Pi

On the radio, a Raspberry Pi Zero 2 W runs the same Python program with no window: the round face
draws the map, and the trackball and knobs are wired to the Pi's pins. The parts list and the pin
map are in [PLAN.md](../PLAN.md); the case is in [case/](../case/README.md).

## Setting up the SD card

1. Flash **Raspberry Pi OS Lite (64-bit)** with Raspberry Pi Imager. In Imager's settings, set the
   hostname to `worlddeskradio`, your Wi-Fi, an SSH key, and a user name (the tools here assume `pi`;
   pass `--host you@worlddeskradio.local` to them if you pick another).
2. Log in and fetch the radio:

   ```bash
   ssh pi@worlddeskradio.local
   sudo apt install -y git
   git clone https://github.com/ethanparent/world-desk-radio.git ~/world-desk-radio
   ```

3. Install it (next section). The first start downloads the station list; it's quicker to copy
   `data/` over from a computer that already has it.

## Making it an appliance

`sh pi/install.sh` (on the Pi, from the checkout) installs what the radio needs,
sets it up as a service that starts at boot and comes back if it stops
(`pi/worlddeskradio.service`, with `--lan` so the phone page is
http://worlddeskradio.local:8737), and a monthly station-list refresh
(`pi/worlddeskradio-stations.timer`, running `radio.py --refresh-stations`:
the new list is swapped in only once it's complete, and the radio starts again
on it when nobody's listening). Bluetooth goes through BlueZ, Wi-Fi through
NetworkManager; with no Wi-Fi for 45 seconds the radio opens its own network and
the setup card. `sh pi/overlay.sh` protects the SD card with the read-only
overlay, keeping the checkout, Bluetooth pairings and Wi-Fi networks on a small
writable spot.

```
systemctl --user status worlddeskradio      # is it running?
journalctl --user-unit worlddeskradio -f      # its lines as they come
systemctl --user stop worlddeskradio        # stop it, to run it by hand over SSH
```

## The trackball and knobs

`worlddeskradio/controls.py` doesn't care where its input comes from. On the Pi,
`worlddeskradio/knobs.py` reads the trackball and the two knobs through gpiozero and
turns them into the same calls the mouse and keys make; `--headless` starts it by
itself (`--no-knobs` leaves the pins alone). The pins only note what happened;
the main loop makes the calls, so nothing runs on two threads at once.

- **Trackball:** each flip of a direction pin is one step of the globe, and a
  quick flick goes further. Press: zoom; double press: surprise me; hold: next theme.
- **Tune knob:** the next station around where you stopped; press: next
  favorite; hold: save this spot.
- **Volume knob:** volume (through the system mixer, `wpctl`, so it works for
  Bluetooth too); press: mute; hold: standby.
- **Both knob buttons:** tap: like the song; hold: the settings menu (Bluetooth,
  phone remote's QR code, Wi-Fi setup). Turn Tune to choose, press it to open.

The knobs bounce a lot, so a click only counts when the knob reaches its next
resting point having passed through the steps in between. The ball sits a
quarter turn round in the case: if it moves the globe the wrong way, start it
once with `--ball-turn 90` (or 180, 270), and `--ball-mirror` if left and right
are still swapped; the radio remembers. `--debug-knobs` prints every step,
click and press. Until the ball's light resistors are checked, its four lights
are lit only through the pins' weak pull-ups (on or off, never driven); once
they are, `"ball_lights": "pwm"` in `settings.json` dims them properly.

## New face firmware over Wi-Fi

The face can't update itself over the air (its 10 MB of firmware leaves no
room in its flash for a second copy), but the Pi is on its USB cable, so the Pi
flashes it. From the laptop, on the same network as the radio:

```bash
python tools/flash_face.py
```

It builds `face/` with the Arduino IDE's own arduino-cli (the same as Sketch >
Export Compiled Binary), copies the firmware to the Pi, and runs
`pi/flash_face.sh` there: that stops the radio, flashes the face with esptool
(it resets the face into its bootloader by itself, no buttons), and starts the
radio again, even if the flashing fails. `--no-build` flashes what's already in
`face/build`, and `--host <user>@<address>` reaches the Pi if `worlddeskradio.local`
doesn't. esptool comes with `requirements.txt` on the Pi; the script adds it if
the Pi's `.venv` doesn't have it yet. The read-only overlay can stay on: the
`.venv` is on the writable spot.
