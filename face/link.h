// link.h: the face's end of the USB cable (PLAN.md, "Messages from the Pi to the face";
// terradio/face_link.py is the Pi's end and the source of truth for the formats).
//
// One JSON object per line. A task reads the port all the time into a ring buffer in PSRAM, so the
// Pi's writes never back up while a frame is being drawn (a write that blocks for 0.5 s makes the
// Pi drop and reopen the port, which resets the board). Between frames the main loop takes whole
// lines from the ring and acts on them: `state` becomes the FaceState below, `stations` and `favs`
// the dots and stars, `world` and `lines` a game world's map, `sleep` and `wake` the backlight's fades.
#pragma once
#include <Arduino.h>
#include "world.h"
#include "data_icons.h"
#include "data_cities.h"
#include "hal/usb_serial_jtag_ll.h"

static const int PROTOCOL = 2;          // the "v" in hello (2: game worlds)
static const int LINE_BYTES = 2048;       // the longest line taken; longer ones are dropped

// --- the state message ---------------------------------------------------------------------------
enum Status { ST_IDLE, ST_TURNING, ST_TUNING, ST_PLAYING, ST_NO_SIGNAL };
static const int TEXT_BYTES = 200;      // 64 characters of UTF-8, and a "…"
static const int PANEL_LINES = 6, PANEL_ITEMS = 32, PANEL_NOTES = 4, ITEM_BYTES = 100;

struct PanelState {
  char title[ITEM_BYTES];
  char lines[PANEL_LINES][ITEM_BYTES];
  int lineCount;
  char items[PANEL_ITEMS][2][ITEM_BYTES];   // (label, detail)
  int itemCount;
  int sel;                                  // -1: none
  char qr[256];
  char notes[PANEL_NOTES][ITEM_BYTES];
  int noteCount;
};

// face_link.FaceState. Text fields are "" when left out, numbers -1 (or NAN) when unknown.
struct FaceState {
  double lat, lon;
  int zoom;
  int theme;
  Status status;
  bool hasName;
  char name[TEXT_BYTES], place[TEXT_BYTES];
  double slat, slon;
  int km, vol;
  bool ring, mute, fav, idle, liked, bt, borders, cities, spin;
  char toast[TEXT_BYTES], song[TEXT_BYTES];
  int local, clock, alarm, timer;           // minutes after midnight (-1: left out)
  int sky;                                  // index in SKY_NAMES, -1: left out
  int temp;
  char date[12];
  int bright;
  bool hasPanel;
  PanelState panel;
};

// --- a small JSON parser -------------------------------------------------------------------------
// Parses one line in place (strings are unescaped where they lie) into a pool of nodes.
enum JType : uint8_t { J_NULL, J_BOOL, J_NUM, J_STR, J_ARR, J_OBJ };
struct JNode {
  JType type;
  int16_t child, next;    // first element or member; next sibling (-1: none)
  const char *key;        // for object members
  const char *str;
  double num;
};
static const int JPOOL = 700;
static JNode jpool[JPOOL];
static int jcount;

static void skipSpace(char *&p) {
  while (*p == ' ' || *p == '\t' || *p == '\r' || *p == '\n') p++;
}

static void putUtf8(char *&w, uint32_t c) {
  if (c < 0x80) {
    *w++ = c;
  } else if (c < 0x800) {
    *w++ = 0xC0 | c >> 6;
    *w++ = 0x80 | (c & 0x3F);
  } else if (c < 0x10000) {
    *w++ = 0xE0 | c >> 12;
    *w++ = 0x80 | ((c >> 6) & 0x3F);
    *w++ = 0x80 | (c & 0x3F);
  } else {
    *w++ = 0xF0 | c >> 18;
    *w++ = 0x80 | ((c >> 12) & 0x3F);
    *w++ = 0x80 | ((c >> 6) & 0x3F);
    *w++ = 0x80 | (c & 0x3F);
  }
}

