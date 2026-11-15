# Game worlds

> Terradio ships no game content. The tool below reads the files of a copy of the game you own
> and writes the world into `worlds/`, which `.gitignore` keeps out of the repo. Don't share or
> publish what it makes. Fallout is a trademark of Bethesda Softworks; this project isn't
> affiliated with or endorsed by Bethesda.

Besides Earth, the radio can be in a game's world: its map on a pretend planet
that's otherwise all sea, with that game's radio stations ([PLAN.md](../PLAN.md), Stage 7).
Hold both knob buttons, choose **World**, and pick one; the radio starts again
there (a few seconds), at the spot, zoom and theme you last had in it. Each
world keeps its own favorites (`favorites-mojave.json`). A world's zooms fit
its map (the whole map, closer, close up), the dial stops at the map's edge,
the idle screen drifts slowly over the whole map instead of spinning a globe,
and its places are labeled as you zoom in, as Earth's cities are.

- **Making one.** A world comes from your own copy of the game. For Fallout: New
  Vegas:

  ```bash
  python tools/fnv_world.py
  ```

  reads the game's files (it finds the Steam copy; `--game` for another) and
  writes `worlds/mojave/` (about 50 MB): the map (the game's roads and Lake
  Mead's shores, with no outline round the playable area, so the map carries
  on to the edges of the view) and four stations,
  Radio New Vegas, Black Mountain Radio, Mojave Music Radio and Camp Guardian's
  distress call, with their songs and DJ converted to Opus. Every song and line
  is leveled to −16 LUFS as it's converted (the game's songs are mastered about
  11 dB quieter than its voices), so the music is as loud as Earth's stations and
  the DJ no longer jumps out. It also takes the Pip-Boy's own symbols for the
  signal slot: the radio tower on air, the radio wave scrolling while tuning,
  a sad Vault Boy for no signal. Only lines that fit
  the start of the game are used, so the news gives nothing away. It never
  changes the game. `--list` shows what each station will play.
- **Live by the clock.** A world's station plays from files, but like the
  game's radio it has been playing its list round and round since 1 January
  2026, so tuning in lands mid-song and coming back later finds it further on.
  The song shown comes from the same clock (`terradio/worlds.py`).
- **Not in git.** `worlds/` is the game's music and voices, for this radio only,
  so `.gitignore` keeps it out of the repo and the updater leaves it alone.
- **On the Pi,** the worlds go in `~/terradio-worlds/` (the checkout's
  writable spot is too small for them). With the overlay off:

  ```bash
  scp -r worlds/mojave pi@terradio.local:terradio-worlds/
  ```

  Then restart the radio and the World entry appears in the settings menu. The
  face needs `face.ino` flashed again once (protocol 2), since it gets the
  world's map over the cable.
