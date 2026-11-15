# Face benchmark

Before writing `face/face.ino`, this sketch checks on the real board whether the round screen can
show the night globe at 20+ frames per second. The question is memory speed more than drawing: the
frame buffers live in PSRAM, the screen reads them from PSRAM all the time, and the CPU shares that
bandwidth. The plan in PLAN.md (Stage 2) draws each frame in strips in fast internal RAM and writes
every pixel to PSRAM once. This sketch measures exactly that, step by step.

It first ran on the board on 2026-11-03; the numbers and what they showed are under
[Results on the board](#results-on-the-board-2026-11-03). Before that, its drawing code was run
on a PC and checked against the Python preview: the same picture, and the same work (whole world:
7,648 line points against the preview's 7,638, 21,457 line pixels against 21,562, 12,163 station
dots against 12,163).

## Files

| File | What's in it |
|---|---|
| `bench.ino` | Settings at the top, the ten tests, timing, the reports, the keys |
| `board.h` | The screen: IO expander, ST7701 start-up commands, the RGB panel with two frame buffers in PSRAM and bounce buffers in internal RAM, the swap at vsync, a helper task on core 0 |
| `render.h` | The one-pass composite, its lookup tables, the on-screen text and the drift ring |
| `world.h` | Map data, the int16 projection, filing lines and dots under strips, drawing them |
| `data_*.h` | Real map data, generated (not in git, see below) |

## Getting it onto the board

1. **Map data.** From the repo folder:

   ```bash
   python tools/face_data.py --res 110m,50m --out face/bench
   ```

   This writes `data_*.h` here (643 KB in flash; the sketch is 1.06 MB in all). Without these
   files the sketch still runs, on made-up data of about the same size, and says so at start-up.
   (The 10m coastline is left out because it needs a bigger program partition; the benchmark
   doesn't use the 500 km view.)

2. **Arduino IDE.** Install "esp32 by Espressif Systems" in the Boards Manager (version 3.3.x;
   this was compiled with 3.3.12). Open `face/bench/bench.ino`, then in the Tools menu:

   | Setting | Value |
   |---|---|
   | Board | ESP32S3 Dev Module |
   | USB CDC On Boot | Enabled, if the cable is in the socket that shows up as Espressif's own USB (`303a:1001`, "USB JTAG/serial debug unit"), as it did on 2026-11-03. Disabled for a socket that goes through a CH343P USB-serial chip |
   | CPU Frequency | 240MHz |
   | Flash Mode | QIO 80MHz |
   | Flash Size | 16MB (128Mb) |
   | Partition Scheme | 16M Flash (3MB APP/9.9MB FATFS) |
   | PSRAM | OPI PSRAM |

   Everything else stays at its default. The same with `arduino-cli`:

   ```bash
   arduino-cli compile --upload -p COM3 --fqbn "esp32:esp32:esp32s3:FlashSize=16M,PSRAM=opi,PartitionScheme=app3M_fat9M_16MB,CDCOnBoot=cdc,CPUFreq=240,FlashMode=qio" face/bench
   ```

   (The Arduino IDE has its own copy of `arduino-cli` at
   `%LOCALAPPDATA%\Programs\Arduino IDE\resources\app\lib\backend\resources\arduino-cli.exe`; give
   it `--config-file %USERPROFILE%\.arduinoIDE\arduino-cli.yaml` so it uses the IDE's boards.)

3. **Upload, then open the Serial Monitor at 115200 baud.** If the board doesn't show up as a COM
   port, install the CH343 driver from WCH. If uploading fails, hold the BOOT button while
   plugging in the USB cable, then upload again. With USB CDC On Boot enabled, keep something
   reading the port (the Serial Monitor, or the Pi): if nothing reads it, the prints stall the
   sketch and the screen freezes at well under 1 fps.

## What happens

At start-up it prints where its buffers went (internal RAM or PSRAM), how much of each core the
screen's interrupts take, then runs all ten tests for 4 seconds each (the first second of each is a
warm-up) and prints a summary table. After that it keeps running test 7 and prints a line every
second. The screen shows the frames per second in big digits, the test, and the settings.

| # | Test | What it shows |
|---|---|---|
| 1 | swap only | Nothing drawn, just swapping buffers: the screen's refresh rate, the most fps possible |
| 2 | fill + memcpy | The raw cost of writing one full frame: strips filled in internal RAM, each copied to PSRAM with `memcpy` |
| 3 | fill + DMA | The same, copied by the GDMA engine (`esp_async_memcpy`) while the CPU fills the next strip |
| 4 | composite direct | The one-pass composite (whole world, lines and dots worked out once beforehand), writing straight into the frame buffer in PSRAM |
| 5 | composite memcpy | The same into internal RAM, then `memcpy` of the part inside the circle |
| 6 | composite DMA | The same into internal RAM, then a GDMA copy of the strip |
| 7 | world, 1 core | Whole frames, everything every frame: turn the globe, project, file under strips, draw, composite, show. Whole-world view at its busiest (12,000 dots, 21,000 line pixels) |
| 8 | 2,000 km, 1 core | The same at the 2,000 km zoom over Germany, the densest stations (4,000 dots, 14,000 line pixels) |
| 9 | world, 2 cores | Test 7 with both cores drawing strips |
| 10 | 2,000 km, 2 cores | Test 8 with both cores drawing strips |

In tests 7-10 the globe sways back and forth around its busiest spot, so lines and dots move every
frame as they will while the radio is being tuned.

### Keys

Type a key in the Serial Monitor and press Enter.

| Key | Does |
|---|---|
| `1`-`9`, `0` | Run test 1-10 |
| `p` | Next pixel clock: 10, 12, 16, 20, 30 MHz (then measures the interrupt load again) |
| `s` | Next strip height: 16, 24, 32, 40 lines |
| `o` | Next way tests 7-10 write to PSRAM: memcpy, DMA, direct |
| `a` | Run every test again and print the summary |
| `r` | Restart the screen's DMA |
| `g` | Tests 7-10: spin the globe steadily (36° a second for the world) instead of swaying, so the smoothness is easy to judge by eye. The screen adds "SPIN" to the test name. Not always the worst case, so measure without it |
| `?` | List the keys |

### Settings at the top of bench.ino

| Setting | Default | Meaning |
|---|---|---|
| `PCLK_MHZ` | 10 | Pixel clock at start-up. 10 is the fastest that stays clean while the globe is drawn (see the results); 12, 16, 20 and 30 glitch. Also the `p` key |
| `BOUNCE_LINES` | 10 | Height of the two bounce buffers in internal RAM, in screen lines (Waveshare's demo: 10). 0 turns them off; 240 must divide by it. 20 made the glitches worse, not better (see the results) |
| `FRAME_BUFFERS` | 2 | 2: draw into one while the other is shown, swapping at vsync (face.ino's plan). 3: never wait for the screen |
| `STRIP_LINES` | 24 | Strip height. Also the `s` key |
| `MAP_OUTPUT` | `OUT_MEMCPY` | How tests 7-10 write to PSRAM. Also the `o` key |
| `START_TEST` | 7 | The test that keeps running after the summary |
| `SWEEP_SECONDS` | 4 | Seconds per test in the start-up run; 0 skips it |
| `USE_REAL_DATA` | 1 | 0 forces the made-up data |

## What the numbers mean

A line every second looks like this (made-up numbers):

```
 7 world, 1 core      21.3 fps  frame  46.9 ms  busy  38.2 | project 2.1 bin 0.7 wait 8.7 clear 0.5 lines 2.2 dots 6.9 comp 17.0 out 8.8 strips 35.4 | 7648 line pts, 21457 line px, 12163 dots | 57.5 Hz
```

All times are milliseconds per frame.

- **fps**: frames shown per second.
- **frame**: time per frame, 1000 / fps.
- **busy**: the frame minus the waiting: how long the frame actually took to make. This is the
  number to compare with the 50 ms that 20 fps allows.
- **project**: turning the visible coastline, border and grid points and all 12,765 stations to
  screen positions (one int16 3x3 multiply each).
- **bin**: filing each line segment and dot under the strips it touches.
- **wait**: waiting for the screen to take the last frame (and, on 2 cores, for the other core).
- **clear**: blanking the strip buffers.
- **lines**: drawing the 1-px coastlines, borders and grid into the strips.
- **dots**: stamping the station glows into the 8-bit light layer.
- **comp**: the one-pass composite: globe shading, the window's soft edge and the text bands, the
  station light through its palette, plus the on-screen text. In "direct" mode this includes
  writing to PSRAM.
- **out**: getting the finished strips to PSRAM: the `memcpy`, or for DMA the time spent waiting
  for a copy to finish before a strip buffer can be reused.
- **strips**: wall-clock time for all strips (clear + lines + dots + comp + out on one core; on 2
  cores, the time until both are done).
- **line pts / line px / dots**: the work in the frame: points projected, line pixels drawn,
  station dots stamped.
- **Hz**: how many times a second the panel refreshed (counted from vsync interrupts). It should
  match the pixel clock: 36 Hz at 10 MHz, 43 at 12, 57 at 16, 72 at 20, 108 at 30.

On 2 cores, the stage times add up both cores' time, so they can exceed the frame time.

At start-up, and after `p`:

- **"Screen interrupts take N% of core 0"**: with bounce buffers, the CPU itself copies every
  frame from PSRAM into the bounce buffers, in an interrupt on core 0. This is that cost. It grows
  with the pixel clock, and the line beside it says how many MB/s of PSRAM reads it is (26.5 MB/s at
  16 MHz, 50 MB/s at 30). Core 1 does the drawing in the 1-core tests.
- **Buffer lines** ("core 1 pixels ... internal RAM"): where each buffer went. Anything that says
  PSRAM didn't fit in internal RAM and will be slower; try a smaller strip height (`s`).

## Results on the board (2026-11-03)

The first run, at the old defaults (16 MHz pixel clock, 10-line bounce buffers, 24-line strips,
memcpy out, real data). Times are ms per frame:

| # | Test | fps | busy | project | dots | comp | out |
|---|---|---|---|---|---|---|---|
| 1 | swap only | 57.5 | 4.9 | | | 0.5 | 4.4 |
| 2 | fill + memcpy | 28.7 | 21.3 | | | 1.3 | 19.9 |
| 3 | fill + DMA | 9.6 | 95.5 | | | 1.3 | 94.2 |
| 4 | composite direct | 9.6 | 96.9 | | 18.7 | 73.8 | |
| 5 | composite memcpy | 9.6 | 102.7 | | 18.7 | 69.4 | 10.2 |
| 6 | composite DMA | 6.7 | 141.4 | | 18.7 | 69.4 | 48.8 |
| 7 | world, 1 core | 8.8 | 111.8 | 7.3 | 16.8 | 69.2 | 10.2 |
| 8 | 2,000 km, 1 core | 8.3 | 117.2 | 10.8 | 16.4 | 73.0 | 10.3 |
| 9 | world, 2 cores | 13.7 | 70.6 | 11.3 | 21.0 | 81.1 | 13.4 |
| 10 | 2,000 km, 2 cores | 11.5 | 75.0 | 18.9 | 21.1 | 85.2 | 13.5 |

What they say:

- **Far short of 20 fps, and the composite is why.** It takes about 70 ms of the 110 ms frame on
  one core, about 380 ns (90 clock cycles) per pixel inside the circle. Writing a whole frame to
  PSRAM takes only 20 ms (test 2), so memory speed isn't the limit; the per-pixel work is. That's
  where to work before face.ino. A second core helps, but only 1.6×.
- **Tests 3 and 6 measured a failed copy.** Every GDMA copy was refused with `address|size not
  aligned` (strip buffer 0x3fcc9540 to frame buffer 0x3c101060, 23,040 bytes), so they say nothing
  about DMA yet. The strip buffers and the frame-buffer offsets they're copied to need aligning
  for GDMA to PSRAM before DMA can be compared. Also, the second strip buffer per core that only
  DMA needs didn't fit in internal RAM and went to PSRAM.

### After fixing the composite

The composite's cost turned out to be three `memcpy` calls per pixel pair (the Xtensa core has
no unaligned loads, and the Arduino build doesn't expand `memcpy` itself, so each was a call into
ROM), and it had been inlined into a function in flash. With aligned reads and writes and the
composite kept in IRAM, at 10 MHz:

| Test | Before | After |
|---|---|---|
| Composite, world | 69 ms | 23 ms |
| 7 world, 1 core | 8.8 fps | 14.4 fps (busy 64 ms) |
| 9 world, 2 cores | 13.7 fps | 18.0 fps (busy 42 ms) |
| 9 world, 2 cores, spinning (`g`) | | 20-24 fps |

The 2-core frame's 42 ms of work would allow 24 fps, but with two frame buffers a frame waits
for a refresh, and at 36 Hz the steps are 36, 18, 12 fps. Past 18 at the worst spot needs a
frame under 28 ms, or three frame buffers. What's left per frame (1 core): station dots 17 ms,
composite 23, the copy to PSRAM 9, projecting and binning 11.

### The glitch: bounce buffers refilled too late

At 16 MHz the picture glitched while the globe was drawn: text with a copy flashing under it, the
globe jumping down and back every few seconds, a second copy of the drift ring's bottom just below
it, and thin dark lines with little ticks of the ring sticking out at even spacing down the
globe's left edge. Photos and 240 fps slow motion showed the spacing of those lines is the
bounce-buffer height (twice as far apart with 20-line buffers as with 10), and the copy under the
ring sits that far below it. So it happens at every bounce-buffer boundary, every frame: the CPU
copy from PSRAM into each bounce buffer finishes a little after the screen starts reading it, so
its first line or so still holds the picture from two buffers higher up. The drawing on core 1
shares PSRAM with that copy and slows it down.

| Setting | What the screen did |
|---|---|
| 16 MHz, nothing drawn (test 1) | Clean |
| 16 MHz, globe (test 7) | Glitches as above |
| 30 MHz | The whole picture wrapped (text near the bottom) |
| 12 MHz, globe | Still glitching |
| 16 MHz, 20-line bounce buffers | Worse: the same glitches, twice as far apart and twice the shift |
| **10 MHz, 10-line bounce buffers, globe** | **Clean**, still 9.0 fps (refresh 35.9 Hz, 16.6 MB/s of PSRAM reads, 5% of core 0) |

So `PCLK_MHZ` is now 10. A bigger bounce buffer doesn't help, because it takes proportionally
longer to fill. Not tried yet: no bounce buffers (`BOUNCE_LINES 0`), and Waveshare's own board
package, which builds the core for faster PSRAM. Either could allow a faster clock if 36 Hz ever
flickers.

### On the Pi

Plugged into the Pi Zero 2 W's USB port with the angled cable, the board ran from the Pi's power
with no undervoltage (`vcgencmd get_throttled` 0x0), showed up as `/dev/ttyACM0` (Espressif
`303a:1001`), and the Pi read the reports and sent keys at 115200. Opening the port from the Pi
reset the board once (`rst:0x15 (USB_UART_CHIP_RESET)`), as Linux raises DTR and RTS on open;
face.ino's `hello` on boot already covers that.

## Reading the results

- **Double buffering locks fps to the refresh rate.** With 2 frame buffers a finished frame goes on
  screen at the next refresh, so fps comes in steps: at 57.5 Hz, a frame that's busy 20 ms shows
  at 28.7 fps, one busy 36 ms at 19.2 fps. If busy is under 50 ms but fps lands just under 20,
  a different pixel clock (or 3 frame buffers) would get it over.
- **Tests 2 and 3** say how fast one frame can be written to PSRAM at all, by CPU and by GDMA. The
  investigation behind this expected about 50 MB/s for internal-RAM-to-PSRAM copies, so about
  9 ms for a full frame, with the screen's own reading eating into that.
- **Tests 4, 5, 6** say which way of writing is best once there's real work per pixel. If DMA wins,
  the copy is happening while the CPU works on the next strip.
- **Tests 7 and 8** are the answer for one core: 20+ fps here means face.ino can draw on one core
  and keep the other for the serial link. **Tests 9 and 10** show what a second core adds; if it
  adds little, PSRAM bandwidth is the limit, not the CPU.
- **Try each pixel clock** (`p`, then `a`). A lower clock refreshes less often, which leaves more
  PSRAM bandwidth and CPU time for drawing; the screen only needs to refresh as often as the
  frames come. 30 MHz refreshes 108 times a second, far more than the globe needs.

## Watching for drift

The RGB interface sends pixels to the panel non-stop. If the frame buffer can't be read fast
enough (PSRAM too busy, or the bounce-buffer copying too late), pixels go missing and the picture
**shifts sideways**: everything slides left or right and wraps around, often with the colors
looking wrong. Projects without bounce buffers drop to a 12 MHz pixel clock to avoid it.

How to spot it here:

- **The thin white ring near the edge** is drawn centered in every test. If it's ever cut off on
  one side, or there's a gap between it and the bezel on the other, the picture has shifted.
- **Thin lines or ticks at even spacing** down the ring's sides, and a second copy of the ring's
  bottom just below it: the bounce buffers being refilled too late, every frame (see the results).
  A lower pixel clock fixes it.
- **A flicker or a band of wrong lines** for a moment. Arduino's ESP32 core (3.3.x) is built with
  `CONFIG_LCD_RGB_RESTART_IN_VSYNC`, so the driver restarts the screen's DMA at every vsync; a
  shift can't stick, it shows up as a glitchy frame instead. Watch for it at 30 MHz and during
  tests 2-6, which push PSRAM hardest.
- **Tearing** is different: a step or seam across the moving bands of tests 1-3 means a frame was
  swapped in mid-refresh. That shouldn't happen with the vsync swap; if it does, note the test and
  settings.
- The `r` key restarts the screen's DMA by hand, for a build without the restart-in-vsync option.
  Setting `BOUNCE_LINES` to 0 shows classic drift at higher pixel clocks.

Writing to flash (saving settings, OTA) also stalls PSRAM briefly and can make the picture jump.
This sketch never writes flash; face.ino shouldn't while it's drawing. (Waveshare's own "XIP"
board package avoids it by running code from PSRAM.)

## Notes for face.ino

- `render.h` turns on `-O2`: the Arduino IDE builds sketches for size (`-Os`) by default.
- The frame swap is queued from core 0, the core whose interrupts run the panel, with interrupts
  off for that moment (see `queueFrame` in `board.h`). Queued from the other core, the driver
  could switch buffers between the two steps and the next frame would be drawn into the buffer on
  screen.
- Internal RAM is the tight resource: the build leaves about 285 KB for the heap and task stacks,
  and the bounce buffers (19 KB), strip buffers (69 KB at 24 lines, 115 KB with the second buffer
  per core that only the GDMA copy needs) and per-frame lists (114 KB) take most of it. The
  start-up report shows what was left.
- The dots keep the brighter value where they overlap (like map_view's `ImageChops.lighter`). The
  Python preview also lets crowded areas add up into a brighter glow; doing that would cost about
  the same per dot.