static int hex4(const char *p) {
  int v = 0;
  for (int i = 0; i < 4; i++) {
    char c = p[i];
    v <<= 4;
    if (c >= '0' && c <= '9') v |= c - '0';
    else if (c >= 'a' && c <= 'f') v |= c - 'a' + 10;
    else if (c >= 'A' && c <= 'F') v |= c - 'A' + 10;
    else return -1;
  }
  return v;
}

// A string starting after its opening quote; unescaped in place. NULL if broken.
static char *parseString(char *&p) {
  char *start = p, *w = p;
  for (;;) {
    char c = *p;
    if (!c) return NULL;
    if (c == '"') {
      p++;
      *w = 0;
      return start;
    }
    if (c != '\\') {
      *w++ = *p++;
      continue;
    }
    c = p[1];
    p += 2;
    switch (c) {
      case 'n': *w++ = '\n'; break;
      case 't': *w++ = '\t'; break;
      case 'r': *w++ = '\r'; break;
      case 'b': *w++ = '\b'; break;
      case 'f': *w++ = '\f'; break;
      case 'u': {
        int u = hex4(p);
        if (u < 0) return NULL;
        p += 4;
        uint32_t cp = u;
        if (u >= 0xD800 && u < 0xDC00 && p[0] == '\\' && p[1] == 'u') {
          int lo = hex4(p + 2);
          if (lo >= 0xDC00 && lo < 0xE000) {
            cp = 0x10000 + ((u - 0xD800) << 10) + (lo - 0xDC00);
            p += 6;
          }
        }
        putUtf8(w, cp);
        break;
      }
      case 0: return NULL;
      default: *w++ = c; break;    // \" \\ \/
    }
  }
}

static int parseValue(char *&p, int depth);

static int newNode(JType t) {
  if (jcount >= JPOOL) return -1;
  JNode &n = jpool[jcount];
  n.type = t;
  n.child = n.next = -1;
  n.key = n.str = NULL;
  n.num = 0;
  return jcount++;
}

static int parseValue(char *&p, int depth) {
  skipSpace(p);
  if (depth > 8) return -1;
  char c = *p;
  if (c == '{' || c == '[') {
    const bool obj = c == '{';
    int node = newNode(obj ? J_OBJ : J_ARR), last = -1;
    if (node < 0) return -1;
    p++;
    skipSpace(p);
    if (*p == (obj ? '}' : ']')) {
      p++;
      return node;
    }
    for (;;) {
      const char *key = NULL;
      if (obj) {
        skipSpace(p);
        if (*p != '"') return -1;
        p++;
        key = parseString(p);
        if (!key) return -1;
        skipSpace(p);
        if (*p != ':') return -1;
        p++;
      }
      int v = parseValue(p, depth + 1);
      if (v < 0) return -1;
      jpool[v].key = key;
      if (last < 0) jpool[node].child = v;
      else jpool[last].next = v;
      last = v;
      skipSpace(p);
      if (*p == ',') {
        p++;
        continue;
      }
      if (*p == (obj ? '}' : ']')) {
        p++;
        return node;
      }
      return -1;
    }
  }
  if (c == '"') {
    p++;
    int node = newNode(J_STR);
    if (node < 0) return -1;
    jpool[node].str = parseString(p);
    return jpool[node].str ? node : -1;
  }
  if (!strncmp(p, "true", 4) || !strncmp(p, "false", 5)) {
    int node = newNode(J_BOOL);
    if (node < 0) return -1;
    jpool[node].num = *p == 't';
    p += *p == 't' ? 4 : 5;
    return node;
  }
  if (!strncmp(p, "null", 4)) {
    p += 4;
    return newNode(J_NULL);
  }
  char *end;
  double v = strtod(p, &end);
  if (end == p) return -1;
  p = end;
  int node = newNode(J_NUM);
  if (node >= 0) jpool[node].num = v;
  return node;
}

