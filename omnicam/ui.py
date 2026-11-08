"""Drawing helpers for the 320x240 screen."""

from __future__ import annotations

from functools import lru_cache
from typing import Optional, Sequence

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

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


def grid(d: ImageDraw.ImageDraw, diagonal: bool = False) -> None:
    """Rule-of-thirds lines, optionally with corner-to-corner diagonals."""
    col = (255, 255, 255, 90)
    for i in (1, 2):
        d.line([(W * i // 3, 0), (W * i // 3, H)], fill=col)
        d.line([(0, H * i // 3), (W, H * i // 3)], fill=col)
    if diagonal:
        d.line([(0, 0), (W, H)], fill=col)
        d.line([(0, H), (W, 0)], fill=col)


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
        if i == 0:  # correct exposure: bold, full-height centre mark
            d.rectangle([x - 1, y - 1, x + 1, y + 9], fill=WHITE)
        else:
            tall = i % 3 == 0
            d.line([(x, y + (2 if tall else 5)), (x, y + 8)], fill=WHITE)
    clamped = max(-2.2, min(2.2, ev))
    mx = cx + int(clamped / 2 * half)
    col = ACCENT if abs(ev) <= 2 else RED
    d.polygon([(mx - 4, y + 15), (mx + 4, y + 15), (mx, y + 10)], fill=col)


_SRGB_TO_LINEAR = np.array([(v / 255 / 12.92) if v / 255 <= 0.04045 else ((v / 255 + 0.055) / 1.055) ** 2.4
                            for v in range(256)], dtype=np.float32)
_yy, _xx = np.mgrid[0:48, 0:64]
_CENTRE_WEIGHTS = np.exp(-(((_xx - 32) / 24) ** 2 + ((_yy - 24) / 18) ** 2)).astype(np.float32)
_CENTRE_WEIGHTS /= _CENTRE_WEIGHTS.sum()


def mean_ev(img: Image.Image, metering: str = "centre") -> float:
    """Mean linear luminance of an sRGB image as EV from 18 % grey.

    metering: centre (centre-weighted), spot (small centre area) or matrix (whole frame).
    """
    lin = _SRGB_TO_LINEAR[np.asarray(img.convert("L").resize((64, 48), Image.BILINEAR))]
    if metering == "matrix":
        m = float(lin.mean())
    elif metering == "spot":
        m = float(lin[21:27, 29:35].mean())  # ~10 % of the frame width
    else:
        m = float((lin * _CENTRE_WEIGHTS).sum())
    return float(np.log2(max(m, 1e-4) / 0.18))


PEAKING_COLOURS = {"red": (255, 40, 40), "yellow": (255, 230, 0), "blue": (60, 140, 255)}


_PEAK_LUTS: dict = {}


def peaking(img: Image.Image, colour: str, threshold: int = 48) -> Image.Image:
    """RGBA overlay colouring in-focus (high-contrast) edges."""
    lut = _PEAK_LUTS.get(threshold)
    if lut is None:
        lut = _PEAK_LUTS[threshold] = [255 if v > threshold else 0 for v in range(256)]
    mask = img.convert("L").filter(ImageFilter.FIND_EDGES).point(lut)
    # FIND_EDGES leaves the 1-px border unfiltered: blank it so the frame edge isn't "in focus".
    w, h = img.size
    ImageDraw.Draw(mask).rectangle([0, 0, w - 1, h - 1], outline=0)
    layer = Image.new("RGBA", img.size, PEAKING_COLOURS.get(colour, PEAKING_COLOURS["red"]) + (255,))
    layer.putalpha(mask)
    return layer


_STRIPES: dict = {}


def _stripes(size: tuple[int, int], phase: int) -> Image.Image:
    """Diagonal black/white zebra stripes (RGBA), cached per size and phase."""
    key = (size, phase)
    if key not in _STRIPES:
        w, h = size
        yy, xx = np.mgrid[0:h, 0:w]
        stripe = ((xx + yy + phase) // 4) % 2 == 0
        out = np.empty((h, w, 4), dtype=np.uint8)
        out[stripe] = (0, 0, 0, 200)
        out[~stripe] = (255, 255, 255, 120)
        _STRIPES[key] = Image.fromarray(out, "RGBA")
    return _STRIPES[key]


def zebras(img: Image.Image, phase: int = 0, level: int = 250) -> Image.Image:
    """RGBA overlay of diagonal stripes over blown-out (clipped) areas."""
    a = np.asarray(img.convert("RGB") if img.mode != "RGB" else img)
    clipped = Image.fromarray(((a.max(axis=2) >= level) * 255).astype(np.uint8), "L")
    clear = Image.new("RGBA", img.size, (0, 0, 0, 0))
    return Image.composite(_stripes(img.size, phase), clear, clipped)


def place(overlay: Image.Image) -> Image.Image:
    """Scale an RGBA overlay made at preview size onto the screen the way fit() places the image."""
    x0, y0, x1, y1 = image_rect(overlay.size)
    screen = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    screen.paste(overlay.resize((x1 - x0, y1 - y0), Image.NEAREST), (x0, y0))
    return screen


def image_rect(img_size: tuple[int, int]) -> tuple[int, int, int, int]:
    """Where fit() places an image of this size on the screen: (x0, y0, x1, y1)."""
    iw, ih = img_size
    scale = min(W / iw, H / ih)
    w, h = int(iw * scale), int(ih * scale)
    x0, y0 = (W - w) // 2, (H - h) // 2
    return x0, y0, x0 + w, y0 + h


def aspect_crop(size: tuple[int, int], aspect: str) -> tuple[int, int, int, int]:
    """Centre crop box (x0, y0, x1, y1) of `size` for an aspect like '3:2'."""
    w, h = size
    try:
        aw, ah = (int(v) for v in aspect.split(":"))
    except ValueError:  # "native": the sensor's own shape
        return 0, 0, w, h
    if abs((w / h) / (aw / ah) - 1) < 0.01:  # already that shape (e.g. 2028x1520 ≈ 4:3)
        return 0, 0, w, h
    if w / h > aw / ah:  # too wide: trim the sides
        cw = int(h * aw / ah)
        return (w - cw) // 2, 0, (w + cw) // 2, h
    ch = int(w * ah / aw)
    return 0, (h - ch) // 2, w, (h + ch) // 2


def aspect_guides(d: ImageDraw.ImageDraw, img_size: tuple[int, int], aspect: str) -> None:
    """Darken the parts of the preview that a non-native aspect ratio crops away."""
    x0, y0, x1, y1 = image_rect(img_size)
    cx0, cy0, cx1, cy1 = aspect_crop((x1 - x0, y1 - y0), aspect)
    shade = (0, 0, 0, 150)
    if cx0 > 0:
        d.rectangle([x0, y0, x0 + cx0 - 1, y1], fill=shade)
        d.rectangle([x0 + cx1, y0, x1, y1], fill=shade)
    if cy0 > 0:
        d.rectangle([x0, y0, x1, y0 + cy0 - 1], fill=shade)
        d.rectangle([x0, y0 + cy1, x1, y1], fill=shade)


def _bolt(d: ImageDraw.ImageDraw, x: int, y: int, colour) -> None:
    """Small lightning bolt, 7x12 px, top-left at (x, y)."""
    d.polygon([(x + 4, y), (x, y + 7), (x + 3, y + 7), (x + 2, y + 12), (x + 7, y + 4),
               (x + 4, y + 4), (x + 5, y)], fill=colour)


def battery_icon(d: ImageDraw.ImageDraw, right: int, y: int, percent, charging) -> int:
    """Battery icon ending at x=right; returns the left edge used.

    percent None means no battery gauge: the camera is on external (USB) power,
    shown as a full battery with a charge bolt.
    """
    f = font(13, "bold")
    external = percent is None
    low = not external and percent <= 15
    col = RED if low else WHITE
    bw, bh = 22, 11
    bx1 = right - 3
    bx0 = bx1 - bw
    d.rectangle([bx0, y + 2, bx1, y + 2 + bh], outline=col, width=1)
    d.rectangle([bx1 + 1, y + 5, bx1 + 2, y + bh - 1], fill=col)
    level = 100 if external else percent
    fill_w = max(1, int((bw - 3) * level / 100))
    d.rectangle([bx0 + 2, y + 4, bx0 + 2 + fill_w, y + bh], fill=GREEN if external else col)
    left = bx0
    if external or charging:
        left -= 10
        _bolt(d, left, y + 2, ACCENT)
    if not external:
        text = f"{percent}%"
        left -= 4 + text_w(d, text, f)
        d.text((left, y), text, font=f, fill=col)
    return left


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


# Button letters as printed on the HAT, for each logical button; changed by the
# left-handed / upside-down settings (see App.update_keymap). MIRRORED: the
# A/B buttons are on the right, so hints for them go on the right.
KEY_LABELS: dict[str, str] = {}
MIRRORED = False
_KEY_RE = None


def keys(text: str) -> str:
    """Rewrite button letters in a help/hint text for the current button layout."""
    global _KEY_RE
    if not KEY_LABELS or all(k == v for k, v in KEY_LABELS.items()):
        return text
    if _KEY_RE is None:
        import re

        _KEY_RE = re.compile(r"(?<![A-Za-z0-9&])([ABXY])(?![A-Za-z0-9&])")
    return _KEY_RE.sub(lambda m: KEY_LABELS.get(m[1], m[1]), text)


def qr(d: ImageDraw.ImageDraw, text: str, x: int, y: int, size: int) -> bool:
    """Draw a QR code (white quiet zone) in a size x size square. False if segno is missing."""
    try:
        import segno
    except ImportError:
        return False
    code = segno.make(text, error="m")
    rows = [list(r) for r in code.matrix]
    n = len(rows) + 4  # quiet zone of 2 modules each side
    cell = max(1, size // n)
    side = cell * n
    x += (size - side) // 2
    y += (size - side) // 2
    d.rectangle([x, y, x + side - 1, y + side - 1], fill=WHITE)
    for r, row in enumerate(rows):
        for c, v in enumerate(row):
            if v:
                px, py = x + (c + 2) * cell, y + (r + 2) * cell
                d.rectangle([px, py, px + cell - 1, py + cell - 1], fill=(0, 0, 0))
    return True


def hints(d: ImageDraw.ImageDraw, left: str, right: str) -> None:
    left, right = keys(left), keys(right)
    if MIRRORED:
        left, right = right, left
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
