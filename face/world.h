// world.h: what's on the globe for one view, cut into strips. (face/bench's world.h, grown up: every
// zoom, every theme, the stations the Pi sends.)
//
// Coastlines, borders and the latitude/longitude grid are unit vectors in int16 (x 32767), turned
// to the screen with one int16 3x3 multiply per point (no sin or cos per frame), in runs of up to
// 33 points with a bounding circle so runs off screen are skipped without touching their points.
// The station dots are the same, one point each. Then each line segment and each dot is filed under
// the strips it touches, so a strip can draw just its own.
//
// The data comes from `python tools/face_data.py` (see README.md).
#pragma once
#include "render.h"
#include "data_coast_110m.h"
#include "data_coast_50m.h"
#include "data_coast_10m.h"
#include "data_border_110m.h"
#include "data_border_50m.h"
#include "data_border_10m.h"
#include "data_stations.h"

static const float EARTH_KM = 6371.0f;
static const float WORLD_R = (160 - 14) * K;                 // radius_for_view(180): 219 px
static const float DETAIL_R = 400 * K;                       // above this: 50m coastlines, 10-degree grid
static const float CLOSE_R = 2500 * K;                       // above this: 10m coastlines, 2-degree grid

// Globe radius in pixels for a zoom in km across the window, 0 = the whole world (radius_for_view).
static float radiusForZoom(int zoomKm) {
  if (zoomKm <= 0) return WORLD_R;
  float width = zoomKm / EARTH_KM;                           // radians across
  if (width >= (float)M_PI) return WORLD_R;
  return (LCD_W / 2) / sinf(width / 2);
}

// --- strips --------------------------------------------------------------------------------------
static const int STRIP_H = 24;
static const int STRIP_COUNT = LCD_H / STRIP_H;
static_assert(LCD_H % STRIP_H == 0, "strips must tile the screen");
static uint8_t rowStrip[LCD_H];

static void setupStrips() {
  for (int y = 0; y < LCD_H; y++) rowStrip[y] = y / STRIP_H;
}

// --- line data -----------------------------------------------------------------------------------
// Runs are also grouped in blocks of 32 neighbors with a circle round each block (made at start-up),
// so a view skips most of the runs that are off screen 32 at a time: the 500 km view keeps about
// 1% of the 10m coastline's 15,000 runs.
struct LineBlock {
  float c[3];             // center (unit vector)
  float r;                // angular radius, radians
  uint32_t first, count;  // runs
};

struct LineSet {
  const int16_t (*pts)[3];
  const CoastRun *runs;
  uint32_t runCount, pointCount;
  LineBlock *blocks;
  uint32_t blockCount;
};

// Lines made on the board (the grid), packed the same way, in PSRAM.
struct LineBuf {
  int16_t (*pts)[3];
  CoastRun *runs;
  uint32_t pointCount, runCount;
};

static void unitVector(float latDeg, float lonDeg, float out[3]) {
  float la = latDeg * (float)M_PI / 180, lo = lonDeg * (float)M_PI / 180;
  out[0] = cosf(la) * cosf(lo);
  out[1] = cosf(la) * sinf(lo);
  out[2] = sinf(la);
}

// Appends a polyline, cut into runs of up to 33 points (runs of one line share their joining
// point). With b.pts == NULL it only counts, to size the buffers.
static void addPolyline(LineBuf &b, const float (*v)[3], int n) {
  for (int a = 0; a + 1 < n; a += 32) {
    int m = (n - a < 33) ? n - a : 33;
    if (b.pts) {
      float s[3] = {0, 0, 0};
      for (int i = 0; i < m; i++)
        for (int q = 0; q < 3; q++) s[q] += v[a + i][q];
      float len = sqrtf(s[0] * s[0] + s[1] * s[1] + s[2] * s[2]);
      for (int q = 0; q < 3; q++) s[q] /= len;
      float lowest = 1;
      for (int i = 0; i < m; i++) {
        float dot = v[a + i][0] * s[0] + v[a + i][1] * s[1] + v[a + i][2] * s[2];
        if (dot < lowest) lowest = dot;
        for (int q = 0; q < 3; q++) b.pts[b.pointCount + i][q] = (int16_t)lrintf(v[a + i][q] * 32767);
      }
      float r = acosf(clampf(lowest, -1, 1)) + 1e-4f;
      CoastRun &run = b.runs[b.runCount];
      run.cx = (int16_t)lrintf(s[0] * 32767);
      run.cy = (int16_t)lrintf(s[1] * 32767);
      run.cz = (int16_t)lrintf(s[2] * 32767);
      run.cos_r = (int16_t)floorf(cosf(r) * 32767);
      float sr = ceilf(sinf(r) * 32767);
      run.sin_r = (int16_t)(sr > 32767 ? 32767 : sr);
      run.count = m;
      run.first = b.pointCount;
    }
    b.pointCount += m;
    b.runCount++;
  }
}

static float (*polyTmp)[3];   // one polyline at a time, while building

// Latitude/longitude lines every `spacing` degrees, a point every `sample` degrees (coastlines.graticule).
static void graticule(LineBuf &b, float spacing, float sample) {
  const int lonSteps = (int)lrintf(360 / sample), latSteps = (int)lrintf(160 / sample);
  for (int i = 1; -90 + i * spacing < 90 - 1e-3f; i++) {
    float lat = -90 + i * spacing;
    for (int j = 0; j <= lonSteps; j++) unitVector(lat, -180 + j * sample, polyTmp[j]);
    addPolyline(b, polyTmp, lonSteps + 1);
  }
  for (int i = 0; -180 + i * spacing < 180 - 1e-3f; i++) {
    float lon = -180 + i * spacing;
    for (int j = 0; j <= latSteps; j++) unitVector(-80 + j * sample, lon, polyTmp[j]);
    addPolyline(b, polyTmp, latSteps + 1);
  }
}

// Runs a builder twice: once to count, then to fill buffers of the right size.
template <typename F>
static LineSet buildLines(LineBuf &b, F build) {
  memset(&b, 0, sizeof b);
  build(b);
  const uint32_t points = b.pointCount, runs = b.runCount;
  b.pts = (int16_t(*)[3])heap_caps_malloc(points * 6, MALLOC_CAP_SPIRAM);
  b.runs = (CoastRun *)heap_caps_malloc(runs * sizeof(CoastRun), MALLOC_CAP_SPIRAM);
  b.pointCount = b.runCount = 0;
  build(b);
  return {(const int16_t(*)[3])b.pts, b.runs, b.runCount, b.pointCount, NULL, 0};
}

// The line layers: grids every 30, 10 and 2 degrees, then borders and coastlines at 110m, 50m, 10m.
static LineSet grids[3];
static LineSet BORDERS[3] = {
  {BORDER_110M_POINTS, BORDER_110M_RUNS, BORDER_110M_RUN_COUNT, BORDER_110M_POINT_COUNT, NULL, 0},
  {BORDER_50M_POINTS, BORDER_50M_RUNS, BORDER_50M_RUN_COUNT, BORDER_50M_POINT_COUNT, NULL, 0},
  {BORDER_10M_POINTS, BORDER_10M_RUNS, BORDER_10M_RUN_COUNT, BORDER_10M_POINT_COUNT, NULL, 0},
};
static LineSet COASTS[3] = {
  {COAST_110M_POINTS, COAST_110M_RUNS, COAST_110M_RUN_COUNT, COAST_110M_POINT_COUNT, NULL, 0},
  {COAST_50M_POINTS, COAST_50M_RUNS, COAST_50M_RUN_COUNT, COAST_50M_POINT_COUNT, NULL, 0},
  {COAST_10M_POINTS, COAST_10M_RUNS, COAST_10M_RUN_COUNT, COAST_10M_POINT_COUNT, NULL, 0},
};

static void buildBlocks(LineSet &set) {
  const uint32_t B = 32;
  set.blockCount = (set.runCount + B - 1) / B;
  set.blocks = (LineBlock *)heap_caps_malloc(set.blockCount * sizeof(LineBlock), MALLOC_CAP_SPIRAM);
  for (uint32_t k = 0; k < set.blockCount; k++) {
    LineBlock &b = set.blocks[k];
    b.first = k * B;
    b.count = set.runCount - b.first < B ? set.runCount - b.first : B;
    float c[3] = {0, 0, 0};
    for (uint32_t r = b.first; r < b.first + b.count; r++) {
      c[0] += set.runs[r].cx;
      c[1] += set.runs[r].cy;
      c[2] += set.runs[r].cz;
    }
    float len = sqrtf(c[0] * c[0] + c[1] * c[1] + c[2] * c[2]);
    if (len < 1e-3f) {
      c[0] = 1;
      c[1] = c[2] = 0;
      len = 1;
    }
    for (int i = 0; i < 3; i++) b.c[i] = c[i] / len;
    float r = 0;
    for (uint32_t i = b.first; i < b.first + b.count; i++) {
      const CoastRun &run = set.runs[i];
      const float n = sqrtf((float)run.cx * run.cx + (float)run.cy * run.cy + (float)run.cz * run.cz);
      const float d = (run.cx * b.c[0] + run.cy * b.c[1] + run.cz * b.c[2]) / n;
      const float a = acosf(clampf(d, -1, 1)) + acosf(clampf(run.cos_r / 32767.0f, -1, 1)) + 1e-3f;
      if (a > r) r = a;
    }
    b.r = r;
  }
}

