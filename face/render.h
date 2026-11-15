// render.h: finishing one strip of the face in internal RAM.
//
// map_view.py builds a frame in layers: the globe's shading, the lines, the station light, the
// window's soft edge and the dark bands behind the text, then the crosshair, the text and the
// symbols. Here a strip (24 rows) is finished in a few passes, each pixel worked out in internal RAM
// and written to PSRAM once:
//   1. compositeStrip: globe shading + lines + station light + soft edge + text bands, one pass.
//      The shading and the edge depend only on the distance from the center, so they come from
//      lookup tables indexed by r^2; the bands depend only on the row; the station light is an
//      8-bit layer colored through a 256-entry palette. (face/bench's composite, now in any theme.)
//   2. drawSprites: 8-bit masks in one color each (text, symbols, the crosshair, city labels,
//      favorite stars, the station's ring), blended over it.
//   3. the volume ring, the card's dimming, the crossfade from the last frame, all only when shown.
#pragma once
#include <math.h>
#include <string.h>
#include "esp_attr.h"
#include "esp_cpu.h"
#include "data_themes.h"

#ifndef LCD_W
#define LCD_W 480
#define LCD_H 480
#endif

// The Arduino IDE builds sketches for size (-Os); the drawing code wants speed.
#pragma GCC optimize("O2")

static inline uint32_t cycles() { return esp_cpu_get_cycle_count(); }
static inline float clampf(float v, float lo, float hi) { return v < lo ? lo : v > hi ? hi : v; }

static const float K = LCD_W / 320.0f;       // map_view's layout scale: positions are designed at 320 px

// --- colors --------------------------------------------------------------------------------------
static uint16_t rgb565(float r, float g, float b) {
  int R = (int)lrintf(clampf(r, 0, 255) * 31 / 255), G = (int)lrintf(clampf(g, 0, 255) * 63 / 255);
  int B = (int)lrintf(clampf(b, 0, 255) * 31 / 255);
  return (uint16_t)(R << 11 | G << 5 | B);
}
static uint16_t rgb565(const FaceColor &c) { return rgb565(c.r, c.g, c.b); }
static uint16_t mix565(const FaceColor &a, const FaceColor &b, float t) {   // a + (b - a) t
  return rgb565(a.r + (b.r - a.r) * t, a.g + (b.g - a.g) * t, a.b + (b.b - a.b) * t);
}

// Aligned 32- and 16-bit access to pixel rows, allowed to alias the uint16_t/uint8_t buffers.
typedef uint32_t __attribute__((may_alias, aligned(4))) word_alias;
typedef uint16_t __attribute__((may_alias, aligned(2))) half_alias;

// RGB565 blending: spread a color out to 0x07E0F81F (green moved up 16 bits) so all three channels
// can be multiplied by a 0-32 weight at once.
static inline uint32_t spread565(uint32_t c) { return (c | (c << 16)) & 0x07E0F81Fu; }
static inline uint32_t blendSpread(uint32_t fg, uint32_t bg, uint32_t a) {   // fg * a/32 + bg * (1 - a/32), rounded
  return ((((fg - bg) * a + 0x02008010u) >> 5) + bg) & 0x07E0F81Fu;
}
static inline uint32_t pack565(uint32_t x) { return ((x >> 16) | x) & 0xFFFF; }
static inline uint16_t blend565(uint16_t fg, uint16_t bg, uint32_t a) {
  return pack565(blendSpread(spread565(fg), spread565(bg), a));
}

// The theme's colors, ready to use.
struct Colors {
  const FaceTheme *t;
  uint16_t space, globe, text, textDim, live, warn, cross, track, grid, border, coast, coastWorld;
};
static Colors col;

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
  float edgeKeepF[R4_ENTRIES];
  uint8_t rowKeep[LCD_H];          // ... and the bands behind the text, per row, 0-32
  int16_t spanL[LCD_H], spanR[LCD_H];   // pixels inside the round window on each row: [L, R), both even
  uint16_t palette[256];           // station light -> color
  uint32_t paletteS[256];          // ... spread for blending
  uint32_t shade;                  // what the edge and the bands fade towards (spread)
};
static Luts lut;

