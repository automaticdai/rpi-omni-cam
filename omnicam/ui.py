"""Drawing helpers for the 320x240 screen."""

from __future__ import annotations

from functools import lru_cache
from typing import Optional, Sequence

import numpy as np
from PIL import Image, ImageDraw, ImageFont

W, H = 320, 240
ACCENT = (245, 196, 0)
WHITE = (255, 255, 255)
GREY = (170, 170, 170)
RED = (230, 60, 50)
GREEN = (80, 200, 100)
BG = (18, 18, 20)
PANEL = (0, 0, 0, 150)

_FONT_DIRS = ("/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/dejavu", "/usr/share/fonts/TTF")
_FONT_FILES = {
    "regular": "DejaVuSans.ttf",
    "bold": "DejaVuSans-Bold.ttf",
    "mono": "DejaVuSansMono-Bold.ttf",
}


@lru_cache(maxsize=None)
def font(size: int, style: str = "regular") -> ImageFont.ImageFont:
    for d in _FONT_DIRS:
        try:
            return ImageFont.truetype(f"{d}/{_FONT_FILES[style]}", size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)  # Pillow >= 10.1
    except TypeError:
        return ImageFont.load_default()


def text_w(draw: ImageDraw.ImageDraw, text: str, f) -> int:
    return int(draw.textlength(text, font=f))


def fit(img: Image.Image, size=(W, H), bg=(0, 0, 0)) -> Image.Image:
    """Letterbox img into size, keeping aspect ratio."""
    if img.size == size:
        return img
    canvas = Image.new("RGB", size, bg)
    im = img.copy()
    im.thumbnail(size)
    canvas.paste(im, ((size[0] - im.width) // 2, (size[1] - im.height) // 2))
    return canvas


def overlay(base: Image.Image) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    return layer, ImageDraw.Draw(layer)


def flatten(base: Image.Image, layer: Image.Image) -> Image.Image:
    return Image.alpha_composite(base.convert("RGBA"), layer).convert("RGB")


def grid(d: ImageDraw.ImageDraw) -> None:
    for i in (1, 2):
        d.line([(W * i // 3, 0), (W * i // 3, H)], fill=(255, 255, 255, 90))
        d.line([(0, H * i // 3), (W, H * i // 3)], fill=(255, 255, 255, 90))


def histogram(d: ImageDraw.ImageDraw, img: Image.Image, box=(W - 74, 26, W - 6, 62)) -> None:
    x0, y0, x1, y1 = box
    d.rectangle(box, fill=(0, 0, 0, 140))
    hist = img.convert("L").histogram()
    bins = x1 - x0 - 2
    per = 256 / bins
    vals = [sum(hist[int(i * per):int((i + 1) * per)]) for i in range(bins)]
    peak = max(vals) or 1
    for i, v in enumerate(vals):
        h = int((y1 - y0 - 2) * v / peak)
        if h:
            d.line([(x0 + 1 + i, y1 - 1), (x0 + 1 + i, y1 - 1 - h)], fill=(255, 255, 255, 200))


def meter(d: ImageDraw.ImageDraw, ev: float, y: int) -> None:
    """Exposure scale −2..+2 with a marker at ev."""
    f = font(10, "bold")
    cx, half = W // 2, 70
    d.rectangle([cx - half - 22, y - 2, cx + half + 22, y + 16], fill=(0, 0, 0, 140))
    d.text((cx - half - 18, y), "−", font=f, fill=WHITE)
    d.text((cx + half + 12, y), "+", font=f, fill=WHITE)
    for i in range(-6, 7):
        x = cx + i * half // 6
        tall = i % 3 == 0
        d.line([(x, y + (2 if tall else 5)), (x, y + 8)], fill=WHITE)
    clamped = max(-2.2, min(2.2, ev))
    mx = cx + int(clamped / 2 * half)
    col = ACCENT if abs(ev) <= 2 else RED
    d.polygon([(mx - 4, y + 15), (mx + 4, y + 15), (mx, y + 10)], fill=col)


def mean_ev(img: Image.Image) -> float:
    """Centre-weighted mean linear luminance of an sRGB image, as EV from 18 % grey."""
    a = np.asarray(img.convert("L").resize((64, 48)), dtype=np.float32) / 255.0
    lin = np.where(a <= 0.04045, a / 12.92, ((a + 0.055) / 1.055) ** 2.4)
    yy, xx = np.mgrid[0:48, 0:64]
    w = np.exp(-(((xx - 32) / 24) ** 2 + ((yy - 24) / 18) ** 2))
    m = float((lin * w).sum() / w.sum())
    return float(np.log2(max(m, 1e-4) / 0.18))


def bar(d: ImageDraw.ImageDraw, y0: int, y1: int) -> None:
    d.rectangle([0, y0, W, y1], fill=PANEL)


def items_row(d: ImageDraw.ImageDraw, y: int, items: Sequence[tuple[str, bool, tuple]],
              f, gap: int = 10, x: int = 6) -> None:
    """Draw (text, highlighted, colour) items left to right."""
    for text, hl, col in items:
        tw = text_w(d, text, f)
        if hl:
            d.rounded_rectangle([x - 3, y - 2, x + tw + 3, y + f.size + 2], 3, fill=ACCENT)
            d.text((x, y), text, font=f, fill=(0, 0, 0))
        else:
            d.text((x, y), text, font=f, fill=col)
        x += tw + gap


def centered(d: ImageDraw.ImageDraw, y: int, text: str, f, fill=WHITE) -> None:
    d.text(((W - text_w(d, text, f)) // 2, y), text, font=f, fill=fill)


def panel_screen(title: str) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 26], fill=(40, 40, 46))
    d.text((10, 5), title, font=font(15, "bold"), fill=WHITE)
    return img, d


def hints(d: ImageDraw.ImageDraw, left: str, right: str) -> None:
    f = font(11)
    d.rectangle([0, H - 18, W, H], fill=(40, 40, 46))
    d.text((6, H - 16), left, font=f, fill=GREY)
    d.text((W - 6 - text_w(d, right, f), H - 16), right, font=f, fill=GREY)


def menu_list(d: ImageDraw.ImageDraw, rows: Sequence[tuple[str, str]], selected: int,
              top: int = 32, row_h: int = 26, visible: Optional[int] = None) -> None:
    visible = visible or (H - 18 - top) // row_h
    start = max(0, min(selected - visible // 2, len(rows) - visible))
    f, fv = font(15), font(14)
    for i, (label, value) in enumerate(rows[start:start + visible]):
        idx = start + i
        y = top + i * row_h
        if idx == selected:
            d.rounded_rectangle([4, y, W - 4, y + row_h - 3], 4, fill=ACCENT)
            fg, fgv = (0, 0, 0), (40, 40, 40)
        else:
            fg, fgv = WHITE, GREY
        d.text((12, y + 4), label, font=f, fill=fg)
        if value:
            d.text((W - 14 - text_w(d, value, fv), y + 5), value, font=fv, fill=fgv)
    if len(rows) > visible:  # scrollbar
        track = H - 18 - top - 6
        th = max(12, track * visible // len(rows))
        ty = top + (track - th) * start // max(1, len(rows) - visible)
        d.rectangle([W - 3, ty, W - 1, ty + th], fill=GREY)


def wrap(d: ImageDraw.ImageDraw, text: str, f, width: int) -> list[str]:
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if text_w(d, trial, f) <= width:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines
