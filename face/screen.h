// screen.h: what the face shows, frame by frame (map_view.FaceScreen and face_hud, ported).
//
// Each frame, frameScene() takes the last state message and the time and works out the camera (it
// glides after the dial, flies long jumps, turns the idle globe) and the text and symbols with
// their fades and scrolls, then lays all of it out as a Scene: the view to draw the globe from, and
// a list of sprites to put over it. face.ino draws the Scene, unless it's the same as last time.
#pragma once
#include "text.h"
#include "link.h"
#include "data_icons.h"
#include "data_cities.h"
#include "qrcode.h"

// --- timings (map_view.py) -----------------------------------------------------------------------
static const float CROSSFADE_S = 0.4f;
static const float TOAST_IN_S = 0.15f, TOAST_OUT_S = 0.4f;
static const float ON_AIR_S = 3.0f, TOP_FADE_S = 0.3f;
static const float IDLE_ZOOM_S = 0.6f, LOCK_GLIDE_S = 0.25f;
static const float FLY_MIN_DEG = 25, FLY_S = 2.4f;
static const float SPIN_DEG_S = 2.0f, SPIN_RAMP_S = 4.0f, SPIN_FPS = 15;
// A game world drifts its whole map instead of spinning (map_view.drift_offset), and flies jumps
// longer than FLY_MIN_SHARE of the map.
static const double DRIFT_SHARE = 0.08, DRIFT_PERIOD_LAT_S = 170, DRIFT_PERIOD_LON_S = 260, DRIFT_RAMP_S = 20;
static const double FLY_MIN_SHARE = 0.3;
static const float CROSSHAIR_S = 15, CROSSHAIR_OUT_S = 1.0f, CROSSHAIR_IN_S = 0.15f;
static const float TUNE_CYCLE_S = 1.2f;
static const float WAVE_CYCLE_S = 4.0f;       // a world's scrolling tuning symbol goes round once in this long
static const float SCROLL_HOLD_S = 2.0f, SCROLL_PX_S = 19 * K, SCROLL_FADE_S = 0.3f;
static const int EDGE_FADE = 15;                 // round(10 * K)
static const float WAIT_SPIN_DEG_S = 6;          // the globe while waiting for the radio (virtual_face.py)
static const float QUIET_S = 3.0f;               // nothing heard this long: the radio has gone

// Where the text goes (map_view.LINES and friends, at 480 px).
enum Field { FIELD_TITLE, FIELD_SUBTITLE, FIELD_DETAIL };
static const FontId LINE_FONT[3] = {F_B27, F_R20, F_R18};
static const float LINE_Y[3] = {224 * K, 246 * K, 266 * K};
static const float COORDS_Y = 24 * K, TOP_Y = 46 * K, DATE_Y = 186 * K, CLOCK_Y = 214 * K;

// --- formatting (map_view, worldtime) ------------------------------------------------------------
static void fmtTime(char *out, size_t n, int minutes) {
  int h = minutes / 60 % 24, m = minutes % 60;
  snprintf(out, n, "%d:%02d %s", (h + 11) % 12 + 1, m, h < 12 ? "am" : "pm");
}

static void fmtDate(char *out, size_t n, const char *iso) {        // "2026-10-21" -> "Wed Oct 21"
  static const char *DAYS[] = {"Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"};
  static const char *MONTHS[] = {"Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"};
  int y, m, d;
  out[0] = 0;
  if (sscanf(iso, "%d-%d-%d", &y, &m, &d) != 3 || m < 1 || m > 12 || d < 1 || d > 31) return;
  static const int T[] = {0, 3, 2, 5, 0, 3, 5, 1, 4, 6, 2, 4};
  int yy = m < 3 ? y - 1 : y;
  int dow = (yy + yy / 4 - yy / 100 + yy / 400 + T[m - 1] + d) % 7;
  snprintf(out, n, "%s %s %d", DAYS[dow], MONTHS[m - 1], d);
}

static void fmtCoords(char *out, size_t n, double lat, double lon) {
  snprintf(out, n, "%.2f° %c   %.2f° %c", fabs(lat), lat >= 0 ? 'N' : 'S', fabs(lon), lon >= 0 ? 'E' : 'W');
}

static void fmtThousands(char *out, size_t n, long v) {          // 12345 -> "12,345"
  char digits[24];
  snprintf(digits, sizeof digits, "%ld", v < 0 ? -v : v);
  int len = strlen(digits), o = 0;
  if (v < 0 && o < (int)n - 1) out[o++] = '-';
  for (int i = 0; i < len && o < (int)n - 1; i++) {
    out[o++] = digits[i];
    if ((len - 1 - i) % 3 == 0 && i < len - 1 && o < (int)n - 1) out[o++] = ',';
  }
  out[o] = 0;
}

// --- the HUD: everything over the map (map_view.Hud) ---------------------------------------------
enum Signal { SIG_NONE, SIG_TUNING, SIG_PLAYING, SIG_NO_SIGNAL };

struct Hud {
  const char *text[3];               // title, subtitle, detail
  char coords[48], clock[16], date[20], reminder[64], local[16], temp[16], away[48];
  const char *toast;
  int sky;                           // weather symbol, -1: none
  bool favorite, liked, bt, panel;
  bool marker, markerLive;
  double mlat, mlon;
  int volume;                        // -1: no ring
  float crosshair, toastAlpha, signalAlpha, localAlpha, signalPhase;
  float signalScroll;                // a world's scrolling tuning symbol: how far round, 0-1
  Signal signal;
  bool scrolling[3];
  float scrollOffset[3];             // NAN: the resting, cut-short line
  float scrollAlpha[3];
};

