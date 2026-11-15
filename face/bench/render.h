// render.h: finishing one strip of the face in internal RAM, in one pass per pixel.
//
// map_view.py builds the frame in layers: the globe's shading, the coastlines, the station light
// on top, then the window's soft edge and the dark bands behind the text. Here all of that happens
// in a single pass over each strip: the globe's shading and the window's edge depend only on a
// pixel's distance from the center, so they come from lookup tables indexed by r^2; the text bands
// depend only on the row; and the station light is an 8-bit layer colored through a 256-entry
// palette. Each pixel is worked out once and written to PSRAM once.
#pragma once
#include <math.h>
#include <string.h>
#include "esp_attr.h"
#include "esp_cpu.h"

#ifndef LCD_W
#define LCD_W 480
#define LCD_H 480
#endif

// The Arduino IDE builds sketches for size (-Os). Drawing code wants speed, and face.ino will too.
#pragma GCC optimize("O2")

static inline uint32_t cycles() { return esp_cpu_get_cycle_count(); }

// --- colors: the night theme from terradio/themes.py --------------------------------------------
struct Rgbf {
  float r, g, b;
};
static const Rgbf NIGHT_SPACE = {2, 3, 7}, NIGHT_GLOBE = {10, 19, 36}, NIGHT_ATMOSPHERE = {34, 78, 140};
static const Rgbf NIGHT_COAST = {54, 92, 112}, NIGHT_COAST_WORLD = {44, 76, 96}, NIGHT_GRID = {17, 29, 46};
static const Rgbf NIGHT_BORDER = {40, 69, 90}, NIGHT_DOT_CORE = {255, 232, 190}, NIGHT_DOT_GLOW = {255, 136, 36};
static const Rgbf NIGHT_TEXT = {238, 241, 245}, NIGHT_SHADE = {0, 0, 0};

static inline float clampf(float v, float lo, float hi) { return v < lo ? lo : v > hi ? hi : v; }

static uint16_t rgb565(float r, float g, float b) {
  int R = (int)lrintf(clampf(r, 0, 255) * 31 / 255), G = (int)lrintf(clampf(g, 0, 255) * 63 / 255);
  int B = (int)lrintf(clampf(b, 0, 255) * 31 / 255);
  return (uint16_t)(R << 11 | G << 5 | B);
}
static uint16_t rgb565(const Rgbf &c) { return rgb565(c.r, c.g, c.b); }

// Aligned 32- and 16-bit access to pixel rows, allowed to alias the uint16_t/uint8_t buffers.
typedef uint32_t __attribute__((may_alias, aligned(4))) word_alias;
typedef uint16_t __attribute__((may_alias, aligned(2))) half_alias;

// RGB565 blending: spread a color out to 0x07E0F81F (green moved up 16 bits) so all three channels
// can be multiplied by a 0-32 weight at once.
static inline uint32_t spread565(uint32_t c) { return (c | (c << 16)) & 0x07E0F81Fu; }
static inline uint32_t blendSpread(uint32_t fg, uint32_t bg, uint32_t a) {   // fg * a/32 + bg * (1 - a/32)
  return ((((fg - bg) * a) >> 5) + bg) & 0x07E0F81Fu;
}
static inline uint32_t pack565(uint32_t x) { return ((x >> 16) | x) & 0xFFFF; }

// --- lookup tables -------------------------------------------------------------------------------
// A pixel's distance from the center as r4 = (2x+1-480)^2 + (2y+1-480)^2, which is 4 r^2 and counts
// up by whole numbers along a row. Tables are indexed by r4 >> R4_SHIFT: steps of 1/15 px at the
// window's edge, coarser near the middle where nothing changes quickly.
static const int R4_SHIFT = 7;
static const int R4_ENTRIES = 1856;          // reaches r = 243.7, past the window's edge

struct Luts {
  uint16_t withEdge[R4_ENTRIES];   // the globe's shading with the window's soft edge baked in
  uint16_t globe[R4_ENTRIES];      // the globe's shading alone, under lines and station light
  uint8_t edgeKeep[R4_ENTRIES];    // how much the window's edge keeps of a pixel, 0-32
  uint8_t rowKeep[LCD_H];          // ... and the bands behind the text, per row, 0-32
  int16_t spanL[LCD_H], spanR[LCD_H];   // pixels inside the round window on each row: [L, R), both even
  uint16_t palette[256];           // station light -> color
  uint32_t shade;                  // what the edge and the bands fade towards (spread)
};
static Luts lut;

