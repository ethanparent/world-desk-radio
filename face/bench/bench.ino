// World Desk Radio face benchmark: can the ESP32-S3 draw the night globe at 20+ fps?
//
// Flash it onto the Waveshare ESP32-S3-LCD-2.8C (board settings in README.md), open the Serial
// Monitor at 115200 baud and read the numbers. At start-up it runs every test for a few seconds and
// prints a summary table; then it stays on one test and prints a line every second. The screen
// shows the frames per second, the test, and the settings.
//
// Keys (type one in the Serial Monitor and press Enter):
//   1-9, 0  run test 1-10            p  next pixel clock: 10, 12, 16, 20, 30 MHz
//   s       next strip height        o  next way tests 7-10 write to PSRAM: memcpy, DMA, direct
//   a       run every test again and print the summary
//   r       restart the screen's DMA (if the picture has shifted)            ?  this list
//   g       tests 7-10: spin the globe steadily instead of swaying, to judge the smoothness by eye
//
// The tests:
//    1  swap only          nothing drawn: the screen's refresh rate is the most fps there can be
//    2  fill + memcpy      fill strips in internal RAM, copy each to the frame buffer in PSRAM
//    3  fill + DMA         the same, copied by GDMA (esp_async_memcpy) while the next strip fills
//    4  composite direct   the one-pass composite (world view, lines and dots already worked
//                          out), writing straight into the frame buffer in PSRAM
//    5  composite memcpy   ... into internal RAM, then memcpy of the part inside the circle
//    6  composite DMA      ... into internal RAM, then GDMA copy of the strip
//    7  world, 1 core      whole frames: turn the globe, project, bin, draw, show. Whole world.
//    8  2,000 km, 1 core   the same at the 2,000 km zoom (over Europe, the densest stations)
//    9  world, 2 cores     7 with both cores sharing the strips
//   10  2,000 km, 2 cores  8 with both cores sharing the strips

// --- settings ------------------------------------------------------------------------------------
#define PCLK_MHZ        10   // pixel clock: 10 is the fastest that stays clean while drawing (README.md). Also the p key.
#define BOUNCE_LINES    10   // bounce buffers in internal RAM, in screen lines; 0 = none. 240 / this must be whole.
#define FRAME_BUFFERS   2    // 2: swap at vsync, as face.ino will. 3: draw the next frame without waiting.
#define STRIP_LINES     24   // strip height: 16, 24, 32 or 40. Also the s key.
#define MAP_OUTPUT      OUT_MEMCPY   // how tests 7-10 write strips to PSRAM: OUT_MEMCPY, OUT_DMA, OUT_DIRECT
#define START_TEST      7    // the test that keeps running after the start-up sweep
#define SWEEP_SECONDS   4    // seconds per test in the sweep, the first one a warm-up; 0 skips it
#define USE_REAL_DATA   1    // use face/bench/data_*.h if they're there (see README.md)

#include "board.h"
#include "render.h"
#include "world.h"
#include "esp_async_memcpy.h"

static_assert(LCD_H % STRIP_LINES == 0 && STRIP_LINES % 8 == 0 && STRIP_LINES >= 16 && STRIP_LINES <= 40,
              "STRIP_LINES: 16, 24, 32 or 40");
static_assert(BOUNCE_LINES == 0 || (LCD_H / 2) % BOUNCE_LINES == 0, "the driver needs 480 / BOUNCE_LINES to be even");
static_assert(FRAME_BUFFERS == 2 || FRAME_BUFFERS == 3, "FRAME_BUFFERS: 2 or 3");
static_assert(SWEEP_SECONDS == 0 || SWEEP_SECONDS >= 2, "SWEEP_SECONDS: 0, or 2 and up (the first second is a warm-up)");

enum { OUT_MEMCPY, OUT_DMA, OUT_DIRECT, OUT_MAP };
static const char *OUT_NAMES[] = {"memcpy", "DMA", "direct"};
enum { KIND_SWAP, KIND_FILL, KIND_COMPOSITE, KIND_MAP };

