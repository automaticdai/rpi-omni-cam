"""Exposure value tables, modes, stepping logic and formatting."""

from __future__ import annotations

import math
from typing import Optional, Sequence

SHUTTER_STEPS: tuple[float, ...] = (
    1 / 4000, 1 / 2000, 1 / 1000, 1 / 500, 1 / 250, 1 / 125, 1 / 60, 1 / 30,
    1 / 15, 1 / 8, 1 / 4, 1 / 2, 1, 2, 4, 8, 15, 30, 60,
)
ISO_STEPS: tuple[int, ...] = (100, 200, 400, 800, 1600, 3200)
EV_STEPS: tuple[float, ...] = (-2.0, -1.5, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5, 2.0)


def shutter_steps(max_exposure_s: float) -> list[float]:
    """Shutter steps the sensor can actually do."""
    steps = [s for s in SHUTTER_STEPS if s <= max_exposure_s * 1.001]
    return steps or [SHUTTER_STEPS[0]]


def iso_steps(max_gain: float) -> list[int]:
    """ISO steps within the sensor's analogue gain range (ISO 100 = gain 1)."""
    steps = [i for i in ISO_STEPS if i / 100 <= max_gain * 1.001]
    return steps or [ISO_STEPS[0]]


def nearest(values: Sequence[float], target: float) -> float:
    """Nearest value in log space (photographic stops)."""
    target = max(target, 1e-9)
    return min(values, key=lambda v: abs(math.log2(v) - math.log2(target)))


# Mode -> values the A/B buttons adjust in live view (Y cycles between them).
MODES = ("P", "Tv", "Sv", "M")
MODE_PARAMS: dict[str, tuple[str, ...]] = {
    "P": ("ev",),
    "Tv": ("shutter", "ev"),
    "Sv": ("iso", "ev"),
    "M": ("shutter", "iso"),
}


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
