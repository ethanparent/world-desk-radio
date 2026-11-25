// world.h: what's on the globe for one view, cut into strips.
//
// Coastlines, borders and the latitude/longitude grid are unit vectors in int16 (x 32767), turned
// to the screen with one int16 3x3 multiply per point (no sin or cos per frame), in runs of up to
// 33 points with a bounding circle so runs off screen are skipped without touching their points.
// The station dots are the same, one point each. Then each line segment and each dot is filed under
// the strips it touches, so a strip can draw just its own.
//
// The real data comes from `python tools/face_data.py --res 110m,50m --out face/bench` (the same
// headers face.ino will use). Without those files the sketch makes up data of about the same size.
#pragma once
#include "render.h"

#ifndef FACE_COAST_RUN
#define FACE_COAST_RUN
// Same as in the generated headers.
typedef struct {
  int16_t cx, cy, cz;      // center of a circle around every point of the run (unit vector * 32767)
  int16_t cos_r, sin_r;    // the circle's angular radius r, as cos(r) * 32767 and sin(r) * 32767
  uint16_t count;          // points in the run
  uint32_t first;          // index of its first point
} CoastRun;
#endif

#if USE_REAL_DATA && __has_include("data_coast_110m.h") && __has_include("data_coast_50m.h") && \
    __has_include("data_border_110m.h") && __has_include("data_border_50m.h") && __has_include("data_stations.h")
#define REAL_DATA 1
#include "data_coast_110m.h"
#include "data_coast_50m.h"
#include "data_border_110m.h"
#include "data_border_50m.h"
#include "data_stations.h"
#else
#define REAL_DATA 0
#endif

static const float WORLD_R = (160 - 14) * LCD_W / 320.0f;          // radius_for_view(180): 219 px
static const float ZOOM_R = (LCD_W / 2) / sinf(2000 / 6371.0f / 2);  // radius_for_view for 2,000 km: 1535 px

// --- strips --------------------------------------------------------------------------------------
static const int MAX_STRIPS = LCD_H / 16;
static int stripH, stripCount;
static uint8_t rowStrip[LCD_H];

static void setStripHeight(int h) {
  stripH = h;
  stripCount = LCD_H / h;
  for (int y = 0; y < LCD_H; y++) rowStrip[y] = y / h;
}

// --- line data -----------------------------------------------------------------------------------
struct LineSet {
  const int16_t (*pts)[3];
  const CoastRun *runs;
  uint32_t runCount, pointCount;
};