// The 50m and 10m lines (3.7 MB) are copied from flash to PSRAM at start-up: flash shares its bus
// with PSRAM, which the screen reads all the time, and reading the lines from flash took 9 ms a
// frame in the 500 km view.
static void copyToPsram(LineSet &set) {
  int16_t (*pts)[3] = (int16_t(*)[3])heap_caps_malloc(set.pointCount * 6, MALLOC_CAP_SPIRAM);
  CoastRun *runs = (CoastRun *)heap_caps_malloc(set.runCount * sizeof(CoastRun), MALLOC_CAP_SPIRAM);
  if (!pts || !runs) {                   // no room: keep reading from flash
    heap_caps_free(pts);
    heap_caps_free(runs);
    return;
  }
  memcpy(pts, set.pts, set.pointCount * 6);
  memcpy(runs, set.runs, set.runCount * sizeof(CoastRun));
  set.pts = pts;
  set.runs = runs;
}

static void buildAllBlocks() {
  for (int i = 1; i < 3; i++) {
    copyToPsram(COASTS[i]);
    copyToPsram(BORDERS[i]);
  }
  for (int i = 0; i < 3; i++) {
    buildBlocks(grids[i]);
    buildBlocks(BORDERS[i]);
    buildBlocks(COASTS[i]);
  }
}

static void buildGrids() {
  static LineBuf g30, g10, g2;
  polyTmp = (float(*)[3])heap_caps_malloc(800 * sizeof *polyTmp, MALLOC_CAP_SPIRAM);
  grids[0] = buildLines(g30, [](LineBuf &b) { graticule(b, 30, 2); });
  grids[1] = buildLines(g10, [](LineBuf &b) { graticule(b, 10, 1); });
  grids[2] = buildLines(g2, [](LineBuf &b) { graticule(b, 2, 0.5f); });
  heap_caps_free(polyTmp);
}

// --- stations ------------------------------------------------------------------------------------
// Unit vectors in PSRAM. The compiled-in list until the Pi sends its own.
static int16_t (*stationXyz)[3];
static int stationCount;
static uint32_t stationsVersion;      // changes whenever the list does (or a game world's lines)

// --- a game world --------------------------------------------------------------------------------
// The world and lines messages (face_link.world_messages): a game's map on an otherwise empty
// globe (worlddeskradio/worlds.py). Its lines take the place of Earth's coastlines and borders at every
// zoom; they're built here in PSRAM as they arrive. A world message with no name is Earth again.
static const int16_t LINE_BREAK = -32768;    // in a lines message: ends one line, starts the next
static bool inWorld;                         // drawing a game world, not Earth
static LineSet worldLayers[2];               // its coastlines [0] and borders [1]: empty until they arrive
static double homeLat, homeLon;              // its whole-map view, for idle and long jumps (map_view's home)
static int homeZoom;                         // km across
static LineBuf worldBufs[2];

static void freeWorldLayer(int layer) {
  LineSet &set = worldLayers[layer];
  heap_caps_free((void *)set.pts);
  heap_caps_free((void *)set.runs);
  heap_caps_free(set.blocks);
  memset(&set, 0, sizeof set);
}

static void setWorld(bool on) {
  for (int i = 0; i < 2; i++) freeWorldLayer(i);
  inWorld = on;
  stationsVersion++;                         // the map changed: project and draw it again
}

// A layer's points, in 1/180ths of a degree, lines split at LINE_BREAK pairs.
static void setWorldLayer(int layer, const int16_t (*pos)[2], int n) {
  freeWorldLayer(layer);
  int longest = 0, run = 0;
  for (int i = 0; i <= n; i++) {
    if (i == n || pos[i][0] == LINE_BREAK) {
      if (run > longest) longest = run;
      run = 0;
    } else {
      run++;
    }
  }
  float (*tmp)[3] = (float(*)[3])heap_caps_malloc((longest ? longest : 1) * sizeof *tmp, MALLOC_CAP_SPIRAM);
  if (!tmp) return;
  worldLayers[layer] = buildLines(worldBufs[layer], [&](LineBuf &b) {
    int start = 0;
    for (int i = 0; i <= n; i++) {
      if (i < n && pos[i][0] != LINE_BREAK) continue;
      const int m = i - start;
      for (int j = 0; j < m; j++) unitVector(pos[start + j][0] / 180.0f, pos[start + j][1] / 180.0f, tmp[j]);
      if (m >= 2) addPolyline(b, tmp, m);
      start = i + 1;
    }
  });
  heap_caps_free(tmp);
  if (worldLayers[layer].runCount) buildBlocks(worldLayers[layer]);
  stationsVersion++;
}