// Rebuilds the tables for a globe radius in pixels (219 for the whole world, 1535 for 2,000 km).
// face.ino needs this only when the zoom changes. Same sums as MapView._background and
// _build_overlays (the map, not the idle clock) at 480 px.
static void buildLuts(float R) {
  const float k = LCD_W / 320.0f;
  const float c = LCD_W / 2.0f;
  const Rgbf &G = NIGHT_GLOBE, &A = NIGHT_ATMOSPHERE, &S = NIGHT_SPACE, &D = NIGHT_SHADE;
  for (int i = 0; i < R4_ENTRIES; i++) {
    float r = sqrtf((i + 0.5f) * (1 << R4_SHIFT)) * 0.5f;
    float rr = fminf(r / R, 1.0f);
    float shade = 0.45f + 0.55f * sqrtf(1 - rr * rr);              // darker towards the globe's edge
    float rim = expf(-fmaxf(R - r, 0) / (3 * k)) * 0.5f;            // the atmosphere's thin glow
    float halo = expf(-fmaxf(r - R, 0) / (6 * k)) * 0.55f;
    float inside = clampf(R - r + 0.5f, 0, 1);                       // smooth limb
    float br = (shade * G.r + rim * A.r) * inside + (S.r + halo * A.r) * (1 - inside);
    float bg = (shade * G.g + rim * A.g) * inside + (S.g + halo * A.g) * (1 - inside);
    float bb = (shade * G.b + rim * A.b) * inside + (S.b + halo * A.b) * (1 - inside);
    float outside = clampf(r - (c - 0.5f), 0, 1);                   // black outside the window
    float edge = clampf((r - (c - 22 * k)) / (22 * k), 0, 1);
    float keep = (1 - outside) * (1 - edge * edge * 0.6f);          // the window's soft edge
    lut.globe[i] = rgb565(br, bg, bb);
    lut.withEdge[i] = rgb565(D.r + (br - D.r) * keep, D.g + (bg - D.g) * keep, D.b + (bb - D.b) * keep);
    lut.edgeKeep[i] = (uint8_t)lrintf(keep * 32);
  }
  for (int y = 0; y < LCD_H; y++) {
    float yc = y + 0.5f;
    float top = clampf((54 * k - yc) / (54 * k), 0, 1) * 0.45f;
    float bottom = powf(clampf((yc - 190 * k) / (100 * k), 0, 1), 1.4f) * 0.7f;
    lut.rowKeep[y] = (uint8_t)lrintf((1 - top) * (1 - bottom) * 32);
    float e = yc - c, w2 = (c + 0.5f) * (c + 0.5f) - e * e;
    if (w2 <= 0) {
      lut.spanL[y] = lut.spanR[y] = 0;
      continue;
    }
    float w = sqrtf(w2);
    int l = (int)floorf(c - w), r = (int)ceilf(c + w) + 1;
    lut.spanL[y] = (l < 0 ? 0 : l) & ~1;
    lut.spanR[y] = (r > LCD_W ? LCD_W : r) & ~1;
  }
  // Station light: the glow color at low levels, turning to the dot-core color at the cores
  // (MapView.set_theme).
  for (int v = 0; v < 256; v++) {
    float ramp = clampf((v - 110) / 145.0f, 0, 1);
    const Rgbf &g = NIGHT_DOT_GLOW, &d = NIGHT_DOT_CORE;
    lut.palette[v] = rgb565(g.r + (d.r - g.r) * ramp, g.g + (d.g - g.g) * ramp, g.b + (d.b - g.b) * ramp);
  }
  lut.shade = spread565(rgb565(D));
}

// A pixel with a line or station light on it.
static inline uint32_t litPixel(uint32_t line, uint32_t light, int i, uint32_t rowKeep) {
  uint32_t base = spread565(line ? line : lut.globe[i]);
  if (light) base = blendSpread(spread565(lut.palette[light]), base, (light + 4) >> 3);
  return pack565(blendSpread(base, lut.shade, (lut.edgeKeep[i] * rowKeep + 16) >> 5));
}

