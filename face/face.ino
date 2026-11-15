// World Desk Radio: the round face. Draws the radio's night globe on the Waveshare ESP32-S3-LCD-2.8C from
// the Pi's messages over USB (PLAN.md, "Messages from the Pi to the face").
//
// The Pi works out everything (where the dial is, the station, the volume, the theme) and sends it
// as a `state` line up to 30 times a second; this program only draws, copying the laptop preview
// (worlddeskradio/map_view.py) as closely as it can. Before the Pi says anything, and 3 s after it last
// did, it shows a slowly turning globe.
//
// How a frame is made (face/bench tried each part on the board; its README has the numbers):
//   - the screen runs from frame buffers in PSRAM, swapped at vsync, with bounce buffers in internal
//     RAM, at a 10 MHz pixel clock (16 MHz glitched while the globe was drawn);
//   - each frame is drawn in 24-line strips in internal RAM, both cores taking strips in turn, each
//     strip finished in one pass and copied to PSRAM once;
//   - nothing is drawn when nothing on screen would change (the radio playing, untouched).
//
// Files: board.h (the screen), render.h (finishing a strip), world.h (lines and dots), text.h
// (fonts), link.h (the USB cable and the messages), screen.h (what to show: map_view.FaceScreen
// ported), data_*.h (made by `python tools/face_data.py`). README.md says how to build and flash it.

// --- settings ------------------------------------------------------------------------------------
#define PCLK_MHZ        10   // pixel clock: 10 is the fastest that stays clean while drawing (face/bench)
#define BOUNCE_LINES    10   // bounce buffers in internal RAM, in screen lines
#define FRAME_BUFFERS   3    // 3: draw the next frame while one is on screen and one waits for vsync.
                             // 2 (face/bench's) locks the frame rate to 36, 18 or 12 fps.
#define TWO_CORES       1    // both cores draw strips

#include "board.h"
#include "screen.h"

static_assert(FRAME_BUFFERS == 2 || FRAME_BUFFERS == 3, "FRAME_BUFFERS: 2 or 3");

// --- buffers -------------------------------------------------------------------------------------
struct CoreBuffers {
  uint16_t *px;            // the strip's lines, then its finished pixels
  uint8_t *core;           // its station light: the cores (world.h)
  uint8_t *glowHalf;       // and the glow, at half size
};
static CoreBuffers bufs[2];
static uint16_t *fadeFrom;  // the frame a crossfade starts from (PSRAM)

static uint32_t inPsram;          // which of the buffers below didn't fit in internal RAM (bits)

static void *fastAlloc(size_t n, uint32_t bit) {
  void *p = heap_caps_aligned_alloc(64, n, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT | MALLOC_CAP_DMA);
  if (!p) {
    p = heap_caps_aligned_alloc(64, n, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    inPsram |= bit;
  }
  return p;
}

// --- what's applied ------------------------------------------------------------------------------
static int lutTheme = -1, lutIdle = -1;
static float lutR = -1;
static Scene scene;
static uint32_t shownHash;
static bool haveShown;

// The last view projected (lines, dots), so a frame that only changes the text skips that work.
static struct {
  float lat, lon, R;
  bool borders;
  int theme;
  uint32_t stations;
  bool valid;
} projected;

// Crossfade (a new theme, the idle clock, a card).
static bool fading;
static double fadeStart;
static uint32_t fadeWeight;      // 0-32 for the new frame

// Standby: the screen's brightness, 0 (dark) to 1 (map_view.FaceScreen's level), times `bright`.
static float level = 1, levelGoal = 1, levelRate;
static bool asleep;
static double darkAt = NAN;      // a fade to sleep ends here, then the backlight goes off

// Statistics, for {"t":"stats"}: where the time goes, summed over the last second.
enum { ST_STYLE, ST_PROJECT, ST_WAIT, ST_STRIPS, ST_LINES, ST_DOTS, ST_COMP, ST_OVER, ST_OUT, ST_PLINES, ST_PDOTS, ST_PBIN, ST_COUNT };
static const char *STAGE_NAMES[ST_COUNT] = {"style", "project", "wait", "strips", "lines", "dots", "comp", "over", "out", "p-lines", "p-dots", "p-bin"};
static uint32_t stageCycles[ST_COUNT], lastStage[ST_COUNT];
static uint32_t framesDrawn, statFrames;
static uint64_t statCycles;
static double statSince;
static float lastFps, lastMs;

// --- jobs for core 0 -----------------------------------------------------------------------------
static esp_err_t panelResult;
static void createPanelJob(void *) { panelResult = panelCreate(PCLK_MHZ, BOUNCE_LINES, FRAME_BUFFERS); }
static void queueFrameJob(void *arg) { queueFrame((int)(intptr_t)arg); }

static View frameView;
static int dotsSplit;
static void sortDotsJob(void *) { sortDots(); }
static void projectDotsJob(void *) { dotsSplit = projectDots(frameView, 0, stationCount / 2, dots); }

// --- one strip -----------------------------------------------------------------------------------
static uint16_t *backFb;
static volatile int stripNext;

static uint32_t coreStage[2][ST_COUNT];

static void renderStrip(int core, int s) {
  CoreBuffers &b = bufs[core];
  uint32_t *acc = coreStage[core];
  const int y0 = s * STRIP_H;
  uint32_t c = cycles(), now;
  memset(b.px, 0, STRIP_H * LCD_W * 2);
  memset(b.core, 0, STRIP_H * LCD_W);
  memset(b.glowHalf, 0, GLOW_W * GLOW_ROWS);
  drawLines(b.px, s);
  acc[ST_LINES] += (now = cycles()) - c;
  c = now;
  stampDots(b.core, b.glowHalf, s);
  acc[ST_DOTS] += (now = cycles()) - c;
  c = now;
  compositeStrip(b.px, b.core, b.glowHalf, y0, STRIP_H);
  acc[ST_COMP] += (now = cycles()) - c;
  c = now;
  drawSprites(b.px, y0, STRIP_H, scene.sprites, scene.under);
  if (scene.dim) dimStrip(b.px, y0, STRIP_H);
  drawSprites(b.px, y0, STRIP_H, scene.sprites + scene.under, scene.count - scene.under);
  if (scene.volume >= 0) drawRing(b.px, y0, STRIP_H, scene.volume);
  if (fading) fadeStrip(b.px, y0, STRIP_H, fadeFrom, fadeWeight);
  acc[ST_OVER] += (now = cycles()) - c;
  c = now;
  uint16_t *fbRows = backFb + y0 * LCD_W;
  for (int j = 0; j < STRIP_H; j++) {             // just the part inside the round window
    const int l = lut.spanL[y0 + j], r = lut.spanR[y0 + j];
    memcpy(fbRows + j * LCD_W + l, b.px + j * LCD_W + l, (r - l) * 2);
  }
  acc[ST_OUT] += cycles() - c;
}

static void renderStrips(int core) {
  int s;
  while ((s = __atomic_fetch_add(&stripNext, 1, __ATOMIC_RELAXED)) < STRIP_COUNT) renderStrip(core, s);
}
static void stripsJob(void *) { renderStrips(0); }

// --- one frame -----------------------------------------------------------------------------------
static void applyStyle(const Scene &s) {
  if (s.theme != lutTheme) {
    useTheme(s.theme);
    lutTheme = s.theme;
    lutR = -1;
    projected.valid = false;                      // line colors
  }
  if (s.view.R != lutR) {
    buildGlobeLut(FACE_THEMES[s.theme], s.view.R);
    lutR = s.view.R;
  }
  if ((int)s.idleBands != lutIdle) {
    buildRowKeep(s.idleBands);
    lutIdle = s.idleBands;
  }
  setDotStyle(s.view.R);
}

static void projectScene(const Scene &s) {
  const View &v = s.view;
  if (projected.valid && projected.lat == v.lat && projected.lon == v.lon && projected.R == v.R &&
      projected.borders == s.borders && projected.theme == s.theme && projected.stations == stationsVersion &&
      spriteKey >= 0)
    return;
  if (TWO_CORES && stationCount > 1) {
    frameView = v;
    uint32_t c = cycles(), t;
    startOnCore0(projectDotsJob, NULL);
    projectLines(v, s.borders);
    stageCycles[ST_PLINES] += (t = cycles()) - c;
    c = t;
    const int half = stationCount / 2;
    const int n = projectDots(v, half, stationCount, dots + half);
    waitCore0();
    stageCycles[ST_PDOTS] += cycles() - c;
    memmove(dots + dotsSplit, dots + half, n * 4);
    dotCount = dotsSplit + n;
  } else {
    projectLines(v, s.borders);
    dotCount = projectDots(v, 0, stationCount, dots);
  }
  const uint32_t cb = cycles();
  startOnCore0(sortDotsJob, NULL);                // the two cores file lines and dots at once
  binSegments();
  waitCore0();
  stageCycles[ST_PBIN] += cycles() - cb;
  projected = {v.lat, v.lon, v.R, s.borders, s.theme, stationsVersion, true};
}

static void drawScene(double now) {
  const uint32_t c0 = cycles();
  uint32_t c = c0, t;
  applyStyle(scene);
  stageCycles[ST_STYLE] += (t = cycles()) - c;
  c = t;
  projectScene(scene);
  stageCycles[ST_PROJECT] += (t = cycles()) - c;
  c = t;
  if (scr.fadeStarted) {                          // fade from the frame last queued for the screen
    scr.fadeStarted = false;
    if (haveShown && fadeFrom) {
      memcpy(fadeFrom, fbs[fbQueued], LCD_W * LCD_H * 2);
      fading = true;
      fadeStart = now;
    }
  }
  if (fading) {
    const double t = (now - fadeStart) / CROSSFADE_S;
    if (t >= 1) {
      fading = false;
    } else {
      const double e = t * t * (3 - 2 * t);
      fadeWeight = (uint32_t)lrint(e * 32);
    }
  }
  const int backIndex = nextBackBuffer();
  backFb = fbs[backIndex];
  stageCycles[ST_WAIT] += (t = cycles()) - c;
  c = t;
  stripNext = 0;
  if (TWO_CORES) {
    startOnCore0(stripsJob, NULL);
    renderStrips(1);
    waitCore0();
  } else {
    renderStrips(1);
  }
  stageCycles[ST_STRIPS] += cycles() - c;
  runOnCore0(queueFrameJob, (void *)(intptr_t)backIndex);
  shownHash = scene.hash;
  haveShown = true;
  framesDrawn++;
  statFrames++;
  statCycles += cycles() - c0;
}

// --- standby -------------------------------------------------------------------------------------
static void slideLevel(float goal, float seconds) {
  levelGoal = goal;
  if (seconds > 0) levelRate = 1 / seconds;
  else level = goal;
}

static void handleSleepWake(double now) {
  if (sleepRequested) {
    sleepRequested = false;
    slideLevel(0, sleepFade);
    if (sleepFade > 0) darkAt = now + sleepFade;   // fade first, then the backlight goes off
    else asleep = true;
  }
  if (wakeRequested) {
    wakeRequested = false;
    asleep = false;
    darkAt = NAN;
    scr.time = now;                                // it hasn't been drawing while dark
    slideLevel(1, wakeFade);
    haveShown = false;                             // draw again
  }
  if (!isnan(darkAt) && now >= darkAt) {
    asleep = true;
    darkAt = NAN;
  }
}

static void updateBacklight(double dt) {
  if (level != levelGoal) {
    const float step = dt * levelRate;
    level = levelGoal > level ? fminf(levelGoal, level + step) : fmaxf(levelGoal, level - step);
  }
  const int bright = haveState ? (faceState.bright < 10 ? 10 : faceState.bright > 100 ? 100 : faceState.bright) : 100;
  backlightSet(asleep ? 0 : level * level * bright / 100.0f);   // slow at the dark end, like dawn
}

// --- statistics ----------------------------------------------------------------------------------
static void printStats(double now) {
  Serial.printf("{\"t\":\"stats\",\"stages\":{");
  for (int i = 0; i < ST_COUNT; i++)
    Serial.printf("%s\"%s\":%.1f", i ? "," : "", STAGE_NAMES[i], lastFps > 0 ? lastStage[i] / 240000.0f / (lastFps) : 0.0f);
  Serial.printf("},");
  Serial.printf("\"fps\":%.1f,\"ms\":%.1f,\"frames\":%u,\"stations\":%d,\"good\":%u,\"bad\":%u,"
                "\"overflows\":%u,\"internal\":%u,\"psram\":%u,\"points\":%d,\"dropped\":%d,\"segdropped\":%d,"
                "\"dots\":%d,\"vsyncs\":%u,\"inpsram\":%u}\n",
                lastFps, lastMs, (unsigned)framesDrawn, stationCount, (unsigned)goodLines, (unsigned)badLines,
                (unsigned)ringOverflows, (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL),
                (unsigned)heap_caps_get_free_size(MALLOC_CAP_SPIRAM), pointCount, pointsDropped, segmentsDropped,
                dotCount, (unsigned)vsyncCount, (unsigned)inPsram);
}

static void updateStats(double now) {
  if (now - statSince < 1) return;
  lastFps = statFrames / (now - statSince);
  lastMs = statFrames ? statCycles / 240000.0f / statFrames : 0;
  for (int i = 0; i < ST_COUNT; i++) {
    lastStage[i] = stageCycles[i] + coreStage[0][i] + coreStage[1][i];
    stageCycles[i] = coreStage[0][i] = coreStage[1][i] = 0;
  }
  statFrames = 0;
  statCycles = 0;
  statSince = now;
}

// A copy of the frame on screen, for checking it against the laptop preview ({"t":"shot"}): a line
// {"t":"shot","w":480,"h":480} and then the 480 x 480 RGB565 pixels, little-endian.
static void sendShot() {
  Serial.printf("{\"t\":\"shot\",\"w\":%d,\"h\":%d}\n", LCD_W, LCD_H);
  Serial.setTxTimeoutMs(200);
  const uint8_t *p = (const uint8_t *)fbs[fbQueued];
  size_t left = LCD_W * LCD_H * 2;
  const int64_t until = esp_timer_get_time() + 5000000;
  while (left && esp_timer_get_time() < until) {
    size_t room = Serial.availableForWrite();
    if (!room) {
      delay(1);
      continue;
    }
    size_t n = Serial.write(p, left < room ? left : room);
    p += n;
    left -= n;
  }
  Serial.setTxTimeoutMs(0);
}

// --- setup and loop ------------------------------------------------------------------------------
void setup() {
  linkStart();
  if (!psramFound()) {
    for (;;) {
      Serial.println("No PSRAM: set Tools > PSRAM to \"OPI PSRAM\".");
      delay(2000);
    }
  }
  core0Start();
  if (!st7701Init()) {
    for (;;) {
      Serial.println("The IO expander doesn't answer: is this the ESP32-S3-LCD-2.8C?");
      delay(2000);
    }
  }
  runOnCore0(createPanelJob, NULL);
  if (panelResult != ESP_OK) {
    for (;;) {
      Serial.printf("Couldn't start the screen: %s\n", esp_err_to_name(panelResult));
      delay(2000);
    }
  }
  backlightInit();
  backlightSet(0);
  clearFrameBuffers();

  setupStrips();
  lightCoreLut = coreLut;
  lightGlowLut = glowLut;
  buildEdgeLuts();
  buildRing();
  buildGrids();
  buildAllBlocks();
  stationCount = STATION_COUNT;
  stationXyz = xyzFromPositions(STATION_POS, STATION_COUNT);
  // Internal RAM, fastest first (anything that doesn't fit goes to PSRAM, slower; stats say which).
  for (int c = 0; c < 2; c++) {
    bufs[c].px = (uint16_t *)fastAlloc(STRIP_H * LCD_W * 2, c ? 1 : 2);
    bufs[c].core = (uint8_t *)fastAlloc(STRIP_H * LCD_W, c ? 4 : 8);
    bufs[c].glowHalf = (uint8_t *)fastAlloc(GLOW_W * GLOW_ROWS, c ? 512 : 1024);
  }
  dotCap = stationCount;
  dots = (uint32_t *)fastAlloc(dotCap * 4, 16);
  // Lines: most views need under 9,000 points, in internal RAM; the rare one with more (a flight
  // between zooms can pass 50,000) uses the big lists in PSRAM for that frame.
  pointsFastCap = 9216;
  pointsFast = (uint32_t *)fastAlloc(pointsFastCap * 4, 32);
  segmentsFastCap = 9216;
  segmentsFast = (uint16_t *)fastAlloc(segmentsFastCap * 2, 64);
  pointsBig = (uint32_t *)heap_caps_malloc(POINTS_BIG * 4, MALLOC_CAP_SPIRAM);
  segmentsBig = (uint16_t *)heap_caps_malloc(POINTS_BIG * 2, MALLOC_CAP_SPIRAM);
  points = pointsFast;
  pointCap = pointsFastCap;
  segments = segmentsFast;
  segmentCap = segmentsFastCap;
  fadeFrom = (uint16_t *)heap_caps_malloc(LCD_W * LCD_H * 2, MALLOC_CAP_SPIRAM);
  screenReset();
  sayHello();
  statSince = nowSeconds();
}

void loop() {
  static double lastLoop = nowSeconds(), lastHello = nowSeconds();
  linkPoll(10);
  const double now = nowSeconds(), dt = now - lastLoop;
  lastLoop = now;
  handleSleepWake(now);
  updateBacklight(dt);
  if (statsRequested) {
    statsRequested = false;
    printStats(now);
  }
  if (sceneRequested) {                            // debugging: the sprites of the last frame
    sceneRequested = false;
    Serial.printf("{\"t\":\"scene\",\"favs\":%d,\"under\":%d,\"sprites\":[", favCount, scene.under);
    for (int i = 0; i < scene.count; i++) {
      const Sprite &sp = scene.sprites[i];
      Serial.printf("%s[%d,%d,%d,%d,%u,%u,%d]", i ? "," : "", sp.x, sp.y, sp.w, sp.h, sp.color, sp.alpha, sp.under);
    }
    Serial.printf("]}\n");
  }
  if (shotRequested && haveShown && !fading) {
    shotRequested = false;
    sendShot();
  }
  updateStats(now);
  if (asleep) {                                    // standby: the backlight is off, nothing to draw
    delay(10);
    return;
  }
  textFrame++;
  bool busy;
  if (!haveState || now - heardAt > QUIET_S) {
    // Waiting for the radio. Say hello now and then, in case the first one went unheard.
    if (now - lastHello > 10) {
      sayHello();
      lastHello = now;
    }
    waitingScene(scene, now, faceState.theme);
    busy = true;
  } else {
    busy = frameScene(scene, faceState, now);
  }
  (void)busy;
  if (haveShown && scene.hash == shownHash && !fading) {
    delay(2);                                      // nothing on screen would change
    return;
  }
  drawScene(now);
}