// Positions as the stations message packs them: int16 (lat, lon) in 1/180ths of a degree.
static int16_t (*xyzFromPositions(const int16_t (*pos)[2], int n))[3] {
  int16_t (*xyz)[3] = (int16_t(*)[3])heap_caps_malloc((n ? n : 1) * 6, MALLOC_CAP_SPIRAM);
  if (!xyz) return NULL;
  for (int i = 0; i < n; i++) {
    float v[3];
    unitVector(pos[i][0] / 180.0f, pos[i][1] / 180.0f, v);
    for (int q = 0; q < 3; q++) xyz[i][q] = (int16_t)lrintf(v[q] * 32767);
  }
  return xyz;
}

// --- one view ------------------------------------------------------------------------------------
struct View {
  // The 3x3 matrix: screen x and y in 1/2^shift px, depth (x 16384). east and south are 32-bit:
  // times a point's int16 (x 32767) their sum stays under 2^31 while R << shift is at most 65,000.
  int32_t east[3], south[3];
  int16_t up[3];
  int shift;                          // 3 (1/8 px) up to a 8,125 px radius, 2 (1/4 px) to 16,250 (the
                                      // Mojave's 200 km close up is 15,291), then 1 and 0
  float upf[3];                       // the view center as a unit vector, for skipping runs
  float m[3][3];                      // the same in floats: east * R, south * R, up
  float cosHalf, sinHalf;             // how far from the center a point can be and still be on screen
  float half;                         // ... as an angle, radians
  float lat, lon, R;
};

// The globe seen from far out in space above (lat, lon), radius R pixels (map_view._view_matrix).
static void makeView(View &v, float latDeg, float lonDeg, float R) {
  float p = latDeg * (float)M_PI / 180, l = lonDeg * (float)M_PI / 180;
  float sp = sinf(p), cp = cosf(p), sl = sinf(l), cl = cosf(l);
  float east[3] = {-sl, cl, 0}, north[3] = {-sp * cl, -sp * sl, cp}, up[3] = {cp * cl, cp * sl, sp};
  v.shift = 3;
  while (v.shift > 0 && R * (1 << v.shift) > 65000) v.shift--;
  const float scale = R * (1 << v.shift);
  for (int i = 0; i < 3; i++) {
    v.east[i] = (int32_t)lrintf(east[i] * scale);
    v.south[i] = (int32_t)lrintf(-north[i] * scale);
    v.up[i] = (int16_t)lrintf(up[i] * 16384);
    v.upf[i] = up[i];
    v.m[0][i] = east[i] * R;
    v.m[1][i] = -north[i] * R;
    v.m[2][i] = up[i];
  }
  float half = asinf(fminf(1.0f, (LCD_W / 2 + 40) * 1.41421356f / R));
  v.cosHalf = cosf(half);
  v.sinHalf = sinf(half);
  v.half = half;
  v.lat = latDeg;
  v.lon = lonDeg;
  v.R = R;
}

// A point (unit vector) on screen: x, y in pixels from the top left, and whether it faces us.
static bool projectPoint(const View &v, const float p[3], float &x, float &y) {
  x = v.m[0][0] * p[0] + v.m[0][1] * p[1] + v.m[0][2] * p[2] + LCD_W / 2;
  y = v.m[1][0] * p[0] + v.m[1][1] * p[1] + v.m[1][2] * p[2] + LCD_H / 2;
  return v.m[2][0] * p[0] + v.m[2][1] * p[1] + v.m[2][2] * p[2] > 0;
}

// Projected line points, packed: x + PT_OFF in bits 0-9, y + PT_OFF in bits 16-25, and two flags.
// A point up to PT_MARGIN px off screen still counts as seen, so a segment running off the edge
// is drawn (a game world's roads close up have points 60 px and more apart).
// They go in internal RAM, which holds most views; a view with more (a flight between zooms can
// pass 50,000) moves them to a bigger list in PSRAM for that frame.
static const int PT_OFF = 256, PT_MARGIN = 200;   // (MapView._draw_lines's margin)
static const uint32_t PT_HIDDEN = 1 << 15;   // behind the globe or well off screen
static const uint32_t PT_START = 1 << 14;    // first point of a run: no segment joins it to the one before
static uint32_t *points, *pointsFast, *pointsBig;
static int pointCap, pointCount, pointsDropped, pointsFastCap;
static const int POINTS_BIG = 65535;         // segment lists hold point numbers in 16 bits
static int layerEnd[3];                      // where each layer's points end
static uint16_t lineColors[3];

