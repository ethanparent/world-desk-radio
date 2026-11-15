"""The trackball's light. The ball has four LEDs under it (red, green, blue and white), so it
can say what the radio is doing without a look at the screen:

    tuning (static)    breathes slowly in the theme's glow color
    playing            a steady, soft glow in the theme's "live" color
    no signal          a faint glow in the warning color
    favorite saved     one bright pulse
    muted              dim white
    idle               very dim, so it doesn't light up a dark room

    light = BallLight()
    r, g, b, w = light.color(face_state, now)    # 0-255 each, for the four LED pins (PWM)

On the laptop, the map window shows the ball as a small dot in its bottom-right corner.
"""

import math

from .themes import THEMES, get_theme

BREATHE_S = 2.4          # one slow breath while tuning
PULSE_S = 0.7            # the flash when a favorite is saved


class BallLight:
    def __init__(self):
        self._toast = ""
        self._pulse_at = None

    def color(self, fs, now, asleep=False):
        """(r, g, b, w) for this FaceState."""
        if asleep:
            return (0, 0, 0, 0)
        try:
            theme = get_theme(fs.theme)
        except ValueError:
            theme = THEMES[0]
        if fs.toast != self._toast:
            self._toast = fs.toast
            if fs.toast.startswith(("Saved as favorite", "Already favorite")):
                self._pulse_at = now

        if fs.mute:
            rgb, level = (255, 255, 255), 0.10
        elif fs.status == "playing":
            rgb, level = theme.live, 0.40
        elif fs.status == "no signal":
            rgb, level = theme.warn, 0.12
        else:   # turning or tuning: breathing
            phase = (now % BREATHE_S) / BREATHE_S
            rgb, level = theme.dot_glow, 0.12 + 0.30 * (0.5 - 0.5 * math.cos(2 * math.pi * phase))
        if fs.idle:
            level *= 0.3
        if self._pulse_at is not None:
            age = now - self._pulse_at
            if age < PULSE_S:
                level = max(level, 1.0 - age / PULSE_S)
                rgb = theme.live
            else:
                self._pulse_at = None
        return to_rgbw(rgb, level * fs.bright / 100)       # the brightness setting dims it too


def to_rgbw(rgb, level):
    """A color at a brightness (0-1) -> the four LEDs: the white LED takes the gray part."""
    r, g, b = (round(c * level) for c in rgb)
    w = min(r, g, b)
    return (r - w, g - w, b - w, w)