static inline float lutRadius(int i) { return sqrtf((i + 0.5f) * (1 << R4_SHIFT)) * 0.5f; }

// The window's soft edge and the round window's spans: the same for every theme and zoom
// (MapView._build_overlays).
static void buildEdgeLuts() {
  const float c = LCD_W / 2.0f;
  for (int i = 0; i < R4_ENTRIES; i++) {
    float r = lutRadius(i);
    float outside = clampf(r - (c - 0.5f), 0, 1);                   // black outside the window
    float edge = clampf((r - (c - 22 * K)) / (22 * K), 0, 1);
    float keep = (1 - outside) * (1 - edge * edge * 0.6f);
    lut.edgeKeepF[i] = keep;
    lut.edgeKeep[i] = (uint8_t)lrintf(keep * 32);
  }
  for (int y = 0; y < LCD_H; y++) {
    float yc = y + 0.5f, e = yc - c, w2 = (c + 0.5f) * (c + 0.5f) - e * e;
    if (w2 <= 0) {
      lut.spanL[y] = lut.spanR[y] = 0;
      continue;
    }
    float w = sqrtf(w2);
    int l = (int)floorf(c - w), r = (int)ceilf(c + w) + 1;
    lut.spanL[y] = (l < 0 ? 0 : l) & ~1;
    lut.spanR[y] = (r > LCD_W ? LCD_W : r) & ~1;
  }
}

// The bands behind the text at the top and bottom. The idle clock's reaches higher, behind the date
// and the big time.
static void buildRowKeep(bool idle) {
  const float start = idle ? 150 : 190, span = idle ? 110 : 100, power = idle ? 1.0f : 1.4f, most = idle ? 0.75f : 0.7f;
  for (int y = 0; y < LCD_H; y++) {
    float yc = y + 0.5f;
    float top = clampf((54 * K - yc) / (54 * K), 0, 1) * 0.45f;
    float bottom = powf(clampf((yc - start * K) / (span * K), 0, 1), power) * most;
    lut.rowKeep[y] = (uint8_t)lrintf((1 - top) * (1 - bottom) * 32);
  }
}

// Colors for a theme: the palette for the station light and the line colors (MapView.set_theme).
static void setColors(const FaceTheme &t) {
  col.t = &t;
  col.space = rgb565(t.space);
  col.globe = rgb565(t.globe);
  col.text = rgb565(t.text);
  col.textDim = rgb565(t.text_dim);
  col.live = rgb565(t.live);
  col.warn = rgb565(t.warn);
  col.cross = rgb565(t.crosshair);
  col.track = rgb565(t.track);
  col.grid = rgb565(t.grid);
  col.border = rgb565(t.border);
  col.coast = rgb565(t.coast);
  col.coastWorld = rgb565(t.coast_world);
  for (uint16_t *c : {&col.grid, &col.border, &col.coast, &col.coastWorld})
    if (!*c) *c = 0x0821;   // 0 means "no line" in the line layer
  for (int v = 0; v < 256; v++) {
    float ramp = clampf((v - 110) / 145.0f, 0, 1);       // GLOW_MAX = 110
    lut.palette[v] = mix565(t.dot_glow, t.dot_core, ramp);
    lut.paletteS[v] = spread565(lut.palette[v]);
  }
  lut.shade = spread565(rgb565(t.shade));
}

static int colorsTheme = -1;
static void useTheme(int theme) {
  if (theme == colorsTheme) return;
  setColors(FACE_THEMES[theme]);
  colorsTheme = theme;
}