static void IRAM_ATTR projectLayer(const View &v, const LineSet &set) {
  const int32_t e0 = v.east[0], e1 = v.east[1], e2 = v.east[2];
  const int32_t s0 = v.south[0], s1 = v.south[1], s2 = v.south[2];
  const int16_t u0 = v.up[0], u1 = v.up[1], u2 = v.up[2];
  const int sh = v.shift, center = (LCD_W / 2) << sh, round = sh ? 1 << (sh - 1) : 0;
  const float q = 1.0f / 32767;
  for (uint32_t k = 0; k < set.blockCount; k++) {
   const LineBlock &blk = set.blocks[k];
   if (blk.r + v.half < (float)M_PI &&
       blk.c[0] * v.upf[0] + blk.c[1] * v.upf[1] + blk.c[2] * v.upf[2] < cosf(blk.r + v.half))
     continue;                                                       // the whole block is off screen
   for (uint32_t r = blk.first; r < blk.first + blk.count; r++) {
    const CoastRun &run = set.runs[r];
    float toCenter = (run.cx * v.upf[0] + run.cy * v.upf[1] + run.cz * v.upf[2]) * q;
    if (toCenter < v.cosHalf * run.cos_r * q - v.sinHalf * run.sin_r * q) continue;   // off screen
    if (pointCount + run.count > pointCap) {
      if (points == pointsFast && pointsBig) {        // too many for internal RAM: move to PSRAM
        memcpy(pointsBig, points, pointCount * 4);
        points = pointsBig;
        pointCap = POINTS_BIG;
      }
      if (pointCount + run.count > pointCap) {
        pointsDropped += run.count;
        continue;
      }
    }
    const int16_t (*p)[3] = set.pts + run.first;
    uint32_t *out = points + pointCount;
    for (int i = 0; i < run.count; i++) {
      const int32_t a = p[i][0], b = p[i][1], c = p[i][2];
      const int x = (((a * e0 + b * e1 + c * e2) >> 15) + center + round) >> sh;
      const int y = (((a * s0 + b * s1 + c * s2) >> 15) + center + round) >> sh;
      const int depth = a * u0 + b * u1 + c * u2;
      const bool seen = depth > 0 && (unsigned)(x + PT_MARGIN) < (unsigned)(LCD_W + 2 * PT_MARGIN) &&
                        (unsigned)(y + PT_MARGIN) < (unsigned)(LCD_H + 2 * PT_MARGIN);
      out[i] = ((uint32_t)(x + PT_OFF) & 0x3FF) | ((uint32_t)(y + PT_OFF) & 0x3FF) << 16 | (seen ? 0 : PT_HIDDEN);
    }
    out[0] |= PT_START;
    pointCount += run.count;
   }
  }
}

// The lines of a view: the grid, the borders (if shown), the coastlines, at the detail the zoom
// calls for (MapView.render). Grid, borders, coast colors go in lineColors.
static void projectLines(const View &v, bool borders) {
  const int res = v.R > CLOSE_R ? 2 : v.R > DETAIL_R ? 1 : 0;
  pointCount = pointsDropped = 0;
  points = pointsFast;
  pointCap = pointsFastCap;
  projectLayer(v, grids[res]);
  layerEnd[0] = pointCount;
  if (inWorld) {                             // a game world: its own lines, the same at every zoom
    if (borders && worldLayers[1].runCount) projectLayer(v, worldLayers[1]);
    layerEnd[1] = pointCount;
    if (worldLayers[0].runCount) projectLayer(v, worldLayers[0]);
  } else {
    if (borders) projectLayer(v, BORDERS[res]);
    layerEnd[1] = pointCount;
    projectLayer(v, COASTS[res]);
  }
  layerEnd[2] = pointCount;
  lineColors[0] = col.grid;
  lineColors[1] = col.border;
  lineColors[2] = res ? col.coast : col.coastWorld;
}

// Segment k joins point k to point k+1. Each is filed under every strip it touches.
static uint16_t *segments, *segmentsFast, *segmentsBig;
static int segmentCap, segmentsDropped, segmentsFastCap;
static uint16_t segStart[STRIP_COUNT + 1];

static inline bool segmentSpan(int k, int &s0, int &s1) {
  uint32_t a = points[k], b = points[k + 1];
  if ((a | b) & PT_HIDDEN || b & PT_START) return false;
  int ya = (int)(a >> 16) - PT_OFF, yb = (int)(b >> 16) - PT_OFF;
  int lo = ya < yb ? ya : yb, hi = ya < yb ? yb : ya;
  if (hi < 0 || lo >= LCD_H) return false;
  s0 = rowStrip[lo < 0 ? 0 : lo];
  s1 = rowStrip[hi >= LCD_H ? LCD_H - 1 : hi];
  return true;
}