// face_hud: a FaceState as what the screen shows.
static void faceHud(const FaceState &fs, Hud &h) {
  memset(&h, 0, sizeof h);
  h.text[0] = h.text[1] = h.text[2] = "";
  h.toast = "";
  h.sky = -1;
  h.volume = -1;
  h.crosshair = h.toastAlpha = h.signalAlpha = h.localAlpha = 1;
  for (int i = 0; i < 3; i++) h.scrollOffset[i] = NAN, h.scrollAlpha[i] = 1;
  const bool playing = fs.status == ST_PLAYING;
  if (fs.hasPanel) {                 // a card over the dimmed map; the volume ring still shows
    h.panel = true;
    h.volume = fs.ring ? fs.vol : -1;
    h.crosshair = 0;
    return;
  }
  if (fs.idle) {                     // nobody's touching it: a big clock, the station and the song
    char sleep[32] = "", alarm[32] = "";
    if (fs.timer >= 0) snprintf(sleep, sizeof sleep, "Sleep in %d min", fs.timer);
    if (fs.alarm >= 0) {
      char t[16];
      fmtTime(t, sizeof t, fs.alarm);
      snprintf(alarm, sizeof alarm, "Alarm %s", t);
    }
    snprintf(h.reminder, sizeof h.reminder, "%s%s%s", sleep, sleep[0] && alarm[0] ? " · " : "", alarm);
    if (fs.clock >= 0) fmtTime(h.clock, sizeof h.clock, fs.clock);
    h.text[FIELD_SUBTITLE] = fs.hasName ? fs.name : "";
    if (fs.date[0]) fmtDate(h.date, sizeof h.date, fs.date);
    h.crosshair = 0;
    h.liked = fs.liked && fs.song[0];
    h.text[FIELD_DETAIL] = fs.song;
    h.marker = fs.hasName;
    h.mlat = fs.slat;
    h.mlon = fs.slon;
    h.markerLive = playing;
    return;
  }
  fmtCoords(h.coords, sizeof h.coords, fs.lat, fs.lon);
  h.volume = fs.ring ? fs.vol : -1;
  h.toast = fs.toast;
  if (!fs.hasName) {
    h.text[FIELD_TITLE] = "No stations";
    return;
  }
  if (fs.local >= 0) fmtTime(h.local, sizeof h.local, fs.local);
  if (fs.sky >= 0) {
    h.sky = fs.sky;
    if (fs.temp < 0) snprintf(h.temp, sizeof h.temp, "−%d°", -fs.temp);   // a real minus sign
    else snprintf(h.temp, sizeof h.temp, "%d°", fs.temp);
  }
  const bool about = h.local[0] || h.sky >= 0;
  h.signal = fs.status == ST_TUNING ? SIG_TUNING : playing ? SIG_PLAYING : fs.status == ST_NO_SIGNAL ? SIG_NO_SIGNAL : SIG_NONE;
  const bool showSignal = h.signal != SIG_NONE && !(playing && about);
  const float freeSlot = fs.toast[0] ? 0 : 1;
  if (fs.status == ST_TURNING || fs.status == ST_IDLE) {
    if (fs.km < 0) {
      h.away[0] = 0;                          // a game world (km -1): its distances mean nothing
    } else if (fs.km < 1) {
      snprintf(h.away, sizeof h.away, "under 1 km away");
    } else {
      char n[24];
      fmtThousands(n, sizeof n, fs.km);
      snprintf(h.away, sizeof h.away, "%s km away", n);
    }
    h.text[FIELD_DETAIL] = h.away;
  } else {
    h.text[FIELD_DETAIL] = playing ? fs.song : "";
  }
  h.text[FIELD_TITLE] = fs.name;
  h.text[FIELD_SUBTITLE] = fs.place;
  h.marker = true;
  h.mlat = fs.slat;
  h.mlon = fs.slon;
  h.markerLive = playing;
  h.favorite = fs.fav;
  h.bt = fs.bt;
  h.liked = fs.liked && playing && fs.song[0];
  h.signalAlpha = showSignal ? freeSlot : 0;
  h.localAlpha = about && !showSignal ? freeSlot : 0;
}

// --- the scene: a view and the sprites over it ---------------------------------------------------
static const int MAX_SPRITES = 160;
struct Scene {
  View view;
  bool borders, idleBands, dim;
  int theme;
  int volume;                     // the volume ring, -1: none
  Sprite sprites[MAX_SPRITES];
  int under;                      // sprites[0..under) go before the card's dimming, the rest after
  int count;
  uint32_t hash;
};

static void addSprite(Scene &s, const uint8_t *mask, int stride, int x, int y, int w, int h, uint16_t color,
                      float alpha, bool under = false) {
  if (s.count >= MAX_SPRITES || alpha <= 0 || !mask) return;
  Sprite &sp = s.sprites[s.count++];
  sp = {mask, (int16_t)stride, (int16_t)x, (int16_t)y, (int16_t)w, (int16_t)h, -32768, 32767, NULL, color,
        (uint16_t)lrintf(clampf(alpha, 0, 1) * 256), under};
}

static void addIcon(Scene &s, const FaceIcon &icon, int x, int y, uint16_t color, float alpha, bool under = false) {
  addSprite(s, icon.mask, icon.w, x + icon.x, y + icon.y, icon.w, icon.h, color, alpha, under);
}

static void addMask(Scene &s, const TextMask *m, int x, int y, uint16_t color, float alpha, bool under = false) {
  if (m) addSprite(s, m->px, m->w, x, y, m->w, m->h, color, alpha, under);
}

// One line of text centered at height y, cut with "…" to fit (MapView._text). Returns its left and
// right edge on screen, or false if there's nothing to draw.
static bool addText(Scene &s, const char *text, FontId f, uint16_t color, float y, float alpha = 1,
                    float *x0 = NULL, float *x1 = NULL) {
  const TextMask *m = textMask(text, f, lineRoom(f, y));
  if (!m) return false;
  const int x = (int)lrintf(LCD_W / 2 - m->adv / 2 + m->left);
  addMask(s, m, x, (int)lrintf(y + m->top), color, alpha);
  if (x0) *x0 = x;
  if (x1) *x1 = x + m->w;
  return true;
}

