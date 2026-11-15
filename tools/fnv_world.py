"""Make the radio's Mojave Wasteland world (PLAN.md, Stage 7) from your own copy of Fallout: New Vegas.

    python tools/fnv_world.py                  # finds the Steam copy, writes worlds/mojave/
    python tools/fnv_world.py --game "D:\\Games\\Fallout New Vegas"
    python tools/fnv_world.py --map-only       # only the map (no audio): quick, for trying things
    python tools/fnv_world.py --list           # what each station will play, without writing anything

It reads the game's own files and never changes them:
  - FalloutNV.esm for the map and the stations. The map is the playable Mojave (the game's map
    regions, its places and its roads), but its outline isn't drawn: the land carries on to the
    edges of the view. The coastlines are Lake Mead's shores, wherever the ground dips under the
    water. The roads (every road piece the game places, joined up) are the lines on the land, in
    the borders layer. The stations' songs and DJ lines are the game's own radio
    dialogue, in the order its radio scripts play them. Only lines that fit the start of the game
    are used, so the news doesn't give away the story, nor tell two versions of it.
  - the songs (loose MP3s in Data\\Sound) and the voices (Fallout - Voices1.bsa), converted to Opus.

Where each station is: Black Mountain Radio and Camp Guardian are where the game puts them. Radio New Vegas and Mojave Music Radio broadcast from a hidden test
cell, so they're placed by hand: on the Strip and in Goodsprings. (NCR Emergency Radio is left
out: all it says is the end of the game.)

The world is the game's music and voices, so it stays out of git (.gitignore) and off the internet:
copy it to the Pi by hand (README.md, "Game worlds"). Needs ffmpeg (winget install Gyan.FFmpeg).
"""

import argparse
import collections
import concurrent.futures
import json
import math
import random
import re
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))

from bethesda import BSA, Plugin, heights, position                                    # noqa: E402
from worlddeskradio import worlds                                                         # noqa: E402

STEAM_GAME = Path(r"C:\Program Files (x86)\Steam\steamapps\common\Fallout New Vegas")
WORLD_ID = "mojave"
WORLD_EDID = "WastelandNV"
ACROSS_KM = 1500            # the map's longer side on the pretend planet
DEG_KM = 111.195            # km in a degree of latitude (an Earth-sized planet)
BOX = (-150000, 200000, -160000, 175000)     # game units (x0, x1, y0, y1) the map is worked out in
STEP = 128                  # grid spacing for the map, game units (one terrain point)
KBPS = 64                   # Opus bitrate: every file the same shape, so ffplay can join them
# The game's songs are mastered quiet (about -28 LUFS, the DJ about -17), far under internet radio
# (about -14 to -16). Each song and line is turned up (or down) to TARGET_LUFS as it's converted,
# but never so far that its loudest moment goes past PEAK_DBFS: no limiter, so nothing is squashed.
TARGET_LUFS = -16.0
PEAK_DBFS = -1.5
LEVELS = "levels.json"      # in audio/: the gain each file was converted with (missing: convert it again)

# The stations: (id, the game's quest, name, place, where it is). Positions are game units, or the
# name of the map marker it's at, or "radio" for the quest's own transmitter in the Mojave.
STATIONS = [
    ("radio-new-vegas", "RadioNewVegas", "Radio New Vegas", "The Strip", ("Freeside's North Gate", 0, 5000)),
    ("black-mountain", "RadioBlackMountain", "Black Mountain Radio", "Black Mountain", "radio"),
    ("mojave-music", "vCountryRadioQuest", "Mojave Music Radio", "Goodsprings", ("Goodsprings", 0, 0)),
    ("camp-guardian", "vCampGuardianRadio", "Camp Guardian Radio", "Camp Guardian", ("Camp Guardian", 0, 0)),
]
TITLE_ORDER = ["radio-new-vegas", "mojave-music"]     # whose song names to use first
# Places to label, like Earth's cities: the game's map markers. Each gets labeled from a zoom on
# (map_view: the web-map zoom, less CITY_ZOOM_GAP, that the window's scale matches): the towns
# everyone knows from the whole map, settlements, camps and vaults closer, the rest close up.
# New Vegas itself has no marker on the Mojave's map (the Strip is a world of its own): it's added.
TOWNS = ["New Vegas", "Goodsprings", "Primm", "Novac", "Nipton", "Boulder City", "Hoover Dam", "Jacobstown",
         "Camp McCarran", "Nellis Air Force Base", "Camp Searchlight", "Cottonwood Cove", "Bitter Springs",
         "Red Rock Canyon", "Black Mountain", "Camp Forlorn Hope", "HELIOS One"]
CLOSER_TYPES = {1, 2, 7, 8, 14}      # the markers' kinds: city, settlement, monument, military, vault
PLACE_ZOOMS = (4.0, 5.5, 7.0)        # labeled from the whole map, closer, close up (1,700 / 600 / 200 km)
NEW_VEGAS = ("Freeside's North Gate", 0, 5000)
# Song names the game's own text doesn't give cleanly.
TITLES = {"mus_eddyarnold_rca_itsasin": "Eddy Arnold - It's a Sin"}