static void binSegments() {
  uint16_t count[STRIP_COUNT] = {0};
  int s0, s1;
  for (int k = 0; k + 1 < pointCount; k++)
    if (segmentSpan(k, s0, s1))
      for (int s = s0; s <= s1; s++) count[s]++;
  uint16_t next[STRIP_COUNT];
  int total = 0;
  for (int s = 0; s < STRIP_COUNT; s++) total += count[s];
  segments = segmentsFast;
  segmentCap = segmentsFastCap;
  if (total > segmentCap && segmentsBig) {          // too many for internal RAM this frame
    segments = segmentsBig;
    segmentCap = POINTS_BIG;
  }
  total = 0;
  for (int s = 0; s < STRIP_COUNT; s++) {
    segStart[s] = next[s] = total < segmentCap ? total : segmentCap;
    total += count[s];
  }
  segStart[STRIP_COUNT] = total < segmentCap ? total : segmentCap;
  segmentsDropped = total > segmentCap ? total - segmentCap : 0;
  for (int k = 0; k + 1 < pointCount; k++)
    if (segmentSpan(k, s0, s1))
      for (int s = s0; s <= s1; s++)
        if (next[s] < segStart[s + 1]) segments[next[s]++] = k;
}

// A 1-px line from (xa, ya) to (xb, yb), only the rows ys..ys+h-1 of it, into a strip of RGB565.
static inline void drawSegment(uint16_t *px, int ys, int h, int xa, int ya, int xb, int yb, uint16_t color) {
  const int dx = xb - xa, dy = yb - ya;
  const int adx = dx < 0 ? -dx : dx, ady = dy < 0 ? -dy : dy;
  if (adx >= ady) {
    if (adx == 0) {
      if ((unsigned)(ya - ys) < (unsigned)h && (unsigned)xa < LCD_W) px[(ya - ys) * LCD_W + xa] = color;
      return;
    }
    const int sx = dx > 0 ? 1 : -1;
    const int32_t step = dy * 65536 / adx;
    int32_t yf = (ya - ys) * 65536 + 32768;
    for (int i = 0, x = xa; i <= adx; i++, x += sx, yf += step) {
      const int y = yf >> 16;
      if ((unsigned)y < (unsigned)h && (unsigned)x < LCD_W) px[y * LCD_W + x] = color;
    }
  } else {
    const int sy = dy > 0 ? 1 : -1;
    int i0 = sy > 0 ? ys - ya : ya - (ys + h - 1);   // only the steps that land inside the strip
    int i1 = sy > 0 ? ys + h - 1 - ya : ya - ys;
    if (i0 < 0) i0 = 0;
    if (i1 > ady) i1 = ady;
    const int32_t step = dx * 65536 / ady;
    int32_t xf = xa * 65536 + 32768 + i0 * step;
    for (int i = i0, y = ya + i0 * sy - ys; i <= i1; i++, y += sy, xf += step) {
      const int x = xf >> 16;
      if ((unsigned)x < LCD_W) px[y * LCD_W + x] = color;
    }
  }
}

// Strip s's lines into its RGB565 buffer.
static void IRAM_ATTR drawLines(uint16_t *px, int s) {
  const int ys = s * STRIP_H;
  for (int i = segStart[s]; i < segStart[s + 1]; i++) {
    const int k = segments[i];
    const uint32_t a = points[k], b = points[k + 1];
    const uint16_t color = k < layerEnd[0] ? lineColors[0] : k < layerEnd[1] ? lineColors[1] : lineColors[2];
    drawSegment(px, ys, STRIP_H, (int)(a & 0x3FF) - PT_OFF, (int)(a >> 16) - PT_OFF, (int)(b & 0x3FF) - PT_OFF,
                (int)(b >> 16) - PT_OFF, color);
  }
}

// --- station dots --------------------------------------------------------------------------------
// The station light as map_view._station_light makes it: every station adds to a core layer (its
// share of a pixel, or of the 3x3 pixels around it when zoomed in, so dots glide smoothly), the
// core goes through a brightness curve, and a blur of it, made at half size and enlarged smoothly,
// through another curve is the glow round it; where they meet the brighter wins.
// Here each station adds two small pre-drawn sprites, saturating at 255: its core to the strip's
// core layer, and its blurred core (x4 for precision) to a half-size glow layer. Crowded places add
// up and glow like city lights, as in the preview. Then the glow layer is enlarged (bilinear, as
// PIL's resize) and the composite puts both through their curves (coreLut, glowLut).
// 4x4 versions of each sprite at quarter-pixel offsets.
static const int GLOW_MAX_SIZE = 7, CORE_MAX_SIZE = 4;
static uint8_t glowSprites[16 * GLOW_MAX_SIZE * GLOW_MAX_SIZE], coreSprites[16 * CORE_MAX_SIZE * CORE_MAX_SIZE];
static int glowSize, coreSize;               // sprite sizes (the glow's in half-size pixels)
static bool wideDots;
static int spriteKey = -1;
static uint8_t coreLut[256], glowLut[256];
static const float GLOW_SCALE = 4;           // the glow layer holds the blurred core x 4

// The half-size glow layer of a strip: its 12 half rows plus one above and one below (for the
// enlarging), and 8 half pixels of margin each side so sprites never need clipping sideways.
static const int GLOW_ROWS = STRIP_H / 2 + 2;     // GLOW_W and GLOW_MARGIN are in render.h

