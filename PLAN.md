# Terradio: project plan

A modern, minimal desk radio. Its face is a round screen: roll a small
light-up trackball and the globe turns with it under a fixed crosshair. When you stop,
static gives way to the live station nearest the crosshair. A Tune knob steps
station by station around that spot, and a Volume knob sets the volume. Sound
comes from a built-in speaker, or from Bluetooth headphones when they're connected.

## Decisions so far

| Area | Decision |
| --- | --- |
| Finish | Nice desk object, used daily. Modern minimal look. |
| Budget | $75–150 for hardware. |
| Brain | Raspberry Pi Zero 2 W: streams, sound, Bluetooth, the controls and all station logic, in Python. |
| Face | An ESP32-S3 board with a **true round 480×480 screen** built in: the Waveshare ESP32-S3-LCD-2.8C, a 2.8″ circle about 71 mm across. It only draws the map, in Arduino C++. The Pi tells it what to show over one USB cable, which also powers it. |
| Map | Night style: dark globe, faint coastlines, every station a glowing dot. The globe turns under a fixed center crosshair. |
| Controls | A trackball to roll the globe (a BlackBerry-style trackball module with colored lights under the ball), plus two push-knobs (rotary encoders with built-in switch): Tune and Volume. All wired to the Pi. |
| Sound | One mono speaker (MAX98357A I2S amp). Bluetooth out to headphones/speaker. |
| Modes in v1 | Favorites. |
| Power | Wall power through a USB-C socket on the box. |
| Setup | Wi-Fi and SSH set once when flashing the SD card; changes over SSH from the laptop. If the radio ever finds no Wi-Fi it knows, it opens its own network and a setup page for your phone (scan the QR code on the face, pick your Wi-Fi, type the password). Bluetooth headphones and speakers are paired on the radio itself, with the knobs. The face is programmed from the Arduino IDE on the laptop. |
| Tools | Soldering iron, 3D printer, basic woodworking. New to CAD. |
| Parts | Everything from AliExpress, bought at once. |

Ruled out, and why:
- **A physical pointer or arm on a printed map:** replaced by the screen map. It has no moving parts and can zoom.
- **Rectangular screens behind a round window** (2.8″ ILI9341, 3.5″ ST7796S, 2.8″ HDMI or DSI): they work, but a truly circular display was the goal.
- **1.28″ GC9A01 round screen on the Pi alone:** round, cheap and simple, but small (~32 mm) and only 240 px. Kept as the **fallback** if the face board goes wrong.
- **HyperPixel 2.1 Round, or bare 480×480 RGB panels, on the Pi:** they use almost every GPIO pin, leaving no room for the amp or knobs.
- **1.85″ round ST77916 (360×360):** needs a quad-SPI link the Pi can't drive.
- **Raspberry Pi 4 + Waveshare 3.4″ round DSI (800×800):** gorgeous, but about US$190–220 in total and a much bigger box.
- **Two knobs for longitude and latitude:** simple, but diagonal moves and long trips meant a lot of turning.
- **A joystick** (e.g. M5Stack Joystick2, Hall-effect, I2C): precise and drift-free, but rolling a ball suits a globe better. A good fallback if the trackball disappoints.
- **Alps tilt-and-turn knobs** (RKJXT/RKJXM): only 4 or 8 fixed directions with no speed control, solder-only parts, and the 8-way one with a turning ring is discontinued.
- **ESP32-S3 doing everything (no Pi):** cheapest, but it would mean rewriting the radio in C++, it has no Bluetooth for headphones, and stream playback is less robust.

## How it fits together

```
 USB-C socket ──5V──► Raspberry Pi Zero 2 W   (Python: the radio)
                       ├─ Wi-Fi ◄── station streams + Radio Browser
                       ├─ mpv ─► PipeWire ─┬─► I2S ─► MAX98357A ─► speaker
                       │                   └─► Bluetooth ─► headphones
                       ├─ GPIO ◄── trackball + 2 push-knobs
                       └─ USB (data + 5 V) ──► ESP32-S3 round face
                                                (Arduino C++: draws the globe)
```

- **The Pi** works out the dial position, the nearest station and what's
  playing. It already does all of this in `dial.py`.
- **The face** keeps its own copy of the coastlines (built into its program) and
  of the station dots (sent by the Pi when it connects). Many times a second the
  Pi sends one short line, such as "you're at 37.6°N 127.0°E, 2,000 km zoom,
  playing KBS Classic FM in Seoul, volume 60, Bluetooth off", and the face draws it.
- **The face boots in about a second,** so it shows a slowly spinning globe while
  the Pi is still starting up.
- **The Python map view from Stage 1** becomes the design reference the face
  copies, and a laptop preview for trying out design changes.

### Messages from the Pi to the face

One JSON object per line (UTF-8, ending in a newline) over the USB serial link.
The face shows up on the Pi as `/dev/ttyACM0`, and on the laptop as a COM port.
`terradio/face_link.py` writes them. `python radio.py --virtual-face` draws
the laptop window from these messages alone, as the face will, and `--compare`
shows it next to the normal preview.

| Message | When | Example |
| --- | --- | --- |
| `hello` (face → Pi) | Face has booted or reset. The Pi answers with `stations` and `state` | `{"t":"hello","v":1}` (`v` is the protocol version) |
| `stations` | When the Pi starts, after each `hello`, and after the monthly refresh. 256 stations per line, so about 50 lines (70 KB) for 12,765 stations | `{"t":"stations","n":12765,"i":0,"pos":"…"}`: `n` stations in all, this line starts at index `i`, and `pos` is base64 of little-endian int16 (lat, lon) pairs in 1/180ths of a degree (about 600 m) |
| `state` | Up to 30 times a second while anything changes, and at least once a second, so the face knows the Pi is still there (after 3 s of silence it goes back to its waiting globe) | `{"t":"state","lat":37.6,"lon":127.0,"zoom":2000,"theme":"night","status":"playing","name":"KBS Classic FM","place":"Seoul, South Korea","slat":37.5665,"slon":126.978,"km":4,"vol":60,"fav":1,"toast":"Nearest","song":"Clair de Lune - Debussy","local":1082,"clock":1202,"sky":"cloud","temp":61}` |
| `favs` | After `hello`, and whenever a favorite is saved | `{"t":"favs","pos":"…"}`: the favorite spots, packed like `stations`, each marked on the map with a star |
| `sleep` / `wake` | Standby: the face fades to black over `fade` seconds and turns its backlight off; `wake` brightens it again over `fade` seconds (1.5 for the Volume button, 60 for the alarm). No `state` while asleep | `{"t":"sleep","fade":1.5}`, `{"t":"wake","fade":60}` |