def main():
    ap = argparse.ArgumentParser(description="Make worlds/mojave from your copy of Fallout: New Vegas.")
    ap.add_argument("--game", type=Path, default=STEAM_GAME, help="the game's folder (the one with Data in it)")
    ap.add_argument("--out", type=Path, help="where to write the world (default: worlds/mojave)")
    ap.add_argument("--map-only", action="store_true", help="only the map: keep the stations and audio there are")
    ap.add_argument("--list", action="store_true", help="print what each station plays, write nothing")
    args = ap.parse_args()
    data = args.game / "Data"
    if not (data / "FalloutNV.esm").exists():
        sys.exit(f"No FalloutNV.esm in {data}. Point --game at the game's folder.")
    if not args.list and not shutil.which("ffmpeg"):
        sys.exit("ffmpeg is needed for the audio:  winget install Gyan.FFmpeg  (then open a new terminal)")
    out = args.out or worlds.WORLDS_DIR / WORLD_ID

    print("Reading FalloutNV.esm ...", flush=True)
    game = Game(data)
    print("Working out the map ...", flush=True)
    coast, roads, bounds = make_map(game)
    planet = Planet(*bounds)
    print(f"  {len(coast)} coastlines ({sum(len(c) for c in coast):,} points), "
          f"{len(roads)} roads ({sum(len(r) for r in roads):,} points)")
    programs = {sid: Program(game, quest) for sid, quest, *_ in STATIONS}
    # A song is on several stations, its name written differently in each (one even misspelled):
    # Radio New Vegas has them best, then Mojave Music Radio.
    titles = {}
    for sid in sorted(programs, key=lambda s: TITLE_ORDER.index(s) if s in TITLE_ORDER else 99):
        for item in programs[sid].items:
            if item.song:
                item.title = titles.setdefault(item.song, item.title)
    if args.list:
        for sid, quest, name, *_ in STATIONS:
            p = programs[sid]
            print(f"\n{name}: {len(p.items)} items, {sum(1 for i in p.items if i.song)} of them songs")
            for item in p.items[:40]:
                print(f"   {'♪ ' + item.title if item.song else item.text[:90]}")
        return 0

    out.mkdir(parents=True, exist_ok=True)
    audio = out / "audio"
    audio.mkdir(exist_ok=True)
    stations = []
    for sid, quest, name, place, where in STATIONS:
        x, y = game.where(where, quest)
        lat, lon = planet(x, y)
        stations.append({"id": sid, "name": name, "place": place, "lat": lat, "lon": lon})
    if not args.map_only:
        convert_all(game, programs, audio)
        for sid, *_ in STATIONS:
            write_station(out / "stations" / sid, programs[sid], audio)
    places = []
    for name, pos in sorted(game.places().items(), key=lambda kv: kv[1][2]):
        lat, lon = planet(*pos[:2])
        places.append({"name": name, "lat": lat, "lon": lon, "zoom": pos[2]})
    south, west = planet(*bounds[:2])
    north, east = planet(*bounds[2:])
    icons = make_icons(data, out / "icons")
    (out / "world.json").write_text(json.dumps({
        "title": game.world_name, "theme": "radar", "area": [south, north, west, east], "places": places,
        "coast": [[list(planet(x, y)) for x, y in line] for line in coast],
        "borders": [[list(planet(x, y)) for x, y in line] for line in roads],
        "icons": icons,
        "stations": stations,
    }, ensure_ascii=False), encoding="utf-8")
    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    print(f"Wrote {out} ({size / 1e6:.0f} MB).")
    return 0


# --- the Pip-Boy's own symbols for the signal slot ------------------------------------------------

# (state, texture in Fallout - Textures2.bsa, the box it's fitted to at 480 px, brightness gain)
ICON_TEXTURES = [
    ("playing", r"textures\interface\icons\message icons\glow_message_radio_tower.dds", (40, 40), 1.0),
    ("tuning", r"textures\interface\radio wave\wave.dds", (None, 34), 2.0),
    ("no signal", r"textures\interface\icons\message icons\glow_message_vaultboy_sad.dds", (38, 38), 1.0),
]
WAVE_WINDOW = 64            # px of the radio wave shown at once (at 480 px); the rest scrolls past