// The dots for a globe radius (map_view._luts and _station_light), rebuilt when the zoom moves on
// by a twentieth of the way.
static void setDotStyle(float R) {
  const bool wide = R > DETAIL_R;            // detailed: each dot shared over 3x3 pixels
  const float t = clampf(logf(R / WORLD_R) / logf(7), 0, 1);
  const int key = (int)lrintf(t * 20) * 2 + wide;
  if (key == spriteKey) return;
  spriteKey = key;
  wideDots = wide;
  const float tq = (key / 2) / 20.0f;
  const float coreGain = 1.5f + 12.5f * tq, glowGain = 7 + 4 * tq;
  const float scale = 255 / 8.0f;            // stations per pixel -> 0-255 (LIGHT_MAX = 8)
  for (int v = 0; v < 256; v++) {
    coreLut[v] = (uint8_t)lrintf(255 * (1 - expf(-coreGain * v * 8 / 255)));
    glowLut[v] = (uint8_t)lrintf(110 * (1 - expf(-(v / GLOW_SCALE) * glowGain / 110)));
  }
  coreSize = wide ? 4 : 1;
  glowSize = wide ? 7 : 5;
  const float sigma = 1.5f * K;              // GLOW_RADIUS x the layout scale, in (full) pixels
  for (int qy = 0; qy < 4; qy++)
    for (int qx = 0; qx < 4; qx++) {
      // the core: the station's center a quarter-pixel step into its pixel
      const float fx = qx * 0.25f + 0.125f, fy = qy * 0.25f + 0.125f;
      uint8_t *core = coreSprites + (qy * 4 + qx) * coreSize * coreSize;
      for (int i = 0; i < coreSize; i++)
        for (int j = 0; j < coreSize; j++) {
          float w = 1;
          if (wide) {                        // map_view._taps: (1 - f) / 2, 1/2, f / 2
            const float dx = j - 1 - fx, dy = i - 1 - fy;
            w = clampf(0.75f - fabsf(dx) * 0.5f, 0, 0.5f) * clampf(0.75f - fabsf(dy) * 0.5f, 0, 0.5f);
          }
          core[i * coreSize + j] = (uint8_t)(w * scale);   // the preview truncates its float sum
        }
      // the glow: the blurred core seen from half-size pixels, the station a quarter step (of a
      // half-size pixel) into its half-size pixel
      const float hx = qx * 0.25f + 0.125f, hy = qy * 0.25f + 0.125f;
      const float spread = wide ? 1.1f : 1.0f;   // the 3x3 core is a little wider than a point
      float sum = coreLut[(int)scale];            // the core's light after its curve, over its taps
      if (wide) {
        const float tapW[3] = {0.25f, 0.5f, 0.25f};
        sum = 0;
        for (int a = 0; a < 3; a++)
          for (int b2 = 0; b2 < 3; b2++) sum += coreLut[(int)(tapW[a] * tapW[b2] * scale)];
      }
      uint8_t *glow = glowSprites + (qy * 4 + qx) * glowSize * glowSize;
      for (int i = 0; i < glowSize; i++)
        for (int j = 0; j < glowSize; j++) {
          // distance in full pixels from this half-size pixel's center to the station
          const float dx = (j - glowSize / 2 - hx) * 2, dy = (i - glowSize / 2 - hy) * 2;
          const float s2 = sigma * sigma * spread;
          const float b = sum * expf(-(dx * dx + dy * dy) / (2 * s2)) / (2 * (float)M_PI * s2);
          glow[i * glowSize + j] = (uint8_t)lrintf(clampf(b * GLOW_SCALE, 0, 255));
        }
    }
}

// Visible dots, packed: the core sprite's left x in bits 0-9, top y in 10-19, quarter-pixel offsets
// in 20-23. Sorted by the strip their top row is in.
static uint32_t *dots;
static int dotCap, dotCount;
static uint16_t dotStart[STRIP_COUNT + 1];

