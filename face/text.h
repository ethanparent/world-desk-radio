// text.h: lines of text as 8-bit masks, in the preview's fonts (data_fonts.h, made by
// tools/face_data.py from map_view.FONT_FILES).
//
// A line is laid out glyph by glyph from the font's advances, cut short with "…" if it's too wide
// for its place in the circle (map_view._text_patch), and drawn into a mask in PSRAM. Korean,
// Japanese and Chinese characters come from the CJK font of the same size (the preview switches
// the whole line to it; the face only those characters). Symbols and emoji are left out, as
// map_view._printable does. Masks are cached, so a line that hasn't changed costs nothing.
#pragma once
#include "render.h"
#include "data_fonts.h"

enum FontId { F_R15, F_R16, F_R18, F_R20, F_R21, F_B20, F_B26, F_B27, F_B51, F_COUNT };
struct FontPair {
  const FaceFont *main, *cjk;
};
static const FontPair FONTS[F_COUNT] = {
  {&FONT_R15, NULL}, {&FONT_R16, NULL}, {&FONT_R18, &FONT_R18_CJK}, {&FONT_R20, &FONT_R20_CJK},
  {&FONT_R21, NULL}, {&FONT_B20, NULL}, {&FONT_B26, NULL}, {&FONT_B27, &FONT_B27_CJK}, {&FONT_B51, NULL},
};

// --- characters ----------------------------------------------------------------------------------
// The next code point from UTF-8 (U+FFFD for a broken sequence).
static uint32_t nextChar(const char *&p) {
  const uint8_t *s = (const uint8_t *)p;
  uint32_t c = s[0];
  int n = c < 0x80 ? 0 : (c & 0xE0) == 0xC0 ? 1 : (c & 0xF0) == 0xE0 ? 2 : (c & 0xF8) == 0xF0 ? 3 : -1;
  if (n < 0) {
    p++;
    return 0xFFFD;
  }
  if (n) c &= 0x3F >> n;
  for (int i = 1; i <= n; i++) {
    if ((s[i] & 0xC0) != 0x80) {
      p += i;
      return 0xFFFD;
    }
    c = c << 6 | (s[i] & 0x3F);
  }
  p += n + 1;
  return c;
}

static bool isCjk(uint32_t c) {     // map_view._is_cjk
  return (c >= 0x2E80 && c <= 0x9FFF) || (c >= 0xAC00 && c <= 0xD7AF) || (c >= 0xF900 && c <= 0xFAFF) ||
         (c >= 0xFF00 && c <= 0xFFEF);
}

static bool isSkipped(uint32_t c) {  // emoji, pictographs and other symbols (map_view._printable)
  if (c >= 0x10000) return true;
  int lo = 0, hi = FACE_SKIP_COUNT - 1;
  while (lo <= hi) {
    int mid = (lo + hi) / 2;
    if (c < FACE_SKIP[mid][0]) hi = mid - 1;
    else if (c > FACE_SKIP[mid][1]) lo = mid + 1;
    else return true;
  }
  return false;
}

static const FaceGlyph *findGlyph(const FaceFont *f, uint32_t c) {
  if (!f) return NULL;
  int lo = 0, hi = f->count - 1;
  while (lo <= hi) {
    int mid = (lo + hi) / 2;
    if (c < f->glyphs[mid].cp) hi = mid - 1;
    else if (c > f->glyphs[mid].cp) lo = mid + 1;
    else return &f->glyphs[mid];
  }
  return NULL;
}

// --- layout --------------------------------------------------------------------------------------
struct Placed {
  const FaceGlyph *g;     // NULL: a character the font doesn't have (drawn as a hollow box)
  const FaceFont *f;
  int32_t pen16;          // where its pen starts, 1/16 px
  int32_t adv16;
};
static const int MAX_GLYPHS = 160;

