# Running World Desk Radio on a computer

Everything the radio does works on a laptop or desktop (Windows, macOS or Linux), with the mouse
and keyboard standing in for the trackball and knobs. Bluetooth and Wi-Fi setup use pretend
devices and networks there.

## Install

You need Python 3.10 or newer and an audio player: [mpv](https://mpv.io) (recommended) or
`ffplay` from FFmpeg.

| System | Install mpv |
| --- | --- |
| Windows | `winget install shinchiro.mpv`, then open a new terminal so `mpv` is on your PATH |
| macOS | `brew install mpv` |
| Debian, Ubuntu, Raspberry Pi OS | `sudo apt install mpv` |

```bash
python -m pip install -r requirements.txt
```

## The text dial

The simplest version: a line of text instead of the map.

```
python radio.py
```

The first run downloads the station list from Radio Browser into `data/`. Only
about 13,000 of their ~55,000 working stations have map coordinates, and that's
all we fetch. Their server can be slow (it has run at 15–50 KB/s), so the first
download can take 30–60 minutes. It's paged and resumable: press **Ctrl+C** to
stop early and start the dial with what has arrived (a random worldwide sample),
then run `python radio.py --update` later to fetch the rest.

| Key | Does |
| --- | --- |
| ← / → | longitude knob |
| ↑ / ↓ | latitude knob |
| + / - | step size (0.1° to 10° per press) |
| Q / Esc | quit |

Options: `--start 37.57,126.98` (start position), `--settle 0.4` (seconds of
stillness before tuning), `--timeout 5` (seconds to wait for a stream before trying
the next-nearest station).

## The map window

The radio's screen, in a window on the laptop:

```
python radio.py --map
```

The window shows the real face's 480×480 round screen pixel for pixel. The real
face is 2.8″ (about 71 mm) across, so smaller than the window. This view is the design
reference the face copies. The first run also downloads the Natural Earth
coastlines (about 12 MB) into `data/`.

| Mouse / key | Does | On the real radio |
| --- | --- | --- |
| Drag the map (or ← → ↑ ↓) | move the globe | roll the trackball |
| Click (or W) | zoom: 2,000 km → 500 km → whole world → back to 2,000 km | press the trackball |
| Double-click (or R) | surprise me: fly to a random station somewhere in the world | double-press the trackball |
| Right-click or long click (or T) | next color theme: night, radar, ice, paper, mono | long-press the trackball |
| Mouse wheel (or [ / ]) | next / previous station around where you stopped: from the nearest (already on) to the 2nd nearest, 3rd, … | turn the Tune knob |
| F | next favorite | press the Tune knob |
| Shift+F | save this spot as a favorite | long-press the Tune knob |
| + / - | volume | turn the Volume knob |
| M | mute / unmute | press the Volume knob |
| Z | standby: the screen and sound fade out (any key or click fades them back in) | long-press the Volume knob |
| H | like the song playing | tap both knob buttons |
| S | settings menu: Bluetooth, Phone remote, Wi-Fi setup (turn Tune to choose, press it to open) | hold both knob buttons |
| B | Bluetooth menu (pretend headphones and speakers on the laptop) | hold both knob buttons, then Bluetooth |
| N | Wi-Fi setup, as when the radio finds no network (pretend networks) | hold both knob buttons, then Wi-Fi setup (and automatic with no network) |
| P | the phone remote's QR code | hold both knob buttons, then Phone remote |
| Q / Esc | quit | — |

Drag the map like an online map. Each arrow press moves 1/40 of the visible map
(50 km at 2,000 km across, 12 km at 500 km), and holding a key speeds it up. Favorites are saved in `favorites.json`, which isn't in git. On
the laptop a volume change restarts the station after a short pause, because
ffplay can't change volume while playing. On the Pi, volume goes through the
system mixer instead (or mpv's control socket), at once.

### What's on the screen

What's on the screen: under the coordinates, the time and the weather at the
station while you roll and listen ("3:12 pm", a cloud, "52°"; the weather comes
from Open-Meteo, free and without an account), and a small symbol while it
tunes (and for a moment once it's on air, or if nothing comes in); under the place, how far the nearest station is
while you roll and the song once it plays (when the station sends it; long
titles scroll once); stars
on your favorite spots, faint borders between countries, the names of a few
major cities once you zoom in, and after 2 minutes
untouched the day and date over a big clock, with the whole world turning
slowly behind like a desk globe (the crosshair steps aside for it). When a station comes on air,
the globe glides gently until it sits under the crosshair, and 15 seconds later
the crosshair fades away until you touch something. Stations fade in over
the static. The dot in the
window's bottom-right corner is the trackball's light. It starts where you left
off (zoom, theme and volume too), kept in `settings.json`.

### The phone remote

The phone page (its address is printed when the map starts, http://127.0.0.1:8737
on the laptop) is a remote: previous and next station, like the song, save it as
a favorite, surprise me, a search box (type a station's name, a city, a country or a style
like "jazz", tap a result and the globe flies there), a volume slider, mute and standby, with the weather and
time at the station and your favorites to tap. Its Map card picks the world
(Earth or a game world you've installed) and the map's colors, each theme shown
as a little globe in its own colors. It also sets two alarms, each
on the days you pick, that wake you to a station, fading in over a minute
(while one rings, M snoozes it for 9 minutes and F turns it off, as the Volume
and Tune buttons will; the phone page has Snooze and Turn off buttons too); a
sleep timer that fades out and goes to standby; a world tour (a new random
station every 3 minutes); the screen's brightness; °F or °C, country borders,
city names and the idle spin on or off (the spin pauses while the sleep timer runs); and
lists the songs you liked, each with a Spotify button that looks it up in the
Spotify app (no account setup: you like it or add it to a playlist there). To use it from your phone, start with `--lan` (a phone
on the same Wi-Fi can then open it; Windows may ask whether to let Python on the
network) and press P for a QR code to scan. Stations that keep failing to start
are skipped for a few days (`health.json`), so stopping somewhere means music,
not static.

### Updates

The radio keeps its own software up to date: every few hours it checks GitHub
for a newer version, and the phone page lists what's new with an Install button.
With "Install updates by itself" on (the default on the Pi), it installs at night
while it's quiet. Installing tries the new version first and goes back if it
won't start, then the radio restarts. A copy with changes of its own (like this
laptop's, while you work on it) never updates itself.

### Bluetooth and Wi-Fi

Bluetooth and Wi-Fi setup are shown with pretend devices and networks on the
laptop. B opens the Bluetooth menu: turn the mouse wheel to choose, F to
connect, M to close. N starts Wi-Fi setup as the radio would when it finds no
network: the face shows a QR code, and the console prints the address of the
setup page (http://127.0.0.1:8737). Open it in a browser, pick a network and
type `worlddeskradio`, the pretend networks' password.

### Extras

Extras: `--theme radar` starts in another theme, `--size 240` previews the 1.28″
fallback screen, `--scale 2` makes the window bigger.
`python -m worlddeskradio.map_view --sheet themes.png` saves every theme side by side. `python -m worlddeskradio.map_view --png map.png --at 37.57,126.98` saves one
frame as an image, and `python -m worlddeskradio.map_view --bench` measures drawing speed.

### Driving the round face

`--virtual-face` draws the window only from the
messages the face will get over USB (see [PLAN.md](../PLAN.md)), `--compare` shows that next
to the normal preview, and `--face COM5` drives the real face.
`python tools/face_data.py` packs the coastlines, stations and themes into C
headers for it.

### Headless, as on the Pi

On the Pi there's no window, and the face draws the map, so the Pi
runs the radio headless:

```
python radio.py --headless --face /dev/ttyACM0
```

It's the same radio (the phone page, the alarm, updates and all), but it
opens no window and never loads the map drawing code, so the Pi's processor is
free for the sound and the face's messages. The keys are the map window's, read
from the terminal, so it works over SSH: arrows roll the globe, W zooms, T
changes the theme, R surprises, [ ] tune, F / Shift+F for favorites, +/-, M and
Z for the volume knob, H likes the song, B, N and P open the cards, Q quits. It
prints a line of text when the station, the song or anything else changes.
`python radio.py --headless` tries it on the laptop, with `--face COM5` to
drive the real face too. `requirements.txt` leaves pygame out on the Pi.

## Your own stations

`my-stations.json` holds stations you add by hand. Same shape as Radio Browser
records; the minimum is:

```json
[
  {
    "name": "My Favorite Station",
    "url_resolved": "https://example.com/stream.mp3",
    "geo_lat": 37.57,
    "geo_long": 126.98,
    "country": "South Korea",
    "language": "korean"
  }
]
```

Give an entry the `stationuuid` of a Radio Browser station to override that
station. Edits apply the next time you start the dial; no re-download needed.

## Checking that everything works

```bash
python tests/run_all.py
```

runs every check, one after another (about 3 minutes; `--quick` skips the
end-to-end face test and takes about 30 seconds). They work on copies in a temporary
folder, so your favorites, settings and liked songs are never touched, and it's
fine to run them while the radio plays.

- `tests/check_features.py` checks the radio's features with stand-ins (no
  sound, no network): the dial, the controls, alarms and snooze, the sleep
  timer, weather, the phone remote and its server, the idle spin, borders, cities,
  brightness and more. It stops at the first failure and says which.
- `tests/check_pi.py` checks the Pi's own parts without the Pi: the knob and
  trackball decoding (and, with gpiozero installed, the whole thing on its
  pretend pins), the system mixer, and Bluetooth and Wi-Fi against made-up
  `bluetoothctl` and `nmcli` answers.
- `tests/check_updater.py` checks software updates against throwaway git
  repos: installing, refusing a copy with changes of its own, going back when a
  new version won't start, and night installs.
- `tests/check_worlds.py` checks game worlds on a pretend island with stations
  of test tones: loading, the clock, the dial, switching, the face's messages
  and the map (needs ffmpeg).
- `tests/check_face.py` runs the whole radio in a hidden window from a script of
  mouse moves, keys and phone-page requests, with the sound muted. At each
  checkpoint the virtual face, drawn only from the messages a real face would
  get, must match the preview pixel for pixel. It saves a screenshot of each
  checkpoint.