// How many pixels a line is too long for its place (MapView.text_overflow).
static float textOverflow(const char *text, Field field) {
  if (!text || !*text) return 0;
  const TextMask *m = textMask(text, LINE_FONT[field], 0);
  return m ? fmaxf(0, m->w - lineRoom(LINE_FONT[field], LINE_Y[field])) : 0;
}

// Part of a long line, `offset` pixels in, with soft ends where more text hides (MapView._scrolled).
static uint8_t scrollRamps[3][LCD_W];
static bool addScrolled(Scene &s, Field field, const char *text, uint16_t color, float offset, float alpha,
                        float *x0, float *x1) {
  const FontId f = LINE_FONT[field];
  const float y = LINE_Y[field];
  const TextMask *m = textMask(text, f, 0);
  if (!m || s.count >= MAX_SPRITES) return false;
  const int width = (int)lineRoom(f, y);
  offset = fminf(fmaxf(0, offset), m->w - width);
  const bool fadeLeft = offset > 0.5f, fadeRight = offset < m->w - width - 0.5f;
  uint8_t *ramp = scrollRamps[field];
  memset(ramp, 255, LCD_W);
  for (int i = 0; i < EDGE_FADE && i < width; i++) {
    const uint8_t v = (uint8_t)lrintf((i + 0.5f) / EDGE_FADE * 255);
    if (fadeLeft) ramp[i] = v;
    if (fadeRight) ramp[width - 1 - i] = v;
  }
  const int left = (int)lrintf(LCD_W / 2 - width / 2.0f);
  const int start = (int)lrintf(offset);
  addSprite(s, m->px, m->w, left - start, (int)lrintf(y + m->top), m->w, m->h, color, alpha);
  Sprite &sp = s.sprites[s.count - 1];
  sp.clipL = left;
  sp.clipR = left + width;
  sp.ramp = ramp;
  *x0 = left;
  *x1 = left + width;
  return true;
}

// --- the QR code on a card -------------------------------------------------------------------------
static char qrText[256];
static uint8_t *qrLight, *qrDark;          // coverage masks, QR_SIDE square
static const int QR_SIDE = 150;            // round(QR_SIZE * K)
static uint8_t *qrModules;                 // 177 x 177 at most, PSRAM
static int qrSize;

static void qrCopy(esp_qrcode_handle_t q) {
  qrSize = esp_qrcode_get_size(q);
  if (!qrModules) qrModules = (uint8_t *)heap_caps_malloc(177 * 177, MALLOC_CAP_SPIRAM);
  if (!qrModules) {
    qrSize = 0;
    return;
  }
  for (int y = 0; y < qrSize; y++)
    for (int x = 0; x < qrSize; x++) qrModules[y * qrSize + x] = esp_qrcode_get_module(q, x, y);
}

// A QR code as a light rounded square with dark modules (MapView._qr), drawn 4x over and averaged.
static bool buildQr(const char *text) {
  if (!strcmp(text, qrText) && qrLight) return true;
  esp_qrcode_config_t cfg = ESP_QRCODE_CONFIG_DEFAULT();
  cfg.display_func = qrCopy;
  cfg.max_qrcode_version = 20;
  cfg.qrcode_ecc_level = ESP_QRCODE_ECC_MED;
  qrSize = 0;
  if (esp_qrcode_generate(&cfg, text) != ESP_OK || !qrSize) return false;
  if (!qrLight) {
    qrLight = (uint8_t *)heap_caps_malloc(QR_SIDE * QR_SIDE, MALLOC_CAP_SPIRAM);
    qrDark = (uint8_t *)heap_caps_malloc(QR_SIDE * QR_SIDE, MALLOC_CAP_SPIRAM);
    if (!qrLight || !qrDark) return false;
  }
  const int big = 4, side = QR_SIDE * big, n = qrSize + 4;
  const float cell = (float)side / n, rad = roundf(5 * K * big);
  for (int y = 0; y < QR_SIDE; y++)
    for (int x = 0; x < QR_SIDE; x++) {
      int light = 0, dark = 0;
      for (int sy = 0; sy < big; sy++)
        for (int sx = 0; sx < big; sx++) {
          const float X = x * big + sx + 0.5f, Y = y * big + sy + 0.5f;
          const float cx = fminf(fmaxf(X, rad), side - rad), cy = fminf(fmaxf(Y, rad), side - rad);
          if ((X - cx) * (X - cx) + (Y - cy) * (Y - cy) > rad * rad) continue;   // the rounded corners
          light++;
          const int c = (int)floorf(X / cell) - 2, r = (int)floorf(Y / cell) - 2;
          if (c >= 0 && r >= 0 && c < qrSize && r < qrSize && qrModules[r * qrSize + c]) dark++;
        }
      qrLight[y * QR_SIDE + x] = light * 255 / (big * big);
      qrDark[y * QR_SIDE + x] = light ? dark * 255 / light : 0;
    }
  copyText(qrText, text, sizeof qrText);
  return true;
}

// --- the screen ----------------------------------------------------------------------------------
struct Camera {
  double lat, lon, R;
  float glide, zoom;
};

struct Flight {
  bool on;
  double start;
  double target[3];
  double a[3], b[3];
  double r0, r1, dip, world;
};

static struct ScreenState {
  bool hasCamera;
  Camera cam;
  Flight flight;
  double time;                     // the last frame's time (NAN: none yet)
  char toastText[TEXT_BYTES];
  float toastAlpha;
  bool topInit, topSettled;
  float topSignal, topLocal;
  double playingSince;             // NAN: not playing
  bool crossInit;
  float cross, crossWant;
  double quietSince;               // NAN: not yet
  uint32_t scrollHash[3];
  double scrollStart[3];
  bool scrollKnown[3];
  bool handsInit, hands[3];        // idle, ring, toast: signs someone is at the controls
  double spinSince;                // NAN: not spinning
  bool modeInit;
  uint32_t mode;                   // theme, idle and the card's title: a change crossfades
  bool fadeStarted;                // face.ino starts the crossfade from the frame on screen
  bool moving;
} scr;

static void screenReset() {
  memset(&scr, 0, sizeof scr);
  scr.time = scr.playingSince = scr.quietSince = scr.spinSince = NAN;
}

static void unitVectorD(double lat, double lon, double out[3]) {
  double la = lat * M_PI / 180, lo = lon * M_PI / 180;
  out[0] = cos(la) * cos(lo);
  out[1] = cos(la) * sin(lo);
  out[2] = sin(la);
}

static double arcDeg(double lat1, double lon1, double lat2, double lon2) {
  double a[3], b[3];
  unitVectorD(lat1, lon1, a);
  unitVectorD(lat2, lon2, b);
  double d = a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
  return acos(d < -1 ? -1 : d > 1 ? 1 : d) * 180 / M_PI;
}

static double wrapLon(double lon) {
  lon = fmod(lon + 180, 360);
  if (lon < 0) lon += 360;
  return lon - 180;
}

// Camera.update: move part of the way to the target. True while still moving.
static bool cameraUpdate(Camera &c, double lat, double lon, double R, double dt) {
  const double k = 1 - exp(-dt / c.glide), kz = 1 - exp(-dt / c.zoom);
  const double dlat = lat - c.lat, dlon = wrapLon(lon - c.lon), dzoom = log(R / c.R);
  const double snap = 0.05 / (c.R * M_PI / 180);
  if (fabs(dlat) < snap && fabs(dlon) * cos(lat * M_PI / 180) < snap && fabs(dzoom) < 0.002) {
    const bool moved = c.lat != lat || c.lon != lon || c.R != R;
    c.lat = lat;
    c.lon = lon;
    c.R = R;
    return moved;
  }
  c.lat += dlat * k;
  c.lon = wrapLon(c.lon + dlon * k);
  c.R *= exp(dzoom * kz);
  return true;
}

static void startFlight(const double target[3], double now) {
  Flight &f = scr.flight;
  f.on = true;
  f.start = now;
  memcpy(f.target, target, sizeof f.target);
  unitVectorD(scr.cam.lat, scr.cam.lon, f.a);
  unitVectorD(target[0], target[1], f.b);
  f.r0 = log(scr.cam.R);
  f.r1 = log(target[2]);
  f.world = log((double)(inWorld && homeZoom > 0 ? radiusForZoom(homeZoom) : WORLD_R));   // a game world: its whole map
  f.dip = fmax(0.0, (f.r0 + f.r1) / 2 - f.world);
}

// Along the great circle, backing out to the whole world halfway (no further).
static void fly(double now) {
  Flight &f = scr.flight;
  Camera &c = scr.cam;
  const double u = fmin(1.0, (now - f.start) / FLY_S);
  if (u >= 1) {
    c.lat = f.target[0];
    c.lon = f.target[1];
    c.R = f.target[2];
    f.on = false;
    return;
  }
  const double e = u * u * (3 - 2 * u);
  double d = f.a[0] * f.b[0] + f.a[1] * f.b[1] + f.a[2] * f.b[2];
  const double angle = acos(d < -1 ? -1 : d > 1 ? 1 : d);
  double p[3];
  for (int i = 0; i < 3; i++)
    p[i] = angle > 1e-6 ? (f.a[i] * sin((1 - e) * angle) + f.b[i] * sin(e * angle)) / sin(angle) : f.b[i];
  c.lat = asin(p[2] < -1 ? -1 : p[2] > 1 ? 1 : p[2]) * 180 / M_PI;
  c.lon = atan2(p[1], p[0]) * 180 / M_PI;
  c.R = exp(fmax(f.world, f.r0 + (f.r1 - f.r0) * e - f.dip * sin(M_PI * u)));
}

// How far the idle globe has turned `elapsed` seconds in: easing in, then steady, in steps of 1/15 s.
static double spinOffset(double elapsed) {
  const double t = floor(elapsed * SPIN_FPS) / SPIN_FPS;
  if (t < SPIN_RAMP_S) return SPIN_DEG_S * t * t / (2 * SPIN_RAMP_S);
  return SPIN_DEG_S * (t - SPIN_RAMP_S / 2);
}

// A game world's idle map: how far it has drifted `elapsed` seconds in (degrees, for a view
// widthDeg across). A slow loop, easing in, in steps of 1/15 s.
static void driftOffset(double elapsed, double widthDeg, double &dlat, double &dlon) {
  const double t = floor(elapsed * SPIN_FPS) / SPIN_FPS;
  const double u = fmin(1.0, t / DRIFT_RAMP_S);
  const double a = DRIFT_SHARE * widthDeg * u * u * (3 - 2 * u);
  dlat = round(a * sin(2 * M_PI * t / DRIFT_PERIOD_LAT_S) * 1e6) / 1e6;
  dlon = round(a * sin(2 * M_PI * t / DRIFT_PERIOD_LON_S) * 1e6) / 1e6;
}

static double widthForZoom(int zoomKm) {        // degrees across the window (map_view.width_for_zoom)
  return zoomKm <= 0 ? 180.0 : zoomKm / (double)EARTH_KM * 180 / M_PI;
}

static void fadeToast(Hud &h, double dt) {
  if (h.toast[0]) {
    if (strcmp(h.toast, scr.toastText)) {
      if (!scr.toastText[0]) scr.toastAlpha = 0;
      copyText(scr.toastText, h.toast, TEXT_BYTES);
    }
    scr.toastAlpha = fminf(1, scr.toastAlpha + dt / TOAST_IN_S);
  } else if (scr.toastText[0]) {
    scr.toastAlpha = fmaxf(0, scr.toastAlpha - dt / TOAST_OUT_S);
    if (scr.toastAlpha == 0) scr.toastText[0] = 0;
  }
  h.toast = scr.toastText;
  h.toastAlpha = scr.toastText[0] ? scr.toastAlpha : 1;
}

