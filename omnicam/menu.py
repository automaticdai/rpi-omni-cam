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
    ("usb_export", "USB export"), ("custom1", "Recall Custom 1"), ("custom2", "Recall Custom 2"),
    ("web", "Phone access on/off"), ("wifi_info", "Wi-Fi & Remote info"),
]

# ---- settings ----------------------------------------------------------------
MODE = Choice("Mode", "mode", [
    ("P", "P · Program"), ("A", "A · Aperture priority"),
    ("S", "S · Shutter priority"), ("M", "M · Manual"),
    ("MOVIE", "Movie · Video"), ("SQ", "S&Q · Slow motion")],
    help="Which exposure values you set yourself",
    value_help={"P": "Camera sets shutter and ISO; Y adjusts EV",
                "A": "You set ISO (aperture on the lens); camera sets shutter",
                "S": "You set the shutter; camera sets ISO",
                "M": "You set shutter and ISO",
                "MOVIE": "Video: X starts and stops recording (VID tab: size)",
                "SQ": "Slow-motion video at 640×480 (VID tab: speed)"})
DRIVE = Choice("Drive", "drive", [
    ("single", "Single"), ("burst5", "Burst ×5"), ("burst10", "Burst ×10"),
    ("bracket3_1", "Bracket 3 · ±1 EV"), ("bracket3_2", "Bracket 3 · ±2 EV"),
    ("bracket5_1", "Bracket 5 · ±1,2 EV"), ("night4", "Night · 4 frames"),
    ("night8", "Night · 8 frames"), ("double", "Double exposure")],
    help="What one press of X shoots",
    short={"single": "Single", "burst5": "Burst 5", "burst10": "Burst 10",
           "bracket3_1": "BKT3 ±1", "bracket3_2": "BKT3 ±2", "bracket5_1": "BKT5",
           "night4": "Night 4", "night8": "Night 8", "double": "Double"},
    value_help={"single": "One photo per press",
                "burst5": "5 fast frames at reduced size",
                "burst10": "10 fast frames at reduced size",
                "bracket3_1": "3 frames at −1, 0, +1 EV",
                "bracket3_2": "3 frames at −2, 0, +2 EV",
                "bracket5_1": "5 frames at −2, −1, 0, +1, +2 EV",
                "night4": "Averages 4 frames into one photo: less noise in the dark",
                "night8": "Averages 8 frames: even less noise; hold steady",
                "double": "Two shots blended into one; the first shows over the view"})
LONG_EXP = Choice("Long exposures", "long_exposure", _ON_OFF,
                  help="Offer shutter speeds past 1 s (S and M modes; use a tripod)")
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
VIDEO_SIZE = Choice("Video size", "video_size", [("1080p", "1080p · 30 fps"), ("720p", "720p · 30 fps")],
                    help="Movie mode recording size (MP4, H.264)")
SQ_FPS = Choice("Slow-motion speed", "sq_fps",
                [(60, "60 fps · 2× slower"), (120, "120 fps · 4× slower"), (200, "200 fps · 6.7× slower")],
                help="S&Q mode: frames per second captured; plays back at 30 fps")
TL_VIDEO = Choice("Make video", "tl_video", _ON_OFF,
                  help="Turn the frames into an MP4 when the timelapse ends")
TL_VIDEO_FPS = Choice("Video speed", "tl_video_fps", [(12, "12 fps"), (24, "24 fps"), (30, "30 fps")],
                      help="Frames per second of the timelapse video")
WIFI = Choice("Wi-Fi", "wifi", [("off", "Off"), ("home", "Home network"), ("hotspot", "Camera hotspot"),
                                ("auto", "Auto")],
              help="How the camera connects for the phone page",
              value_help={"off": "Wi-Fi radio off (saves battery)",
                          "home": "Join the saved home network",
                          "hotspot": "The camera makes its own network for your phone",
                          "auto": "Home network; hotspot if none is found within a minute"})
WEB = Choice("Phone access", "web", _ON_OFF,
             help="Gallery and remote control in a phone browser over Wi-Fi")
LEFT_HANDED = Choice("Left-handed", "left_handed", _ON_OFF,
                     help="Mirror the buttons: shutter and Y on the left side")
ROTATE = Choice("Upside down", "rotate180", _ON_OFF,
                help="Camera mounted upside down: rotates screen, photos and buttons")
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
    Tab("SHOT", [MODE, DRIVE, SELF_TIMER, EV, METERING, LONG_EXP]),
    Tab("IMG", [WB, IMAGE_SIZE, ASPECT, JPEG_QUALITY, SAVE_RAW]),
    Tab("FOCUS", [AF_MODE, PEAKING, ZEBRAS, GRID, HISTOGRAM]),
    Tab("STY", [FILTER, SHARPNESS, CONTRAST, SATURATION, NOISE]),
    Tab("VID", [VIDEO_SIZE, SQ_FPS]),
    Tab("TL", [TL_INTERVAL, TL_COUNT, TL_LOCK, TL_VIDEO, TL_VIDEO_FPS,
               Action("Start timelapse", "timelapse_start", help="Hold X in the live view also starts/stops")]),
    Tab("SETUP", [
        Section("Wi-Fi & Remote", [
            WIFI, WEB,
            Action("Connection info", "wifi_info", help="Network, address and QR codes for your phone"),
        ], help="Wi-Fi, hotspot, phone gallery and remote"),
        Section("Custom sets", [
            Action("Recall Custom 1", "recall1", help="Load the settings saved in Custom 1"),
            Action("Recall Custom 2", "recall2", help="Load the settings saved in Custom 2"),
            Action("Save to Custom 1", "save1", help="Save the current shooting settings as Custom 1"),
            Action("Save to Custom 2", "save2", help="Save the current shooting settings as Custom 2"),
        ], help="Save and recall two sets of shooting settings"),
        Section("Controls", [FN_BUTTON, FN_BUTTON_LONG, LEFT_HANDED, ROTATE],
                help="B button functions, left-handed, upside down"),
        Section("Display", [BRIGHTNESS, SCREEN_OFF], help="Screen brightness, screen-off timer"),
        Section("Storage", [
            Action("Playback", "playback", help="Browse, inspect and delete photos and videos"),
            Action("USB export", "usb_export", help="Show the photos as a USB drive on a computer"),
            Action("Storage info", "storage_info", help="Space used and photos stored"),
            Action("Erase all photos", "erase", help="Delete every photo and video (asks first)"),
        ], help="Playback, USB export, space, erase"),
        Section("System", [
            Action("Date & time", "datetime",
                   help="Set the clock; the Pi has no clock battery, so set it when offline"),
            Action("Camera info", "camera_info", help="Version, board, sensor, temperature, battery"),
            Action("Reset settings", "reset", help="Restore all settings to their defaults"),
            Action("Shut down", "shutdown", help="Power off the Pi (unplug afterwards)"),
        ], help="Date & time, camera info, reset, shut down"),
    ]),
]

# Settings a custom set saves: shooting settings, not setup.
CUSTOM_KEYS = ("mode", "shutter", "iso", "ev", "wb", "metering", "drive", "long_exposure",
               "af_mode", "lens_position", "filter", "sharpness", "contrast", "saturation",
               "noise_reduction", "image_size", "aspect", "jpeg_quality", "save_raw",
               "self_timer", "grid", "histogram", "peaking", "zebras", "video_size", "sq_fps")

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
