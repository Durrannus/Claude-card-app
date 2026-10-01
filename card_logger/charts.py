"""A small single-series line chart drawn on a Tk canvas, with a hover readout."""

import tkinter as tk
from datetime import date
from tkinter import font as tkfont

from . import theme

GRID = "#223a4f"


class LineChart(tk.Canvas):
    """Plot (day, value) points; None values leave a gap in the line.

    One series per chart: the title above it says what's plotted, so there's
    no legend. The last point is labelled; hover shows any point's value.
    An optional projection continues the line, dashed, from its last point.
    """

    PAD_LEFT, PAD_RIGHT, PAD_TOP, PAD_BOTTOM = 52, 56, 12, 24

    def __init__(self, parent, color: str, fmt=str, zero_based: bool = False, height: int = 140, **kw):
        super().__init__(parent, height=height, background=theme.SURFACE, highlightthickness=0, **kw)
        self.color, self.fmt, self.zero_based = color, fmt, zero_based
        self.points: list[tuple[str, float | None]] = []
        self.projection: list[tuple[str, float]] = []
        self.empty_text = ""
        self._xy: list[tuple[float, float, str, float]] = []
        self.bind("<Configure>", lambda _: self.draw())
        self.bind("<Motion>", self._hover)
        self.bind("<Leave>", lambda _: self.delete("hover"))

    def set_data(self, points: list[tuple[str, float | None]], empty_text: str = "",
                 projection: list[tuple[str, float]] | None = None) -> None:
        self.points, self.empty_text, self.projection = points, empty_text, projection or []
        self.draw()

    def draw(self) -> None:
        self.delete("all")
        self._xy = []
        w, h = self.winfo_width(), self.winfo_height()
        if w < 50 or h < 50:
            return
        values = [v for _, v in self.points if v is not None]
        if len(values) < 2:
            self.create_text(w / 2, h / 2, text=self.empty_text, fill=theme.MUTED, font=theme.font(9),
                             width=w - 30, justify="center")
            return

        values += [v for _, v in self.projection]
        lo, hi = (0.0 if self.zero_based else min(values)), max(values)
        if hi - lo < 1e-9:
            lo, hi = lo - (abs(lo) * 0.1 or 1), hi + (abs(hi) * 0.1 or 1)
        pad = (hi - lo) * 0.08
        lo, hi = (lo if self.zero_based else lo - pad), hi + pad
        if self.zero_based:
            lo = 0.0

        # Make room for the widest axis label and the end-point label.
        axis_font = tkfont.Font(font=theme.font(8))
        end_font = tkfont.Font(font=theme.font(9, "bold"))
        widest_axis = max(axis_font.measure(self.fmt(lo + (hi - lo) * f)) for f in (0, 0.5, 1))
        last_value = (self.projection or [(None, None)])[-1][1] or next(
            v for _, v in reversed(self.points) if v is not None)
        left = max(self.PAD_LEFT, widest_axis + 14)
        right = w - max(self.PAD_RIGHT, end_font.measure(self.fmt(last_value)) + 16)
        top, bottom = self.PAD_TOP, h - self.PAD_BOTTOM
        days = [date.fromisoformat(d).toordinal() for d, _ in self.points + self.projection]
        d0, d1 = min(days), max(days)
        span = max(d1 - d0, 1)

        def x_of(i):
            return left + (days[i] - d0) / span * (right - left)

        def y_of(v):
            return bottom - (v - lo) / (hi - lo) * (bottom - top)

        # Recessive hairline grid with values at the left.
        for frac in ((0, 0.5, 1) if bottom - top >= 60 else (0, 1)):  # fewer labels on short charts
            v = lo + (hi - lo) * frac
            y = y_of(v)
            self.create_line(left, y, right, y, fill=GRID, width=1)
            self.create_text(left - 8, y, text=self.fmt(v), anchor="e", fill=theme.MUTED, font=theme.font(8))
        self.create_text(left, h - 6, text=_short(self.points[0][0]), anchor="sw", fill=theme.MUTED,
                         font=theme.font(8))
        self.create_text(right, h - 6, text=_short((self.points + self.projection)[-1][0]), anchor="se",
                         fill=theme.MUTED, font=theme.font(8))

        # 2px line, broken where there's no value.
        segment: list[float] = []
        for i, (d, v) in enumerate(self.points):
            if v is None:
                if len(segment) >= 4:
                    self.create_line(*segment, fill=self.color, width=2, capstyle="round", joinstyle="round")
                segment = []
                continue
            x, y = x_of(i), y_of(v)
            segment += [x, y]
            self._xy.append((x, y, d, v))
        if len(segment) >= 4:
            self.create_line(*segment, fill=self.color, width=2, capstyle="round", joinstyle="round")
        elif len(segment) == 2:
            self._dot(*segment, self.color)

        # End marker with a surface ring, labelled in text ink.
        x, y, _, v = self._xy[-1]
        if self.projection:
            # Dashed continuation to the projected value, labelled at its end.
            n = len(self.points)
            coords = [c for i in range(len(self.projection)) for c in (x_of(n + i), y_of(self.projection[i][1]))]
            self.create_line(x, y, *coords[2:], fill=self.color, width=2, dash=(5, 4))
            px, py = coords[-2], coords[-1]
            self.create_oval(px - 3, py - 3, px + 3, py + 3, outline=self.color, width=2, fill=theme.SURFACE)
            self.create_text(px + 8, py, text=self.fmt(self.projection[-1][1]), anchor="w", fill=theme.MUTED,
                             font=theme.font(9, "bold"))
            self._dot(x, y, self.color)
            self.create_text(x, y - 9, text=self.fmt(v), anchor="s", fill=theme.TEXT, font=theme.font(9, "bold"))
            return
        self._dot(x, y, self.color)
        self.create_text(x + 8, y, text=self.fmt(v), anchor="w", fill=theme.TEXT, font=theme.font(9, "bold"))

    def _dot(self, x, y, color, tag=""):
        self.create_oval(x - 6, y - 6, x + 6, y + 6, fill=theme.SURFACE, outline="", tags=tag)
        self.create_oval(x - 4, y - 4, x + 4, y + 4, fill=color, outline="", tags=tag)

    def _hover(self, event) -> None:
        self.delete("hover")
        if not self._xy:
            return
        x, y, d, v = min(self._xy, key=lambda p: abs(p[0] - event.x))
        self.create_line(x, self.PAD_TOP, x, self.winfo_height() - self.PAD_BOTTOM, fill=theme.MUTED,
                         width=1, tags="hover")
        self._dot(x, y, self.color, tag="hover")
        label = f"{_short(d)}  ·  {self.fmt(v)}"
        text = self.create_text(0, 0, text=label, anchor="nw", fill=theme.TEXT, font=theme.font(9), tags="hover")
        x0, y0, x1, y1 = self.bbox(text)
        tw, th = x1 - x0 + 12, y1 - y0 + 8
        tx = x + 10 if x + 10 + tw < self.winfo_width() else x - 10 - tw
        ty = max(2, min(y - th - 6, self.winfo_height() - th - 2))
        self.create_rectangle(tx, ty, tx + tw, ty + th, fill=theme.RAISED, outline=theme.BORDER, tags="hover")
        self.coords(text, tx + 6, ty + 4)
        self.tag_raise(text)


def _short(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.day} {d:%b}" + (f" {d:%y}" if d.year != date.today().year else "")