def make_icons(data, folder):
    """The radio tower (on air), the Pip-Boy's radio wave (tuning: a strip that scrolls) and a sad
    Vault Boy (no signal) as 8-bit masks in folder/, drawn in the theme's colors like the usual
    symbols. Returns world.json's "icons": {state: {"file": ..., "window": px or absent}}."""
    import io
    from PIL import Image
    bsa = BSA(data / "Fallout - Textures2.bsa")
    folder.mkdir(parents=True, exist_ok=True)
    out = {}
    for state, texture, (w, h), gain in ICON_TEXTURES:
        im = Image.open(io.BytesIO(bsa.read(texture))).convert("RGBA")
        lum, alpha = im.convert("L"), im.getchannel("A")
        mask = Image.composite(lum, Image.new("L", im.size), alpha)        # glow where it's drawn
        mask = mask.crop(mask.getbbox())
        if w is None:                                   # the wave: as tall as h, as long as it is
            w = round(mask.width * h / mask.height)
        mask.thumbnail((w, h), Image.LANCZOS)
        if gain != 1:                                   # thin lines go faint when shrunk
            mask = mask.point(lambda v: min(255, round(v * gain)))
        entry = {"file": f"icons/{state.replace(' ', '-')}.png"}
        if state == "tuning":
            # The strip goes round: its first WAVE_WINDOW px again at the end, so a window can
            # slide along it and start over without a jump in what's drawn.
            loop = mask.width
            strip = Image.new("L", (loop + WAVE_WINDOW, mask.height))
            strip.paste(mask, (0, 0))
            strip.paste(mask.crop((0, 0, WAVE_WINDOW, mask.height)), (loop, 0))
            mask, entry["window"] = strip, WAVE_WINDOW
        mask.save(folder / Path(entry["file"]).name)
        out[state] = entry
    print(f"  Pip-Boy symbols: {', '.join(out)}")
    return out


# --- the game's data ------------------------------------------------------------------------------

class Game:
    """What the tool needs from FalloutNV.esm and the archives, read once."""

    def __init__(self, data):
        self.data = data
        recs = collections.defaultdict(dict)
        self.cells, self.lands, self.markers, self.road_refs, self.radio_refs = {}, {}, {}, [], {}
        self.marker_types = {}
        self.regions = []
        esm = Plugin(data / "FalloutNV.esm")
        for r in esm.records({"WRLD", "QUST", "DIAL", "INFO", "SOUN", "STAT", "TACT", "REGN", "CELL", "LAND",
                              "REFR"}):
            if r.type in ("CELL", "LAND", "REFR"):
                continue
            recs[r.type][r.form_id] = r
        self.world = next(k for k, w in recs["WRLD"].items() if w.text("EDID") == WORLD_EDID)
        self.world_name = recs["WRLD"][self.world].text("FULL") or "Mojave Wasteland"
        roads = {k for k, s in recs["STAT"].items()
                 if re.search(r"\\roads\\|\\highway\\", s.text("MODL").lower())}
        tact_quest = {}
        for r in esm.records({"CELL", "LAND", "REFR"}):
            if r.world != self.world:
                continue
            if r.type == "CELL" and r.get("XCLC"):
                x, y = struct.unpack_from("<ii", r.get("XCLC"))
                w = r.get("XCLW")
                level = struct.unpack_from("<f", w)[0] if w else None
                self.cells[r.form_id] = (x, y, level if level is not None and abs(level) < 1e6 else None)
            elif r.type == "LAND":
                self.lands[r.cell] = r
            elif r.type == "REFR":
                base, pos = r.uint("NAME"), position(r)
                if r.get("XMRK") is not None:
                    self.markers[r.text("FULL")] = pos
                    tnam = r.get("TNAM")
                    self.marker_types[r.text("FULL")] = tnam[0] if tnam else 0
                elif base in roads and pos:
                    self.road_refs.append(pos[:2])
                elif r.get("XRDO") is not None and base in recs["TACT"]:
                    self.radio_refs[recs["TACT"][base].text("EDID")] = pos
        self.regions = [r for r in recs["REGN"].values()
                        if r.uint("WNAM") == self.world and "border" not in r.text("EDID").lower()]
        self.quests = {k: q.text("EDID") for k, q in recs["QUST"].items()}
        self.quest_ids = {v: k for k, v in self.quests.items()}
        self.topics = {k: d.text("EDID") for k, d in recs["DIAL"].items()}
        self.souns = {k: s.text("FNAM") for k, s in recs["SOUN"].items()}
        self.infos = recs["INFO"]
        self._voices = None

    def where(self, where, quest):
        """A station's spot in game units (STATIONS)."""
        if where == "radio":
            # The transmitter of the quest's station: Black Mountain's TACT is "RadioBlackMountainTA".
            for edid, pos in self.radio_refs.items():
                if edid.lower().startswith(quest.lower()):
                    return pos[:2]
            raise SystemExit(f"No transmitter for {quest} in the Mojave")
        marker, dx, dy = where
        x, y = self.markers[marker][:2]
        return x + dx, y + dy

    def places(self):
        """{name: (x, y, zoom)}: the map markers to label, and the web-map zoom each is labeled from."""
        out = {"New Vegas": (*self.where(NEW_VEGAS, None), PLACE_ZOOMS[0])}
        for name, pos in self.markers.items():
            if not name:
                continue
            tier = 0 if name in TOWNS else 1 if self.marker_types.get(name) in CLOSER_TYPES else 2
            out[name] = (pos[0], pos[1], PLACE_ZOOMS[tier] + (TOWNS.index(name) if tier == 0 else 0) * 0.001)
        return out

    def infos_of(self, quest, topic):
        q, out = self.quest_ids[quest], []
        for i in self.infos.values():
            if i.uint("QSTI") == q and self.topics.get(i.topic) == topic:
                out.append(i)
        return sorted(out, key=lambda i: i.form_id)

    # --- the audio ---------------------------------------------------------------------------------

    def voice_files(self, info):
        """An INFO's voice files in the archive, in response order (one per line it says)."""
        if self._voices is None:
            self._voices = collections.defaultdict(list)
            self._voice_bsa = BSA(self.data / "Fallout - Voices1.bsa")
            for name in self._voice_bsa.names():
                m = re.search(r"_([0-9a-f]{8})_(\d+)\.ogg$", name, re.I)
                if m:
                    self._voices[int(m.group(1), 16)].append((int(m.group(2)), name))
        found = self._voices.get(info.form_id, [])
        if not found:
            return []
        # One speaker's folder (a line can be voiced for several), its responses in order.
        folders = collections.Counter(n.rsplit("\\", 1)[0] for _, n in found)
        folder = folders.most_common(1)[0][0]
        return [n for _, n in sorted(found) if n.startswith(folder)]

    def read_voice(self, name):
        return self._voice_bsa.read(name)

    def song_file(self, soun):
        """A song's file: the loose MP3 in Data\\Sound (full quality), else the archive's copy."""
        fnam = self.souns.get(soun, "")
        if not fnam:
            return None
        loose = self.data / "Sound" / fnam
        if loose.exists():
            return loose
        folder = loose.parent
        if folder.exists():
            for f in folder.iterdir():
                if f.name.lower() == loose.name.lower():
                    return f
        return None