// Lays out text with the font pair; returns the number of glyphs and the total advance.
static int layoutText(const char *text, FontId id, Placed *out, int32_t &adv16) {
  const FontPair &fp = FONTS[id];
  int n = 0;
  adv16 = 0;
  const char *p = text;
  while (*p && n < MAX_GLYPHS) {
    uint32_t c = nextChar(p);
    if (c == '\n' || c == '\r' || c == '\t') c = ' ';
    if (isSkipped(c)) continue;
    const FaceFont *f = isCjk(c) && fp.cjk ? fp.cjk : fp.main;
    const FaceGlyph *g = findGlyph(f, c);
    if (!g && f == fp.cjk) g = findGlyph(f = fp.main, c);
    if (!g && c == 0xFFFD) g = findGlyph(f = fp.main, '?');
    int32_t a = g ? g->adv16 : fp.main->px * 10;   // tofu: 0.6 em
    out[n++] = {g, f, adv16, a};
    adv16 += a;
  }
  // _printable strips spaces at both ends
  int first = 0;
  while (first < n && out[first].g && out[first].g->cp == ' ') first++;
  while (n > first && out[n - 1].g && out[n - 1].g->cp == ' ') n--;
  if (first) {
    int32_t shift = out[first < n ? first : 0].pen16;
    for (int i = first; i < n; i++) {
      out[i - first] = out[i];
      out[i - first].pen16 -= shift;
    }
    n -= first;
  }
  adv16 = n ? out[n - 1].pen16 + out[n - 1].adv16 : 0;
  return n;
}

// Cuts a laid-out line to fit maxW pixels with "…" (map_view._text_patch). Returns the new count.
static int cutToFit(Placed *pl, int n, FontId id, float maxW, int32_t &adv16) {
  if (adv16 <= maxW * 16) return n;
  const FaceGlyph *dots = findGlyph(FONTS[id].main, 0x2026);
  const int32_t dotsAdv = dots ? dots->adv16 : 0;
  while (n > 0 && pl[n - 1].pen16 + pl[n - 1].adv16 + dotsAdv > maxW * 16) n--;
  while (n > 0 && pl[n - 1].g && pl[n - 1].g->cp == ' ') n--;     // rstrip
  int32_t pen = n ? pl[n - 1].pen16 + pl[n - 1].adv16 : 0;
  if (dots) pl[n++] = {dots, FONTS[id].main, pen, dotsAdv};
  adv16 = pen + dotsAdv;
  return n;
}

// --- masks ---------------------------------------------------------------------------------------
struct TextMask {
  uint8_t *px;            // w x h coverage, PSRAM
  int16_t w, h;
  int16_t left;           // the mask's left edge, px from the pen's start
  int16_t top;            // its top, px from the font's middle (anchor "m"), downwards
  float adv;              // the line's advance in px
};

// Draws laid-out glyphs into a new mask (with a one-pixel outline around them when outlined: city
// labels, MaxFilter(3) in map_view).
static bool drawMask(const Placed *pl, int n, FontId id, TextMask &m, bool outlined) {
  const FaceFont *mainFont = FONTS[id].main;
  const int midUp = (int)lrintf(mainFont->mid);
  int l = 1 << 30, r = -(1 << 30), t = 1 << 30, b = -(1 << 30);
  for (int i = 0; i < n; i++) {
    const int pen = (pl[i].pen16 + 8) >> 4;
    int gl, gr, gt, gb;
    if (pl[i].g) {
      if (!pl[i].g->w) continue;
      gl = pen + pl[i].g->x, gr = gl + pl[i].g->w, gt = pl[i].g->y, gb = gt + pl[i].g->h;
    } else {                                  // tofu box
      gl = pen + 1, gr = pen + (int)(mainFont->px * 0.55f), gt = -(int)(mainFont->px * 0.7f), gb = 0;
    }
    if (gl < l) l = gl;
    if (gr > r) r = gr;
    if (gt < t) t = gt;
    if (gb > b) b = gb;
  }
  if (l >= r) {
    l = 0, r = 1, t = -1, b = 0;
  }
  const int o = outlined ? 1 : 0;
  m.w = r - l + 2 * o;
  m.h = b - t + 2 * o;
  m.left = l - o;
  m.top = t - o + midUp;
  m.px = (uint8_t *)heap_caps_calloc(1, m.w * m.h, MALLOC_CAP_SPIRAM);
  if (!m.px) return false;
  for (int i = 0; i < n; i++) {
    const int pen = (pl[i].pen16 + 8) >> 4;
    const FaceGlyph *g = pl[i].g;
    if (!g) {
      const int x0 = pen + 1 - m.left, x1 = pen + (int)(mainFont->px * 0.55f) - 1 - m.left;
      const int y0 = -(int)(mainFont->px * 0.7f) - t + o, y1 = -1 - t + o;
      for (int y = y0; y <= y1; y++)
        for (int x = x0; x <= x1; x++)
          if (y == y0 || y == y1 || x == x0 || x == x1) m.px[y * m.w + x] = 255;
      continue;
    }
    if (!g->w) continue;
    const uint8_t *bits = pl[i].f->bits + g->off;
    const int bw = (g->w + 1) / 2;
    const int x0 = pen + g->x - m.left, y0 = g->y - t + o;
    for (int y = 0; y < g->h; y++)
      for (int x = 0; x < g->w; x++) {
        const uint8_t byte = bits[y * bw + x / 2];
        const uint8_t v = ((x & 1) ? byte & 15 : byte >> 4) * 17;
        uint8_t &d = m.px[(y0 + y) * m.w + x0 + x];
        if (v > d) d = v;
      }
  }
  if (outlined) {                             // grow it by a pixel all round (3x3 maximum)
    uint8_t *grown = (uint8_t *)heap_caps_calloc(1, m.w * m.h, MALLOC_CAP_SPIRAM);
    if (!grown) return false;
    for (int y = 0; y < m.h; y++)
      for (int x = 0; x < m.w; x++) {
        uint8_t v = 0;
        for (int dy = -1; dy <= 1; dy++)
          for (int dx = -1; dx <= 1; dx++) {
            int yy = y + dy, xx = x + dx;
            if (yy >= 0 && yy < m.h && xx >= 0 && xx < m.w && m.px[yy * m.w + xx] > v) v = m.px[yy * m.w + xx];
          }
        grown[y * m.w + x] = v;
      }
    heap_caps_free(m.px);
    m.px = grown;
  }
  return true;
}

