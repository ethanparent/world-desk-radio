"""The map's color themes. The trackball's light (ball_light.py) uses them too, and
tools/face_data.py copies them into the face's C headers.

    theme = get_theme("radar")
    theme.live                  -> (130, 255, 150)
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Theme:
    """One look for the map. Colors are (R, G, B); the crosshair's is (R, G, B, opacity)."""
    name: str
    space: tuple          # around the globe in the whole-world view
    globe: tuple          # the globe's surface
    atmosphere: tuple     # the thin glow at the globe's edge
    coast: tuple          # coastlines when zoomed in
    coast_world: tuple    # coastlines in the whole-world view (dimmer: there are more of them)
    grid: tuple           # latitude/longitude lines
    dot_core: tuple       # the middle of each station dot
    dot_glow: tuple       # the glow around the dots
    text: tuple
    text_dim: tuple
    live: tuple           # "on air", the station ring, the volume ring
    warn: tuple           # "no signal"
    crosshair: tuple
    track: tuple          # the unlit part of the volume ring
    border: tuple         # borders between countries: fainter than the coastlines
    shade: tuple = (0, 0, 0)   # what the window's edge and the text bands fade towards


THEMES = (
    # Night: near-black space, a deep navy globe, faint teal coastlines, warm amber stations.
    Theme("night", space=(2, 3, 7), globe=(10, 19, 36), atmosphere=(34, 78, 140),
          coast=(54, 92, 112), coast_world=(44, 76, 96), grid=(17, 29, 46),
          dot_core=(255, 232, 190), dot_glow=(255, 136, 36),
          text=(238, 241, 245), text_dim=(138, 154, 170), live=(255, 186, 84), warn=(232, 112, 92),
          crosshair=(226, 233, 240, 200), track=(52, 62, 74), border=(40, 69, 90)),
    # Radar: green phosphor, like an old radar scope.
    Theme("radar", space=(0, 4, 2), globe=(3, 20, 12), atmosphere=(20, 110, 60),
          coast=(40, 150, 85), coast_world=(32, 120, 70), grid=(10, 46, 26),
          dot_core=(215, 255, 215), dot_glow=(40, 230, 100),
          text=(205, 255, 215), text_dim=(90, 170, 115), live=(130, 255, 150), warn=(255, 140, 90),
          crosshair=(190, 255, 205, 200), track=(22, 64, 38), border=(26, 100, 58)),
    # Ice: cool and quiet, white-blue stations on charcoal.
    Theme("ice", space=(2, 3, 5), globe=(12, 15, 20), atmosphere=(70, 120, 170),
          coast=(105, 125, 145), coast_world=(85, 102, 120), grid=(24, 30, 40),
          dot_core=(235, 248, 255), dot_glow=(60, 160, 255),
          text=(240, 245, 250), text_dim=(130, 145, 160), live=(120, 200, 255), warn=(240, 120, 110),
          crosshair=(230, 240, 250, 200), track=(45, 55, 68), border=(68, 82, 97)),
    # Paper: a light, old-atlas look with ink coastlines and red-orange stations.
    Theme("paper", space=(214, 205, 184), globe=(238, 231, 213), atmosphere=(0, 0, 0),
          coast=(70, 92, 112), coast_world=(95, 110, 125), grid=(216, 207, 186),
          dot_core=(170, 30, 20), dot_glow=(235, 110, 50),
          text=(34, 38, 46), text_dim=(105, 105, 110), live=(200, 70, 30), warn=(190, 40, 40),
          crosshair=(40, 46, 58, 210), track=(205, 198, 180), border=(152, 158, 160),
          shade=(246, 241, 228)),
    # Mono: black and white, nothing else.
    Theme("mono", space=(0, 0, 0), globe=(14, 14, 14), atmosphere=(90, 90, 90),
          coast=(95, 95, 95), coast_world=(75, 75, 75), grid=(30, 30, 30),
          dot_core=(255, 255, 255), dot_glow=(170, 170, 170),
          text=(240, 240, 240), text_dim=(140, 140, 140), live=(255, 255, 255), warn=(230, 110, 100),
          crosshair=(230, 230, 230, 200), track=(55, 55, 55), border=(62, 62, 62)),
)


def get_theme(name):
    for theme in THEMES:
        if theme.name == name:
            return theme
    raise ValueError(f"No theme called {name!r}. Themes: {', '.join(t.name for t in THEMES)}")