def song_of(info):
    """The SOUN an INFO plays (its response data's sound), or 0."""
    trdt = info.get("TRDT")
    return struct.unpack_from("<I", trdt, 16)[0] if trdt and len(trdt) >= 20 else 0


# --- conditions: the start of the game ---------------------------------------------------------------

GET_QUEST_VARIABLE, GET_GLOBAL_VALUE, GET_STAGE, GET_STAGE_DONE = 79, 74, 58, 59
GET_QUEST_COMPLETED = 546
OPS = [lambda a, b: a == b, lambda a, b: a != b, lambda a, b: a > b, lambda a, b: a >= b,
       lambda a, b: a < b, lambda a, b: a <= b]


def conditions(info):
    """[(compare, value, function, param1, param2, or_next)] from an INFO's CTDA subrecords."""
    out = []
    for kind, v in info.subrecords():
        if kind == "CTDA" and len(v) >= 20:
            op = v[0]
            value = struct.unpack_from("<f", v, 4)[0] if not op & 0x04 else 0.0   # 0x04: compared to a global
            func, p1, p2 = struct.unpack_from("<III", v, 8)
            out.append((op >> 5, value, func, p1, p2, bool(op & 0x01)))
    return out


def early(info, own_quest, var_equals=None):
    """Would this line play at the start of the game? Every game event is still to come (globals,
    quest stages and other quests' variables 0). The station's own variables only order its
    lines, so they pass, except var_equals (index, value), which must hold."""
    def one(compare, value, func, p1, p2):
        if func == GET_QUEST_VARIABLE and p1 == own_quest:
            if var_equals is not None and p2 == var_equals[0]:
                return compare == 0 and value == var_equals[1]
            return True
        if func in (GET_QUEST_VARIABLE, GET_GLOBAL_VALUE, GET_STAGE, GET_STAGE_DONE, GET_QUEST_COMPLETED):
            return OPS[compare](0.0, value) if compare < len(OPS) else True
        return True
    # Conditions joined by OR bind first, then AND: (A or B) and (C or D).
    result, group = True, None
    for compare, value, func, p1, p2, or_next in conditions(info):
        ok = one(compare, value, func, p1, p2)
        group = ok if group is None else group or ok
        if not or_next:
            result, group = result and group, None
    if group is not None:
        result = result and group
    return result


def own_var(info, own_quest, index):
    """The value this line wants the station's variable `index` to have (== or !=), or None."""
    for compare, value, func, p1, p2, _ in conditions(info):
        if func == GET_QUEST_VARIABLE and p1 == own_quest and p2 == index and compare in (0, 1):
            return int(value)
    return None


def sets_story(info):
    """The story number a line's script sets (RadioNewVegas.nStory), for its follow-up; 0 if none."""
    m = re.search(r"nStory\s+to\s+(\d+)", info.text("SCTX"), re.I)
    return int(m.group(1)) if m else 0


# --- what each station plays -----------------------------------------------------------------------

class Item:
    """One thing on air: a song (soun) or a DJ line (an INFO, with its voice files), or a pause."""

    def __init__(self, info=None, song=0, title="", text="", gap=0.0):
        self.info, self.song, self.title, self.text, self.gap = info, song, title, text, gap

    @property
    def key(self):
        return ("song", self.song) if self.song else ("gap", self.gap) if self.gap else ("info", self.info.form_id)