// The cache: a line is found again by its text, font, width limit and outline.
struct CachedText {
  uint32_t hash;
  uint32_t lastUsed;
  TextMask mask;
  bool used;
};
static const int TEXT_CACHE = 128;
static CachedText textCache[TEXT_CACHE];
static uint32_t textFrame;           // bumped once a frame by the caller

static uint32_t hashText(const char *s, uint32_t seed) {
  uint32_t h = 2166136261u ^ seed;
  while (*s) h = (h ^ (uint8_t)*s++) * 16777619u;
  return h;
}

// A line of text as a mask: cut with "…" to fit maxW pixels (0: never cut; the whole line, for
// scrolling). NULL if there's nothing to draw.
static const TextMask *textMask(const char *text, FontId id, float maxW, bool outlined = false) {
  if (!text || !*text) return NULL;
  const uint32_t h = hashText(text, id * 7919u + (uint32_t)lrintf(maxW * 4) * 31u + outlined);
  int slot = -1, oldest = 0;
  for (int i = 0; i < TEXT_CACHE; i++) {
    if (textCache[i].used && textCache[i].hash == h) {
      textCache[i].lastUsed = textFrame;
      return &textCache[i].mask;
    }
    if (!textCache[i].used && slot < 0) slot = i;
    if (textCache[i].lastUsed < textCache[oldest].lastUsed) oldest = i;
  }
  if (slot < 0) {
    if (textCache[oldest].lastUsed == textFrame) return NULL;   // all in use by this frame
    slot = oldest;
    heap_caps_free(textCache[slot].mask.px);
    textCache[slot].used = false;
  }
  static Placed pl[MAX_GLYPHS + 1];
  int32_t adv;
  int n = layoutText(text, id, pl, adv);
  if (!n) return NULL;
  if (maxW > 0) n = cutToFit(pl, n, id, maxW, adv);
  CachedText &c = textCache[slot];
  if (!drawMask(pl, n, id, c.mask, outlined)) return NULL;
  c.mask.adv = adv / 16.0f;
  c.hash = h;
  c.lastUsed = textFrame;
  c.used = true;
  return &c.mask;
}

// How wide a line of text at height y may be, inside the circle (map_view._line_width).
static float lineRoom(FontId id, float y) {
  const float c = LCD_W / 2.0f, edge = fabsf(y - c) + FONTS[id].main->px * 0.6f;
  return 2 * sqrtf(fmaxf(0, c * c - edge * edge)) - 30 * K;
}