static void fadeTop(Hud &h, const FaceState &fs, double now, double dt) {
  if (fs.status == ST_PLAYING) {
    if (isnan(scr.playingSince)) scr.playingSince = now;
  } else {
    scr.playingSince = NAN;
  }
  const bool confirming = !isnan(scr.playingSince) && now - scr.playingSince < ON_AIR_S;
  const bool about = h.local[0] || h.sky >= 0;
  const float wantSignal = h.signal != SIG_NONE && (fs.status != ST_PLAYING || confirming || !about) ? 1 : 0;
  const float wantLocal = about && !wantSignal ? 1 : 0;
  if (!scr.topInit) {
    scr.topSignal = wantSignal;
    scr.topLocal = wantLocal;
    scr.topInit = true;
  }
  const float step = dt / TOP_FADE_S;
  scr.topSignal = wantSignal > scr.topSignal ? fminf(wantSignal, scr.topSignal + step) : fmaxf(wantSignal, scr.topSignal - step);
  scr.topLocal = wantLocal > scr.topLocal ? fminf(wantLocal, scr.topLocal + step) : fmaxf(wantLocal, scr.topLocal - step);
  scr.topSettled = scr.topSignal == wantSignal && scr.topLocal == wantLocal && !confirming;
  const float freeSlot = 1 - (h.toast[0] ? h.toastAlpha : 0);
  h.signalAlpha = scr.topSignal * freeSlot;
  h.localAlpha = scr.topLocal * freeSlot;
}

static void fadeCrosshair(Hud &h, const FaceState &fs, double now, double dt) {
  if (fs.status != ST_PLAYING || fs.toast[0] || fs.ring || isnan(scr.quietSince)) scr.quietSince = now;
  const float want = fs.idle || fs.hasPanel || now - scr.quietSince >= CROSSHAIR_S ? 0 : 1;
  if (!scr.crossInit) {
    scr.cross = want;
    scr.crossInit = true;
  } else if (want > scr.cross) {
    scr.cross = fminf(want, scr.cross + dt / CROSSHAIR_IN_S);
  } else {
    scr.cross = fmaxf(want, scr.cross - dt / CROSSHAIR_OUT_S);
  }
  scr.crossWant = want;
  h.crosshair = scr.cross;
}

// (offset or NAN for the resting line, alpha) t seconds into a line's scroll (FaceScreen._scroll_at).
static void scrollAt(const char *text, Field field, double t, float &offset, float &alpha) {
  offset = NAN;
  alpha = 1;
  const float over = textOverflow(text, field);
  if (over <= 0) return;
  const float glide = over / SCROLL_PX_S;
  if (t < SCROLL_HOLD_S) {
    offset = 0;
    return;
  }
  t -= SCROLL_HOLD_S;
  if (t < glide) {
    const float u = t / glide;
    offset = over * u * u * (3 - 2 * u);
    return;
  }
  t -= glide;
  if (t < SCROLL_HOLD_S) {
    offset = over;
    return;
  }
  t -= SCROLL_HOLD_S;
  if (t < SCROLL_FADE_S) {
    offset = over;
    alpha = 1 - t / SCROLL_FADE_S;
    return;
  }
  t -= SCROLL_FADE_S;
  if (t < SCROLL_FADE_S) alpha = t / SCROLL_FADE_S;
}

// Long lines scroll once when they change, and again when someone reaches for a control.
static void scrollLines(Hud &h, const FaceState &fs, double now) {
  const bool hands[3] = {fs.idle, fs.ring, fs.toast[0] != 0};
  const bool *before = scr.handsInit ? scr.hands : hands;
  const bool again = (before[0] && !fs.idle) || (fs.ring && !before[1]) || (hands[2] && !before[2]);
  memcpy(scr.hands, hands, sizeof hands);
  scr.handsInit = true;
  const Field fields[2] = {fs.idle ? FIELD_SUBTITLE : FIELD_TITLE, FIELD_DETAIL};
  for (Field f : fields) {
    const uint32_t hash = hashText(h.text[f], 1);
    if (!scr.scrollKnown[f] || scr.scrollHash[f] != hash || again) {
      scr.scrollKnown[f] = true;
      scr.scrollHash[f] = hash;
      scr.scrollStart[f] = now;
    }
    float offset, alpha;
    scrollAt(h.text[f], f, now - scr.scrollStart[f], offset, alpha);
    if (!isnan(offset) || alpha < 1) {
      h.scrolling[f] = true;
      h.scrollOffset[f] = offset;
      h.scrollAlpha[f] = alpha;
    }
  }
}

// True while something is still moving (FaceScreen.busy): the face keeps drawing.
static bool screenBusy(const Hud &h) {
  return scr.moving || (scr.toastText[0] && scr.toastAlpha < 1) || !scr.topSettled || scr.cross != scr.crossWant ||
         scr.flight.on || !isnan(scr.spinSince) || h.scrolling[0] || h.scrolling[1] || h.scrolling[2];
}