// The one pass. px: the strip's lines (RGB565, 0 where there's no line); light: its station light
// (0-255). Writes rows y0..y0+h-1 to dst, rows dstStride pixels apart: either px itself (to be
// copied to PSRAM afterwards) or the frame buffer in PSRAM directly. Only pixels inside the round
// window are written.
//
// The buffers start on 4-byte boundaries, rows are 960 bytes and spans start on an even pixel, so
// every read and write below is aligned and done directly. (They used to be memcpy calls: the
// Xtensa core has no unaligned loads and the Arduino build doesn't expand memcpy itself, so each
// one was a call into ROM, three per pixel pair, most of the composite's time: 90 cycles a pixel
// on the first board run.) noinline keeps it in IRAM: inlined into its caller it ran from flash.
static void IRAM_ATTR __attribute__((noinline))
compositeStrip(const uint16_t *px, const uint8_t *light, int y0, int h, uint16_t *dst, int dstStride) {
  const uint16_t *withEdge = lut.withEdge;
  const uint32_t shade = lut.shade;
  for (int j = 0; j < h; j++) {
    const int y = y0 + j;
    const int xl = lut.spanL[y], xr = lut.spanR[y];
    const uint16_t *src = px + j * LCD_W;
    const uint8_t *lit = light + j * LCD_W;
    uint16_t *out = dst + j * dstStride;
    const int e = 2 * y + 1 - LCD_H;
    int d = 2 * xl + 1 - LCD_W;
    int r4 = d * d + e * e;
    const uint32_t rowKeep = lut.rowKeep[y];
    for (int x = xl; x < xr; x += 2) {        // two pixels at a time
      const int i0 = r4 >> R4_SHIFT;
      r4 += 4 * d + 4;
      d += 2;
      const int i1 = r4 >> R4_SHIFT;
      r4 += 4 * d + 4;
      d += 2;
      const uint32_t lines = *(const word_alias *)(src + x);
      const uint32_t lights = *(const half_alias *)(lit + x);
      uint32_t o;
      if ((lines | lights) == 0) {             // most pixels: plain globe (or space)
        if (rowKeep == 32) {
          o = withEdge[i0] | (uint32_t)withEdge[i1] << 16;
        } else {
          o = pack565(blendSpread(spread565(withEdge[i0]), shade, rowKeep)) |
              pack565(blendSpread(spread565(withEdge[i1]), shade, rowKeep)) << 16;
        }
      } else {
        o = litPixel(lines & 0xFFFF, lights & 0xFF, i0, rowKeep) |
            litPixel(lines >> 16, lights >> 8, i1, rowKeep) << 16;
      }
      *(word_alias *)(out + x) = o;
    }
  }
}

// --- raw fill (tests 1-3) ------------------------------------------------------------------------
// Bands of color drifting down the screen: the cheapest possible "drawing", so the copy to PSRAM is
// all that's measured. A torn frame shows as a step in the bands.
static const uint16_t BAND_COLORS[8] = {0x1084, 0x2126, 0x1A6A, 0x3186, 0x2A08, 0x18E8, 0x3166, 0x2148};

static void IRAM_ATTR fillStrip(uint16_t *px, int y0, int h, uint32_t frame) {
  for (int j = 0; j < h; j++) {
    uint32_t c = BAND_COLORS[((y0 + j + frame * 3) >> 5) & 7];
    c |= c << 16;
    uint32_t *p = (uint32_t *)(px + j * LCD_W);
    for (int i = 0; i < LCD_W / 2; i++) p[i] = c;
  }
}

// --- the drift ring and the on-screen text -------------------------------------------------------
// A thin ring just inside the window's edge. If the picture ever shifts sideways (the RGB interface
// losing its place), the ring is no longer centered: one side gets cut off by the bezel.
static const float RING_R = 236.0f;
static int16_t ringL[LCD_H], ringR[LCD_H];   // per row, the ring's pixels on the left half: [L, R)

static void buildRing() {
  for (int y = 0; y < LCD_H; y++) {
    float e = y + 0.5f - LCD_H / 2;
    ringL[y] = ringR[y] = 0;
    for (int x = 0; x < LCD_W / 2; x++) {
      float dx = x + 0.5f - LCD_W / 2, r = sqrtf(dx * dx + e * e);
      if (r >= RING_R - 0.5f && r < RING_R + 0.5f) {
        if (ringL[y] == ringR[y]) ringL[y] = x;
        ringR[y] = x + 1;
      }
    }
  }
}

