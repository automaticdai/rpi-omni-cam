"""Menu definitions: the tabbed main menu and the quick-menu tiles.

Main menu: six tabs, each short enough to fit the screen without scrolling.
Settings are changed in place (◀ value ▶); long lists open a picker; On/Off
settings toggle. Every item has a one-line help text shown at the bottom.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Union

from . import filters
from .exposure import EV_STEPS, format_ev

PICKER_MIN_OPTIONS = 7  # lists this long open a picker instead of ◀ ▶ editing


@dataclass
class Choice:
    label: str
    key: str  # Settings attribute
    options: list  # [(value, label)]
    help: str = ""
    value_help: dict = field(default_factory=dict)  # value -> help line
    short: dict = field(default_factory=dict)  # value -> compact label (quick menu)

    def value_label(self, value: Any) -> str:
        for v, lbl in self.options:
            if v == value:
                return lbl
        return str(value)

    def short_label(self, value: Any) -> str:
        if value in self.short:
            return self.short[value]
        return self.value_label(value).split(" · ")[0]

    @property
    def is_toggle(self) -> bool:
        return [v for v, _ in self.options] == [False, True]

    @property
    def uses_picker(self) -> bool:
        return len(self.options) >= PICKER_MIN_OPTIONS

    def help_for(self, value: Any) -> str:
        return self.value_help.get(value, self.help)


@dataclass
class Action:
    label: str
    action: str  # handled by App.run_action
    help: str = ""


@dataclass
class Section:
    """A sub-page inside a tab (opened with Y)."""
    label: str
    items: list = field(default_factory=list)
    help: str = ""


@dataclass
class Tab:
    name: str
    items: list


Item = Union[Choice, Action, Section]

_ON_OFF = [(False, "Off"), (True, "On")]
_LEVELS = [("low", "Low"), ("normal", "Normal"), ("high", "High")]


def _seconds(v: int) -> str:
    if v >= 3600:
        return f"{v // 3600} h"
    if v >= 60:
        return f"{v // 60} min"
    return f"{v} s"


# Function button (B in live view) choices.
FN_ACTIONS = [
    ("playback", "Playback"), ("magnify", "Magnify 1×/2×/4×"), ("drive", "Drive mode (cycle)"),
    ("filter", "Filter (cycle)"), ("wb", "White balance (cycle)"), ("mode", "Mode (cycle)"),
    ("metering", "Metering (cycle)"), ("af_mode", "Focus mode (cycle)"),
    ("peaking", "Focus peaking (cycle)"), ("zebras", "Highlight warning on/off"),
    ("grid", "Grid (cycle)"), ("histogram", "Histogram on/off"),
    ("self_timer", "Self-timer (cycle)"), ("timelapse", "Timelapse start/stop"),
    ("usb_export", "USB export"),
]

# ---- settings ----------------------------------------------------------------
MODE = Choice("Mode", "mode", [
    ("P", "P · Program"), ("A", "A · Aperture priority"),
    ("S", "S · Shutter priority"), ("M", "M · Manual")],
    help="Which exposure values you set yourself",
    value_help={"P": "Camera sets shutter and ISO; Y adjusts EV",
                "A": "You set ISO (aperture on the lens); camera sets shutter",
                "S": "You set the shutter; camera sets ISO",
                "M": "You set shutter and ISO"})
DRIVE = Choice("Drive", "drive", [
    ("single", "Single"), ("burst5", "Burst ×5"), ("burst10", "Burst ×10"),
    ("bracket3_1", "Bracket 3 · ±1 EV"), ("bracket3_2", "Bracket 3 · ±2 EV"),
    ("bracket5_1", "Bracket 5 · ±1,2 EV")],
    help="What one press of X shoots",
    short={"single": "Single", "burst5": "Burst 5", "burst10": "Burst 10",
           "bracket3_1": "BKT3 ±1", "bracket3_2": "BKT3 ±2", "bracket5_1": "BKT5"},
    value_help={"single": "One photo per press",
                "burst5": "5 fast frames at reduced size",
                "burst10": "10 fast frames at reduced size",
                "bracket3_1": "3 frames at −1, 0, +1 EV",
                "bracket3_2": "3 frames at −2, 0, +2 EV",
                "bracket5_1": "5 frames at −2, −1, 0, +1, +2 EV"})
SELF_TIMER = Choice("Self-timer", "self_timer", [(0, "Off"), (2, "2 s"), (10, "10 s")],
                    help="Delay between pressing X and the shot")
EV = Choice("Exposure comp.", "ev", [(v, format_ev(v)) for v in EV_STEPS],
            help="Make auto exposure darker (−) or brighter (+)")
METERING = Choice("Metering", "metering", [
    ("centre", "Centre-weighted"), ("spot", "Spot"), ("matrix", "Matrix")],
    help="Which part of the scene sets the exposure",
    short={"centre": "Centre"},
    value_help={"centre": "Whole frame, centre counts most",
                "spot": "Only a small spot in the centre",
                "matrix": "Whole frame, evenly"})
WB = Choice("White balance", "wb", [
    ("auto", "Auto"), ("daylight", "Daylight"), ("cloudy", "Cloudy"),
    ("tungsten", "Tungsten"), ("fluorescent", "Fluorescent")],
    help="Colour of the light, so whites look white",
    short={"daylight": "Day", "fluorescent": "Fluor.", "tungsten": "Tungst."})
FILTER = Choice("Filter", "filter", [(k, lbl) for k, (lbl, _) in filters.FILTERS.items()],
                help="Look applied to the preview and the saved JPEG",
                short={"contrast": "Contrast"})
IMAGE_SIZE = Choice("Image size", "image_size", [("L", "L · Full"), ("M", "M · ½"), ("S", "S · ¼")],
                    help="Pixels saved: full, about half, or a quarter")
ASPECT = Choice("Aspect ratio", "aspect", [("native", "Native (sensor)")] +
                [(a, a) for a in ("4:3", "3:2", "16:9", "1:1")],
                help="Crop of the saved photo; the live view shades the rest",
                short={"native": "Native"})
JPEG_QUALITY = Choice("JPEG quality", "jpeg_quality", [(95, "Fine"), (90, "Normal"), (75, "Basic")],
                      help="Higher quality means bigger files")
SAVE_RAW = Choice("Save RAW (DNG)", "save_raw", _ON_OFF,
                  help="Also save the unprocessed sensor data (unfiltered)")
SHARPNESS = Choice("Sharpness", "sharpness", _LEVELS, help="Edge sharpening in the JPEG")
CONTRAST = Choice("Contrast", "contrast", _LEVELS, help="Difference between darks and lights")
SATURATION = Choice("Saturation", "saturation", _LEVELS, help="Colour intensity")
NOISE = Choice("Noise reduction", "noise_reduction", [
    ("off", "Off"), ("fast", "Fast"), ("hq", "High quality")],
    help="Smooths grain in the saved photo; Off keeps the most detail")
AF_MODE = Choice("Focus mode", "af_mode", [
    ("continuous", "AF-C · Continuous"), ("single", "AF-S · Single"), ("manual", "MF · Manual")],
    help="Autofocus cameras only (e.g. Camera Module 3)",
    value_help={"continuous": "Keeps focusing all the time",
                "single": "Focuses once when you press X",
                "manual": "Y lets you set the distance with A/B"})
PEAKING = Choice("Focus peaking", "peaking", [
    ("off", "Off"), ("red", "Red"), ("yellow", "Yellow"), ("blue", "Blue")],
    help="Colours sharp edges in the live view")
ZEBRAS = Choice("Highlight warning", "zebras", _ON_OFF,
                help="Stripes over blown-out areas (live view and playback)")
GRID = Choice("Grid", "grid", [("off", "Off"), ("thirds", "Thirds"), ("diagonal", "Thirds + diagonals")],
              help="Composition lines over the live view")
HISTOGRAM = Choice("Histogram", "histogram", _ON_OFF, help="Brightness graph in the live view")
TL_INTERVAL = Choice("Interval", "tl_interval",
                     [(v, _seconds(v)) for v in (1, 2, 5, 10, 30, 60, 300, 600, 1800, 3600)],
                     help="Time between timelapse frames")
TL_COUNT = Choice("Shots", "tl_count",
                  [(0, "Unlimited")] + [(v, str(v)) for v in (10, 30, 60, 100, 300, 500, 1000)],
                  help="Frames before the timelapse stops by itself")
TL_LOCK = Choice("Exposure lock", "tl_lock_exposure", _ON_OFF,
                 help="Keep exposure and colour fixed so frames don't flicker")
FN_BUTTON = Choice("B press", "fn_button", FN_ACTIONS,
                   help="What a short press of B does in the live view")
FN_BUTTON_LONG = Choice("B hold", "fn_button_long", [("none", "None")] + FN_ACTIONS,
                        help="What holding B (0.7 s) does in the live view")
BRIGHTNESS = Choice("Brightness", "brightness", [(v, f"{v} %") for v in (25, 50, 75, 100)],
                    help="Screen backlight")
SCREEN_OFF = Choice("Screen off", "screen_off",
                    [(0, "Never"), (30, "30 s"), (60, "1 min"), (300, "5 min")],
                    help="Turn the screen off after this long without a press")

TABS: list[Tab] = [
    Tab("SHOOT", [MODE, DRIVE, SELF_TIMER, EV, METERING, WB]),
    Tab("IMAGE", [FILTER, IMAGE_SIZE, ASPECT, JPEG_QUALITY, SAVE_RAW]),
    Tab("STYLE", [SHARPNESS, CONTRAST, SATURATION, NOISE]),
    Tab("FOCUS", [AF_MODE, PEAKING, ZEBRAS, GRID, HISTOGRAM]),
    Tab("TL", [TL_INTERVAL, TL_COUNT, TL_LOCK,
               Action("Start timelapse", "timelapse_start", help="Hold X in the live view also starts/stops")]),
    Tab("SETUP", [
        FN_BUTTON, FN_BUTTON_LONG, BRIGHTNESS, SCREEN_OFF,
        Section("Storage", [
            Action("Playback", "playback", help="Browse, zoom and delete photos"),
            Action("USB export", "usb_export", help="Show the photos as a USB drive on a computer"),
            Action("Storage info", "storage_info", help="Space used and photos stored"),
            Action("Erase all photos", "erase", help="Delete every photo (asks first)"),
        ], help="Playback, USB export, space, erase"),
        Section("About", [
            Action("Camera info", "camera_info", help="Version, board, sensor, temperature, battery"),
            Action("Reset settings", "reset", help="Restore all settings to their defaults"),
            Action("Shut down", "shutdown", help="Power off the Pi (unplug afterwards)"),
        ], help="Camera info, reset settings, shut down"),
    ]),
]

# Quick menu (A in live view): eight tiles, changed in place.
QUICK: list[Choice] = [MODE, DRIVE, EV, WB, METERING, AF_MODE, FILTER, ASPECT]


def all_choices(items=None):
    """Every Choice in the menu (used by the function button)."""
    if items is None:
        items = [i for tab in TABS for i in tab.items]
    for item in items:
        if isinstance(item, Choice):
            yield item
        elif isinstance(item, Section):
            yield from all_choices(item.items)


def choice_for(key: str) -> Optional[Choice]:
    return next((c for c in all_choices() if c.key == key), None)
