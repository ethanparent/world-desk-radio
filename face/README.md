# The face

`face.ino` is the round face's program: the Waveshare ESP32-S3-LCD-2.8C draws the radio's globe from
the Pi's messages over USB (PLAN.md, "Messages from the Pi to the face"). It copies the laptop
preview (`worlddeskradio/map_view.py`, `FaceScreen`) as closely as it can: the orthographic globe, the
coastlines and borders, the station dots and their glow, the crosshair, the station's name, place
and song (long ones scroll once), the time and weather slot, toasts, the volume ring, favorite
stars, city labels, the idle clock and its slow spin, the Bluetooth and Wi-Fi cards (with a QR
code), the theme crossfade, standby fades and the brightness setting. Before the Pi says
anything, and 3 s after it last did, it shows a slowly turning globe.

`face/bench` is the speed test that came first; its README has the measurements this program is
built on.

## Files

| File | What's in it |
|---|---|
| `face.ino` | Settings, the main loop, drawing a frame on both cores, standby and the backlight, statistics |
| `board.h` | The screen (from `face/bench`): IO expander, ST7701 start-up, frame buffers in PSRAM, bounce buffers, the swap at vsync, the backlight |
| `render.h` | Finishing a strip: the one-pass composite, text and symbols over it, the volume ring, the card's dimming, the crossfade |
| `world.h` | Coastlines, borders, the grid and the station dots for a view, filed under the strips |
| `text.h` | Lines of text in the preview's fonts, cut with "…" to fit the circle |
| `link.h` | The USB cable: a reader task, a small JSON parser, the messages |
| `screen.h` | What to show: `map_view.FaceScreen` and `face_hud` ported (camera glides and flights, fades, scrolling, cities, cards) |
| `partitions.csv` | One 15.9 MB program partition: the map data and fonts take about 9 MB |
| `data_*.h` | Generated, not in git (see below) |

## Building and flashing