struct Test {
  const char *name;
  uint8_t kind, out, cores;
  bool zoomed;
};
static const Test TESTS[] = {
  {"swap only", KIND_SWAP, OUT_MEMCPY, 1, false},
  {"fill + memcpy", KIND_FILL, OUT_MEMCPY, 1, false},
  {"fill + DMA", KIND_FILL, OUT_DMA, 1, false},
  {"composite direct", KIND_COMPOSITE, OUT_DIRECT, 1, false},
  {"composite memcpy", KIND_COMPOSITE, OUT_MEMCPY, 1, false},
  {"composite DMA", KIND_COMPOSITE, OUT_DMA, 1, false},
  {"world, 1 core", KIND_MAP, OUT_MAP, 1, false},
  {"2,000 km, 1 core", KIND_MAP, OUT_MAP, 1, true},
  {"world, 2 cores", KIND_MAP, OUT_MAP, 2, false},
  {"2,000 km, 2 cores", KIND_MAP, OUT_MAP, 2, true},
};
static const int TEST_COUNT = sizeof TESTS / sizeof TESTS[0];

static const int PCLKS[] = {10, 12, 16, 20, 30};
static const int PCLK_COUNT = sizeof PCLKS / sizeof PCLKS[0];
static const int STRIP_HEIGHTS[] = {16, 24, 32, 40};

// Where the time goes, per frame. Each core adds up its own cycles.
enum { ST_PROJECT, ST_BIN, ST_WAIT, ST_CLEAR, ST_LINES, ST_DOTS, ST_COMP, ST_OUT, ST_STRIPS, ST_COUNT };
static const char *STAGE_NAMES[ST_COUNT] = {"project", "bin", "wait", "clear", "lines", "dots", "comp", "out", "strips"};
static uint32_t stageCycles[2][ST_COUNT];
static uint32_t linePixels[2];

// --- state ---------------------------------------------------------------------------------------
static int pclkMHz = PCLK_MHZ, mapOut = MAP_OUTPUT;
static int testNum, frameOut;
static uint32_t frameNo, testStartMs;
static uint16_t *backFb;
static int backIndex;
static int stripNext;              // the next strip to draw (both cores take from it)
static bool hudStripsOnly;         // test 1: redraw only the strips with text on them
static bool spinning;              // g: tests 7-10 spin the globe steadily instead of swaying

// Strip buffers in internal RAM, per core: two for the lines and the finished pixels (two so one
// can be drawn while GDMA copies the other), and the 8-bit station light.
struct CoreBuffers {
  uint16_t *px[2];
  uint8_t *light;
  int next;
  bool busy[2];
  SemaphoreHandle_t copied[2];
};
static CoreBuffers bufs[2];
static async_memcpy_handle_t dma;
static uint32_t dmaErrors;

// Spin-loop counts on each core before the screen started, to see how much its interrupts take.
static uint32_t spinBefore[2], spinNow[2];

// Counts for the reports. (Types used by functions in this file go above the first function:
// the Arduino IDE declares every function there.)
struct Totals {
  uint32_t frames, ms;
  uint64_t stages[ST_COUNT];
  uint64_t linePx, linePts, dots;
};
static Totals second, test;          // this second; this test since its warm-up second
static uint32_t secondStartMs, secondVsyncs, secondsInTest;

struct Result {
  bool valid;
  float fps, frameMs, busyMs, stageMs[ST_COUNT], linePts, linePx, dots;
};
static Result results[TEST_COUNT];
static bool sweeping;
static View frameView;
static int dotsSplit;