static int jget(int obj, const char *key) {
  if (obj < 0 || jpool[obj].type != J_OBJ) return -1;
  for (int c = jpool[obj].child; c >= 0; c = jpool[c].next)
    if (!strcmp(jpool[c].key, key)) return c;
  return -1;
}
static bool jhas(int obj, const char *key) {
  int n = jget(obj, key);
  return n >= 0 && jpool[n].type != J_NULL;
}
static double jnum(int obj, const char *key, double dflt) {
  int n = jget(obj, key);
  return n >= 0 && (jpool[n].type == J_NUM || jpool[n].type == J_BOOL) ? jpool[n].num : dflt;
}
static bool jbool(int obj, const char *key) {
  int n = jget(obj, key);
  if (n < 0) return false;
  if (jpool[n].type == J_STR) return jpool[n].str[0] != 0;
  return jpool[n].num != 0;
}
static const char *jstr(int obj, const char *key) {
  int n = jget(obj, key);
  return n >= 0 && jpool[n].type == J_STR ? jpool[n].str : "";
}
static void copyText(char *dst, const char *src, size_t size) {
  strncpy(dst, src, size - 1);
  dst[size - 1] = 0;
  // don't leave half a UTF-8 character at the end
  size_t n = strlen(dst);
  if (n == size - 1) {
    while (n > 0 && ((uint8_t)dst[n - 1] & 0xC0) == 0x80) n--;
    if (n > 0 && ((uint8_t)dst[n - 1] & 0xC0) == 0xC0) n--;
    dst[n] = 0;
  }
}

// --- base64 --------------------------------------------------------------------------------------
static int b64value(char c) {
  if (c >= 'A' && c <= 'Z') return c - 'A';
  if (c >= 'a' && c <= 'z') return c - 'a' + 26;
  if (c >= '0' && c <= '9') return c - '0' + 52;
  if (c == '+') return 62;
  if (c == '/') return 63;
  return -1;
}

// Decodes into out (up to cap bytes); returns the bytes written.
static int b64decode(const char *s, uint8_t *out, int cap) {
  uint32_t acc = 0;
  int bits = 0, n = 0;
  for (; *s; s++) {
    int v = b64value(*s);
    if (v < 0) continue;            // '=' padding, or anything stray
    acc = acc << 6 | v;
    bits += 6;
    if (bits >= 8) {
      bits -= 8;
      if (n < cap) out[n++] = (acc >> bits) & 0xFF;
    }
  }
  return n;
}

// --- what the Pi has told us ---------------------------------------------------------------------
static FaceState faceState;              // the last state message
static bool haveState;
static double heardAt = -1e9;            // when the last message arrived
static uint32_t badLines, goodLines;

// sleep and wake: picked up by the screen (face.ino)
static bool sleepRequested, wakeRequested;
static float sleepFade, wakeFade;
static bool statsRequested, shotRequested, sceneRequested;

// favorite spots, as unit vectors
static const int FAV_MAX = 256;
static float favXyz[FAV_MAX][3];
static int favCount;
static uint32_t favVersion;

// The station list as it comes in, 256 to a line (face_link.StationsReceiver).
static int16_t (*rxPos)[2];
static uint8_t *rxGot;                   // one flag per line of 256
static int rxCount;

static double nowSeconds() { return esp_timer_get_time() / 1e6; }

static int themeIndex(const char *name) {
  for (int i = 0; i < FACE_THEME_COUNT; i++)
    if (!strcmp(FACE_THEMES[i].name, name)) return i;
  return -1;
}