// The globe, its limb shading and the atmosphere's glow for a globe radius in pixels
// (MapView._background), with the window's soft edge baked into withEdge.
static void buildGlobeLut(const FaceTheme &t, float R) {
  const FaceColor &G = t.globe, &A = t.atmosphere, &S = t.space, &D = t.shade;
  for (int i = 0; i < R4_ENTRIES; i++) {
    float r = lutRadius(i);
    float rr = fminf(r / R, 1.0f);
    float shade = 0.45f + 0.55f * sqrtf(1 - rr * rr);
    float rim = R - r < 40 * K ? expf(-fmaxf(R - r, 0) / (3 * K)) * 0.5f : 0;
    float halo = r - R < 80 * K ? expf(-fmaxf(r - R, 0) / (6 * K)) * 0.55f : 0;
    float inside = clampf(R - r + 0.5f, 0, 1);
    float br = (shade * G.r + rim * A.r) * inside + (S.r + halo * A.r) * (1 - inside);
    float bg = (shade * G.g + rim * A.g) * inside + (S.g + halo * A.g) * (1 - inside);
    float bb = (shade * G.b + rim * A.b) * inside + (S.b + halo * A.b) * (1 - inside);
    br = roundf(clampf(br, 0, 255));      // the preview's background is 8-bit
    bg = roundf(clampf(bg, 0, 255));
    bb = roundf(clampf(bb, 0, 255));
    float keep = lut.edgeKeepF[i];
    lut.globe[i] = rgb565(br, bg, bb);
    lut.withEdge[i] = rgb565(D.r + (br - D.r) * keep, D.g + (bg - D.g) * keep, D.b + (bb - D.b) * keep);
  }
}

// --- 1. the composite ----------------------------------------------------------------------------
// A pixel with a line or station light on it. The light is the brighter of the core and the glow,
// through their curves (world.h sets them for the zoom).
static uint8_t *lightCoreLut, *lightGlowLut;
static inline uint32_t litPixel(uint32_t line, uint32_t core, uint32_t glow, int i, uint32_t rowKeep) {
  uint32_t light = lightCoreLut[core], g = lightGlowLut[glow];
  if (g > light) light = g;
  uint32_t base = spread565(line ? line : lut.globe[i]);
  if (light) base = blendSpread(lut.paletteS[light], base, (light + 4) >> 3);
  const uint32_t keep = lut.edgeKeep[i] * rowKeep;
  if (keep == 32 * 32) return pack565(base);            // most of the window: no edge, no band
  return pack565(blendSpread(base, lut.shade, (keep + 16) >> 5));
}

// The one pass. px: the strip's lines (RGB565, 0 where there's no line); core: its station light's
// cores; glowHalf: the glow at half size (world.h), enlarged here on the way (bilinear, as PIL's
// resize: each pixel takes 3/4 of its own half-size pixel and 1/4 of the next one, across and
// down). Writes the finished rows y0..y0+h-1 back into px. Only pixels inside the round window.
static const int GLOW_W = LCD_W / 2 + 16, GLOW_MARGIN = 8;   // the half-size layer: 8 spare columns each side
//
// The buffers start on 4-byte boundaries, rows are 960 bytes and spans start on an even pixel, so
// every read and write is aligned and done directly (unaligned access was most of the composite's
// time on the bench's first run). noinline keeps it in IRAM: inlined into its caller it ran from flash.
static void IRAM_ATTR __attribute__((noinline))
compositeStrip(uint16_t *px, const uint8_t *core, const uint8_t *glowHalf, int y0, int h) {
  const uint16_t *withEdge = lut.withEdge;
  const uint32_t shade = lut.shade;
  for (int j = 0; j < h; j++) {
    const int y = y0 + j;
    const int xl = lut.spanL[y], xr = lut.spanR[y];
    uint16_t *row = px + j * LCD_W;
    const uint8_t *cores = core + j * LCD_W;
    const uint8_t *near = glowHalf + (j / 2 + 1) * GLOW_W + GLOW_MARGIN;       // its own half row
    const uint8_t *far = near + ((j & 1) ? GLOW_W : -GLOW_W);                   // the one below or above
    uint32_t prev = 3 * near[xl / 2 - 1] + far[xl / 2 - 1], cur = 3 * near[xl / 2] + far[xl / 2];
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
      const uint32_t lines = *(const word_alias *)(row + x);
      const uint32_t c2 = *(const half_alias *)(cores + x);
      const uint32_t next = 3 * near[x / 2 + 1] + far[x / 2 + 1];
      uint32_t o;
      if ((lines | c2 | prev | cur | next) == 0) {             // most pixels: plain globe (or space)
        if (rowKeep == 32) {
          o = withEdge[i0] | (uint32_t)withEdge[i1] << 16;
        } else {
          o = pack565(blendSpread(spread565(withEdge[i0]), shade, rowKeep)) |
              pack565(blendSpread(spread565(withEdge[i1]), shade, rowKeep)) << 16;
        }
      } else if ((lines | c2) == 0) {
        // Glow alone, which is a smooth blur: one color for the pair (half the work, and the most
        // common lit pixel where stations crowd).
        const uint32_t light = lightGlowLut[(prev + 6 * cur + next + 16) >> 5];
        uint32_t p;
        if (!light) {
          p = rowKeep == 32 ? withEdge[i0] : pack565(blendSpread(spread565(withEdge[i0]), shade, rowKeep));
        } else {
          const uint32_t base = blendSpread(lut.paletteS[light], spread565(lut.globe[i0]), (light + 4) >> 3);
          const uint32_t keep = lut.edgeKeep[i0] * rowKeep;
          p = keep == 32 * 32 ? pack565(base) : pack565(blendSpread(base, shade, (keep + 16) >> 5));
        }
        o = p | p << 16;
      } else {
        o = litPixel(lines & 0xFFFF, c2 & 0xFF, (prev + 3 * cur + 8) >> 4, i0, rowKeep) |
            litPixel(lines >> 16, c2 >> 8, (3 * cur + next + 8) >> 4, i1, rowKeep) << 16;
      }
      *(word_alias *)(row + x) = o;
      prev = cur;
      cur = next;
    }
  }
}