class Program:
    """A station's list, in airing order, built the way its radio script plays: the songs with
    the DJ's introductions, news between them, segments, a sign-off. It's played round and round."""

    def __init__(self, game, quest):
        self.game, self.quest = game, game.quest_ids[quest]
        self.rng = random.Random(f"worlddeskradio-{quest}")
        build = {"RadioNewVegas": self._radio_new_vegas, "RadioBlackMountain": self._black_mountain,
                 "vCountryRadioQuest": self._songs_only}.get(quest, self._broadcast)
        self.items = build(quest)

    def _say(self, info):
        return Item(info=info, text=re.sub(r"\s+", " ", info.text("NAM1")).strip())

    def _songs(self, quest, topic):
        out, seen = [], set()
        for i in self.game.infos_of(quest, topic):
            soun = song_of(i)
            if soun and soun not in seen and self.game.song_file(soun):
                seen.add(soun)
                out.append((i, Item(song=soun, title=self._title(i, soun))))
        return out

    def _title(self, info, soun):
        stem = Path(self.game.souns[soun].replace("\\", "/")).stem.lower()
        if stem in TITLES:
            return TITLES[stem]
        text = re.sub(r"[{}]", "", info.text("NAM1")).strip()
        text = re.sub(r"^song\s*\d+\s*(-\s*)?", "", text, flags=re.I).strip()
        if len(text) > 2:
            return text
        words = re.sub(r"^mus_", "", stem).split("_")
        return " ".join(w.capitalize() for w in words if w)

    def _lines(self, quest, topic, **kw):
        return [i for i in self.game.infos_of(quest, topic) if early(i, self.quest, **kw)
                and self.game.voice_files(i)]

    # Radio New Vegas: Mr. New Vegas says hello, introduces songs (some by name), reads the news.
    def _radio_new_vegas(self, quest):
        songs = self._songs(quest, "RNVMusic")
        by_id = {own_var(i, self.quest, 4): item for i, item in songs}
        specific = {own_var(i, self.quest, 4): i for i in self._lines(quest, "RNVMusicIntroSpecific")}
        generic = self._lines(quest, "RNVMusicIntroGeneric")
        hellos = self._lines(quest, "RadioHello")
        news_intros, posts = self._lines(quest, "RNVNewsIntro"), self._lines(quest, "RNVNewsPost")
        transitions = self._lines(quest, "RNVNewsTransition")
        follow = {}
        for topic in ("RNVNewsStory2", "RNVNewsStory3"):
            for i in self.game.infos_of(quest, topic):
                n = own_var(i, self.quest, 1)
                if n and early(i, self.quest, var_equals=(1, n)) and self.game.voice_files(i):
                    follow.setdefault(n, i)
        stories = []
        for i in self._lines(quest, "RNVNewsStory"):
            chain, n = [i], sets_story(i)
            while n and n in follow and len(chain) < 6:
                chain.append(follow[n])
                n = sets_story(follow[n])
            stories.append(chain)
        rng = self.rng
        rng.shuffle(stories)
        out = [self._say(rng.choice(hellos))] if hellos else []
        order = [k for k in by_id if k is not None]
        played = 0
        while stories or played < len(order):
            rng.shuffle(order)
            for k in order:
                if k in specific and rng.random() < 0.6:
                    out.append(self._say(specific[k]))
                elif generic and rng.random() < 0.5:
                    out.append(self._say(rng.choice(generic)))
                out.append(by_id[k])
                played += 1
                if played % 3 == 0 and stories:
                    block = [rng.choice(news_intros)] if news_intros else []
                    for n in range(min(len(stories), rng.choice((1, 2, 2)))):
                        if n and transitions:
                            block.append(rng.choice(transitions))
                        block += stories.pop()
                    if posts:
                        block.append(rng.choice(posts))
                    out += [self._say(i) for i in block]
                if played % 10 == 0 and hellos:
                    out.append(self._say(rng.choice(hellos)))
        return out

    # Black Mountain Radio: Rhonda and Tabitha's segments, music between them.
    def _black_mountain(self, quest):
        songs = [item for _, item in self._songs(quest, "RadioBMMusic")]
        intros = self._lines(quest, "RadioBMMusicIntros")
        segments = []
        for n in range(1, 20):
            topic = f"RadioBMSegment{n:02d}"
            lines = [i for i in self._lines(quest, topic)]
            lines.sort(key=lambda i: own_var(i, self.quest, 6) or 0)
            if lines:
                segments.append(lines)
        rng = self.rng
        rng.shuffle(songs)
        out, s = [], 0
        for seg in segments:
            out += [self._say(i) for i in seg]
            if intros:
                out.append(self._say(rng.choice(intros)))
            for _ in range(2):
                out.append(songs[s % len(songs)])
                s += 1
        while s < len(songs):
            out.append(songs[s])
            s += 1
        return out

    # Mojave Music Radio: just the songs.
    def _songs_only(self, quest):
        songs = [item for _, item in self._songs(quest, "RadioHello")]
        self.rng.shuffle(songs)
        return songs

    # The emergency broadcasts: their messages in turn, a pause between, a sign-off, round again.
    def _broadcast(self, quest):
        out = []
        for i in self._lines(quest, "RadioHello"):
            out += [self._say(i), Item(gap=4.0)]
        for i in self._lines(quest, "RadioGoodbye"):
            out += [self._say(i), Item(gap=12.0)]
        return out