The `state` fields. Fields that are false or empty are left out, to keep the
line short. The face does the formatting, as `map_view.face_hud` does:

| Field | Meaning |
| --- | --- |
| `lat`, `lon` | Dial position under the crosshair, to 5 decimals (about 1 m). Shown at the top to 2 decimals |
| `zoom` | km across the window: 2000, 500, or 0 for the whole world |
| `theme` | Color theme: `night`, `radar`, `ice`, `paper` or `mono` |
| `status` | `turning` (rolling), `tuning`, `playing`, `no signal` or `idle`. Under the coordinates is one slot: the time at the station while you roll and while you listen; a symbol (a dot with radio waves) while tuning, with the waves sweeping outward; the symbol in the theme's live color for 3 s when a station starts, then the time again; and a hollow dot struck through for no signal. A toast takes the slot while it shows |
| `name`, `place` | The station shown and its region and country, cut to 64 characters. Left out when there are no stations at all (show "No stations"). A name too long for the screen scrolls once (see below) |
| `slat`, `slon` | That station's position, circled on the map |
| `km` | Distance from the crosshair to that station, rounded. 0 means "under 1 km". Shown under the place only while rolling ("2,140 km away"), when it tells you how far the nearest station is |
| `vol` | Volume, 0–100 |
| `ring` | `1` while the volume ring shows (1.5 s after a change) |
| `mute` | `1` when muted |
| `fav` | `1` when the station is a favorite: a star by its name |
| `toast` | Short message under the coordinates, such as "3rd nearest", "Saved as favorite 1" or "Radar theme". The face fades it in and out |
| `song` | What's playing, from the stream ("Artist - Title"), cut to 64 characters. Shown under the place while the station plays (the line the distance uses while rolling); scrolls once when too long (see below) |
| `local` | Time at the station, in minutes after midnight, shown as "6:02 pm" in the slot under the coordinates (see `status`). Left out when unknown |
| `sky` | The weather at the station, as a small symbol after the time in the same slot: `sun`, `moon` (clear at night), `sun-cloud`, `moon-cloud`, `cloud`, `fog`, `rain`, `snow` or `storm`. Left out when unknown (offline, or still being looked up) |
| `temp` | The temperature there, already in the radio's units (°F unless the phone page says °C), shown as "61°" after the symbol, with a real minus sign below zero |
| `borders` | `1` to draw the borders between countries, fainter than the coastlines (a switch on the phone page, on by default) |
| `cities` | `1` to label the major cities (a switch on the phone page, on by default): a small ring and the name, in the theme's dim text color with a one-pixel outline in the globe's color, drawn over the station light. None in the whole-world view; a city gets a label once the view is 1.2 past the web-map zoom Natural Earth gives it, so only big ones at 2,000 km and more at 500 km. At most 8 at once, biggest first, each to the right of its ring (or the left, if that's taken), kept clear of each other, the text at the top and bottom, and the crosshair; one that doesn't fit isn't drawn. `map_view.MapView._draw_cities` is the reference |
| `spin` | `1` while the idle globe should turn like a desk globe: the face turns it itself, west to east (the land drifting right) about the station's latitude, easing in over 4 s to 2° a second, in steps of 1/15 s. Sent only while idle, when the phone page's switch is on and the sleep timer isn't running. When idle ends, the face glides (or flies) back to the station |
| `clock` | Time where the radio is, in minutes after midnight, for the idle clock |
| `date` | Today where the radio is, `"2026-10-21"`, shown over the idle clock as "Wed Oct 21" (in English; the face works out the weekday) |
| `idle` | `1` when nobody has touched the controls for 2 minutes: the face backs out to the whole world and shows the date, a big clock, the station and the song, with the alarm and sleep timer at the top, over a darker band behind the text |
| `liked` | `1` when the song playing is one you liked: a small heart before it |
| `alarm` | When the next alarm rings (of the two, on their days), minutes after midnight, while one is on. The idle clock shows "Alarm 7:00 am" at the top |
| `timer` | Minutes left on the sleep timer, while it runs. The idle clock shows "Sleep in 23 min" at the top |
| `bright` | Screen brightness, 10–100 % (a slider on the phone page), when it isn't 100: the face sets its backlight. The trackball's light dims with it |
| `bt` | `1` when Bluetooth audio is connected: a small Bluetooth symbol after the coordinates |
| `panel` | A card over the dimmed map, while one is open: `{"title":…, "lines":[…], "items":[[label, detail], …], "sel":n, "qr":"…", "notes":[…]}` (empty parts left out). The Bluetooth menu is a list (`items`, with `sel` the highlighted row, drawn like a drum: neighbors fading above and below). Wi-Fi setup is `lines`, a `qr` code to join the radio's network (the face draws it with an Arduino QR library) and `notes`. The alarm going off is a card too: "Good morning", its time, and how to snooze or turn it off |

`state` lines are usually 190–350 bytes, and about 600 at most (the longest
station name and place in today's list, plus a 64-character song title in
Korean). `stations` lines are up to 1.4 KB, so a 2 KB line buffer on the face is
enough. While the globe is being dragged, the Pi sends about 28–30 `state` lines
a second (6.7 KB/s); when idle, about one a second. That's tiny for the S3's USB
port, and even a 115200-baud serial link (11.5 KB/s) would cope.

## Controls

| Control | Roll / turn | Short press | Long press |
| --- | --- | --- | --- |
| Trackball (on top, front right) | Roll the globe in any direction; flick to cross oceans | Zoom: 2,000 km → 500 km → whole world, then round again. Double press: surprise me (the globe flies to a random station somewhere in the world) | Next color theme |
| Tune knob (left) | Step station by station outward from where you stopped rolling: the nearest is already on, so the first click goes to the 2nd nearest, then the 3rd, … Turning back retraces | Next favorite. While an alarm rings: turn it off | Save this spot as a favorite |
| Volume knob (right) | Volume | Mute. While an alarm rings: snooze 9 minutes (quiet and dark, then the alarm again, fading in over 10 s) | Standby (screen off, audio stops; any turn wakes it) |
| Both knob buttons | | Tap: like the song playing (saved with its station, listed on the phone page) | Bluetooth menu: turn Tune to choose, press it to connect or disconnect, press Volume to close |

Rolling moves the globe the way rolling the ball would, by a fixed fraction of
the visible map per step, so it's fine zoomed in and fast in world view. The
trackball is coarse (about 9 steps per full roll), which is why the Tune knob
does the precise part: you never have to aim at a single dot. Stations that
share a spot (often a city's center point) each get their own Tune click.
When a station comes on air, the dial locks onto it, like a radio's automatic
frequency control: the globe glides gently until the station sits under the
crosshair, without tuning again.

The phone page (`terradio/setup_page.py`) is a remote as well: previous and
next station (the Tune knob's steps), like, save as a favorite, surprise me, a
volume slider, mute and standby, and the favorites list (tap one to tune it). It
shows the weather and time at the station, and sets the alarm, sleep timer,
world tour, °F/°C and software updates. A phone gets there by scanning the QR
code the radio shows (hold both knob buttons, then Phone remote; P on the laptop).

## Shopping list

The best-value option for each part from shops that ship to Australia, checked
on 20 Oct 2026. Prices are in **Australian dollars including GST** and need
checking at checkout. AliExpress shows A$1.49 "new shopper" deals on some items;
the normal price is in brackets.

Total: about **A$175–210**, plus Core Electronics' shipping for the Pi and power supply.

| Part | Where | ≈ A$ |
| --- | --- | --- |
| Raspberry Pi Zero 2 W with soldered headers ("WH") | [Core Electronics](https://core-electronics.com.au/raspberry-pi-zero-2-w-wireless-soldered-male-headers.html) (limit 1 per customer) | 34.20 |
| Official Raspberry Pi USB-C power supply, 5.1 V 3 A | [Core Electronics](https://core-electronics.com.au/raspberry-pi-4-official-power-supply-usb-c-5v-15w-white.html) | 11.90 |
| Waveshare ESP32-S3-LCD-2.8C: round 2.8″ 480×480 screen, 8 MB PSRAM, 16 MB flash (choose "2.8C - No Touch") | [AliExpress](https://www.aliexpress.com/item/1005009463569380.html) | 49.99 |
| BlackBerry-style trackball module with blue, red, green and white LEDs under a clear ball ×2 (one spare). Same seller as the face board, so they can ship together | [AliExpress](https://www.aliexpress.com/item/1005009307385885.html) | 11.18 |
| MAX98357A I2S amp module ×2: one "Big chip soldered" (pins and speaker terminal already on: this one goes in the radio, no soldering) and one "Big chip NO solder" (spare and soldering practice) | [AliExpress](https://www.aliexpress.com/item/1005011932126750.html) | 6–16 |
| EC11 encoders with push switch, threaded bushing + nut, 15 mm knurled shaft, 5-pack (choose "Plum 15mm") | [AliExpress](https://www.aliexpress.com/item/1005012010901888.html) | 5.55 |
| Low black aluminum knobs 20×10 mm, 2-pack (choose "O 20x10mm 2pcs": the O-type insert grips the knurled shaft). With the 15 mm encoders they sit just above the nut | [AliExpress](https://www.aliexpress.com/item/1005006063377230.html) | ~3 |
| AUIN 2″ full-range speakers, 4 Ω 10 W, dual magnet, 2-pack (choose "2 pcs 4 ohm 10W"): 53 mm square frame, screws to four bosses under the top grille. The cheap 50 mm pair you ordered first is a spare | [AliExpress](https://www.aliexpress.com/item/1005006957225238.html) | 9.24 |
| USB-C to micro-USB OTG **data** cable, 27 cm, with **both ends bent 90°** (Pi to face, no soldering). Inside the case the face's USB-C sockets point down toward the knobs, so a straight plug won't fit. The straight cable you ordered first stays handy while the case is open | [AliExpress](https://www.aliexpress.com/item/1005012469612939.html) | 5.30 |
| USB-C power socket board for the back of the box, with a mounting plate (choose "6Pin": reviews confirm it has the 5.1 kΩ resistors). Its plate is glued into a pocket inside the back panel | [AliExpress](https://www.aliexpress.com/item/1005007596840310.html) | 1.49 (~3) |
| 830-point breadboard + 65 jumper wires | [AliExpress](https://www.aliexpress.com/item/1005007171057389.html) | 1.49 (~5) |
| Dupont jumper wire kit: male-male, male-female, female-female | [AliExpress](https://www.aliexpress.com/item/1005012434313344.html) | 1.49 (~8) |
| ANENG AN8008 multimeter | [AliExpress](https://www.aliexpress.com/item/1005008497684166.html) | 28.19 |
| Double-sided perfboard, 20-pack | [AliExpress](https://www.aliexpress.com/item/1005010707074043.html) | 8.49 |
| 2.54 mm single-row pin headers, 1×40, 10 male + 10 female (snap to length) | [AliExpress](https://www.aliexpress.com/item/1005010759653046.html) | 1.49 (~7) |
| Silicone hookup wire, 10 colors (choose 26 AWG) | [AliExpress](https://www.aliexpress.com/item/1005010622872230.html) | 1.49 (~9) |
| Nylon M2.5 standoff kit, mixed lengths, with screws and nuts (choose "200PCS-M2.5 Mix", "black") | [AliExpress](https://www.aliexpress.com/item/32862529967.html) | ~4 |
| Brass heat-set inserts (choose M3). No longer used: the case now holds nuts in slots instead | [AliExpress](https://www.aliexpress.com/item/1005006472962973.html) | 1.49 (~5) |
| No other screws needed: every screw in the case is an M2.5 × 6 mm from the standoff kit above (19 screws, 8 nuts; see case/README.md) | | |

Already have: microSD card, Bluetooth headphones, soldering iron, 3D printer, a
USB-C cable for plugging the face into the laptop, and an old micro-USB cable to
cut up for the power wiring (any phone cable will do).

Buying notes:
- **Pi:** on AliExpress a Zero 2 W costs A$150–225 right now (a manufacturing
  delay), five times Core Electronics' price, so buy it from Core. Make sure it
  says **Zero 2 W**, not "Zero W".
- **Face board:** the No Touch option is enough; the trackball and knobs do the
  controlling (touch is about A$11 more). The seller is a reseller with 50 sales
  and no reviews yet; the same board from bigger sellers is about A$65. If you
  ever pick another board, check it has an **ESP32-S3 with 8 MB PSRAM** (often
  written N16R8 or N8R8), a **round** 480×480 screen (usually an ST7701 chip), a
  USB-C port, and a maker with a wiki and Arduino examples (Waveshare, Elecrow,
  Makerfabs, Guition). Its extras (motion sensor, clock chip, microSD slot,
  battery charger) are harmless.
- **Trackball:** a copy of SparkFun's Trackballer board, with blue, red, green
  and white LEDs under a clear ball (the photo shows BLU, RED, GRN and WHT pins).
  It has no reviews yet, so buy two and keep one as a spare. Power it from the
  Pi's **3.3 V** pin, never 5 V. The Pimoroni Trackball Breakout is the polished
  version, but it's now only sold overseas (about A$45–55 delivered from The Pi
  Hut, UK).
- **Encoders and knobs:** the encoders have a knurled ("plum") 6 mm shaft, so the
  knobs must be the knurled-shaft kind, as linked.
- **Face to Pi, inside the radio:** the angled cable, no soldering. It must
  be a *data* cable, not charge-only. Plug its USB-C end into the face's
  socket marked **UART** (its USB-to-serial chip resets the board for uploads
  by itself, and the firmware keeps USB CDC On Boot: Disabled), turned so the
  cable heads back into the case, and its micro-USB end into the Pi's port
  marked USB (not PWR). Inside the case that socket is at the screen's lower
  left, seen from the front. *Check on the board (2026-11-03):* the socket
  used for the first tests showed up as the ESP32-S3's own USB (Espressif
  `303a:1001`, `/dev/ttyACM0` on the Pi), not a USB-serial chip, and needed
  USB CDC On Boot: Enabled; its label still needs reading, and whichever
  socket the radio uses sets that option. (A no-plug alternative, if ever needed: the
  board's 12-pin header carries ground, 5 V, D− and D+, so the 12-pin cable
  that comes with it can be soldered to a cut micro-USB cable.)
- **Power:** the official supply has a USB-C plug because it plugs into the
  **USB-C socket on the back of the box**, not into the Pi. Inside, the socket's
  two wires (5 V and ground) are soldered to an old micro-USB cable cut in half
  (red = 5 V, black = ground; check with the multimeter), and its plug goes into
  the Pi's PWR port. The socket must have the two 5.1 kΩ resistors: USB-C
  supplies only switch on when they see them, and plain USB-C-to-micro-USB
  adapter cables usually leave them out. Solder the micro-USB cable's wires to
  the board's VBUS (5 V) and GND pads.
- **Speaker:** the cheap pair is fine to start. The SOTAMIA 50 mm full-range pair
  (about A$20) should sound better if you upgrade later.

## Pin map (Pi, BCM numbering)

| Function | GPIO | Physical pin |
| --- | --- | --- |
| Amp BCLK | 18 | 12 |
| Amp LRC | 19 | 35 |
| Amp DIN | 21 | 40 |
| Amp Vin / GND | 5 V / GND | 2 / 6 |
| Trackball up / down / left / right / press | 16 / 23 / 26 / 24 / 25 | 36 / 16 / 37 / 18 / 22 |
| Trackball power / GND (3.3 V, never 5 V) | 3.3 V / GND | 1 / 9 |
| Trackball lights white / red / green / blue | 4 / 12 / 7 / 8 | 7 / 32 / 26 / 24 |
| Tune knob A / B / push | 5 / 6 / 13 | 29 / 31 / 33 |
| Volume knob A / B / push | 17 / 27 / 22 | 11 / 13 / 15 |
| Tune knob C (middle leg) / other switch leg | GND / GND | 30 / 34 |
| Volume knob C (middle leg) / other switch leg | GND / GND | 14 / 20 |
| Face | USB port (the one marked USB, not PWR) | — |

Free for later: GPIO 2/3 (I2C), 9–11, 14/15 (serial), and ground pins 25
and 39. Avoid GPIO 20:
the I2S audio overlay claims it even though the amp doesn't use it.

## Stages

Each stage ends with something that works. Parts arrive together, but the order
below lowers the risk.

### Stage 1: Map on the laptop *(software only; done)*

Build the face's look before there's a face.
- `map_view.py` renders a round view with an orthographic globe centered
  on the dial. It draws coastlines from Natural Earth (free, public domain),
  station dots, a crosshair, and the station name and city. Three zooms:
  2,000 km across, 500 km across, and the whole world. Five color themes
  (night, radar, ice, paper, mono), defined together in `map_view.py`.
- `screen.py` provides a desktop window backend (pygame) that shows it.
- Laptop controls: drag the map (the trackball), click to zoom, right-click for
  the next theme, mouse wheel or `[`/`]` (the Tune knob), `+`/`-` volume, `M`
  mute, `F` / `Shift+F` favorites. Favorites live in `favorites.json`.
- Measure the render time per frame and estimate whether the Pi Zero 2 W can do
  15+ fps.

**Done when** the laptop window looks like the finished radio and the
frame-time numbers say the Pi can keep up.

**Frame-time results (20 Oct 2026).** The Pi no longer draws the map, so these
numbers matter only for the fallback (a 1.28″ round screen driven by the Pi). Run
`python radio.py --map` to see it.
The numbers come from `python -m terradio.map_view --bench`: the median of 200
frames while the map keeps moving, over three runs. Laptop: Ryzen 5 PRO 5650U,
plugged in.

| View, 240×240 (fallback screen) | Laptop | Pi Zero 2 W guess (6–10× slower) |
| --- | --- | --- |
| 2,000 km across, panning over Europe | 3.8–4.4 ms | 23–44 ms, so 23–43 fps |
| 500 km across, panning over Europe | 3.0–3.1 ms | 18–31 ms, so 32–55 fps |
| Whole world, spinning | 5.7–6.6 ms | 34–66 ms, so 15–29 fps |

- **Verdict:** a Pi driving the 240 px fallback screen should manage 15+ fps
  in every view, but the whole-world view has no room to spare at the slow end.
- **At 480×480,** the face's resolution, the laptop preview takes 9–13 ms a
  frame. That's fine on the laptop, but too slow for a Pi Zero 2 W (about
  8–18 fps). The face draws its own frames in C++, so this doesn't affect it.
- **Where the time goes** (240×240, per frame on the laptop):
  - stations (dots and glow): 1.0–1.9 ms
  - coastlines and grid: 0.6–1.6 ms
  - text and crosshair: 0.8–1.9 ms
  - putting the layers together: ~0.4 ms
  - The text figure is pessimistic. The benchmark redraws the distance and
    coordinates every frame, but the real radio only redraws them once per
    knob click.
- **Not counted above** (these apply to a Pi-driven screen):
  - converting each frame to the screen's color format: 0.4 ms on the laptop,
    so about 3–4 ms on the Pi
  - the SPI link's own ceiling of about 35 fps for a 240×240 frame
- **Already fixed along the way:**
  - The first version took 43 ms per frame. Most of the time went on
    full-image numpy maths, now done with 8-bit Pillow operations, and on
    drawing all 60,000 coastline points every frame. Now only the parts near
    the view are drawn.
  - The nearest-station lookup in `dial.py` took 4.8 ms per knob click, which
    would be 30–50 ms on the Pi, on every click. It now uses numpy and takes
    0.03 ms.
- **If the Pi falls short:**
  - render in a separate process: the Pi has 4 cores, but one Python process
    only uses one
  - drop the glow while the knob is turning
  - lower the frame rate during a glide

### Stage 2: The face, driven from the laptop *(first part to arrive)*

The face board needs no Pi, so it can be built as soon as it arrives.
- Install the Espressif ESP32 board package in the Arduino IDE and flash the
  maker's demo, to confirm the screen and toolchain work.
- `face/face.ino` (Arduino C++) draws the night globe at 480×480, copying the
  Stage 1 look: an orthographic projection, coastlines compiled into the
  program, station dots, a crosshair, the station name and place, the three
  zooms, plus volume and Bluetooth icons. Frames are drawn in PSRAM and sent to
  the screen in the background.
- `python tools/face_data.py` (done) packs the same Natural Earth coastlines
  and country borders, the station positions and the color themes into
  `face/data_*.h`. All three coastlines take 3.1 MB of the 16 MB flash (10m
  alone is 2.6 MB), and the borders another 0.74 MB: 3.8 MB in all. That's more
  than the 3 MB program space of the Arduino IDE's usual 16 MB partition scheme,
  so `face/` needs a `partitions.csv` with a bigger program space (no OTA
  needed).
- `terradio/face_link.py` (done) sends `hello`/`stations`/`state` messages over
  USB serial. Run `python radio.py --face COM5` on the laptop and the arrow
  keys drive the real face. `--virtual-face` already shows what the face should
  draw from those messages.
- The laptop preview already has the finishing touches for the face to copy
  (`map_view.FaceScreen`): the station's local time and the song, favorite
  stars on the map, the toast fading in and out, a 0.4 s
  crossfade when the theme changes, and the idle screen's slow zoom out to the
  whole world with a big clock. Also the slot under the coordinates (time,
  symbol or toast, crossfading in 0.3 s) and the status symbol (its tuning waves follow
  the clock, so any two screens agree), and lines too long for the circle that
  scroll once: they wait 2 s, glide to the end at about 28 px/s with soft
  edges, wait 2 s and fade back to the start, cut short with "…". They scroll
  again when the station or song changes, when the radio wakes from idle, and
  when the volume ring or a toast appears (signs of a hand on the controls).
  While a station plays, the camera glides slowly (the only move then is the
  dial locking on). The crosshair is for aiming, so it fades out over a second
  15 s after a station locks on with nobody at the controls (and on the idle
  clock), and comes back within 0.15 s when the ball rolls or anything else is
  touched (the face sees the status change, a toast or the volume ring). On the
  idle clock the globe can turn slowly (`spin`), stepping 15 times a second so
  the whole-world view, the heaviest frame, stays light; check that it runs
  cool on the real board, and lower the step rate if not.
- **First run on the board (2026-11-03, `face/bench`, details in its README):**
  the screen and toolchain work, but the whole-world frame takes 110 ms on
  one core (8.8 fps; 13.7 on two), and about 70 ms of it is the per-pixel
  composite, so that's the part to speed up before face.ino. At the 16 MHz
  pixel clock the picture glitched (doubled text and ring, jumps) because the
  bounce buffers were refilled from PSRAM too late while the globe was drawn;
  at **10 MHz** (36 Hz refresh) it's clean, so face.ino starts there.
  It also ran plugged into the Pi, powered by it.

- **`face/face.ino` written and running (2026-11-14, details in `face/README.md`):** the face draws
  the preview's whole look from the Pi's messages, checked pixel by pixel against
  `map_view.FaceScreen` with a test-only `shot` message. 19-24 fps with the globe moving in the
  heaviest views (the whole world, or 2,000 km over Europe), nothing drawn while the screen is
  still. On the way: the station light now adds up like the preview's (a core layer and a half-size
  glow), and the face reads USB itself, because the board package's driver dropped most of the
  station list. Driven from the Pi (`--headless --face /dev/ttyACM0`): the globe follows the keys.

**Done when** the round screen glides smoothly (20+ fps) as you press arrow keys
on the laptop, and shows the station that's playing through the laptop speakers.

### Stage 3: Pi + speaker + Bluetooth

- Flash Raspberry Pi OS Lite (64-bit) with Raspberry Pi Imager. In Imager, set the
  hostname `terradio`, Wi-Fi, SSH key and user.
- SSH in, `git clone` this repo, and install mpv, PipeWire and Python packages.
- Use the pre-soldered amp (no soldering). Solder two wires to the speaker's
  tabs, screw them into the amp's terminal, breadboard the amp and enable the
  I2S overlay. The radio's three solder jobs are the speaker wires, the power
  socket's two wires and the trackball's header pins (see the wiring sheet).
- Plug the face into the Pi's USB port with the angled cable and check that
  `/dev/ttyACM0` appears.
- Run `python radio.py --headless --face /dev/ttyACM0` over SSH: hear it on
  the speaker, see it on the face. Headless opens no window (Pi OS Lite has no
  desktop) and never loads the map drawing code (the face draws the map), so the
  main loop runs light at 30 a second. It takes the map window's keys from the
  terminal (arrows roll the globe, W zoom, T theme, [ ] tune, F favorites, +/-
  volume, M mute, Z standby, B, N and P the cards, Q quits) and prints a line
  when the station or the song changes. `requirements.txt` skips pygame on the
  Pi (aarch64).
- Fades: on the laptop, ffplay fades each station in over 1.5 s while the static
  fades out. On the Pi, do the same with mpv through its control socket (untested
  so far), and add a gentle bass and treble lift at low volume. Also start mpv's
  static at a random point, as ffplay's does (`--start`, checking that the loop
  then goes back to the beginning, not to that point).
- Bluetooth and Wi-Fi for real (done on the laptop with pretend ones:
  `terradio/connectivity.py`, `panels.py`, `setup_page.py`; press B and N in
  the map window). Write `BluezBluetooth` (pairing, trusting, connecting through
  BlueZ) and `NetworkManagerWiFi` (scanning, connecting and the setup hotspot
  through `nmcli`) with the same methods as the pretend ones. For the setup page
  to pop up by itself, the hotspot answers every web address with the page
  (NetworkManager's shared mode with a DNS redirect). The Zero 2 W has one Wi-Fi
  radio, so the hotspot drops when the radio joins your network; the page tells
  you to watch the radio's screen for the result. Audio should switch to the
  headphones automatically when they connect and back to the speaker when they
  disconnect.
- Software updates (`terradio/updater.py`, done on the laptop: checked every 6
  hours, installed from the phone page, or by itself between 2 and 5 am while
  the radio is in standby or idle and silent). The Pi needs read access to the
  private GitHub repo: a read-only deploy key. Run the radio from a virtualenv so
  the updater's `pip install` has somewhere to go. It restarts itself after an
  update (`os.execv`, the same process to systemd).
- The phone page on the home network (`--lan` on the laptop): serve it on all
  addresses and announce `terradio.local` with avahi, so the remote is
  http://terradio.local:8737.
- **Risk check:** stream for 30 minutes over Bluetooth. On the Zero 2 W, Wi-Fi and
  Bluetooth share one radio chip and can stutter together. If it does, try a
  lower Bluetooth codec or larger mpv buffers. The fallback is speaker-only, or
  a USB Bluetooth dongle (via a small USB hub, since the face uses the Pi's only
  USB port).

**Done when** the radio runs on the Pi, plays through both outputs without
dropouts and shows on the face.

### Stage 4: Trackball and two knobs

- Wire the trackball and the two encoders on the breadboard (see Pin map). Power
  the trackball from the Pi's 3.3 V pin, never 5 V, so its signals stay safe for
  the Pi. The Pi's internal pull-ups are enough, so no resistors are needed.
- `knobs.py` feeds `controls.py`, which the laptop preview and `--headless`
  already use. Each flip of a trackball direction pin is one step for `roll()`;
  its press and long press are zoom and theme. The Tune encoder goes to
  `turn_tune()` and the favorites, and the Volume encoder to volume and mute
  (see Controls). The ball's four LEDs follow `terradio/ball_light.py` (done;
  the laptop window shows it as a dot in its corner): they breathe while tuning,
  glow in the theme's color while a station plays, and pulse once when you save
  a favorite.
- Volume goes through the system mixer (`wpctl`), so it works for the speaker and
  for Bluetooth.
- The phone remote's QR code: holding both knob buttons opens a small settings
  menu (Bluetooth, Phone remote, Wi-Fi setup) instead of going straight to
  Bluetooth. Turn Tune to choose, press it to open. Done 2026-11-14.

**Done when** you can roll, tune, zoom, change the volume, save and recall
favorites, and enter standby, all by hand, with the face following along.

**Done on the bench (2026-11-14),** with `terradio/knobs.py`: every item above
worked by hand with the face following, plus double press (surprise), the
long presses, both buttons (like, the Bluetooth menu) and waking from standby.
What the real parts taught: the EC11s bounce a lot, but the quadrature state
machine counts each detent exactly once; clockwise runs B before A, so the
code flips them (`"knobs_reversed"` in settings.json flips them back); a double
press needs 0.4 s, not 0.3; pressing the ball wobbles it, so rolls are ignored
while it's down and for 0.3 s after; and the ball's up/down and left/right
steps arrive separately, so each step glides in over 0.08 s, which turns
"right, up, right, up" into a smooth diagonal. Still to set once it's in the
case: which way the ball is turned (`--ball-turn 90` etc.). The ball's lights
are on the weak pull-ups until their resistors are checked (`"ball_lights":
"pwm"` after).

### Stage 5: Make it an appliance

- A systemd service starts the radio at boot (`radio.py --headless --face
  /dev/ttyACM0`; with no terminal it skips the keys) and restarts it if it crashes.
- The face shows a spinning globe while the Pi boots, then the last spot, zoom,
  theme and volume are restored (done on the laptop: `settings.json`).
- Standby sends `sleep`, which turns off the face's backlight and stops audio
  (done on the laptop: Volume long press, the sleep timer's end). Going into
  standby fades the screen and the sound out over 1.5 s; any control wakes it,
  fading both back in (the alarm takes a minute). On the laptop the sound fades
  through the Windows mixer (pycaw); on the Pi, fade through the system mixer.
- A monthly systemd timer refreshes the station list in the background and
  resends the dots to the face.
- Protect the SD card from sudden unplugging: few writes, plus Raspberry Pi
  OS's read-only overlay filesystem once things are stable, keeping favorites
  and settings on a small writable spot. Software updates write to the checkout,
  so keep it on the writable spot too, or have the updater lift the overlay,
  update, and reboot.

**Where it stands (2026-11-14):** `sh pi/install.sh` makes the radio a user
service (`pi/terradio.service`, with lingering, after PipeWire) and adds the
monthly refresh (`pi/terradio-stations.timer`: `--refresh-stations` swaps a
new list in only when it's complete; the radio restarts on it when quiet).
Standby fades through `wpctl`; stations fade in through mpv's control socket.
Unplugged and plugged back in, it played again by itself after 40–60 s. Boot
is the slow part: the radio's user service waits for `network.target`, which
waits for NetworkManager (20 s) after cloud-init (6.5 s). The read-only overlay
is written (`pi/overlay.sh prepare`, then `on`: a 360 MB ext4 image on the boot
partition holds the checkout, Bluetooth pairings and Wi-Fi networks) but not
turned on yet.

**Done when** you can unplug it and plug it back in, and within about 40 seconds
it's playing again with no laptop involved.

### Stage 6: Enclosure

- **First design, done:** [`case/radio_case.scad`](case/radio_case.scad), a
  parametric OpenSCAD model (see [case/README.md](case/README.md)). It's a
  box 108 × 94 × 114 mm, all 3D printed: the front leans back 10° with the
  round face filling it and a knob in each bottom corner; the trackball sits on
  the top at the front right, beside the speaker grille; the USB-C socket is on
  the back. On top, the trackball board is turned a quarter turn, so its four
  direction pins need swapping in the Stage 4 code: roll it and check the globe
  follows your finger.
  Rounded 3 mm edges, a shadow line round each panel and a beveled screen
  window; no screws show on the front (the front panel hooks in under the top
  and is screwed from underneath), and the back panel screws into nuts
  in slots in the corner posts. The screen (the No
  Touch board: a 73 mm round LCD on a 66 mm board) drops into a pocket behind
  the front, and four thin clips press on the LCD's back rim, placed (from
  Waveshare's diagram of the board's back) to miss its buttons, power switch,
  USB-C sockets, card slot and mounting tabs. Once it's in, the board's BOOT
  and RESET buttons and battery switch are behind the panel; none of them is
  needed: uploads go over USB through the UART socket, and there's no
  battery. The face's angled USB-C cable fits beside the Tune knob.
- **Measure** the real parts when they arrive (the screen's thickness and depth,
  trackball frame and holes, USB-C plate, the standoff kit's nuts), update the numbers
  at the top of the file, and print the two test pieces (`test_holes`,
  `test_face`) before the full case.
- **Learn CAD** if you want to change the shape beyond its numbers: OpenSCAD's
  own tutorial, or Onshape (free, browser-based) for drawing by hand.
- **Wiring:** move from the breadboard to soldered wires and connectors on
  perfboard, so the front panel can come off for repairs.

**Done when** it's a closed box on your desk.

### Stage 7: Game worlds *(idea, for after the radio is finished)*

Switch the globe from Earth to a game's map and tune that game's radio
stations: Fallout New Vegas first, then GTA V, Skyrim and others.

- **A world is a folder,** `worlds/<name>/`: a `world.json` with the outlines,
  the stations and their places, plus the stations' audio. It stays out of git
  (`.gitignore`) and the updater leaves it alone. It's several GB, and it's
  copied from games you own, for this radio only, never shared. On the Pi it's
  read-only content, so it can be copied in while the overlay is off and doesn't
  need the 360 MB writable image.
- **A fake planet per world:** the game map becomes the only land on an
  otherwise empty globe, scaled to about 1,500 km across. The 2,000 km view
  then shows all of it, the 500 km view a neighborhood, and the whole-world
  view a lone island. The crosshair, gliding, nearest-station lookup, Tune knob
  and favorites all work unchanged. Place names come from `world.json`
  ("Freeside, New Vegas"); the distance, local time and weather are hidden, and
  the clock shows your own time. Each world can pick a theme (radar's green for
  Fallout).
- **Outlines, traced by hand:** a few hundred to a few thousand points for the
  edge of the land (or of the playable area), plus region lines such as
  Skyrim's holds, drawn in the borders layer. A small laptop tool,
  `tools/trace_world.py`, shows a map image and saves the points you click.
- **Face:** Earth's coastlines are compiled in, but a world's outlines come
  from the Pi in a new `world` message (packed like `stations`), kept in PSRAM
  and used at every zoom; `earth` switches back. Station dots and favorites are
  sent as now. Bump the protocol `v`.
- **"Live" stations from files:** a station is a playlist of local files,
  shuffled once with a fixed seed. Where it is now comes from the clock:
  (seconds since a fixed start) mod (playlist length). So tuning in drops you
  mid-song, and coming back later finds it further along, like in the game.
  mpv starts the file at that point (`--start`) and carries on through the
  list; static and fades work as now. The song name comes from the file's tags,
  so liked songs work too. A world station can also be a stream URL instead,
  for the fan streams Radio Browser already has (such as "GTA SA: K-Rose" and
  "Fallout '26 Radio").
- **Switching:** a World entry in the settings menu (hold both knob buttons)
  and on the phone page. Each world remembers its own last spot, zoom and
  theme, and keeps its own favorites.
- **Getting the audio,** on the PC from your own copies, converted to Opus with
  ffmpeg to save space (check the SD card's size first):
  - **Fallout New Vegas:** Radio New Vegas, Mojave Music Radio and Black
    Mountain Radio, from the game's sound archive with a Bethesda archive
    extractor, each pinned where it broadcasts from in the game.
  - **Fallout 3 and 4:** Galaxy News Radio and Diamond City Radio, the same way.
  - **GTA V:** about 20 stations, exported with OpenIV. The game keeps songs,
    DJ lines, ads and idents in separate files. The first version plays songs
    with DJ lines between them in a simple pattern; mixing them the way the
    game does is polish for later.
  - **Skyrim:** it has no radio, so each hold's city is a station playing that
    hold's music and bard songs.
  - **GTA VI:** due out 2026-11-19, but only on PS5 and Xbox, so there are no
    files to extract until a PC version comes out.

**Done when** you can pick the Mojave from the menu, roll to New Vegas and hear
Radio New Vegas already mid-song, then switch back to Earth and find it where
you left it.

**Where it stands (2026-11-14):** built and working on the laptop.
`terradio/worlds.py` loads a world and runs each station by the clock; the
player plays the files (mpv as a playlist, ffplay as one joined ffconcat
stream); the World card (settings menu and phone page) starts the radio again
in the world picked. The face takes the `world` and `lines` messages
(protocol 2) and compiles, but hasn't been flashed or seen on the board yet.
`python tools/fnv_world.py` makes `worlds/mojave/` (53 MB) from the game's own
files (`tools/bethesda.py` reads its archives and master file):
- **The map:** the playable area is the game's map regions, joined and smoothed
  around its places and roads. Its outline only sets where the map is and its
  zooms (`area` in `world.json`); it isn't drawn, so the map carries on to the
  edges of the view. The coastlines are Lake Mead's shores, wherever the terrain
  dips under the water, past the playable edge too, left open where the game's
  water stops at a cell's edge. The roads are the game's 4,500 road pieces joined into lines, in the
  borders layer: the Vegas grid, I-15 and 95 come out as on the Pip-Boy map.
- **Stations:** Radio New Vegas (Mr. New Vegas: songs, intros, news, 2.6 hours
  before it repeats), Black Mountain Radio (Rhonda and Tabitha's segments, 1.5
  hours), Mojave Music Radio (15 songs) and Camp Guardian's distress call, in
  the order the game's radio scripts play them. Only lines that fit the start
  of the game are used, so the news has no spoilers or contradictions. NCR
  Emergency Radio is left out: it's all the end of the game. Black Mountain and
  Camp Guardian are where the game puts them; Radio New Vegas (the Strip) and
  Mojave Music Radio (Goodsprings) are placed by hand, as the game keeps them
  in a hidden cell.
- **Fitted to the map:** a world has its own zooms (the whole map, closer, close
  up: 1,700, 600 and 200 km for the Mojave, said as words, not km), the dial
  stops at the map's edge, idle backs out to the whole map and drifts it slowly
  in a loop instead of spinning the globe, and long jumps fly, backing out no
  further than the whole map. Its places (the game's map markers, plus New
  Vegas) are labeled like Earth's cities: well-known towns from the whole map,
  settlements, camps and vaults closer, the rest close up. The face gets them
  in `places` messages.
- **Loudness:** the game's songs are mastered about −28 LUFS and its voices
  about −17, so the DJ jumped out and the music was far under Earth's stations.
  `fnv_world.py` levels every song and line to −16 LUFS as it converts it
  (gains capped so peaks stay under −1.5 dBFS; `audio/levels.json` records them).
- **The Pip-Boy's symbols:** a world can bring its own symbols for the signal
  slot (`icons` in `world.json`: 8-bit masks for the 480 px face). The Mojave
  has the game's radio tower on air, its radio wave as a strip that scrolls
  while tuning (4 s round), and a sad Vault Boy for no signal, in the colors
  the usual ((•)) uses. The face gets them in `icon` messages, 1 KB of mask to
  a line; a face that doesn't know them draws the usual one.
- **Tuning in a world:** every spot is near one of a handful of stations, so
  on Earth's rules rolling the ball kept stopping and restarting stations. In a
  world the station on air keeps playing while you roll, until another is
  clearly nearer (under `dial.HOLD`, 0.75, times as far); the dial settles in
  0.25 s instead of 0.4; and mpv starts without its extras (1.35 s to on air on
  the Pi, from 1.45). A world's player gets at most `CUE_FILES` (250) files at
  a time: Camp Guardian's 66 s loop over 12 hours was 10,500 files, and mpv took
  15 s to start, so the dial marked it as failing and skipped it. A world's
  stations are never marked as failing now.
- **Done 2026-11-15:** the world copied to the Pi and mpv's playlist start
  (`--{ --start=… --}`) checked there; the face flashed from the Pi.

## Risks to watch

| Risk | Where it shows up | Plan B |
| --- | --- | --- |
| Face board has poor Arduino support | Stage 2 | Choose a board with a wiki and examples; ESP-IDF examples as a fallback; worst case the 1.28″ GC9A01 on the Pi |
| Globe too slow on the ESP32 at 480×480 (measured 8.8 fps on one core, 13.7 on two, 2026-11-03; the composite is most of it) | Stage 2 | A faster composite, simpler coastlines, drawing only changed areas, 20 fps while turning |
| Pi's USB port won't talk to the face (checked 2026-11-03: it does, both ways, with the angled cable) | Stage 3 | Different OTG cable; or wire the Pi's serial pins (GPIO 14/15) to the board's RX/TX if it exposes them |
| Bluetooth + Wi-Fi stutter on the Zero 2 W | Stage 3 | Bigger buffers / codec tweak, else speaker-only or a USB Bluetooth dongle via a hub |
| AliExpress part isn't what the listing said (wrong Pi model, no PSRAM) | On arrival | Check `cat /proc/cpuinfo` on the Pi and the PSRAM size in the Arduino IDE on day one; open a dispute within the protection window |
| Trackball gets gritty or sticky over the years | Always | Make the ball lift out for cleaning; a spare clone module is about $4 |
| SD card corruption from unplugging | Stage 5 | Read-only overlay filesystem |
| Radio Browser download is slow (~20 min) | First boot | Copy `data/` from the laptop to the Pi |
| Stations with wrong or missing coordinates | Always | `my-stations.json` overrides |

## Later

- **More on the phone page:** the idle-clock delay.
- **Saved songs straight into Spotify:** today each saved song has a button that
  opens a Spotify search for it. The full version adds them to a "Terradio"
  playlist through Spotify's Web API: it needs a free Spotify developer app (its
  client ID) and a one-time sign-in, done on the laptop because Spotify only
  allows plain-http sign-in addresses on the computer itself (127.0.0.1); the
  saved permission then goes to the Pi. Show the matched track before adding.
- **Face firmware updates:** the Pi flashes new face firmware over USB (esptool)
  when the face's `hello` reports an older version, so updates cover both boards.
  The by-hand half is done: `python tools/flash_face.py` on the laptop builds
  the firmware, copies it to the Pi and has `pi/flash_face.sh` flash it there.
- **Touch on the face**, if the board has it: tap a station dot to jump there.