// --- memory --------------------------------------------------------------------------------------
// Internal RAM if there's room, else PSRAM (slower: the numbers will show it). 64-byte aligned, which
// GDMA wants with 64-byte bursts.
static void *fastAlloc(size_t n, const char *what, bool quiet) {
  void *p = heap_caps_aligned_alloc(64, n, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT | MALLOC_CAP_DMA);
  if (p) {
    if (!quiet) Serial.printf("  %-26s %7u bytes, internal RAM\n", what, (unsigned)n);
    return p;
  }
  p = heap_caps_aligned_alloc(64, n, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
  Serial.printf("  %-26s %7u bytes, PSRAM  <-- internal RAM is full; this will be slower\n", what, (unsigned)n);
  if (!p) {
    Serial.println("Out of memory.");
    for (;;) delay(1000);
  }
  return p;
}

static void freeStrips() {
  for (int c = 0; c < 2; c++) {
    for (int i = 0; i < 2; i++) heap_caps_free(bufs[c].px[i]);
    heap_caps_free(bufs[c].light);
    memset(bufs[c].px, 0, sizeof bufs[c].px);
    bufs[c].light = NULL;
  }
}

static void allocStrips(int h) {
  freeStrips();
  setStripHeight(h);
  Serial.printf("Strip buffers, %d lines:\n", h);
  for (int c = 1; c >= 0; c--) {
    char what[40];
    snprintf(what, sizeof what, "core %d pixels", c);
    bufs[c].px[0] = (uint16_t *)fastAlloc(h * LCD_W * 2, what, false);
    snprintf(what, sizeof what, "core %d station light", c);
    bufs[c].light = (uint8_t *)fastAlloc(h * LCD_W, what, false);
  }
}

// The second pixel buffer per core is only needed while GDMA copies, so it exists only during the
// DMA tests, leaving that internal RAM free the rest of the time.
static void setDmaBuffers(bool on) {
  for (int c = 1; c >= 0; c--) {
    CoreBuffers &b = bufs[c];
    if (on && !b.px[1]) {
      char what[40];
      snprintf(what, sizeof what, "core %d 2nd pixels (DMA)", c);
      b.px[1] = (uint16_t *)fastAlloc(stripH * LCD_W * 2, what, true);
    } else if (!on && b.px[1]) {
      heap_caps_free(b.px[1]);
      b.px[1] = NULL;
    }
    b.next = 0;
  }
}

static void printMemory() {
  Serial.printf("Internal RAM free: %u bytes (largest block %u). PSRAM free: %u bytes.\n",
                (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL),
                (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL),
                (unsigned)heap_caps_get_free_size(MALLOC_CAP_SPIRAM));
}

// --- jobs for core 0 -----------------------------------------------------------------------------
static esp_err_t panelResult;
static void createPanelJob(void *) { panelResult = panelCreate(pclkMHz, BOUNCE_LINES, FRAME_BUFFERS); }
static void queueFrameJob(void *arg) { queueFrame((int)(intptr_t)arg); }

static void installDmaJob(void *) {
  async_memcpy_config_t cfg;
  memset(&cfg, 0, sizeof cfg);
  cfg.backlog = 8;
  cfg.dma_burst_size = 64;
  if (esp_async_memcpy_install(&cfg, &dma) != ESP_OK) {
    cfg.dma_burst_size = 16;
    if (esp_async_memcpy_install(&cfg, &dma) != ESP_OK) dma = NULL;
  }
}

static uint32_t spin(uint32_t ms) {
  uint32_t n = 0, start = cycles(), length = ms * 240000;
  while (cycles() - start < length) n++;
  return n;
}
static void spinJob(void *arg) { *(uint32_t *)arg = spin(100); }

// How much of each core the screen's interrupts take (mostly the bounce-buffer copying).
static void measureLoad(uint32_t *counts) {
  runOnCore0(spinJob, &counts[0]);
  counts[1] = spin(100);
}

static void printLoad() {
  measureLoad(spinNow);
  float hz = refreshHz(pclkMHz);
  Serial.printf("Pixel clock %d MHz: the panel refreshes %.1f times a second", pclkMHz, hz);
  if (BOUNCE_LINES)
    Serial.printf(", and the bounce-buffer copying reads %.1f MB/s from PSRAM", hz * LCD_W * LCD_H * 2 / 1e6f);
  Serial.printf(".\nScreen interrupts take %.0f%% of core 0 and %.0f%% of core 1.\n",
                100.0f * (1 - (float)spinNow[0] / spinBefore[0]), 100.0f * (1 - (float)spinNow[1] / spinBefore[1]));
}

// --- one strip -----------------------------------------------------------------------------------
static bool IRAM_ATTR onCopied(async_memcpy_handle_t, async_memcpy_event_t *, void *arg) {
  BaseType_t woke = pdFALSE;
  xSemaphoreGiveFromISR((SemaphoreHandle_t)arg, &woke);
  return woke == pdTRUE;
}

static void waitCopies(int core) {
  CoreBuffers &b = bufs[core];
  uint32_t c = cycles();
  for (int i = 0; i < 2; i++)
    if (b.busy[i]) {
      xSemaphoreTake(b.copied[i], portMAX_DELAY);
      b.busy[i] = false;
    }
  stageCycles[core][ST_OUT] += cycles() - c;
}

static void renderStrip(int core, int s) {
  const Test &t = TESTS[testNum];
  CoreBuffers &b = bufs[core];
  uint32_t *acc = stageCycles[core];
  const int y0 = s * stripH, h = stripH;
  uint32_t c = cycles(), now;

  int bi = 0;
  if (frameOut == OUT_DMA) {           // a strip buffer that GDMA has finished copying
    bi = b.next;
    b.next ^= 1;
    if (b.busy[bi]) {
      xSemaphoreTake(b.copied[bi], portMAX_DELAY);
      b.busy[bi] = false;
    }
    acc[ST_OUT] += (now = cycles()) - c;
    c = now;
  }
  uint16_t *px = b.px[bi];
  uint16_t *fbRows = backFb + y0 * LCD_W;

  if (t.kind == KIND_SWAP || t.kind == KIND_FILL) {
    fillStrip(px, y0, h, frameNo);
    drawHud(px, y0, h, LCD_W);
    acc[ST_COMP] += (now = cycles()) - c;
    c = now;
  } else {
    memset(px, 0, h * LCD_W * 2);
    memset(b.light, 0, h * LCD_W);
    acc[ST_CLEAR] += (now = cycles()) - c;
    c = now;
    linePixels[core] += drawLines(px, s);
    acc[ST_LINES] += (now = cycles()) - c;
    c = now;
    stampDots(b.light, s);
    acc[ST_DOTS] += (now = cycles()) - c;
    c = now;
    uint16_t *dst = frameOut == OUT_DIRECT ? fbRows : px;
    compositeStrip(px, b.light, y0, h, dst, LCD_W);
    drawHud(dst, y0, h, LCD_W);
    acc[ST_COMP] += (now = cycles()) - c;
    c = now;
  }

  if (frameOut == OUT_MEMCPY) {
    if (t.kind == KIND_SWAP || t.kind == KIND_FILL) {
      memcpy(fbRows, px, h * LCD_W * 2);
    } else {                           // just the part inside the round window
      for (int j = 0; j < h; j++) {
        const int l = lut.spanL[y0 + j], r = lut.spanR[y0 + j];
        memcpy(fbRows + j * LCD_W + l, px + j * LCD_W + l, (r - l) * 2);
      }
    }
  } else if (frameOut == OUT_DMA) {
    if (esp_async_memcpy(dma, fbRows, px, h * LCD_W * 2, onCopied, b.copied[bi]) == ESP_OK) {
      b.busy[bi] = true;
    } else {
      memcpy(fbRows, px, h * LCD_W * 2);
      dmaErrors++;
    }
  }
  acc[ST_OUT] += cycles() - c;
}

static bool stripHasText(int s) { return s * stripH < HUD_BOTTOM && (s + 1) * stripH > HUD_TOP; }

static void renderStrips(int core) {
  int s;
  while ((s = __atomic_fetch_add(&stripNext, 1, __ATOMIC_RELAXED)) < stripCount)
    if (!hudStripsOnly || stripHasText(s)) renderStrip(core, s);
  waitCopies(core);
}

static void stripsJob(void *) { renderStrips(0); }

// --- one frame -----------------------------------------------------------------------------------
static void projectDotsJob(void *) {        // core 0's half of the stations
  uint32_t c = cycles();
  dotsSplit = projectDots(frameView, 0, stationCount / 2, dots);
  stageCycles[0][ST_PROJECT] += cycles() - c;
}

// The camera: swaying around the worst case of each view (the most stations and lines on screen,
// measured from the real data), so lines and dots move every frame as they will while turning.
// With g, a steady turn instead (36 degrees a second for the world, about 110 px/s at the 2,000 km
// zoom): easy to judge by eye, though no longer always the worst case.
static void cameraView(View &v, bool zoomed, float t) {
  if (spinning) {
    if (zoomed) makeView(v, 52, 14 + fmodf(4 * t, 360), ZOOM_R);
    else makeView(v, 30, -10 + fmodf(36 * t, 360), WORLD_R);
  } else if (zoomed) makeView(v, 52 + 1.5f * sinf(t * 1.05f), 14 + 3 * cosf(t * 1.05f), ZOOM_R);
  else makeView(v, 30, -10 + 15 * sinf(t * 0.8f), WORLD_R);
}

static void projectAndBin(const View &v, bool zoomed, int cores) {
  uint32_t *acc = stageCycles[1];
  uint32_t c = cycles(), now;
  if (cores == 2) {
    frameView = v;
    startOnCore0(projectDotsJob, NULL);
    projectLines(v, zoomed ? zoomLayers : worldLayers);
    const int half = stationCount / 2;
    const int n = projectDots(v, half, stationCount, dots + half);
    acc[ST_PROJECT] += (now = cycles()) - c;
    c = now;
    waitCore0();
    memmove(dots + dotsSplit, dots + half, n * 4);
    dotCount = dotsSplit + n;
    acc[ST_WAIT] += (now = cycles()) - c;
    c = now;
  } else {
    projectLines(v, zoomed ? zoomLayers : worldLayers);
    dotCount = projectDots(v, 0, stationCount, dots);
    acc[ST_PROJECT] += (now = cycles()) - c;
    c = now;
  }
  binSegments();
  sortDots();
  acc[ST_BIN] += cycles() - c;
}

static void runFrame() {
  const Test &t = TESTS[testNum];
  uint32_t *acc = stageCycles[1];
  // Work out this frame's lines and dots while the last one waits to go on screen.
  if (t.kind == KIND_MAP) {
    View v;
    cameraView(v, t.zoomed, (millis() - testStartMs) / 1000.0f);
    projectAndBin(v, t.zoomed, t.cores);
  }
  uint32_t c = cycles(), now;
  backIndex = nextBackBuffer();
  backFb = fbs[backIndex];
  acc[ST_WAIT] += (now = cycles()) - c;
  c = now;

  hudStripsOnly = t.kind == KIND_SWAP && frameNo >= FRAME_BUFFERS;   // fill every buffer once first
  stripNext = 0;
  if (t.cores == 2) {
    startOnCore0(stripsJob, NULL);
    renderStrips(1);
    waitCore0();
  } else {
    renderStrips(1);
  }
  acc[ST_STRIPS] += (now = cycles()) - c;
  c = now;

  runOnCore0(queueFrameJob, (void *)(intptr_t)backIndex);
  acc[ST_WAIT] += cycles() - c;
  frameNo++;
}

// --- tests and reports ---------------------------------------------------------------------------
static void setHud() {
  const Test &t = TESTS[testNum];
  snprintf(hud[1].text, sizeof hud[1].text, "%d %s%s", testNum + 1, t.name,
           spinning && t.kind >= KIND_MAP ? ", SPIN" : "");
  if (t.kind >= KIND_COMPOSITE)
    snprintf(hud[2].text, sizeof hud[2].text, "%d MHZ  BB %d  STRIP %d  %s", pclkMHz, BOUNCE_LINES, stripH,
             OUT_NAMES[frameOut]);
  else
    snprintf(hud[2].text, sizeof hud[2].text, "%d MHZ  BB %d  STRIP %d", pclkMHz, BOUNCE_LINES, stripH);
}

static void startTest(int i) {
  testNum = i;
  const Test &t = TESTS[i];
  frameOut = t.out == OUT_MAP ? mapOut : t.out;
  if (frameOut == OUT_DMA && !dma) frameOut = OUT_MEMCPY;
  setDmaBuffers(frameOut == OUT_DMA);
  clearFrameBuffers();
  uint32_t c = cycles();
  buildLuts(t.zoomed ? ZOOM_R : WORLD_R);
  float lutMs = (cycles() - c) / 240000.0f;
  setViewStyle(t.zoomed);
  if (t.kind == KIND_COMPOSITE) {      // the lines and dots are worked out once, before the test
    View v;
    cameraView(v, false, 0);
    projectLines(v, worldLayers);
    dotCount = projectDots(v, 0, stationCount, dots);
    binSegments();
    sortDots();
  }
  frameNo = 0;
  snprintf(hud[0].text, sizeof hud[0].text, "-- FPS");
  setHud();
  memset(stageCycles, 0, sizeof stageCycles);
  memset(linePixels, 0, sizeof linePixels);
  memset(&second, 0, sizeof second);
  memset(&test, 0, sizeof test);
  testStartMs = secondStartMs = millis();
  secondVsyncs = vsyncCount;
  secondsInTest = 0;
  Serial.printf("\n== Test %d: %s", i + 1, t.name);
  if (t.kind >= KIND_COMPOSITE) Serial.printf(", writing to PSRAM by %s", OUT_NAMES[frameOut]);
  Serial.printf(" (lookup tables built in %.1f ms; face.ino rebuilds them only when the zoom changes)\n", lutMs);
}

static float perFrame(uint64_t v, uint32_t frames) { return frames ? v / (float)frames : 0; }

static void printLine(const Totals &s, float hz) {
  const float frameMs = perFrame(s.ms, s.frames);
  const float waitMs = perFrame(s.stages[ST_WAIT], s.frames) / 240000;
  Serial.printf("%2d %-17s %5.1f fps  frame %5.1f ms  busy %5.1f |", testNum + 1, TESTS[testNum].name,
                s.ms ? s.frames * 1000.0f / s.ms : 0, frameMs, frameMs - waitMs);
  for (int i = 0; i < ST_COUNT; i++) {
    float ms = perFrame(s.stages[i], s.frames) / 240000;
    if (ms >= 0.05f || i == ST_WAIT) Serial.printf(" %s %.1f", STAGE_NAMES[i], ms);
  }
  if (TESTS[testNum].kind >= KIND_COMPOSITE)
    Serial.printf(" | %.0f line pts, %.0f line px, %.0f dots", perFrame(s.linePts, s.frames),
                  perFrame(s.linePx, s.frames), perFrame(s.dots, s.frames));
  Serial.printf(" | %.1f Hz", hz);
  if (dmaErrors) Serial.printf(" | %u DMA copies failed", (unsigned)dmaErrors);
  if (pointsDropped || segmentsDropped)
    Serial.printf(" | lists full: %d line points and %d segments left out", pointsDropped, segmentsDropped);
  Serial.println();
}

static void printSummary() {
  Serial.printf("\n=== Summary: pixel clock %d MHz (%.1f Hz), bounce buffers %d lines, %d frame buffers, "
                "strips %d lines, tests 7-10 write by %s, %s data ===\n",
                pclkMHz, refreshHz(pclkMHz), BOUNCE_LINES, FRAME_BUFFERS, stripH, OUT_NAMES[mapOut],
                REAL_DATA ? "real" : "made-up");
  Serial.printf(" # test                 fps  frame ms  busy ms |");
  for (int i = 0; i < ST_COUNT; i++) Serial.printf(" %7s", STAGE_NAMES[i]);
  Serial.printf(" | line pts  line px    dots\n");
  for (int i = 0; i < TEST_COUNT; i++) {
    const Result &r = results[i];
    if (!r.valid) continue;
    Serial.printf("%2d %-17s %6.1f %9.1f %8.1f |", i + 1, TESTS[i].name, r.fps, r.frameMs, r.busyMs);
    for (int k = 0; k < ST_COUNT; k++) Serial.printf(" %7.1f", r.stageMs[k]);
    if (TESTS[i].kind >= KIND_COMPOSITE) Serial.printf(" | %8.0f %8.0f %7.0f", r.linePts, r.linePx, r.dots);
    Serial.println();
  }
  Serial.println("Times are ms per frame. busy = frame - wait (wait: for the screen to take the frame, and on\n"
                 "2 cores for the other core). Stage times on 2 cores add up both cores.\n");
}

static void saveResult() {
  Result &r = results[testNum];
  const Totals &s = test;
  if (!s.frames) return;
  r.valid = true;
  r.fps = s.frames * 1000.0f / s.ms;
  r.frameMs = perFrame(s.ms, s.frames);
  for (int i = 0; i < ST_COUNT; i++) r.stageMs[i] = perFrame(s.stages[i], s.frames) / 240000;
  r.busyMs = r.frameMs - r.stageMs[ST_WAIT];
  r.linePts = perFrame(s.linePts, s.frames);
  r.linePx = perFrame(s.linePx, s.frames);
  r.dots = perFrame(s.dots, s.frames);
}

// Called after every frame: once a second, print a line and update the fps on screen.
static void tick() {
  const Test &t = TESTS[testNum];
  second.frames++;
  if (t.kind >= KIND_COMPOSITE) {
    second.linePts += pointCount;
    second.dots += dotCount;
  }
  const uint32_t now = millis();
  if (now - secondStartMs < 1000) return;
  second.ms = now - secondStartMs;
  for (int i = 0; i < ST_COUNT; i++) second.stages[i] = (uint64_t)stageCycles[0][i] + stageCycles[1][i];
  second.linePx = linePixels[0] + linePixels[1];
  const float hz = (vsyncCount - secondVsyncs) * 1000.0f / second.ms;
  printLine(second, hz);
  snprintf(hud[0].text, sizeof hud[0].text, "%.1f FPS", second.frames * 1000.0f / second.ms);
  setHud();
  if (++secondsInTest > 1) {           // the first second is a warm-up
    test.frames += second.frames;
    test.ms += second.ms;
    test.linePts += second.linePts;
    test.linePx += second.linePx;
    test.dots += second.dots;
    for (int i = 0; i < ST_COUNT; i++) test.stages[i] += second.stages[i];
    saveResult();
  }
  memset(stageCycles, 0, sizeof stageCycles);
  memset(linePixels, 0, sizeof linePixels);
  memset(&second, 0, sizeof second);
  secondStartMs = now;
  secondVsyncs = vsyncCount;

  if (sweeping && secondsInTest >= SWEEP_SECONDS) {
    if (testNum + 1 < TEST_COUNT) {
      startTest(testNum + 1);
    } else {
      sweeping = false;
      printSummary();
      startTest(START_TEST - 1);
    }
  }
}

static void startSweep() {
  memset(results, 0, sizeof results);
  sweeping = true;
  Serial.printf("\nRunning every test for %d seconds...\n", SWEEP_SECONDS);
  startTest(0);
}

static void printKeys() {
  Serial.println("Keys: 1-9, 0 run test 1-10 | p pixel clock | s strip height | o output for tests 7-10 |\n"
                 "      a every test + summary | r restart the screen's DMA | g spin the globe | ? this list");
}

static void handleKeys() {
  while (Serial.available()) {
    const int ch = Serial.read();
    if (ch >= '0' && ch <= '9') {
      sweeping = false;
      startTest(ch == '0' ? 9 : ch - '1');
    } else if (ch == 'p') {
      int i = 0;
      while (i < PCLK_COUNT - 1 && PCLKS[i] != pclkMHz) i++;
      pclkMHz = PCLKS[(i + 1) % PCLK_COUNT];
      esp_lcd_rgb_panel_set_pclk(panel, pclkMHz * 1000000);
      delay(100);
      printLoad();
      startTest(testNum);
    } else if (ch == 's') {
      int i = 0;
      while (i < 3 && STRIP_HEIGHTS[i] != stripH) i++;
      allocStrips(STRIP_HEIGHTS[(i + 1) % 4]);
      printMemory();
      startTest(testNum);
    } else if (ch == 'o') {
      mapOut = (mapOut + 1) % 3;
      Serial.printf("Tests 7-10 now write to PSRAM by %s.\n", OUT_NAMES[mapOut]);
      startTest(testNum);
    } else if (ch == 'a') {
      startSweep();
    } else if (ch == 'g') {
      spinning = !spinning;
      Serial.println(spinning ? "Tests 7-10 now spin the globe steadily." : "Tests 7-10 now sway the globe.");
      startTest(testNum);
    } else if (ch == 'r') {
      esp_lcd_rgb_panel_restart(panel);
      Serial.println("Restarted the screen's DMA.");
    } else if (ch == '?' || ch == 'h') {
      printKeys();
    }
  }
}

// --- setup and loop ------------------------------------------------------------------------------
void setup() {
  Serial.setTxBufferSize(4096);       // so printing never stalls a frame
  Serial.begin(115200);
  delay(500);
  Serial.println("\nWorld Desk Radio face benchmark");
  Serial.printf("Pixel clock %d MHz, bounce buffers %d lines, %d frame buffers, strips %d lines, %s data.\n",
                pclkMHz, BOUNCE_LINES, FRAME_BUFFERS, STRIP_LINES,
                REAL_DATA ? "real (tools/face_data.py)" : "made-up (no data_*.h found)");
  if (!psramFound()) {
    Serial.println("No PSRAM: set Tools > PSRAM to \"OPI PSRAM\".");
    for (;;) delay(1000);
  }

  core0Start();
  spinBefore[1] = spin(100);
  runOnCore0(spinJob, &spinBefore[0]);

  if (!st7701Init()) {
    Serial.println("The IO expander doesn't answer: is this the ESP32-S3-LCD-2.8C?");
    for (;;) delay(1000);
  }
  runOnCore0(createPanelJob, NULL);
  if (panelResult != ESP_OK) {
    Serial.printf("Couldn't start the screen: %s\n", esp_err_to_name(panelResult));
    for (;;) delay(1000);
  }
  runOnCore0(installDmaJob, NULL);
  if (!dma) Serial.println("GDMA memcpy isn't available: the DMA tests will use memcpy.");
  for (int c = 0; c < 2; c++)
    for (int i = 0; i < 2; i++) bufs[c].copied[i] = xSemaphoreCreateBinary();
  pinMode(PIN_BACKLIGHT, OUTPUT);
  digitalWrite(PIN_BACKLIGHT, HIGH);

  textColor = rgb565(NIGHT_TEXT);
  buildRing();
  buildAllSprites();
  loadWorld();
  Serial.printf("Stations: %d. Lines: whole world %u + %u + %u points (grid, borders, coastlines), "
                "2,000 km %u + %u + %u.\n", stationCount,
                (unsigned)worldLayers[0].pointCount, (unsigned)worldLayers[1].pointCount, (unsigned)worldLayers[2].pointCount,
                (unsigned)zoomLayers[0].pointCount, (unsigned)zoomLayers[1].pointCount, (unsigned)zoomLayers[2].pointCount);

  // Along the test camera paths the real data peaks at 9,825 line points (2,000 km), 7,214 segment
  // entries (16-line strips) and 12,178 dots (whole world).
  Serial.println("Per-frame lists:");
  pointCap = 11264;
  points = (uint32_t *)fastAlloc(pointCap * 4, "projected line points", false);
  segmentCap = 9216;
  segments = (uint16_t *)fastAlloc(segmentCap * 2, "line segments by strip", false);
  dots = (uint32_t *)fastAlloc(stationCount * 4, "station dots", false);
  allocStrips(STRIP_LINES);
  printMemory();
  printLoad();
  printKeys();

  if (SWEEP_SECONDS > 0) startSweep();
  else startTest(START_TEST - 1);
}

void loop() {
  handleKeys();
  runFrame();
  tick();
}
