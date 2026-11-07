"""Menu tree definition."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Union

from . import filters
from .exposure import EV_STEPS, format_ev


@dataclass
class Choice:
    label: str
    key: str  # Settings attribute
    options: list  # [(value, label)]

    def value_label(self, value: Any) -> str:
        for v, lbl in self.options:
            if v == value:
                return lbl
        return str(value)


@dataclass
class Action:
    label: str
    action: str  # handled by App.run_action
    value: str = ""


@dataclass
class Section:
    label: str
    items: list = field(default_factory=list)


Item = Union[Choice, Action, Section]

_ON_OFF = [(False, "Off"), (True, "On")]


def _seconds(v: int) -> str:
    if v >= 3600:
        return f"{v // 3600} h"
    if v >= 60:
        return f"{v // 60} min"
    return f"{v} s"


MENU: list[Item] = [
    Choice("Mode", "mode", [
        ("P", "P · Program"), ("Tv", "Tv · Shutter priority"),
        ("Sv", "Sv · ISO priority"), ("M", "M · Manual")]),
    Section("Shooting", [
        Choice("White balance", "wb", [
            ("auto", "Auto"), ("daylight", "Daylight"), ("cloudy", "Cloudy"),
            ("tungsten", "Tungsten"), ("fluorescent", "Fluorescent")]),
        Choice("Exposure comp.", "ev", [(v, format_ev(v)) for v in EV_STEPS]),
        Choice("Filter", "filter", [(k, lbl) for k, (lbl, _) in filters.FILTERS.items()]),
        Choice("JPEG quality", "jpeg_quality", [(95, "Fine"), (90, "Normal"), (75, "Basic")]),
        Choice("Save RAW (DNG)", "save_raw", _ON_OFF),
        Choice("Self-timer", "self_timer", [(0, "Off"), (2, "2 s"), (10, "10 s")]),
    ]),
    Section("Timelapse", [
        Choice("Interval", "tl_interval",
               [(v, _seconds(v)) for v in (1, 2, 5, 10, 30, 60, 300, 600, 1800, 3600)]),
        Choice("Shots", "tl_count",
               [(0, "Unlimited")] + [(v, str(v)) for v in (10, 30, 60, 100, 300, 500, 1000)]),
        Choice("Exposure lock", "tl_lock_exposure", _ON_OFF),
    ]),
    Section("Display", [
        Choice("Grid", "grid", _ON_OFF),
        Choice("Histogram", "histogram", _ON_OFF),
        Choice("Brightness", "brightness", [(v, f"{v} %") for v in (25, 50, 75, 100)]),
        Choice("Screen off", "screen_off", [(0, "Never"), (30, "30 s"), (60, "1 min"), (300, "5 min")]),
    ]),
    Action("USB export", "usb_export"),
    Section("System", [
        Action("Storage info", "storage_info"),
        Action("Erase all photos", "erase"),
        Action("Camera info", "camera_info"),
        Action("Shut down", "shutdown"),
    ]),
]
