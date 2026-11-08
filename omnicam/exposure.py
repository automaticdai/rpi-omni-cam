"""Exposure value tables, modes, stepping logic and formatting."""

from __future__ import annotations

import math
from typing import Optional, Sequence

SHUTTER_STEPS: tuple[float, ...] = (
    1 / 4000, 1 / 2000, 1 / 1000, 1 / 500, 1 / 250, 1 / 125, 1 / 60, 1 / 30,
    1 / 15, 1 / 8, 1 / 4, 1 / 2, 1,
)
MAX_SHUTTER_S = SHUTTER_STEPS[-1]  # longest shutter normally offered (1 s)
# With "Long exposures" on, these follow (as far as the sensor allows).
LONG_SHUTTER_STEPS: tuple[float, ...] = (2, 4, 8, 15, 30, 60, 120, 240)
LONG_SHUTTER_S = LONG_SHUTTER_STEPS[-1]
ISO_STEPS: tuple[int, ...] = (100, 200, 400, 800, 1600, 3200)
EV_STEPS: tuple[float, ...] = (-2.0, -1.5, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5, 2.0)


def shutter_steps(max_exposure_s: float, long: bool = False) -> list[float]:
    """Shutter steps the sensor can actually do (past 1 s only with `long`)."""
    table = SHUTTER_STEPS + LONG_SHUTTER_STEPS if long else SHUTTER_STEPS
    steps = [s for s in table if s <= max_exposure_s * 1.001]
    return steps or [SHUTTER_STEPS[0]]


def iso_steps(max_gain: float) -> list[int]:
    """ISO steps within the sensor's analogue gain range (ISO 100 = gain 1)."""
    steps = [i for i in ISO_STEPS if i / 100 <= max_gain * 1.001]
    return steps or [ISO_STEPS[0]]


def nearest(values: Sequence[float], target: float) -> float:
    """Nearest value in log space (photographic stops)."""
    target = max(target, 1e-9)
    return min(values, key=lambda v: abs(math.log2(v) - math.log2(target)))


# Manual focus positions in dioptres (1/distance in m); 0 = infinity.
FOCUS_STEPS: tuple[float, ...] = (0.0, 0.25, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0, 10.0)

# Drive modes: (frames, EV offsets) for bracketing; burst handled separately.
BRACKETS: dict[str, tuple[float, ...]] = {
    "bracket3_1": (0.0, -1.0, 1.0),
    "bracket3_2": (0.0, -2.0, 2.0),
    "bracket5_1": (0.0, -1.0, 1.0, -2.0, 2.0),
}
BURSTS: dict[str, int] = {"burst5": 5, "burst10": 10}


def format_focus(dioptres: float) -> str:
    if dioptres <= 0:
        return "MF ∞"
    m = 1 / dioptres
    return f"MF {m:.1f}m" if m < 10 else f"MF {m:.0f}m"


def bracket_exposure(shutter: float, gain: float, ev: float, max_shutter: float,
                     max_gain: float) -> tuple[float, float]:
    """Shift an exposure by ev stops: shutter first, then gain once the
    shutter hits max_shutter (or 1/4000 s at the short end)."""
    target = shutter * gain * 2 ** ev
    s = min(max(shutter * 2 ** ev, SHUTTER_STEPS[0]), max_shutter)
    g = min(max(target / s, 1.0), max_gain)
    return s, g


# Mode -> values the A/B buttons adjust in live view (Y cycles between them).
MODES = ("P", "A", "S", "M", "MOVIE", "SQ")  # Sony naming and order
MODE_PARAMS: dict[str, tuple[str, ...]] = {
    "P": ("ev",),
    "A": ("iso", "ev"),   # aperture priority: aperture on the lens ring, ISO here
    "S": ("shutter", "ev"),
    "M": ("shutter", "iso"),
    "MOVIE": ("ev",),
    "SQ": ("ev",),
}

# Drive: night mode stacks this many frames into one photo.
NIGHT_FRAMES: dict[str, int] = {"night4": 4, "night8": 8}

# Video: (width, height, fps) for each video size; slow motion is 640x480.
VIDEO_SIZES: dict[str, tuple[int, int, int]] = {"1080p": (1920, 1080, 30), "720p": (1280, 720, 30)}
SQ_SIZE = (640, 480)
SQ_PLAYBACK_FPS = 30


def step(values: Sequence[float], current: float, direction: int, log: bool = True) -> float:
    """Move one step through values, stopping at either end.

    direction +1 increases (slower shutter / higher ISO / brighter EV), -1 decreases.
    """
    if current not in values:
        current = nearest(values, current) if log else min(values, key=lambda v: abs(v - current))
    i = values.index(current) + direction
    return values[max(0, min(i, len(values) - 1))]


def format_shutter(seconds: Optional[float]) -> str:
    if seconds is None:
        return "AUTO"
    if seconds >= 1:
        return f'{seconds:g}"'
    if seconds >= 0.3:
        return f'0.{round(seconds * 10)}"'
    return f"1/{round(1 / seconds)}"


def format_iso(iso: Optional[float]) -> str:
    if iso is None:
        return "AUTO"
    return f"ISO {round(iso)}"


def format_ev(ev: float) -> str:
    if abs(ev) < 0.05:
        return "±0"
    return f"{ev:+.1f}"


def luminance_ev(gray_mean_linear: float) -> float:
    """EV offset of a mean linear luminance (0..1) from 18 % grey."""
    return math.log2(max(gray_mean_linear, 1e-4) / 0.18)