// --- laying out the scene ------------------------------------------------------------------------
// The biggest cities in view for this zoom, labeled (MapView._draw_cities). A game world: its places.
static void addCities(Scene &s, const View &v) {
  const float limit = log2f(2 * (float)M_PI * v.R / 256) - 1.2f;
  if (limit < 1) return;
  const FaceCity *CITIES = inWorld ? worldPlaces : ::CITIES;
  const char *CITY_NAMES = inWorld ? worldPlaceNames : ::CITY_NAMES;
  const int CITY_COUNT = inWorld ? placesReady : ::CITY_COUNT;
  if (!CITIES || !CITY_COUNT) return;
  int n = 0;
  while (n < CITY_COUNT && CITIES[n].zoom100 <= limit * 100) n++;
  const float c = LCD_W / 2.0f, top = 60 * K, bottom = 208 * K;
  const float ring = 2.2f * K, gap = 4 * K, pad = 3 * K, edge2 = (c - 12 * K) * (c - 12 * K), clear = 30 * K;
  float taken[8][4];
  int count = 0;
  for (int i = 0; i < n && count < 8; i++) {
    const float p[3] = {CITIES[i].x / 32767.0f, CITIES[i].y / 32767.0f, CITIES[i].z / 32767.0f};
    float cx, cy;
    if (!projectPoint(v, p, cx, cy)) continue;
    if (!(cy > top && cy < bottom) || hypotf(cx - c, cy - c) >= c - 16 * K) continue;
    const char *name = CITY_NAMES + CITIES[i].name;
    const TextMask *m = textMask(name, F_R15, 0);
    if (!m) continue;
    const float w = m->w, ty = cy + m->top;
    for (int side = 0; side < 2; side++) {
      const float left = side == 0 ? cx + ring + gap : cx - ring - gap - w;
      const float b[4] = {fminf(left, cx - ring), ty, fmaxf(left + w, cx + ring), ty + m->h};
      bool fits = b[1] >= top && b[3] <= bottom &&
                  fmaxf((b[0] - c) * (b[0] - c), (b[2] - c) * (b[2] - c)) + fmaxf((b[1] - c) * (b[1] - c), (b[3] - c) * (b[3] - c)) <= edge2 &&
                  !(b[0] < c + clear && b[2] > c - clear && b[1] < c + clear && b[3] > c - clear);
      for (int t = 0; fits && t < count; t++)
        if (b[0] < taken[t][2] + pad && b[2] > taken[t][0] - pad && b[1] < taken[t][3] + pad && b[3] > taken[t][1] - pad)
          fits = false;
      if (!fits) continue;
      const TextMask *halo = textMask(name, F_R15, 0, true);
      m = textMask(name, F_R15, 0);       // (the halo may have pushed it out of the cache)
      if (!halo || !m) break;
      const int x = (int)lrintf(left), y = (int)lrintf(ty);
      addMask(s, halo, x + halo->left - m->left, y + halo->top - m->top, col.globe, 1, true);
      addMask(s, m, x, y, col.textDim, 1, true);
      addIcon(s, ICON_CITY_RING, (int)lrintf(cx), (int)lrintf(cy), col.textDim, 1, true);
      memcpy(taken[count++], b, sizeof b);
      break;
    }
  }
}

static void addSignal(Scene &s, const Hud &h) {
  if (h.signal == SIG_NONE || h.signalAlpha <= 0) return;
  const float a = h.signalAlpha;
  // A game world's own symbol (the Pip-Boy's in the Mojave), in the colors the usual one uses,
  // centered where it sits (MapView._draw_world_signal). The tuning one may be a strip: a window
  // slides along it.
  const int k = h.signal == SIG_PLAYING ? 0 : h.signal == SIG_TUNING ? 1 : 2;
  const WorldIcon &wi = worldIcons[k];
  if (inWorld && wi.ready) {
    int w = wi.w, x0 = 0;
    if (wi.win > 0) {
      const int loop = wi.w - wi.win;
      x0 = loop > 0 ? (int)lrintf(h.signalScroll * loop) % loop : 0;
      w = wi.win;
    }
    const float cy = ICON_SIGNAL_DOT.y + ICON_SIGNAL_DOT.h / 2.0f;
    addSprite(s, wi.mask + x0, wi.w, (int)lrintf(LCD_W / 2.0f - w / 2.0f), (int)lrintf(cy - wi.h / 2.0f), w, wi.h,
              k == 0 ? col.live : k == 1 ? col.text : col.warn, a);
    return;
  }
  auto put = [&](const FaceIcon &icon, uint16_t color) { addIcon(s, icon, 0, 0, color, a); };
  if (h.signal == SIG_PLAYING) {
    put(ICON_SIGNAL_DOT, col.live);
    put(ICON_SIGNAL_NEAR, col.live);
    put(ICON_SIGNAL_FAR, col.live);
  } else if (h.signal == SIG_TUNING) {          // the waves sweep outward
    put(ICON_SIGNAL_DOT, col.textDim);
    for (int i = 0; i < 2; i++) {
      const float b = 0.5f + 0.5f * cosf(2 * (float)M_PI * (h.signalPhase - 0.3f * i));
      put(i ? ICON_SIGNAL_FAR : ICON_SIGNAL_NEAR, mix565(col.t->text_dim, col.t->text, b));
    }
  } else {                                      // nothing: a hollow dot, struck through
    put(ICON_SIGNAL_NEAR, col.textDim);
    put(ICON_SIGNAL_FAR, col.textDim);
    put(ICON_SIGNAL_RING, col.warn);
    put(ICON_SIGNAL_SLASH, col.warn);
  }
}

// The slot under the coordinates: the time at the station and the weather there (MapView._draw_about).
static void addAbout(Scene &s, const Hud &h) {
  if (h.localAlpha <= 0 || !(h.local[0] || h.sky >= 0)) return;
  const float y = TOP_Y;
  if (h.sky < 0) {
    addText(s, h.local, F_R20, col.text, y, h.localAlpha);
    return;
  }
  const TextMask *local = h.local[0] ? textMask(h.local, F_R20, lineRoom(F_R20, y)) : NULL;
  const TextMask *temp = textMask(h.temp, F_R20, lineRoom(F_R20, y));
  const FaceIcon &icon = *SKY_ICONS[h.sky];
  const float gapIcon = local ? 11 * K : 0, gapTemp = 4 * K;
  float x = LCD_W / 2 - ((local ? local->w : 0) + gapIcon + icon.w + gapTemp + (temp ? temp->w : 0)) / 2;
  if (local) {
    addMask(s, local, (int)lrintf(x), (int)lrintf(y + local->top), col.text, h.localAlpha);
    x += local->w;
  }
  x += gapIcon;
  addSprite(s, icon.mask, icon.w, (int)lrintf(x), (int)lrintf(y - icon.h / 2.0f + 0.6f * K), icon.w, icon.h, col.text, h.localAlpha);
  x += icon.w + gapTemp;
  if (temp) addMask(s, temp, (int)lrintf(x), (int)lrintf(y + temp->top), col.text, h.localAlpha);
}