// --- 2. sprites ----------------------------------------------------------------------------------
// One mask in one color: a line of text, a symbol, the crosshair. `under` ones go below the window's
// soft edge and the text bands (cities, favorite stars, the station's ring), as in map_view, where
// they're drawn before the overlay: their color is faded towards the shade like the map under them.
struct Sprite {
  const uint8_t *mask;
  int16_t stride;          // bytes from one mask row to the next
  int16_t x, y, w, h;      // where the mask's top left lands, and its size
  int16_t clipL, clipR;    // drawn only where clipL <= x < clipR
  const uint8_t *ramp;     // a scrolling line's soft ends: a weight 0-255 per column from clipL, or NULL
  uint16_t color;
  uint16_t alpha;          // 0-256
  bool under;
};

static void IRAM_ATTR __attribute__((noinline))
drawSprites(uint16_t *px, int y0, int h, const Sprite *list, int n) {
  for (int s = 0; s < n; s++) {
    const Sprite &sp = list[s];
    int ra = sp.y > y0 ? sp.y : y0, rb = sp.y + sp.h < y0 + h ? sp.y + sp.h : y0 + h;
    if (ra >= rb || sp.alpha == 0) continue;
    const uint32_t fgPlain = spread565(sp.color);
    for (int y = ra; y < rb; y++) {
      int xa = sp.x, xb = sp.x + sp.w;
      if (xa < sp.clipL) xa = sp.clipL;
      if (xb > sp.clipR) xb = sp.clipR;
      if (xa < lut.spanL[y]) xa = lut.spanL[y];
      if (xb > lut.spanR[y]) xb = lut.spanR[y];
      if (xa >= xb) continue;
      const uint8_t *m = sp.mask + (y - sp.y) * sp.stride - sp.x;
      uint16_t *o = px + (y - y0) * LCD_W;
      const int e = 2 * y + 1 - LCD_H;
      for (int x = xa; x < xb; x++) {
        uint32_t a = m[x];
        if (!a) continue;
        a = a * sp.alpha >> 8;
        if (sp.ramp) a = a * sp.ramp[x - sp.clipL] >> 8;
        a = (a * 32 + 128) >> 8;
        if (!a) continue;
        uint32_t fg = fgPlain;
        if (sp.under) {
          const int d = 2 * x + 1 - LCD_W;
          const int i = (d * d + e * e) >> R4_SHIFT;
          fg = blendSpread(fgPlain, lut.shade, (lut.edgeKeep[i < R4_ENTRIES ? i : R4_ENTRIES - 1] * lut.rowKeep[y] + 16) >> 5);
        }
        o[x] = pack565(blendSpread(fg, spread565(o[x]), a));
      }
    }
  }
}