// Lines made on the board (the grid, and made-up coastlines), packed the same way, in PSRAM.
struct LineBuf {
  int16_t (*pts)[3];
  CoastRun *runs;
  uint32_t pointCount, runCount, pointCap, runCap;
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

// Latitude/longitude lines every `spacing` degrees, a point every `sample` degrees
// (coastlines.graticule).
static void graticule(LineBuf &b, float spacing, float sample) {
  for (float lat = -90 + spacing; lat < 90 - 1e-3f; lat += spacing) {
    int n = 0;
    for (float lon = -180; lon <= 180 + 1e-3f; lon += sample) unitVector(lat, lon, polyTmp[n++]);
    addPolyline(b, polyTmp, n);
  }
  for (float lon = -180; lon < 180 - 1e-3f; lon += spacing) {
    int n = 0;
    for (float lat = -80; lat <= 80 + 1e-3f; lat += sample) unitVector(lat, lon, polyTmp[n++]);
    addPolyline(b, polyTmp, n);
  }
}

#if !REAL_DATA
// Made-up data, for when the real headers aren't there.
static uint32_t rngState = 12345;
static float frand() {
  rngState = rngState * 1664525u + 1013904223u;
  return (rngState >> 8) * (1.0f / 16777216.0f);
}

// Where made-up stations cluster: (lat, lon, spread in degrees, share).
static const float CLUSTERS[][4] = {
  {50, 10, 9, 0.42f}, {40, -80, 8, 0.2f}, {-15, -55, 12, 0.1f}, {30, 110, 14, 0.1f}, {0, 0, 80, 0.18f},
};

static void randomPlace(float &lat, float &lon) {
  float u = frand();
  int c = 0;
  while (c < 4 && u > CLUSTERS[c][3]) u -= CLUSTERS[c++][3];
  lat = clampf(CLUSTERS[c][0] + (frand() + frand() - 1) * CLUSTERS[c][2], -80, 80);
  lon = CLUSTERS[c][1] + (frand() + frand() - 1) * CLUSTERS[c][2] * 1.5f;
}

// Made-up coastlines: wandering lines, a tenth of them starting in Europe (whose coast is long and
// ragged), the rest anywhere.
static void randomLines(LineBuf &b, int lines, int points, float stepDeg, uint32_t seed) {
  rngState = seed;
  for (int l = 0; l < lines; l++) {
    float lat, lon, heading = frand() * 6.283f;
    if (frand() < 0.1f) {
      lat = 50 + (frand() + frand() - 1) * 9;
      lon = 10 + (frand() + frand() - 1) * 14;
    } else {
      lat = asinf(frand() * 2 - 1) * 180 / (float)M_PI;
      lon = frand() * 360 - 180;
    }
    for (int i = 0; i < points; i++) {
      unitVector(lat, lon, polyTmp[i]);
      heading += (frand() - 0.5f) * 0.8f;
      lat = clampf(lat + cosf(heading) * stepDeg, -85, 85);
      lon += sinf(heading) * stepDeg / cosf(lat * (float)M_PI / 180);
    }
    addPolyline(b, polyTmp, points);
  }
}
#endif

// Runs a builder twice: once to count, then to fill buffers of the right size.
template <typename F>
static LineSet buildLines(LineBuf &b, F build) {
  memset(&b, 0, sizeof b);
  build(b);
  b.pointCap = b.pointCount;
  b.runCap = b.runCount;
  b.pts = (int16_t(*)[3])heap_caps_malloc(b.pointCap * 6, MALLOC_CAP_SPIRAM);
  b.runs = (CoastRun *)heap_caps_malloc(b.runCap * sizeof(CoastRun), MALLOC_CAP_SPIRAM);
  b.pointCount = b.runCount = 0;
  build(b);
  return {(const int16_t(*)[3])b.pts, b.runs, b.runCount, b.pointCount};
}

// The three line layers of each view, drawn in this order: grid, borders, coastlines.
static LineSet worldLayers[3], zoomLayers[3];
static int16_t (*stationXyz)[3];   // in PSRAM
static int stationCount;

static void loadWorld() {
  static LineBuf grid30, grid10;
  polyTmp = (float(*)[3])heap_caps_malloc(400 * sizeof *polyTmp, MALLOC_CAP_SPIRAM);
  worldLayers[0] = buildLines(grid30, [](LineBuf &b) { graticule(b, 30, 2); });
  zoomLayers[0] = buildLines(grid10, [](LineBuf &b) { graticule(b, 10, 1); });
#if REAL_DATA
  worldLayers[1] = {BORDER_110M_POINTS, BORDER_110M_RUNS, BORDER_110M_RUN_COUNT, BORDER_110M_POINT_COUNT};
  worldLayers[2] = {COAST_110M_POINTS, COAST_110M_RUNS, COAST_110M_RUN_COUNT, COAST_110M_POINT_COUNT};
  zoomLayers[1] = {BORDER_50M_POINTS, BORDER_50M_RUNS, BORDER_50M_RUN_COUNT, BORDER_50M_POINT_COUNT};
  zoomLayers[2] = {COAST_50M_POINTS, COAST_50M_RUNS, COAST_50M_RUN_COUNT, COAST_50M_POINT_COUNT};
  stationCount = STATION_COUNT;
  stationXyz = (int16_t(*)[3])heap_caps_malloc(stationCount * 6, MALLOC_CAP_SPIRAM);
  for (int i = 0; i < stationCount; i++) {
    float v[3];
    unitVector(STATION_POS[i][0] / 180.0f, STATION_POS[i][1] / 180.0f, v);   // 1/180ths of a degree
    for (int q = 0; q < 3; q++) stationXyz[i][q] = (int16_t)lrintf(v[q] * 32767);
  }
#else
  // About the size of Natural Earth's 110m and 50m coastlines and borders, and today's station list.
  static LineBuf b110, c110, b50, c50;
  worldLayers[1] = buildLines(b110, [](LineBuf &b) { randomLines(b, 15, 200, 0.6f, 1); });
  worldLayers[2] = buildLines(c110, [](LineBuf &b) { randomLines(b, 26, 200, 0.6f, 2); });
  zoomLayers[1] = buildLines(b50, [](LineBuf &b) { randomLines(b, 100, 200, 0.12f, 3); });
  zoomLayers[2] = buildLines(c50, [](LineBuf &b) { randomLines(b, 308, 200, 0.12f, 4); });
  stationCount = 12765;
  stationXyz = (int16_t(*)[3])heap_caps_malloc(stationCount * 6, MALLOC_CAP_SPIRAM);
  rngState = 5;
  for (int i = 0; i < stationCount; i++) {
    float lat, lon, v[3];
    randomPlace(lat, lon);
    unitVector(lat, lon, v);
    for (int q = 0; q < 3; q++) stationXyz[i][q] = (int16_t)lrintf(v[q] * 32767);
  }
#endif
  heap_caps_free(polyTmp);
}

// --- one view ------------------------------------------------------------------------------------
struct View {
  int16_t east[3], south[3], up[3];   // the 3x3 matrix: screen x and y in 1/8 px, depth (x 16384)
  float upf[3];                       // the view center as a unit vector, for skipping runs
  float cosHalf, sinHalf;             // how far from the center a point can be and still be on screen
};

// The globe seen from far out in space above (lat, lon), radius R pixels (map_view._view_matrix).
static void makeView(View &v, float latDeg, float lonDeg, float R) {
  float p = latDeg * (float)M_PI / 180, l = lonDeg * (float)M_PI / 180;
  float sp = sinf(p), cp = cosf(p), sl = sinf(l), cl = cosf(l);
  float east[3] = {-sl, cl, 0}, north[3] = {-sp * cl, -sp * sl, cp}, up[3] = {cp * cl, cp * sl, sp};
  for (int i = 0; i < 3; i++) {
    v.east[i] = (int16_t)lrintf(east[i] * R * 8);
    v.south[i] = (int16_t)lrintf(-north[i] * R * 8);
    v.up[i] = (int16_t)lrintf(up[i] * 16384);
    v.upf[i] = up[i];
  }
  float half = asinf(fminf(1.0f, (LCD_W / 2 + 40) * 1.41421356f / R));
  v.cosHalf = cosf(half);
  v.sinHalf = sinf(half);
}

// Projected line points, packed: x + 64 in bits 0-9, y + 64 in bits 16-25, and two flags.
static const uint32_t PT_HIDDEN = 1 << 15;   // behind the globe or well off screen
static const uint32_t PT_START = 1 << 14;    // first point of a run: no segment joins it to the one before
static uint32_t *points;
static int pointCap, pointCount, pointsDropped;
static int layerEnd[3];                      // where each layer's points end
static uint16_t lineColors[3];

static void IRAM_ATTR projectLayer(const View &v, const LineSet &set) {
  const int16_t e0 = v.east[0], e1 = v.east[1], e2 = v.east[2];
  const int16_t s0 = v.south[0], s1 = v.south[1], s2 = v.south[2];
  const int16_t u0 = v.up[0], u1 = v.up[1], u2 = v.up[2];
  const float q = 1.0f / 32767;
  for (uint32_t r = 0; r < set.runCount; r++) {
    const CoastRun &run = set.runs[r];
    float toCenter = (run.cx * v.upf[0] + run.cy * v.upf[1] + run.cz * v.upf[2]) * q;
    if (toCenter < v.cosHalf * run.cos_r * q - v.sinHalf * run.sin_r * q) continue;   // off screen
    if (pointCount + run.count > pointCap) {
      pointsDropped += run.count;
      continue;
    }
    const int16_t (*p)[3] = set.pts + run.first;
    uint32_t *out = points + pointCount;
    for (int i = 0; i < run.count; i++) {
      const int32_t a = p[i][0], b = p[i][1], c = p[i][2];
      const int x = (((a * e0 + b * e1 + c * e2) >> 15) + LCD_W / 2 * 8 + 4) >> 3;
      const int y = (((a * s0 + b * s1 + c * s2) >> 15) + LCD_H / 2 * 8 + 4) >> 3;
      const int depth = a * u0 + b * u1 + c * u2;
      const bool seen = depth > 0 && (unsigned)(x + 39) < 559 && (unsigned)(y + 39) < 559;
      out[i] = ((uint32_t)(x + 64) & 0x3FF) | ((uint32_t)(y + 64) & 0x3FF) << 16 | (seen ? 0 : PT_HIDDEN);
    }
    out[0] |= PT_START;
    pointCount += run.count;
  }
}

static void projectLines(const View &v, const LineSet *layers) {
  pointCount = pointsDropped = 0;
  for (int i = 0; i < 3; i++) {
    projectLayer(v, layers[i]);
    layerEnd[i] = pointCount;
  }
}

// Segment k joins point k to point k+1. Each is filed under every strip it touches.
static uint16_t *segments;
static int segmentCap, segmentsDropped;
static uint16_t segStart[MAX_STRIPS + 1];

static inline bool segmentSpan(int k, int &s0, int &s1) {
  uint32_t a = points[k], b = points[k + 1];
  if ((a | b) & PT_HIDDEN || b & PT_START) return false;
  int ya = (int)(a >> 16) - 64, yb = (int)(b >> 16) - 64;
  int lo = ya < yb ? ya : yb, hi = ya < yb ? yb : ya;
  if (hi < 0 || lo >= LCD_H) return false;
  s0 = rowStrip[lo < 0 ? 0 : lo];
  s1 = rowStrip[hi >= LCD_H ? LCD_H - 1 : hi];
  return true;
}

static void binSegments() {
  uint16_t count[MAX_STRIPS] = {0};
  int s0, s1;
  for (int k = 0; k + 1 < pointCount; k++)
    if (segmentSpan(k, s0, s1))
      for (int s = s0; s <= s1; s++) count[s]++;
  uint16_t next[MAX_STRIPS];
  int total = 0;
  for (int s = 0; s < stripCount; s++) {
    segStart[s] = next[s] = total < segmentCap ? total : segmentCap;
    total += count[s];
  }
  segStart[stripCount] = total < segmentCap ? total : segmentCap;
  segmentsDropped = total > segmentCap ? total - segmentCap : 0;
  for (int k = 0; k + 1 < pointCount; k++)
    if (segmentSpan(k, s0, s1))
      for (int s = s0; s <= s1; s++)
        if (next[s] < segStart[s + 1]) segments[next[s]++] = k;
}

// A 1-px line from (xa, ya) to (xb, yb), only the rows ys..ys+h-1 of it, into a strip of RGB565.
static inline int drawSegment(uint16_t *px, int ys, int h, int xa, int ya, int xb, int yb, uint16_t color) {
  const int dx = xb - xa, dy = yb - ya;
  const int adx = dx < 0 ? -dx : dx, ady = dy < 0 ? -dy : dy;
  int n = 0;
  if (adx >= ady) {
    if (adx == 0) {
      if ((unsigned)(ya - ys) < (unsigned)h && (unsigned)xa < LCD_W) {
        px[(ya - ys) * LCD_W + xa] = color;
        return 1;
      }
      return 0;
    }
    const int sx = dx > 0 ? 1 : -1;
    const int32_t step = dy * 65536 / adx;
    int32_t yf = (ya - ys) * 65536 + 32768;
    for (int i = 0, x = xa; i <= adx; i++, x += sx, yf += step) {
      const int y = yf >> 16;
      if ((unsigned)y < (unsigned)h && (unsigned)x < LCD_W) {
        px[y * LCD_W + x] = color;
        n++;
      }
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
      if ((unsigned)x < LCD_W) {
        px[y * LCD_W + x] = color;
        n++;
      }
    }
  }
  return n;
}

// Strip s's lines into its RGB565 buffer. Returns the pixels drawn.
static int IRAM_ATTR drawLines(uint16_t *px, int s) {
  const int ys = s * stripH;
  int n = 0;
  for (int i = segStart[s]; i < segStart[s + 1]; i++) {
    const int k = segments[i];
    const uint32_t a = points[k], b = points[k + 1];
    const uint16_t color = k < layerEnd[0] ? lineColors[0] : k < layerEnd[1] ? lineColors[1] : lineColors[2];
    n += drawSegment(px, ys, stripH, (int)(a & 0x3FF) - 64, (int)(a >> 16) - 64, (int)(b & 0x3FF) - 64,
                     (int)(b >> 16) - 64, color);
  }
  return n;
}

// --- station dots --------------------------------------------------------------------------------
// Each dot is a small pre-drawn sprite: a bright core and a soft glow, stamped into the strip's 8-bit
// light layer, keeping the brighter value where dots overlap (like map_view's ImageChops.lighter,
// so crowded areas glow orange with pale specks instead of turning into pale blobs). 4x4 versions
// at quarter-pixel offsets keep dots gliding smoothly as the globe turns. Whole world: 5x5 around
// a one-pixel core. 2,000 km: 9x9 around a bright three-pixel core. (map_view._station_light, with
// the blur baked in.) Sprites must stay at most 16 pixels tall, so a dot spans two strips at most.
static uint8_t spritesWorld[16 * 5 * 5], spritesZoom[16 * 9 * 9];
static const uint8_t *sprites;
static int spriteSize;

static void buildSprites(uint8_t *out, int S, bool wide, float coreGain, float glowPeak, float sigma) {
  for (int py = 0; py < 4; py++)
    for (int px = 0; px < 4; px++) {
      float fx = S / 2 + px * 0.25f + 0.125f, fy = S / 2 + py * 0.25f + 0.125f;
      uint8_t *sp = out + (py * 4 + px) * S * S;
      for (int i = 0; i < S; i++)
        for (int j = 0; j < S; j++) {
          float dx = j - fx, dy = i - fy;
          // share the dot between its nearest pixels (two of them, or three when wide)
          float wx = wide ? clampf(0.75f - fabsf(dx) * 0.5f, 0, 0.5f) : clampf(1 - fabsf(dx), 0, 1);
          float wy = wide ? clampf(0.75f - fabsf(dy) * 0.5f, 0, 0.5f) : clampf(1 - fabsf(dy), 0, 1);
          float core = 255 * (1 - expf(-coreGain * wx * wy));
          float glow = glowPeak * expf(-(dx * dx + dy * dy) / (2 * sigma * sigma));
          sp[i * S + j] = (uint8_t)lrintf(clampf(fmaxf(core, glow), 0, 255));
        }
    }
}

// Visible dots, packed: sprite's left x in bits 0-9, top y in 10-19, quarter-pixel offsets in 20-23.
// Sorted by the strip their top row is in; a dot can spill into the next strip.
static uint32_t *dots;
static int dotCount;
static uint16_t dotStart[MAX_STRIPS + 1];

static int IRAM_ATTR projectDots(const View &v, int from, int to, uint32_t *out) {
  const int16_t e0 = v.east[0], e1 = v.east[1], e2 = v.east[2];
  const int16_t s0 = v.south[0], s1 = v.south[1], s2 = v.south[2];
  const int16_t u0 = v.up[0], u1 = v.up[1], u2 = v.up[2];
  const int S = spriteSize, limit = LCD_W - S;
  int n = 0;
  for (int i = from; i < to; i++) {
    const int32_t a = stationXyz[i][0], b = stationXyz[i][1], c = stationXyz[i][2];
    if (a * u0 + b * u1 + c * u2 <= 0) continue;                   // far side of the globe
    const int x8 = ((a * e0 + b * e1 + c * e2) >> 15) + LCD_W / 2 * 8 - 4;   // pixel centers, 1/8 px
    const int y8 = ((a * s0 + b * s1 + c * s2) >> 15) + LCD_H / 2 * 8 - 4;
    const int x0 = (x8 >> 3) - S / 2, y0 = (y8 >> 3) - S / 2;
    if ((unsigned)x0 > (unsigned)limit || (unsigned)y0 > (unsigned)limit) continue;
    out[n++] = x0 | y0 << 10 | ((x8 & 7) >> 1) << 20 | ((y8 & 7) >> 1) << 22;
  }
  return n;
}

// Sorts the dots by strip, in place (one pass of an in-place radix sort).
static void sortDots() {
  uint16_t count[MAX_STRIPS] = {0}, next[MAX_STRIPS];
  for (int i = 0; i < dotCount; i++) count[rowStrip[(dots[i] >> 10) & 0x3FF]]++;
  int total = 0;
  for (int s = 0; s < stripCount; s++) {
    dotStart[s] = next[s] = total;
    total += count[s];
  }
  dotStart[stripCount] = total;
  for (int s = 0; s < stripCount; s++) {
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

// Strip s's dots into its light layer. Returns the dots stamped.
static int IRAM_ATTR stampDots(uint8_t *light, int s) {
  const int ys = s * stripH, ye = ys + stripH, S = spriteSize;
  const int from = dotStart[s > 0 ? s - 1 : 0], to = dotStart[s + 1];
  int n = 0;
  for (int i = from; i < to; i++) {
    const uint32_t d = dots[i];
    const int x0 = d & 0x3FF, y0 = (d >> 10) & 0x3FF;
    int r0 = ys - y0, r1 = ye - y0;
    if (r0 < 0) r0 = 0;
    if (r1 > S) r1 = S;
    if (r0 >= r1) continue;            // a dot from the strip above that doesn't reach this one
    const uint8_t *sp = sprites + (((d >> 22) & 3) * 4 + ((d >> 20) & 3)) * S * S + r0 * S;
    uint8_t *o = light + (y0 + r0 - ys) * LCD_W + x0;
    for (int r = r0; r < r1; r++, sp += S, o += LCD_W)
      for (int c = 0; c < S; c++)
        if (sp[c] > o[c]) o[c] = sp[c];
    n++;
  }
  return n;
}

// Sprites and line colors for a view.
static void setViewStyle(bool zoomed) {
  sprites = zoomed ? spritesZoom : spritesWorld;
  spriteSize = zoomed ? 9 : 5;
  lineColors[0] = rgb565(NIGHT_GRID);
  lineColors[1] = rgb565(NIGHT_BORDER);
  lineColors[2] = rgb565(zoomed ? NIGHT_COAST : NIGHT_COAST_WORLD);
  for (int i = 0; i < 3; i++)
    if (!lineColors[i]) lineColors[i] = 0x0821;   // 0 means "no line"
}

static void buildAllSprites() {
  buildSprites(spritesWorld, 5, false, 1.5f, 55, 1.2f);
  buildSprites(spritesZoom, 9, true, 14, 105, 2.0f);
}
