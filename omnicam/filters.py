"""Image filters, applied to both the live preview and the saved photo."""

from __future__ import annotations

from functools import lru_cache
from typing import Callable

from PIL import Image, ImageChops, ImageEnhance, ImageOps


def _bw(img: Image.Image) -> Image.Image:
    return ImageOps.grayscale(img).convert("RGB")


def _sepia(img: Image.Image) -> Image.Image:
    gray = ImageOps.grayscale(img)
    return ImageOps.colorize(gray, black="#1e140a", white="#fff4dc", mid="#a0784b")


def _high_contrast(img: Image.Image) -> Image.Image:
    img = ImageOps.autocontrast(img, cutoff=1)
    return ImageEnhance.Contrast(img).enhance(1.5)


def _vivid(img: Image.Image) -> Image.Image:
    img = ImageEnhance.Color(img).enhance(1.6)
    return ImageEnhance.Contrast(img).enhance(1.15)


def _scale_channels(img: Image.Image, r: float, g: float, b: float) -> Image.Image:
    luts = []
    for k in (r, g, b):
        luts.extend(min(255, int(v * k)) for v in range(256))
    return img.point(luts)


def _cool(img: Image.Image) -> Image.Image:
    return _scale_channels(img, 0.88, 0.98, 1.12)


def _warm(img: Image.Image) -> Image.Image:
    return _scale_channels(img, 1.12, 1.02, 0.86)


@lru_cache(maxsize=4)
def _vignette(size: tuple[int, int]) -> Image.Image:
    """RGB multiply mask: white centre fading to dark corners."""
    grad = Image.radial_gradient("L")  # 256x256, black centre -> white edge
    grad = grad.resize(size)
    mask = grad.point(lambda v: 255 - int(min(255, max(0, v - 90)) * 0.75))
    return Image.merge("RGB", (mask, mask, mask))


def _vintage(img: Image.Image) -> Image.Image:
    img = ImageEnhance.Color(img).enhance(0.65)
    luts = []
    for lift, gain in ((28, 0.86), (22, 0.84), (10, 0.78)):  # faded, warm blacks
        luts.extend(int(lift + v * gain) for v in range(256))
    img = img.point(luts)
    return ImageChops.multiply(img, _vignette(img.size))


FILTERS: dict[str, tuple[str, Callable[[Image.Image], Image.Image] | None]] = {
    "none": ("None", None),
    "bw": ("B&W", _bw),
    "sepia": ("Sepia", _sepia),
    "contrast": ("High Contrast", _high_contrast),
    "vivid": ("Vivid", _vivid),
    "cool": ("Cool", _cool),
    "warm": ("Warm", _warm),
    "vintage": ("Vintage", _vintage),
}


def apply(name: str, img: Image.Image) -> Image.Image:
    fn = FILTERS.get(name, FILTERS["none"])[1]
    if img.mode != "RGB":
        img = img.convert("RGB")
    return fn(img) if fn else img


def label(name: str) -> str:
    return FILTERS.get(name, FILTERS["none"])[0]