# --- audio files -------------------------------------------------------------------------------------

def file_names(item):
    """The Opus files an item plays, in order."""
    if item.song:
        return [f"song-{item.song:08x}.opus"]
    if item.gap:
        return [f"gap-{item.gap:g}.opus"]
    return [f"v-{item.info.form_id:08x}-{n}.opus" for n in range(len(ITEM_VOICES[item.info.form_id]))]


ITEM_VOICES = {}


def convert_all(game, programs, audio):
    """Every song, line and pause the stations play, once each, as Opus (already there: kept)."""
    jobs = {}
    for p in programs.values():
        for item in p.items:
            if item.song:
                jobs[audio / file_names(item)[0]] = ("file", game.song_file(item.song))
            elif item.gap:
                jobs[audio / file_names(item)[0]] = ("gap", item.gap)
            else:
                voices = ITEM_VOICES.setdefault(item.info.form_id, game.voice_files(item.info))
                for name, src in zip(file_names(item), voices):
                    jobs[audio / name] = ("voice", src)
    try:
        levels = json.loads((audio / LEVELS).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        levels = {}
    if levels.get("target") != TARGET_LUFS or levels.get("peak") != PEAK_DBFS:
        levels = {"target": TARGET_LUFS, "peak": PEAK_DBFS, "gain_db": {}}
    gains = levels["gain_db"]
    # Not there yet, or converted before it was leveled (or at another level): convert it.
    todo = {out: job for out, job in jobs.items()
            if not out.exists() or (job[0] != "gap" and out.name not in gains)}
    print(f"Converting {len(todo)} of {len(jobs)} audio files ...", flush=True)
    voices = {out: game.read_voice(src) for out, (kind, src) in todo.items() if kind == "voice"}

    def one(out):
        kind, src = todo[out]
        if kind == "gap":
            source = ["-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo", "-t", str(src)]
            ffmpeg(source, out)
            return
        source = ["-i", str(src)] if kind == "file" else ["-f", "ogg", "-i", "pipe:0"]
        stdin = voices.get(out)
        gain = level_gain(source, stdin)
        ffmpeg(source, out, stdin=stdin, gain_db=gain)
        gains[out.name] = gain
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        for n, _ in enumerate(pool.map(one, todo), 1):
            if n % 50 == 0:
                print(f"  {n} / {len(todo)}", flush=True)
    (audio / LEVELS).write_text(json.dumps(levels, indent=1, sort_keys=True), encoding="utf-8")
    done = list(gains.values())
    if done:
        print(f"  leveled to {TARGET_LUFS:g} LUFS: gains {min(done):+.1f} to {max(done):+.1f} dB")


def level_gain(source, stdin=None):
    """dB to turn a source by so it sits at TARGET_LUFS, as far as its true peak allows (ffmpeg's
    EBU R128 meter). 0 for silence, or if the meter can't read it."""
    out = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", *source, "-vn", "-af", "ebur128=peak=true",
                          "-f", "null", "-"], input=stdin, capture_output=True,
                         creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
    text = out.stderr.decode("utf-8", "replace")
    loud = re.findall(r"I:\s+(-?[\d.]+) LUFS", text)
    peak = re.findall(r"Peak:\s+(-?[\d.]+|-inf) dBFS", text)
    if not loud or not peak or peak[-1] == "-inf" or float(loud[-1]) <= -69:
        return 0.0
    return round(min(TARGET_LUFS - float(loud[-1]), PEAK_DBFS - float(peak[-1])), 1)


