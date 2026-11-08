"""Night mode: average several frames into one photo, aligning small hand movements."""

from __future__ import annotations

from typing import Optional

import numpy as np
from PIL import Image

ALIGN_SCALE = 8  # alignment runs on frames this many times smaller
MAX_SHIFT_FRACTION = 0.1  # ignore "shifts" bigger than this (alignment failed)


def _small_gray(img: Image.Image) -> np.ndarray:
    w, h = img.size
    small = img.convert("L").resize((max(8, w // ALIGN_SCALE), max(8, h // ALIGN_SCALE)), Image.BILINEAR)
    a = np.asarray(small, dtype=np.float32)
    a -= a.mean()
    # taper the edges so the FFT doesn't see the frame border as structure
    wy = np.hanning(a.shape[0])[:, None]
    wx = np.hanning(a.shape[1])[None, :]
    return a * wy * wx


def find_shift(ref: np.ndarray, small: np.ndarray) -> tuple[int, int]:
    """(dy, dx) in small-frame pixels that moves `small` onto `ref` (phase correlation)."""
    f = np.fft.fft2(ref) * np.conj(np.fft.fft2(small))
    f /= np.abs(f) + 1e-9
    corr = np.fft.ifft2(f).real
    dy, dx = np.unravel_index(int(np.argmax(corr)), corr.shape)
    h, w = corr.shape
    if dy > h // 2:
        dy -= h
    if dx > w // 2:
        dx -= w
    return int(dy), int(dx)


class Stacker:
    """Add frames one by one (memory: one uint16 copy of the photo), then average."""

    def __init__(self, align: bool = True):
        self.align = align
        self.sum: Optional[np.ndarray] = None
        self.count = 0
        self._ref: Optional[np.ndarray] = None
        self.margin = [0, 0]  # (y, x) pixels to crop: edges wrapped by shifting

    def add(self, img: Image.Image) -> None:
        a = np.asarray(img.convert("RGB"), dtype=np.uint8)
        if self.sum is None:
            self.sum = a.astype(np.uint16)
            if self.align:
                self._ref = _small_gray(img)
            self.count = 1
            return
        if a.shape != self.sum.shape:
            return
        if self.align and self._ref is not None:
            dy, dx = find_shift(self._ref, _small_gray(img))
            h, w = a.shape[:2]
            dy, dx = dy * ALIGN_SCALE, dx * ALIGN_SCALE
            if abs(dy) <= h * MAX_SHIFT_FRACTION and abs(dx) <= w * MAX_SHIFT_FRACTION:
                if dy or dx:
                    a = np.roll(a, (dy, dx), axis=(0, 1))
                self.margin[0] = max(self.margin[0], abs(dy))
                self.margin[1] = max(self.margin[1], abs(dx))
        self.sum += a
        self.count += 1

    def result(self) -> Image.Image:
        if self.sum is None:
            raise ValueError("no frames")
        avg = (self.sum + self.count // 2) // self.count
        img = Image.fromarray(avg.astype(np.uint8), "RGB")
        my, mx = self.margin
        if my or mx:
            w, h = img.size
            img = img.crop((mx, my, w - mx, h - my))
        return img