// 3x5 pixel font for ' ' to 'Z'; each glyph is five rows of three bits, top row first.
#define GLYPH(a, b, c, d, e) (uint16_t)((a) << 12 | (b) << 9 | (c) << 6 | (d) << 3 | (e))
static const uint16_t FONT[] = {
  0,                       // space
  GLYPH(2, 2, 2, 0, 2),    // !
  0, 0, 0,                 // " # $
  GLYPH(5, 1, 2, 4, 5),    // %
  0, 0,                    // & '
  GLYPH(2, 4, 4, 4, 2),    // (
  GLYPH(2, 1, 1, 1, 2),    // )
  0,                       // *
  GLYPH(0, 2, 7, 2, 0),    // +
  GLYPH(0, 0, 0, 2, 4),    // ,
  GLYPH(0, 0, 7, 0, 0),    // -
  GLYPH(0, 0, 0, 0, 2),    // .
  GLYPH(1, 1, 2, 4, 4),    // /
  GLYPH(7, 5, 5, 5, 7),    // 0
  GLYPH(2, 6, 2, 2, 7),    // 1
  GLYPH(7, 1, 7, 4, 7),    // 2
  GLYPH(7, 1, 7, 1, 7),    // 3
  GLYPH(5, 5, 7, 1, 1),    // 4
  GLYPH(7, 4, 7, 1, 7),    // 5
  GLYPH(7, 4, 7, 5, 7),    // 6
  GLYPH(7, 1, 1, 2, 2),    // 7
  GLYPH(7, 5, 7, 5, 7),    // 8
  GLYPH(7, 5, 7, 1, 7),    // 9
  GLYPH(0, 2, 0, 2, 0),    // :
  0, 0,                    // ; <
  GLYPH(0, 7, 0, 7, 0),    // =
  0,                       // >
  GLYPH(7, 1, 2, 0, 2),    // ?
  0,                       // @
  GLYPH(2, 5, 7, 5, 5),    // A
  GLYPH(6, 5, 6, 5, 6),    // B
  GLYPH(3, 4, 4, 4, 3),    // C
  GLYPH(6, 5, 5, 5, 6),    // D
  GLYPH(7, 4, 6, 4, 7),    // E
  GLYPH(7, 4, 6, 4, 4),    // F
  GLYPH(3, 4, 5, 5, 3),    // G
  GLYPH(5, 5, 7, 5, 5),    // H
  GLYPH(7, 2, 2, 2, 7),    // I
  GLYPH(1, 1, 1, 5, 2),    // J
  GLYPH(5, 5, 6, 5, 5),    // K
  GLYPH(4, 4, 4, 4, 7),    // L
  GLYPH(5, 7, 7, 5, 5),    // M
  GLYPH(6, 5, 5, 5, 5),    // N
  GLYPH(2, 5, 5, 5, 2),    // O
  GLYPH(6, 5, 6, 4, 4),    // P
  GLYPH(2, 5, 5, 6, 3),    // Q
  GLYPH(6, 5, 6, 5, 5),    // R
  GLYPH(3, 4, 2, 1, 6),    // S
  GLYPH(7, 2, 2, 2, 2),    // T
  GLYPH(5, 5, 5, 5, 7),    // U
  GLYPH(5, 5, 5, 5, 2),    // V
  GLYPH(5, 5, 7, 7, 5),    // W
  GLYPH(5, 5, 2, 5, 5),    // X
  GLYPH(5, 5, 2, 2, 2),    // Y
  GLYPH(7, 1, 2, 4, 7),    // Z
};
static_assert(sizeof FONT / sizeof FONT[0] == 'Z' - ' ' + 1, "one glyph per character from space to Z");

// Lines of text on the screen: the fps, the test, the settings.
struct HudLine {
  char text[40];
  int top, scale;
};
static HudLine hud[3] = {{"", 166, 7}, {"", 214, 3}, {"", 236, 3}};
static const int HUD_TOP = 166, HUD_BOTTOM = 251;
static uint16_t textColor;

static void drawText(uint16_t *rows, int y0, int h, int stride, const HudLine &line) {
  const int bottom = line.top + 5 * line.scale;
  if (line.top >= y0 + h || bottom <= y0) return;
  int len = strlen(line.text);
  while (len > 0 && (len * 4 - 1) * line.scale > LCD_W - 80) len--;   // keep it inside the circle
  if (!len) return;
  const int left = LCD_W / 2 - (len * 4 - 1) * line.scale / 2;
  for (int gy = 0; gy < 5; gy++) {
    int ra = line.top + gy * line.scale, rb = ra + line.scale;
    if (ra < y0) ra = y0;
    if (rb > y0 + h) rb = y0 + h;
    if (ra >= rb) continue;
    for (int i = 0; i < len; i++) {
      char ch = line.text[i];
      if (ch >= 'a' && ch <= 'z') ch -= 32;
      uint16_t glyph = (ch >= ' ' && ch <= 'Z') ? FONT[ch - ' '] : 0;
      int bits = (glyph >> (12 - 3 * gy)) & 7;
      for (int gx = 0; gx < 3; gx++) {
        if (!(bits & (4 >> gx))) continue;
        int xa = left + (i * 4 + gx) * line.scale;
        for (int y = ra; y < rb; y++) {
          uint16_t *o = rows + (y - y0) * stride + xa;
          for (int q = 0; q < line.scale; q++) o[q] = textColor;
        }
      }
    }
  }
}

// The ring and the text, over rows y0..y0+h-1 of a strip (or of the frame buffer).
static void drawHud(uint16_t *rows, int y0, int h, int stride) {
  for (int j = 0; j < h; j++) {
    int y = y0 + j;
    uint16_t *o = rows + j * stride;
    for (int x = ringL[y]; x < ringR[y]; x++) o[x] = o[LCD_W - 1 - x] = textColor;
  }
  if (y0 < HUD_BOTTOM && y0 + h > HUD_TOP)
    for (int i = 0; i < 3; i++) drawText(rows, y0, h, stride, hud[i]);
}