// A card over the map (MapView._panel): a title, a few lines, a QR code or a list to turn through
// (the highlighted row in the middle, its neighbors fading above and below), notes at the bottom.
static void addPanel(Scene &s, const PanelState &p) {
  float y = 66;
  addText(s, p.title, F_B26, col.text, y * K);
  y += 24;
  const bool quiet = p.qr[0] || p.itemCount;
  for (int i = 0; i < p.lineCount; i++, y += 18) addText(s, p.lines[i], F_R20, quiet ? col.textDim : col.text, y * K);
  if (p.qr[0] && buildQr(p.qr)) {
    const int x = (int)lrintf(LCD_W / 2.0f - QR_SIDE / 2.0f), top = (int)lrintf((y + 2) * K);
    addSprite(s, qrLight, QR_SIDE, x, top, QR_SIDE, QR_SIDE, rgb565(238, 241, 245), 1);
    addSprite(s, qrDark, QR_SIDE, x, top, QR_SIDE, QR_SIDE, rgb565(6, 8, 14), 1);
  }
  const float middle = 150, step = 28;
  for (int i = 0; i < p.itemCount; i++) {
    const int d = i - p.sel;
    if (d < -2 || d > 2) continue;
    if (d == 0) {
      addText(s, p.items[i][0], F_B26, col.text, (middle - 4) * K);
      const char *detail = p.items[i][1];
      const uint16_t tone = !strcmp(detail, "connected") ? col.live : !strcmp(detail, "couldn't connect") ? col.warn : col.textDim;
      addText(s, detail, F_R18, tone, (middle + 13) * K);
    } else {
      const float g = d > 0 ? step + 8 : step;
      addText(s, p.items[i][0], F_R20, col.textDim, (middle + d * g) * K, d == 1 || d == -1 ? 0.85f : 0.4f);
    }
  }
  y = p.noteCount < 2 ? 252 : 246;
  for (int i = 0; i < p.noteCount; i++, y += 15) addText(s, p.notes[i], F_R16, col.textDim, y * K);
}

// Everything over the map, given the view.
static void layoutScene(Scene &s, const Hud &h, const FaceState *fs, bool cities) {
  s.count = 0;
  s.volume = h.volume;
  s.dim = h.panel;
  const View &v = s.view;
  const float c = LCD_W / 2.0f;
  // under the window's edge and the bands: cities, favorite stars, the station's ring
  if (cities) addCities(s, v);
  for (int i = 0; i < favCount; i++) {
    float fx, fy;
    if (projectPoint(v, favXyz[i], fx, fy) && hypotf(fx - c, fy - c) < c - 8 * K) {
      addIcon(s, ICON_STAR_EDGE, (int)lrintf(fx), (int)lrintf(fy), col.space, 1, true);
      addIcon(s, ICON_STAR_FAV, (int)lrintf(fx), (int)lrintf(fy), col.live, 1, true);
    }
  }
  if (h.marker) {
    float p[3], mx, my;
    unitVector(h.mlat, h.mlon, p);
    if (projectPoint(v, p, mx, my) && hypotf(mx - c, my - c) < c - 8 * K)
      addIcon(s, ICON_MARKER, (int)lrintf(mx), (int)lrintf(my), h.markerLive ? col.live : col.textDim, 1, true);
  }
  if (h.crosshair > 0)
    addIcon(s, ICON_CROSSHAIR, 0, 0, col.cross, h.crosshair * col.t->crosshair_alpha / 255.0f);
  s.under = s.count;
  if (h.panel) {
    addPanel(s, fs->panel);
    return;
  }
  float x0, x1;
  if (addText(s, h.coords, F_R16, col.textDim, COORDS_Y, 1, &x0, &x1) && h.bt)
    addIcon(s, ICON_BLUETOOTH, (int)lrintf(x1 + 9 * K), (int)lrintf(COORDS_Y), col.textDim, 1);
  addSignal(s, h);
  addAbout(s, h);
  addText(s, h.toast, F_B20, col.text, TOP_Y, h.toastAlpha);
  if (h.clock[0]) addText(s, h.clock, F_B51, col.text, CLOCK_Y);
  addText(s, h.date, F_R21, col.textDim, DATE_Y);
  addText(s, h.reminder, F_R18, col.textDim, TOP_Y);
  const uint16_t colors[3] = {col.text, col.textDim, col.textDim};
  float spanL[3], spanR[3];
  bool drawn[3] = {false, false, false};
  for (int f = 0; f < 3; f++) {
    const char *text = h.text[f];
    if (!text[0]) continue;
    if (h.scrolling[f] && !isnan(h.scrollOffset[f]))
      drawn[f] = addScrolled(s, (Field)f, text, colors[f], h.scrollOffset[f], h.scrollAlpha[f], &spanL[f], &spanR[f]);
    else
      drawn[f] = addText(s, text, LINE_FONT[f], colors[f], LINE_Y[f], h.scrolling[f] ? h.scrollAlpha[f] : 1, &spanL[f], &spanR[f]);
  }
  if (drawn[FIELD_TITLE] && h.favorite)
    addIcon(s, ICON_STAR_TITLE, (int)lrintf(spanL[FIELD_TITLE] - 10 * K), (int)lrintf(225 * K), col.live, 1);
  if (drawn[FIELD_DETAIL] && h.liked)
    addIcon(s, ICON_HEART, (int)lrintf(spanL[FIELD_DETAIL] - 9 * K), (int)lrintf(266 * K), col.live, 1);
}

static uint32_t hashBytes(uint32_t h, const void *p, size_t n) {
  const uint8_t *b = (const uint8_t *)p;
  for (size_t i = 0; i < n; i++) h = (h ^ b[i]) * 16777619u;
  return h;
}