def ffmpeg(source, out, stdin=None, gain_db=0.0):
    tmp = out.with_suffix(".part.opus")
    level = ["-af", f"volume={gain_db:.1f}dB"] if gain_db else []
    subprocess.run(["ffmpeg", "-v", "error", "-y", *source, "-vn", *level, "-ac", "2", "-ar", "48000", "-c:a", "libopus",
                    "-b:a", f"{KBPS}k", "-map_metadata", "-1", str(tmp)], input=stdin, check=True,
                   creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
    tmp.replace(out)


def write_station(folder, program, audio):
    """stations/<id>/playlist.json: the program's files (in audio/) with their lengths and songs."""
    folder.mkdir(parents=True, exist_ok=True)
    lengths = {}
    rows = []
    for item in program.items:
        if not item.song and not item.gap:
            ITEM_VOICES.setdefault(item.info.form_id, program.game.voice_files(item.info))
        for name in file_names(item):
            path = audio / name
            if not path.exists():
                continue
            if name not in lengths:
                lengths[name] = round(worlds.seconds_of(path), 3)
            rows.append({"file": f"../../audio/{name}", "seconds": lengths[name],
                         "title": item.title if item.song else ""})
    (folder / "playlist.json").write_text(json.dumps({"tracks": rows}, indent=1, ensure_ascii=False),
                                          encoding="utf-8")
    total = sum(r["seconds"] for r in rows)
    print(f"  {folder.name}: {len(rows)} files, {total / 3600:.1f} hours round")


# --- the map --------------------------------------------------------------------------------------------

class Planet:
    """Game units (x east, y north) -> (lat, lon) on the radio's Earth-sized pretend planet: the map
    centered on 0° 0°, its longer side ACROSS_KM long. Near the equator a degree is about as wide
    as it is tall, so the map keeps its shape."""

    def __init__(self, x0, y0, x1, y1, across_km=ACROSS_KM):
        self.cx, self.cy = (x0 + x1) / 2, (y0 + y1) / 2
        self.k = across_km / DEG_KM / max(x1 - x0, y1 - y0)

    def __call__(self, x, y):
        return round((y - self.cy) * self.k, 5), round((x - self.cx) * self.k, 5)


def make_map(game):
    """(shorelines, roads, (x0, y0, x1, y1)): lines in game units, and the playable land's bounding
    box. The playable Mojave's outline is worked out only for that box (where the map is, and its
    zooms): it isn't drawn, so the map carries on to the edges of the view. The shorelines are Lake
    Mead's, everywhere the game has water, past the playable edge too."""
    import contourpy
    from matplotlib.path import Path as MPath
    from scipy import ndimage

    x0, x1, y0, y1 = BOX
    nx, ny = (x1 - x0) // STEP + 1, (y1 - y0) // STEP + 1
    height = np.full((ny, nx), np.nan, np.float32)
    level = np.full((ny, nx), np.nan, np.float32)
    for fid, (cx, cy, wl) in game.cells.items():
        c0, r0 = (cx * 4096 - x0) // STEP, (cy * 4096 - y0) // STEP
        if c0 <= -33 or r0 <= -33 or c0 >= nx or r0 >= ny or fid not in game.lands:
            continue
        h = heights(game.lands[fid])
        if h is None:
            continue
        a = np.array(h, np.float32)
        rs, cs = slice(max(r0, 0), min(r0 + 33, ny)), slice(max(c0, 0), min(c0 + 33, nx))
        height[rs, cs] = a[rs.start - r0:rs.stop - r0, cs.start - c0:cs.stop - c0]
        if wl is not None:
            level[rs, cs] = wl

    def grid(points):
        p = np.asarray(points, np.float64)
        return np.column_stack([(p[:, 0] - x0) / STEP, (p[:, 1] - y0) / STEP])

    # The playable Mojave: the game's map regions, and around its places and roads.
    yy, xx = np.mgrid[0:ny, 0:nx]
    centers = np.column_stack([xx.ravel(), yy.ravel()]) * STEP + [x0, y0]
    land = np.zeros(ny * nx, bool)
    for r in game.regions:
        for kind, v in r.subrecords():
            if kind == "RPLD":
                poly = np.frombuffer(v, "<f4").reshape(-1, 2)
                if len(poly) >= 3:
                    land |= MPath(poly).contains_points(centers)
    land = land.reshape(ny, nx)
    near = np.zeros((ny, nx), bool)
    for (gx, gy), radius in [(p, 7000) for p in grid([m[:2] for m in game.markers.values()])] + \
                            [(p, 3000) for p in grid(game.road_refs)]:
        r = int(radius / STEP)
        cx, cy = int(gx), int(gy)
        if 0 <= cx < nx and 0 <= cy < ny:
            near[max(cy - r, 0):cy + r + 1, max(cx - r, 0):cx + r + 1] = True
    land = ndimage.binary_closing(land | near, iterations=10)
    land = ndimage.binary_fill_holes(land)
    land = ndimage.gaussian_filter(land.astype(np.float32), 20) > 0.5
    labels, n = ndimage.label(land)                  # the main land only: no stray bits offshore
    if n > 1:
        land = labels == 1 + int(np.argmax(ndimage.sum(land, labels, range(1, n + 1))))
    # Lake Mead and the Colorado: wherever the ground is under the water, in big enough pieces.
    wet = np.nan_to_num(level, nan=-1e9) > np.nan_to_num(height, nan=1e9)
    labels, n = ndimage.label(wet)
    sizes = ndimage.sum(wet, labels, range(1, n + 1))
    wet = np.isin(labels, 1 + np.flatnonzero(sizes >= 400))
    # The box: the playable land's outline, with the lake cut out (as the map has always been
    # placed, so the stations and favorites stay where they were).
    shape = ndimage.gaussian_filter((land & ~wet).astype(np.float32), 1.5)
    outline = [simplify(line * STEP + [x0, y0], 150) for line in
               contourpy.contour_generator(z=shape).lines(0.5) if len(line) >= 20]
    pts = np.concatenate([line for line in outline if len(line) >= 4])
    bounds = (pts[:, 0].min(), pts[:, 1].min(), pts[:, 0].max(), pts[:, 1].max())
    # The shores. Where the game's water stops at a cell's edge (a cell beyond it has none), the
    # line is left open there, as if the water carried on, rather than drawn straight along the cell.
    cut = wet & ndimage.binary_dilation(np.isnan(level))
    cut = ndimage.binary_dilation(cut, iterations=4)
    shape = ndimage.gaussian_filter((~wet).astype(np.float32), 1.5)
    coast = []
    for line in contourpy.contour_generator(z=shape).lines(0.5):
        if len(line) < 20:
            continue
        ix = np.clip(np.round(line).astype(int), 0, [nx - 1, ny - 1])
        keep = ~cut[ix[:, 1], ix[:, 0]]
        # Runs of kept points, each its own line.
        edges = np.flatnonzero(np.diff(np.concatenate(([0], keep.astype(np.int8), [0]))))
        for a, b in zip(edges[::2], edges[1::2]):
            if b - a >= 8:
                coast.append(simplify(line[a:b] * STEP + [x0, y0], 150))
    coast = [line for line in coast if len(line) >= 2]
    return coast, join_roads(np.asarray(game.road_refs, np.float64)), bounds


def join_roads(points, cluster=900, reach=5500, loop=4000, least=4000):
    """The road pieces (their centers) joined into lines. Pieces close together (a junction, a
    wide road's two halves) become one point; each point joins its neighbors along the road (a
    minimum spanning tree within `reach`), plus the links of up to `loop` that close a loop the
    tree went the long way round (a city block); bits of road shorter than `least` are left out."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components, minimum_spanning_tree
    from scipy.spatial import cKDTree

    # Pieces within `cluster` of each other: one point, their average.
    # (Squares of `cluster` on a side: chaining near neighbors would merge a whole road into one.)
    _, groups = np.unique(np.floor(points / cluster), axis=0, return_inverse=True)
    groups = groups.ravel()
    count = np.bincount(groups)
    points = np.column_stack([np.bincount(groups, points[:, k]) / count for k in (0, 1)])
    tree = cKDTree(points)
    pairs = np.array(sorted(tree.query_pairs(reach)))
    d = np.linalg.norm(points[pairs[:, 0]] - points[pairs[:, 1]], axis=1)
    n = len(points)
    mst = minimum_spanning_tree(coo_matrix((d + 1e-3, (pairs[:, 0], pairs[:, 1])), shape=(n, n))).tocoo()
    edges = {(min(a, b), max(a, b)) for a, b in zip(mst.row.tolist(), mst.col.tolist())}
    adj = collections.defaultdict(set)
    for a, b in edges:
        adj[a].add(b)
        adj[b].add(a)

    def hops(a, b, limit=6):
        """How many tree links from a to b, if no more than limit."""
        seen, frontier = {a}, [a]
        for h in range(1, limit + 1):
            frontier = [m for v in frontier for m in adj[v] if m not in seen]
            if b in frontier:
                return h
            seen.update(frontier)
        return None
    for (a, b), dist in sorted(zip(pairs.tolist(), d), key=lambda x: x[1]):
        if dist <= loop and (a, b) not in edges and hops(a, b) is None:
            edges.add((a, b))
            adj[a].add(b)
            adj[b].add(a)
    # Leave out bits of road on their own (a car park, a ramp): pieces shorter than `least`.
    e = np.array(sorted(edges))
    comp_n, comp = connected_components(coo_matrix((np.ones(len(e)), (e[:, 0], e[:, 1])), shape=(n, n)),
                                        directed=False)
    length = collections.Counter()
    for a, b in e:
        length[comp[a]] += np.linalg.norm(points[a] - points[b])
    keep = {(a, b) for a, b in edges if length[comp[a]] >= least}
    # Chain the edges into lines: walk from every point that isn't simply the middle of a road.
    nbrs = collections.defaultdict(set)
    for a, b in keep:
        nbrs[a].add(b)
        nbrs[b].add(a)
    used, lines = set(), []
    for start in sorted(nbrs, key=lambda v: len(nbrs[v]) == 2):
        for nxt in sorted(nbrs[start]):
            if (min(start, nxt), max(start, nxt)) in used:
                continue
            line, prev, cur = [start], start, nxt
            used.add((min(start, nxt), max(start, nxt)))
            while True:
                line.append(cur)
                if len(nbrs[cur]) != 2:
                    break
                (after,) = nbrs[cur] - {prev}
                key = (min(cur, after), max(cur, after))
                if key in used:
                    line.append(after)
                    break
                used.add(key)
                prev, cur = cur, after
            lines.append(simplify(points[line], 200))
    return lines


def simplify(line, tolerance):
    """Douglas-Peucker: fewer points, within `tolerance` of the line."""
    line = np.asarray(line, np.float64)
    if len(line) < 3:
        return line
    keep = np.zeros(len(line), bool)
    keep[[0, -1]] = True
    stack = [(0, len(line) - 1)]
    while stack:
        a, b = stack.pop()
        if b - a < 2:
            continue
        p, q = line[a], line[b]
        seg = q - p
        rest = line[a + 1:b] - p
        norm = math.hypot(*seg)
        dist = np.abs(seg[0] * rest[:, 1] - seg[1] * rest[:, 0]) / norm if norm else np.hypot(rest[:, 0], rest[:, 1])
        i = int(np.argmax(dist))
        if dist[i] > tolerance:
            k = a + 1 + i
            keep[k] = True
            stack += [(a, k), (k, b)]
    return line[keep]


if __name__ == "__main__":
    sys.exit(main())