1. **Data.** From the repo folder:

   ```bash
   python tools/face_data.py
   ```

   This writes `face/data_*.h`: coastlines and borders at three detail levels, the station
   positions, the themes, the fonts (from the preview's font list: Segoe UI on Windows), the
   symbols and the cities. Run it again after a station refresh or a change to the themes or
   symbols in `map_view.py`.

2. **Arduino IDE** (esp32 by Espressif Systems 3.3.x). Open `face/face.ino`, then in Tools:

   | Setting | Value |
   |---|---|
   | Board | ESP32S3 Dev Module |
   | USB CDC On Boot | Enabled (the face's socket shows up as Espressif `303a:1001`) |
   | CPU Frequency | 240MHz |
   | Flash Mode | QIO 80MHz |
   | Flash Size | 16MB (128Mb) |
   | Partition Scheme | Custom (uses `partitions.csv` here) |
   | PSRAM | OPI PSRAM |

   The same with `arduino-cli` (the IDE's own copy: see `face/bench/README.md`):

   ```bash
   arduino-cli compile --upload -p COM3 --fqbn "esp32:esp32:esp32s3:FlashSize=16M,PSRAM=opi,PartitionScheme=custom,CDCOnBoot=cdc,CPUFreq=240,FlashMode=qio" face
   ```

   Compiling takes a few minutes (the fonts are 20 MB of source) and uploading about a minute.

   **Flashing through the Pi** (when the face's cable is inside the case, on the Pi). Not tried
   yet. Build on the laptop with `--output-dir face-bin` added to the compile command (no
   `--upload`), then copy `face.ino.bootloader.bin`, `face.ino.partitions.bin` and `face.ino.bin`
   to the Pi with `scp`. On the Pi, first check that nothing holds the port
   (`pgrep -af "radio.py|esptool|mpv"`; stop the radio first if it's running), then:

   ```bash
   ~/world-desk-radio/.venv/bin/python -m pip install esptool
   ```

   ```bash
   ~/world-desk-radio/.venv/bin/python -m esptool --chip esp32s3 -p /dev/ttyACM0 -b 921600 write_flash -z --flash-mode keep --flash-freq keep --flash-size keep 0x0 face.ino.bootloader.bin 0x8000 face.ino.partitions.bin 0x10000 face.ino.bin
   ```

   The ESP32-S3's own USB resets into its bootloader by itself (no BOOT button). The addresses are
   the ones arduino-cli used from the laptop (bootloader 0x0, partitions 0x8000, program 0x10000).

3. **Try it.** On the laptop, `python radio.py --face COM3` drives it from the map window. On
   the Pi: `python radio.py --headless --face /dev/ttyACM0`.

## Messages it understands besides PLAN.md's

For testing from the laptop; the radio never sends them.

| Message | Answer |
|---|---|
| `{"t":"stats"}` | One line: frames per second, ms per frame and where it goes (`stages`), messages taken and rejected, free memory |
| `{"t":"shot"}` | `{"t":"shot","w":480,"h":480}` and then the frame on screen, 480 x 480 RGB565 little-endian (460,800 bytes). Read it promptly: bytes the computer doesn't collect in time are lost |
| `{"t":"scene"}` | The last frame's sprites (position, size, color, opacity) and the favorites it holds |

## How a frame is made

- The screen runs from three frame buffers in PSRAM (draw one while one is shown and one waits for
  vsync), with bounce buffers in internal RAM, at a 10 MHz pixel clock: 16 MHz glitched in
  `face/bench`. 36 refreshes a second.
- Each frame is drawn in 20 strips of 24 lines in internal RAM, both cores taking strips in turn.
  A strip gets its lines, its station light (each station adds a small core sprite to a full-size
  layer and a glow sprite to a half-size one, as the preview blurs at half size), then one pass
  works out every pixel: globe shading, lines, the light through the preview's brightness curves,
  the window's soft edge and the text bands. Text and symbols go over it as 8-bit masks, then the
  strip is copied to PSRAM once.
- Nothing is drawn when nothing on screen would change: a station playing untouched costs nothing.
- A view's lines are worked out only when the view moves; runs of coastline are skipped 32 at a time
  when they're off screen, and the 50m and 10m lines are copied to PSRAM at start-up (reading them
  from flash, which shares its bus with the PSRAM the screen reads, cost 9 ms a frame at 500 km).

Measured on the board (2026-11-14), the globe moving, while the station list and states arrive:

| View | fps |
|---|---|
| Whole world spinning over Europe (12,000 dots, the heaviest) | 19-24 |
| 2,000 km panning over Europe, the densest stations | 19-24 |
| 500 km | about 20 |
| Waiting globe (stepped 20 times a second) | 20 |
| Playing, untouched | 0 (nothing to draw) |

A flight between zooms dips to 12-15 for a moment (the 50m coastlines over a wide view).

## Receiving from the Pi

The board package's USB driver puts each received byte in a small queue from its interrupt and
drops what doesn't fit, so most of a burst like the station list (50 lines, 70 KB) was lost.
`link.h` turns that interrupt off and empties the USB chip's 64-byte receive buffer itself, into a
128 KB ring in PSRAM; when the ring is full it stops reading, and the chip makes the Pi wait instead
of losing anything (tested with 210 KB sent at once: every line arrived). The ring is parsed between
frames.

## Checking it against the preview

The `shot` message gives the frame on screen, so it can be compared pixel by pixel with what
`map_view.FaceScreen` draws from the same state and the same station positions. On 2026-11-14 the
scenes tried (a playing station in Seoul with a Korean song, the whole world in the radar theme,
500 km over Paris in the paper theme, the idle clock in ice, tuning in mono, the Bluetooth and
Wi-Fi cards) matched the preview by eye; the remaining differences are RGB565's coarser colors,
glyph-by-glyph text layout, and the QR code's mask pattern (both codes decode to the same text).