// --- 3. the volume ring, the card's dimming, the crossfade ----------------------------------------
// The ring (MapView._volume_ring): PIL's arc in a box 7k in from the edges, 4 px wide, from 135
// degrees (lower left) clockwise round the top to 405 (lower right). Its pixels are listed once,
// row by row, each with its angle.
struct RingPx {
  int16_t x;
  int16_t angle10;         // degrees x 10, 1350-4050 along the arc
};
static RingPx *ringPx;
static uint16_t ringRow[LCD_H + 1];   // where each row's pixels start in ringPx

static void buildRing() {
  const float c = LCD_W / 2.0f, outer = c - 7 * K, inner = outer - (int)fmaxf(2, roundf(3 * K));
  int n = 0;
  for (int pass = 0; pass < 2; pass++) {
    n = 0;
    for (int y = 0; y < LCD_H; y++) {
      ringRow[y] = n;
      for (int x = 0; x < LCD_W; x++) {
        float dx = x + 0.5f - c, dy = y + 0.5f - c, r = sqrtf(dx * dx + dy * dy);
        if (r <= inner || r > outer) continue;
        float a = atan2f(dy, dx) * 180 / (float)M_PI;     // clockwise from 3 o'clock, as PIL
        if (a < 135) a += 360;
        if (a > 405) continue;
        if (pass) ringPx[n] = {(int16_t)x, (int16_t)lrintf(a * 10)};
        n++;
      }
    }
    ringRow[LCD_H] = n;
    if (!pass) ringPx = (RingPx *)heap_caps_malloc(n * sizeof(RingPx), MALLOC_CAP_SPIRAM);
  }
}

static void drawRing(uint16_t *px, int y0, int h, int volume) {
  const int lit = 1350 + 2700 * volume / 100;
  for (int y = y0; y < y0 + h; y++) {
    uint16_t *o = px + (y - y0) * LCD_W;
    for (int i = ringRow[y]; i < ringRow[y + 1]; i++)
      o[ringPx[i].x] = volume > 0 && ringPx[i].angle10 <= lit ? col.live : col.track;
  }
}

// A card is open: the map fades nearly to the theme's space color (PANEL_DIM 0.84).
static void dimStrip(uint16_t *px, int y0, int h) {
  // Near black, RGB565 has few shades, and dimming turns a one-step difference in the map (where the
  // text bands begin) into a visible contour. Rounding alternately down and up in a checker pattern
  // (a 2x2 dither) hides it.
  const uint32_t to = spread565(col.space);
  static const uint32_t ROUND[2] = {8u | 8u << 11 | 8u << 21, 24u | 24u << 11 | 24u << 21};
  for (int j = 0; j < h; j++) {
    uint16_t *o = px + j * LCD_W;
    const int y = y0 + j;
    for (int x = lut.spanL[y]; x < lut.spanR[y]; x++) {
      const uint32_t fg = spread565(o[x]);
      o[x] = pack565(((((fg - to) * 5 + ROUND[(x + y) & 1]) >> 5) + to) & 0x07E0F81Fu);   // keeps 0.16
    }
  }
}

// The theme or the idle clock changed: fade from the last frame (in PSRAM), weight 0-32 for the new.
static void IRAM_ATTR __attribute__((noinline))
fadeStrip(uint16_t *px, int y0, int h, const uint16_t *from, uint32_t w) {
  for (int j = 0; j < h; j++) {
    uint16_t *o = px + j * LCD_W;
    const uint16_t *f = from + (y0 + j) * LCD_W;
    for (int x = lut.spanL[y0 + j]; x < lut.spanR[y0 + j]; x++)
      o[x] = pack565(blendSpread(spread565(o[x]), spread565(f[x]), w));
  }
}