static void takeStations(int msg) {
  const int n = (int)jnum(msg, "n", -1), i = (int)jnum(msg, "i", -1);
  if (n < 0 || n > 60000 || i < 0 || i > n) {
    badLines++;
    return;
  }
  const int chunks = (n + 255) / 256;
  if (i == 0 || !rxPos || rxCount != n) {
    heap_caps_free(rxPos);
    heap_caps_free(rxGot);
    rxPos = (int16_t(*)[2])heap_caps_calloc(n ? n : 1, 4, MALLOC_CAP_SPIRAM);
    rxGot = (uint8_t *)heap_caps_calloc(chunks ? chunks : 1, 1, MALLOC_CAP_SPIRAM);
    rxCount = n;
    if (!rxPos || !rxGot) {
      rxCount = -1;
      return;
    }
  }
  const int got = b64decode(jstr(msg, "pos"), (uint8_t *)(rxPos + i), (n - i) * 4) / 4;
  if (got > 0 && i % 256 == 0) rxGot[i / 256] = 1;
  for (int c = 0; c < chunks; c++)
    if (!rxGot[c]) return;
  // The whole list is in: swap it in for the dots.
  int16_t (*xyz)[3] = xyzFromPositions(rxPos, n);
  if (!xyz) return;
  if (n > dotCap) {
    uint32_t *d = (uint32_t *)heap_caps_malloc(n * 4, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    if (!d) d = (uint32_t *)heap_caps_malloc(n * 4, MALLOC_CAP_SPIRAM);
    if (!d) {
      heap_caps_free(xyz);
      return;
    }
    heap_caps_free(dots);
    dots = d;
    dotCap = n;
  }
  heap_caps_free(stationXyz);
  stationXyz = xyz;
  stationCount = n;
  stationsVersion++;
  heap_caps_free(rxPos);
  heap_caps_free(rxGot);
  rxPos = NULL;
  rxGot = NULL;
}

// A game world's lines as they come in, like the stations: [0] coastlines, [1] borders.
static int16_t (*wrxPos[2])[2];
static uint8_t *wrxGot[2];
static int wrxCount[2];

// A world's places, labeled as Earth's cities are (screen.h's addCities), as they come in.
static const int PLACE_NAME_BYTES = 200;    // 64 characters of UTF-8, and a "…"
static FaceCity *worldPlaces;
static char *worldPlaceNames;               // PLACE_NAME_BYTES each
static uint8_t *placeGot;
static int placeCount, placesReady;         // how many announced; how many to label (0 until all in)

static void freePlaces() {
  heap_caps_free(worldPlaces);
  heap_caps_free(worldPlaceNames);
  heap_caps_free(placeGot);
  worldPlaces = NULL;
  worldPlaceNames = NULL;
  placeGot = NULL;
  placeCount = placesReady = 0;
}

// {"t":"places","n":187,"i":0,"pos":"…","zoom":[400,…],"names":["New Vegas",…]}: some of them.
static bool takePlaces(int msg) {
  const int n = (int)jnum(msg, "n", -1), i = (int)jnum(msg, "i", -1);
  if (!worldPlaces || n != placeCount || i < 0 || i >= n) return false;
  static int16_t pos[64][2];
  const int got = b64decode(jstr(msg, "pos"), (uint8_t *)pos, sizeof pos) / 4;
  const int zooms = jget(msg, "zoom"), names = jget(msg, "names");
  if (zooms < 0 || names < 0 || jpool[zooms].type != J_ARR || jpool[names].type != J_ARR) return false;
  int z = jpool[zooms].child, nm = jpool[names].child;
  for (int k = 0; k < got && i + k < n && z >= 0 && nm >= 0; k++, z = jpool[z].next, nm = jpool[nm].next) {
    FaceCity &c = worldPlaces[i + k];
    float v[3];
    unitVector(pos[k][0] / 180.0f, pos[k][1] / 180.0f, v);
    c.x = (int16_t)lrintf(v[0] * 32767);
    c.y = (int16_t)lrintf(v[1] * 32767);
    c.z = (int16_t)lrintf(v[2] * 32767);
    c.zoom100 = (uint16_t)jpool[z].num;
    c.name = (uint32_t)(i + k) * PLACE_NAME_BYTES;
    copyText(worldPlaceNames + c.name, jpool[nm].type == J_STR ? jpool[nm].str : "", PLACE_NAME_BYTES);
    placeGot[i + k] = 1;
  }
  for (int k = 0; k < n; k++)
    if (!placeGot[k]) return true;
  placesReady = n;                          // all in: label them (they come lowest zoom first)
  stationsVersion++;
  return true;
}

// A game world's own symbols for the signal slot (the Pip-Boy's in the Mojave): [0] on air,
// [1] tuning, [2] no signal. 8-bit masks sized for this screen; win > 0: a strip shown win wide,
// sliding along it (its last win columns repeat its first). screen.h's addSignal draws them.
static const int ICON_CHUNK = 1024;         // bytes of mask in one icon message (face_link.ICON_CHUNK)
struct WorldIcon {
  uint8_t *mask, *got;
  int w, h, win, n;
  bool ready;
};
static WorldIcon worldIcons[3];

static void freeWorldIcons() {
  for (WorldIcon &ic : worldIcons) {
    heap_caps_free(ic.mask);
    heap_caps_free(ic.got);
    ic = {};
  }
}

// {"t":"icon","name":"tuning","w":300,"h":34,"win":64,"n":10200,"i":0,"px":"…"}: 1 KB of one.
static bool takeIcon(int msg) {
  const char *name = jstr(msg, "name");
  const int k = !strcmp(name, "playing") ? 0 : !strcmp(name, "tuning") ? 1 : !strcmp(name, "no signal") ? 2 : -1;
  const int w = (int)jnum(msg, "w", 0), h = (int)jnum(msg, "h", 0), win = (int)jnum(msg, "win", 0);
  const int n = (int)jnum(msg, "n", -1), i = (int)jnum(msg, "i", -1);
  if (k < 0 || w <= 0 || w > 1024 || h <= 0 || h > 64 || n != w * h || win < 0 || win >= w ||
      i < 0 || i >= n || i % ICON_CHUNK)
    return false;
  WorldIcon &ic = worldIcons[k];
  if (!ic.mask || ic.w != w || ic.h != h || ic.win != win) {
    heap_caps_free(ic.mask);
    heap_caps_free(ic.got);
    ic = {};
    ic.mask = (uint8_t *)heap_caps_calloc(n, 1, MALLOC_CAP_SPIRAM);
    ic.got = (uint8_t *)heap_caps_calloc((n + ICON_CHUNK - 1) / ICON_CHUNK, 1, MALLOC_CAP_SPIRAM);
    if (!ic.mask || !ic.got) {
      heap_caps_free(ic.mask);
      heap_caps_free(ic.got);
      ic = {};
      return false;
    }
    ic.w = w, ic.h = h, ic.win = win, ic.n = n;
  }
  if (b64decode(jstr(msg, "px"), ic.mask + i, n - i) > 0) ic.got[i / ICON_CHUNK] = 1;
  if (!ic.ready) {
    for (int c = 0; c < (n + ICON_CHUNK - 1) / ICON_CHUNK; c++)
      if (!ic.got[c]) return true;
    ic.ready = true;                        // all in: addSignal draws it from now on
  }
  return true;
}

static void freeWorldRx(int layer) {
  heap_caps_free(wrxPos[layer]);
  heap_caps_free(wrxGot[layer]);
  wrxPos[layer] = NULL;
  wrxGot[layer] = NULL;
  wrxCount[layer] = 0;
}

// {"t":"world","name":"Mojave Wasteland","coast":812,"border":240,"lat":0,"lon":0,"zoom":1700}: a
// game world, its lines to follow (that many points of each), and its whole-map view. {"t":"world"}: Earth.
static void takeWorld(int msg) {
  const bool on = jhas(msg, "name");
  setWorld(on);
  freePlaces();
  freeWorldIcons();                         // the new world's own come after, if it has any
  const int places = on ? (int)jnum(msg, "places", 0) : 0;
  if (places > 0 && places <= 5000) {
    worldPlaces = (FaceCity *)heap_caps_calloc(places, sizeof(FaceCity), MALLOC_CAP_SPIRAM);
    worldPlaceNames = (char *)heap_caps_calloc(places, PLACE_NAME_BYTES, MALLOC_CAP_SPIRAM);
    placeGot = (uint8_t *)heap_caps_calloc(places, 1, MALLOC_CAP_SPIRAM);
    if (worldPlaces && worldPlaceNames && placeGot) placeCount = places;
    else freePlaces();
  }
  homeLat = jnum(msg, "lat", 0);
  homeLon = jnum(msg, "lon", 0);
  homeZoom = (int)jnum(msg, "zoom", 0);
  for (int layer = 0; layer < 2; layer++) {
    freeWorldRx(layer);
    const int n = on ? (int)jnum(msg, layer ? "border" : "coast", 0) : 0;
    if (n <= 0 || n > 200000) continue;
    wrxPos[layer] = (int16_t(*)[2])heap_caps_calloc(n, 4, MALLOC_CAP_SPIRAM);
    wrxGot[layer] = (uint8_t *)heap_caps_calloc((n + 255) / 256, 1, MALLOC_CAP_SPIRAM);
    if (!wrxPos[layer] || !wrxGot[layer]) {
      freeWorldRx(layer);
      continue;
    }
    wrxCount[layer] = n;
  }
}

// {"t":"lines","layer":"coast","n":812,"i":0,"pos":"…"}: 256 points of a layer, packed like stations.
static bool takeLines(int msg) {
  const char *name = jstr(msg, "layer");
  const int layer = !strcmp(name, "coast") ? 0 : !strcmp(name, "border") ? 1 : -1;
  const int n = (int)jnum(msg, "n", -1), i = (int)jnum(msg, "i", -1);
  if (layer < 0 || !wrxPos[layer] || n != wrxCount[layer] || i < 0 || i >= n) return false;
  const int got = b64decode(jstr(msg, "pos"), (uint8_t *)(wrxPos[layer] + i), (n - i) * 4) / 4;
  if (got > 0 && i % 256 == 0) wrxGot[layer][i / 256] = 1;
  for (int c = 0; c < (n + 255) / 256; c++)
    if (!wrxGot[layer][c]) return true;
  setWorldLayer(layer, wrxPos[layer], n);    // the whole layer is in
  freeWorldRx(layer);
  return true;
}

static void takeFavorites(int msg) {
  static int16_t pos[FAV_MAX][2];
  const int n = b64decode(jstr(msg, "pos"), (uint8_t *)pos, sizeof pos) / 4;
  for (int i = 0; i < n; i++) unitVector(pos[i][0] / 180.0f, pos[i][1] / 180.0f, favXyz[i]);
  favCount = n;
  favVersion++;
}

static void takeTextArray(int arr, char (*out)[ITEM_BYTES], int cap, int &count) {
  count = 0;
  if (arr < 0 || jpool[arr].type != J_ARR) return;
  for (int c = jpool[arr].child; c >= 0 && count < cap; c = jpool[c].next)
    if (jpool[c].type == J_STR) copyText(out[count++], jpool[c].str, ITEM_BYTES);
}

static bool takeState(int msg) {
  if (!jhas(msg, "lat") || !jhas(msg, "lon")) return false;
  FaceState &s = faceState;
  s.lat = jnum(msg, "lat", 0);
  s.lon = jnum(msg, "lon", 0);
  s.zoom = (int)jnum(msg, "zoom", 2000);
  int t = themeIndex(jstr(msg, "theme"));
  if (t >= 0) s.theme = t;            // a theme this face doesn't know: keep the one it has
  const char *st = jstr(msg, "status");
  s.status = !strcmp(st, "turning") ? ST_TURNING : !strcmp(st, "tuning") ? ST_TUNING
           : !strcmp(st, "playing") ? ST_PLAYING : !strcmp(st, "no signal") ? ST_NO_SIGNAL : ST_IDLE;
  s.hasName = jget(msg, "name") >= 0;
  copyText(s.name, jstr(msg, "name"), TEXT_BYTES);
  copyText(s.place, jstr(msg, "place"), TEXT_BYTES);
  s.slat = jnum(msg, "slat", 0);
  s.slon = jnum(msg, "slon", 0);
  s.km = (int)jnum(msg, "km", 0);
  s.vol = (int)jnum(msg, "vol", 80);
  s.ring = jbool(msg, "ring");
  s.mute = jbool(msg, "mute");
  s.fav = jbool(msg, "fav");
  s.idle = jbool(msg, "idle");
  s.liked = jbool(msg, "liked");
  s.bt = jbool(msg, "bt");
  s.borders = jbool(msg, "borders");
  s.cities = jbool(msg, "cities");
  s.spin = jbool(msg, "spin");
  copyText(s.toast, jstr(msg, "toast"), TEXT_BYTES);
  copyText(s.song, jstr(msg, "song"), TEXT_BYTES);
  s.local = jhas(msg, "local") ? (int)jnum(msg, "local", 0) : -1;
  s.clock = jhas(msg, "clock") ? (int)jnum(msg, "clock", 0) : -1;
  s.alarm = jhas(msg, "alarm") ? (int)jnum(msg, "alarm", 0) : -1;
  s.timer = jhas(msg, "timer") ? (int)jnum(msg, "timer", 0) : -1;
  s.sky = -1;
  if (jhas(msg, "sky") && jhas(msg, "temp")) {
    const char *sky = jstr(msg, "sky");
    for (int i = 0; i < SKY_COUNT; i++)
      if (!strcmp(SKY_NAMES[i], sky)) s.sky = i;
    if (s.sky < 0) s.sky = 4;           // a sky this face doesn't know: a plain cloud
    s.temp = (int)lrint(jnum(msg, "temp", 0));
  }
  copyText(s.date, jstr(msg, "date"), sizeof s.date);
  s.bright = (int)jnum(msg, "bright", 100);
  const int panel = jget(msg, "panel");
  s.hasPanel = panel >= 0 && jpool[panel].type == J_OBJ;
  if (s.hasPanel) {
    PanelState &p = s.panel;
    copyText(p.title, jstr(panel, "title"), ITEM_BYTES);
    takeTextArray(jget(panel, "lines"), p.lines, PANEL_LINES, p.lineCount);
    takeTextArray(jget(panel, "notes"), p.notes, PANEL_NOTES, p.noteCount);
    p.itemCount = 0;
    int items = jget(panel, "items");
    if (items >= 0 && jpool[items].type == J_ARR)
      for (int c = jpool[items].child; c >= 0 && p.itemCount < PANEL_ITEMS; c = jpool[c].next) {
        if (jpool[c].type != J_ARR) continue;
        int a = jpool[c].child, b = a >= 0 ? jpool[a].next : -1;
        copyText(p.items[p.itemCount][0], a >= 0 && jpool[a].type == J_STR ? jpool[a].str : "", ITEM_BYTES);
        copyText(p.items[p.itemCount][1], b >= 0 && jpool[b].type == J_STR ? jpool[b].str : "", ITEM_BYTES);
        p.itemCount++;
      }
    p.sel = (int)jnum(panel, "sel", -1);
    copyText(p.qr, jstr(panel, "qr"), sizeof p.qr);
  }
  haveState = true;
  return true;
}

static void handleLine(char *line) {
  jcount = 0;
  char *p = line;
  int msg = parseValue(p, 0);
  if (msg < 0 || jpool[msg].type != J_OBJ) {
    badLines++;
    return;
  }
  const char *t = jstr(msg, "t");
  bool ok = true;
  if (!strcmp(t, "state")) ok = takeState(msg);
  else if (!strcmp(t, "stations")) takeStations(msg);
  else if (!strcmp(t, "favs")) takeFavorites(msg);
  else if (!strcmp(t, "world")) takeWorld(msg);
  else if (!strcmp(t, "lines")) ok = takeLines(msg);
  else if (!strcmp(t, "places")) ok = takePlaces(msg);
  else if (!strcmp(t, "icon")) ok = takeIcon(msg);
  else if (!strcmp(t, "sleep")) {
    sleepRequested = true;
    sleepFade = jnum(msg, "fade", 0);
  } else if (!strcmp(t, "wake")) {
    wakeRequested = true;
    wakeFade = jnum(msg, "fade", 0);
  } else if (!strcmp(t, "stats")) statsRequested = true;
  else if (!strcmp(t, "shot")) shotRequested = true;
  else if (!strcmp(t, "scene")) sceneRequested = true;
  else ok = false;
  if (!ok) {
    badLines++;
    return;
  }
  goodLines++;
  heardAt = nowSeconds();
}

// --- the port ------------------------------------------------------------------------------------
static const int RING_BYTES = 128 * 1024;
static uint8_t *ring;
static volatile uint32_t ringHead, ringTail;   // written by the reader / by the main loop
static volatile uint32_t ringOverflows;   // times the ring was full (the sender was made to wait)

// The board package's driver (HWCDC) copies each incoming byte into a small queue from its interrupt
// and drops what doesn't fit, so a burst like the station list (70 KB) was mostly lost. Instead,
// its receive interrupt is turned off and this task empties the USB chip's 64-byte receive FIFO
// itself, into a 128 KB ring in PSRAM. When the ring is full it stops reading, and the USB chip
// then makes the sender wait (it refuses new packets until the FIFO is read): nothing is lost.
static void readerTask(void *) {
  uint8_t chunk[64];
  for (;;) {
    if (!usb_serial_jtag_ll_rxfifo_data_available()) {
      vTaskDelay(1);                           // nothing yet: look again in a millisecond
      continue;
    }
    int64_t quietSince = esp_timer_get_time();
    for (;;) {                                 // read while packets keep coming
      const uint32_t used = (ringHead - ringTail + RING_BYTES) % RING_BYTES;
      if (RING_BYTES - 1 - used < sizeof chunk) {
        ringOverflows = ringOverflows + 1;     // full: leave it in the FIFO, the sender waits
        break;
      }
      if (usb_serial_jtag_ll_rxfifo_data_available()) {
        const uint32_t n = usb_serial_jtag_ll_read_rxfifo(chunk, sizeof chunk);
        uint32_t head = ringHead;
        for (uint32_t i = 0; i < n; i++) {
          ring[head] = chunk[i];
          head = (head + 1) % RING_BYTES;
        }
        ringHead = head;
        quietSince = esp_timer_get_time();
        continue;
      }
      if (esp_timer_get_time() - quietSince > 300) break;   // a pause: let the drawing have the core
    }
    vTaskDelay(1);
  }
}

static void sayHello() {
  Serial.printf("{\"t\":\"hello\",\"v\":%d}\n", PROTOCOL);
}

static void linkStart() {
  Serial.setRxBufferSize(256);        // unused: readerTask takes the data (see above)
  Serial.setTxBufferSize(4096);
  Serial.begin(921600);
  Serial.setTxTimeoutMs(0);           // never stall a frame on a port nobody reads
  usb_serial_jtag_ll_disable_intr_mask(USB_SERIAL_JTAG_INTR_SERIAL_OUT_RECV_PKT);
  ring = (uint8_t *)heap_caps_malloc(RING_BYTES, MALLOC_CAP_SPIRAM);
  xTaskCreatePinnedToCore(readerTask, "usb rx", 3072, NULL, 5, NULL, 1);
}

// Acts on every whole line that has arrived, for at most `budgetMs`.
static void linkPoll(float budgetMs) {
  static char line[LINE_BYTES + 1];
  static int len;
  static bool tooLong;
  const int64_t until = esp_timer_get_time() + (int64_t)(budgetMs * 1000);
  while (ringTail != ringHead) {
    const char c = ring[ringTail];
    ringTail = (ringTail + 1) % RING_BYTES;
    if (c == '\n') {
      if (!tooLong && len) {
        line[len] = 0;
        handleLine(line);
      }
      len = 0;
      tooLong = false;
      if (esp_timer_get_time() > until) return;
    } else if (len < LINE_BYTES) {
      line[len++] = c;
    } else {
      tooLong = true;
    }
  }
}