static void hashScene(Scene &s) {
  uint32_t h = 2166136261u;
  const float view[3] = {s.view.lat, s.view.lon, s.view.R};
  h = hashBytes(h, view, sizeof view);
  const int flags[6] = {s.borders, s.idleBands, s.dim, s.theme, s.volume, (int)stationsVersion};
  h = hashBytes(h, flags, sizeof flags);
  for (int i = 0; i < s.count; i++) {
    const Sprite &sp = s.sprites[i];
    h = hashBytes(h, &sp.mask, sizeof sp.mask);
    const int16_t v[8] = {sp.x, sp.y, sp.w, sp.h, sp.clipL, sp.clipR, (int16_t)sp.color, (int16_t)sp.alpha};
    h = hashBytes(h, v, sizeof v);
  }
  s.hash = h;
}

// --- the two kinds of frame ----------------------------------------------------------------------
// Before the radio has started, or after it's gone quiet: a slowly turning globe, nothing else.
static void waitingScene(Scene &s, double now, int theme) {
  useTheme(theme);
  const double t = floor(now * 20) / 20;           // 20 steps a second is plenty at 6 degrees a second
  makeView(s.view, 20, (float)(fmod(t * WAIT_SPIN_DEG_S, 360) - 180), WORLD_R);
  s.borders = false;
  s.idleBands = false;
  s.theme = theme;
  Hud h;
  static FaceState empty;                          // all zeros (and too big for the stack)
  faceHud(empty, h);
  h.text[0] = "";
  h.coords[0] = 0;
  h.marker = false;
  h.crosshair = 1;
  layoutScene(s, h, NULL, false);
  hashScene(s);
}

// FaceScreen.frame: the camera and the HUD for this state, now. Returns true while busy (moving).
static bool frameScene(Scene &s, const FaceState &fs, double now) {
  useTheme(fs.theme);
  const bool home = inWorld && homeZoom > 0;       // a game world: idle shows its whole map
  const double radius = fs.idle ? (home ? radiusForZoom(homeZoom) : WORLD_R) : radiusForZoom(fs.zoom);
  Hud h;
  faceHud(fs, h);
  const double dt = isnan(scr.time) ? 0 : now - scr.time;
  scr.time = now;
  double lat = fs.lat, lon = fs.lon;
  if (fs.idle && home) {
    // A game world, idle: the whole map, drifting slowly under the window.
    lat = homeLat;
    lon = homeLon;
    if (fs.spin) {
      if (isnan(scr.spinSince)) scr.spinSince = now;
      double dlat, dlon;
      driftOffset(now - scr.spinSince, widthForZoom(homeZoom), dlat, dlon);
      lat += dlat;
      lon += dlon;
    } else {
      scr.spinSince = NAN;
    }
  } else if (fs.idle && fs.spin) {
    // Idle: the globe turns about its axis around the station's latitude; touching anything flies it back.
    if (isnan(scr.spinSince)) scr.spinSince = now;
    lon = wrapLon(fs.lon - spinOffset(now - scr.spinSince));
  } else {
    scr.spinSince = NAN;
  }
  if (!scr.hasCamera) {
    scr.cam = {lat, lon, radius, 0.07f, 0.12f};
    scr.hasCamera = true;
    scr.moving = false;
  } else {
    scr.cam.zoom = fs.idle ? IDLE_ZOOM_S : 0.12f;
    // On air: the dial locking on, gently. A game world going idle glides out as slowly as it zooms.
    scr.cam.glide = fs.idle && home ? IDLE_ZOOM_S : fs.status == ST_PLAYING ? LOCK_GLIDE_S : 0.07f;
    const double target[3] = {lat, lon, radius};
    if (scr.flight.on && memcmp(scr.flight.target, target, sizeof target)) scr.flight.on = false;
    const double flyMin = home ? FLY_MIN_SHARE * widthForZoom(homeZoom) : FLY_MIN_DEG;
    if (!scr.flight.on && !(fs.idle && home) && arcDeg(scr.cam.lat, scr.cam.lon, lat, lon) > flyMin)
      startFlight(target, now);
    if (scr.flight.on) {
      fly(now);
      scr.moving = true;
    } else if (!isnan(scr.spinSince) && fabs(log(radius / scr.cam.R)) < 0.002 &&
               (!home || arcDeg(scr.cam.lat, scr.cam.lon, lat, lon) < 0.02)) {
      // Zoomed out: follow the turn (or drift) exactly, one step at a time.
      scr.moving = scr.cam.lat != lat || scr.cam.lon != lon || scr.cam.R != radius;
      scr.cam.lat = lat;
      scr.cam.lon = lon;
      scr.cam.R = radius;
    } else {
      scr.moving = cameraUpdate(scr.cam, lat, lon, radius, dt);
    }
  }
  fadeToast(h, dt);
  fadeTop(h, fs, now, dt);
  fadeCrosshair(h, fs, now, dt);
  scrollLines(h, fs, now);
  if (h.signal == SIG_TUNING) {
    h.signalPhase = roundf(fmod(now, TUNE_CYCLE_S) / TUNE_CYCLE_S * 100) / 100;
    h.signalScroll = roundf(fmod(now, WAVE_CYCLE_S) / WAVE_CYCLE_S * 1000) / 1000;
  }

  // the theme, the idle clock or a card changed: crossfade
  const uint32_t mode = hashText(fs.hasPanel ? fs.panel.title : "", fs.theme * 2 + fs.idle + (fs.hasPanel ? 7 : 0));
  if (scr.modeInit && mode != scr.mode) scr.fadeStarted = true;
  scr.mode = mode;
  scr.modeInit = true;

  makeView(s.view, (float)scr.cam.lat, (float)scr.cam.lon, (float)scr.cam.R);
  s.borders = fs.borders;
  s.idleBands = h.clock[0] != 0;
  s.theme = fs.theme;
  layoutScene(s, h, &fs, fs.cities);
  hashScene(s);
  return screenBusy(h);
}
