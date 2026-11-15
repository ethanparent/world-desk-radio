"""Where finished frames go: a window on the laptop.

The real radio's face draws the map itself (the Pi just sends it the dial state),
so a Pi-side backend is only needed for the 1.28" fallback screen.

On the laptop this previews the round face. Backends take square RGB Pillow images
from map_view (480x480 for the face):

    display = PygameWindow(scale=1)  # a 480x480 window
    display.show(image)
    for event in display.events():  # the laptop window also collects keys and mouse moves
        ...
    display.close()
"""

import os
import sys
import time

SIZE = (480, 480)


class Display:
    """The interface. A backend for the fallback screen would subclass this."""

    size = SIZE

    def show(self, image):
        raise NotImplementedError

    def events(self):
        """Input since the last call. Only the laptop window has a keyboard and mouse."""
        return []

    def close(self):
        pass


class PygameWindow(Display):
    """A desktop window showing the screen's pixels enlarged `scale` times.

    Pixels are enlarged as crisp squares, not smoothed, so what you see is what
    the real screen will show. (The real face is 2.1-2.8 inches across, so smaller.)
    """

    # Keys that repeat while held down, like a knob being turned steadily.
    REPEATING = {"left", "right", "up", "down", "+", "-", "[", "]"}
    DRAG_START_PX = 4      # the mouse must move this far before a press becomes a drag
    LONG_PRESS_S = 0.6

    def __init__(self, size=SIZE, scale=2, title="Terradio"):
        if sys.platform == "win32":
            # Without this, Windows display scaling stretches the window and blurs the pixels.
            try:
                import ctypes
                ctypes.windll.shcore.SetProcessDpiAwareness(1)
            except (AttributeError, OSError):
                pass
        os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
        import pygame

        self._pg = pygame
        self.size = size
        self.scale = scale
        pygame.display.init()        # only the display: audio stays with mpv/ffplay
        pygame.display.set_caption(title)
        self._window = pygame.display.set_mode((size[0] * scale, size[1] * scale))
        pygame.key.set_repeat(300, 45)
        self._held = set()
        self._press = None           # (time, position) while the left button is down
        self._dragging = False
        self._names = {pygame.K_LEFT: "left", pygame.K_RIGHT: "right", pygame.K_UP: "up",
                       pygame.K_DOWN: "down", pygame.K_ESCAPE: "esc",
                       pygame.K_KP_PLUS: "+", pygame.K_KP_MINUS: "-"}

    def show(self, image):
        pg = self._pg
        surface = pg.image.frombuffer(image.tobytes(), image.size, "RGB")
        if self.scale != 1:
            surface = pg.transform.scale(surface, self._window.get_size())
        self._window.blit(surface, (0, 0))
        pg.display.flip()

    def events(self):
        """Since the last call: key names ("left", "f", "F" for Shift+F, ...), "quit",
        "click" and "long-click" (left button held, or a right click), ("wheel", steps),
        and at most one ("drag", dx, dy) with the movement as fractions of the window size."""
        pg = self._pg
        out = []
        drag_x = drag_y = 0
        for event in pg.event.get():
            if event.type == pg.QUIT:
                out.append("quit")
            elif event.type == pg.MOUSEBUTTONDOWN and event.button == 1:
                self._press = (time.monotonic(), event.pos)
                self._dragging = False
            elif event.type == pg.MOUSEBUTTONDOWN and event.button == 3:
                out.append("long-click")
            elif event.type == pg.MOUSEMOTION and self._press:
                if self._dragging:
                    drag_x += event.rel[0]
                    drag_y += event.rel[1]
                else:
                    x0, y0 = self._press[1]
                    dx, dy = event.pos[0] - x0, event.pos[1] - y0
                    if abs(dx) + abs(dy) >= self.DRAG_START_PX:
                        self._dragging = True
                        drag_x += dx
                        drag_y += dy
            elif event.type == pg.MOUSEBUTTONUP and event.button == 1 and self._press:
                if not self._dragging:
                    held = time.monotonic() - self._press[0]
                    out.append("long-click" if held >= self.LONG_PRESS_S else "click")
                self._press = None
            elif event.type == pg.MOUSEWHEEL and event.y:
                out.append(("wheel", event.y))
            elif event.type == pg.KEYUP:
                self._held.discard(event.key)
            elif event.type == pg.KEYDOWN:
                name = self._names.get(event.key)
                if name is None:
                    # Letters by the character typed, so Shift+F arrives as "F".
                    ch = event.unicode
                    name = {"=": "+", "_": "-"}.get(ch, ch) if ch else None
                repeat = event.key in self._held
                self._held.add(event.key)
                if name and (not repeat or name in self.REPEATING):
                    out.append(name)
        if drag_x or drag_y:
            width, height = self._window.get_size()
            out.append(("drag", drag_x / width, drag_y / height))
        return out

    def close(self):
        self._pg.display.quit()