static int IRAM_ATTR projectDots(const View &v, int from, int to, uint32_t *out) {
  const int32_t e0 = v.east[0], e1 = v.east[1], e2 = v.east[2];
  const int32_t s0 = v.south[0], s1 = v.south[1], s2 = v.south[2];
  const int16_t u0 = v.up[0], u1 = v.up[1], u2 = v.up[2];
  const int off = wideDots ? 1 : 0, sh = v.shift;
  const int center = (LCD_W / 2) << sh, half = sh ? 1 << (sh - 1) : 0;
  const int fracMask = (1 << sh) - 1;
  // Quarter-pixel offsets from the sub-pixel bits (fewer than 2 of them close up: shifted up).
  auto quarter = [sh](int f) { return sh >= 2 ? f >> (sh - 2) : f << (2 - sh); };
  int n = 0;
  for (int i = from; i < to; i++) {
    const int32_t a = stationXyz[i][0], b = stationXyz[i][1], c = stationXyz[i][2];
    if (a * u0 + b * u1 + c * u2 <= 0) continue;                    // far side of the globe
    const int xs = ((a * e0 + b * e1 + c * e2) >> 15) + center - half;   // pixel centers
    const int ys = ((a * s0 + b * s1 + c * s2) >> 15) + center - half;
    const int ax = xs >> sh, ay = ys >> sh;
    if ((unsigned)(ax - 4) > (unsigned)(LCD_W - 9) || (unsigned)(ay - 4) > (unsigned)(LCD_H - 9)) continue;
    out[n++] = (ax - off) | (ay - off) << 10 | quarter(xs & fracMask) << 20 | quarter(ys & fracMask) << 22;
  }
  return n;
}

// Sorts the dots by strip, in place (one pass of an in-place radix sort).
static void sortDots() {
  uint16_t count[STRIP_COUNT] = {0}, next[STRIP_COUNT];
  for (int i = 0; i < dotCount; i++) count[rowStrip[(dots[i] >> 10) & 0x3FF]]++;
  int total = 0;
  for (int s = 0; s < STRIP_COUNT; s++) {
    dotStart[s] = next[s] = total;
    total += count[s];
  }
  dotStart[STRIP_COUNT] = total;
  for (int s = 0; s < STRIP_COUNT; s++) {
    while (next[s] < dotStart[s + 1]) {
      uint32_t v = dots[next[s]];
      int t = rowStrip[(v >> 10) & 0x3FF];
      while (t != s) {               // swap it into its own strip's part until one belongs here
        uint32_t w = dots[next[t]];
        dots[next[t]++] = v;
        v = w;
        t = rowStrip[(v >> 10) & 0x3FF];
      }
      dots[next[s]++] = v;
    }
  }
}

// Strip s's dots into its core layer (full size) and its glow layer (half size).
// (S is a template argument so each size's loop is unrolled.)
template <int S>
static inline void addSprite8(uint8_t *o, const uint8_t *sp, int stride, int r0, int r1) {
  sp += r0 * S;
  for (int r = r0; r < r1; r++, sp += S, o += stride)
#pragma GCC unroll 8
    for (int c = 0; c < S; c++) {
      const uint32_t v = o[c] + sp[c];
      o[c] = v > 255 ? 255 : v;
    }
}

template <int C, int G>
static void stampDotsSized(uint8_t *core, uint8_t *glowHalf, int s);

static void IRAM_ATTR stampDots(uint8_t *core, uint8_t *glowHalf, int s) {
  if (wideDots) stampDotsSized<4, 7>(core, glowHalf, s);
  else stampDotsSized<1, 5>(core, glowHalf, s);
}

template <int C, int G>
static void IRAM_ATTR stampDotsSized(uint8_t *core, uint8_t *glowHalf, int s) {
  const int ys = s * STRIP_H, ye = ys + STRIP_H, off = C > 1 ? 1 : 0;
  const int hTop = ys / 2 - 1;                 // the glow layer's first half row
  const int from = dotStart[s > 0 ? s - 1 : 0], to = dotStart[s + 2 <= STRIP_COUNT ? s + 2 : STRIP_COUNT];
  for (int i = from; i < to; i++) {
    const uint32_t d = dots[i];
    const int x0 = d & 0x3FF, y0 = (d >> 10) & 0x3FF, qx = (d >> 20) & 3, qy = (d >> 22) & 3;
    // the core, if it reaches this strip
    int c0 = ys - y0, c1 = ye - y0;
    if (c0 < 0) c0 = 0;
    if (c1 > C) c1 = C;
    if (c0 < c1) addSprite8<C>(core + (y0 + c0 - ys) * LCD_W + x0, coreSprites + (qy * 4 + qx) * C * C, LCD_W, c0, c1);
    // the glow: the station's center in 1/32 half-size pixels (half-size pixel j's center is j)
    const int vx = ((x0 + off) * 4 + qx) * 4 + 2 - 8, vy = ((y0 + off) * 4 + qy) * 4 + 2 - 8;
    const int hx = vx >> 5, hy = vy >> 5, q = ((vy & 31) >> 3) * 4 + ((vx & 31) >> 3);
    const int gy0 = hy - G / 2 - hTop, gx0 = hx - G / 2 + GLOW_MARGIN;
    int r0 = -gy0, r1 = GLOW_ROWS - gy0;
    if (r0 < 0) r0 = 0;
    if (r1 > G) r1 = G;
    if (r0 < r1) addSprite8<G>(glowHalf + (gy0 + r0) * GLOW_W + gx0, glowSprites + q * G * G, GLOW_W, r0, r1);
  }
}
