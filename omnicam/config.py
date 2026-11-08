"""Persistent user settings."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Optional

DEFAULT_CONFIG_PATH = Path(
    os.environ.get("OMNICAM_CONFIG", Path.home() / ".config" / "omnicam" / "settings.json")
)


@dataclass
class Settings:
    # Exposure mode, Sony naming: P (all auto), A (aperture priority: ISO set,
    # aperture on the lens ring, shutter auto), S (shutter set), M (both set),
    # MOVIE (video) and SQ (slow motion). shutter/iso hold the manual values;
    # they only apply in modes that use them.
    mode: str = "P"
    shutter: float = 1 / 125  # seconds
    iso: int = 200
    ev: float = 0.0
    wb: str = "auto"
    metering: str = "centre"  # centre | spot | matrix
    # single | burst5 | burst10 | bracket3_1 | bracket3_2 | bracket5_1 | night4 | night8 | double
    drive: str = "single"
    long_exposure: bool = False  # offer shutter speeds past 1 s
    # Focus (autofocus cameras only)
    af_mode: str = "continuous"  # continuous | single | manual
    lens_position: float = 1.0  # dioptres (1/m); 0 = infinity
    # Image
    filter: str = "none"
    sharpness: str = "normal"  # low | normal | high
    contrast: str = "normal"
    saturation: str = "normal"
    noise_reduction: str = "hq"  # off | fast | hq
    image_size: str = "L"  # L | M | S
    aspect: str = "native"  # native (sensor) | 4:3 | 3:2 | 16:9 | 1:1
    jpeg_quality: int = 90
    save_raw: bool = False
    self_timer: int = 0  # seconds, 0 = off
    # Timelapse
    tl_interval: int = 10  # seconds
    tl_count: int = 0  # 0 = unlimited
    tl_lock_exposure: bool = True
    tl_video: bool = True  # make an MP4 from the frames when the timelapse ends
    tl_video_fps: int = 24
    # Video
    video_size: str = "1080p"  # 1080p | 720p
    sq_fps: int = 120  # slow-motion capture rate; played back at 30 fps
    # Display
    grid: str = "off"  # off | thirds | diagonal (thirds + diagonals)
    histogram: bool = False
    peaking: str = "off"  # off | red | yellow | blue
    zebras: bool = False
    brightness: int = 100  # percent
    screen_off: int = 60  # seconds, 0 = never
    # Controls
    fn_button: str = "playback"  # B press in live view, see menu.FN_ACTIONS
    fn_button_long: str = "magnify"  # B hold (0.7 s) in live view; "none" = nothing
    left_handed: bool = False  # mirror the buttons: A<->X, B<->Y
    rotate180: bool = False  # camera mounted upside down: rotate screen, image and buttons
    # Wi-Fi and the phone web page
    wifi: str = "home"  # off | home | hotspot | auto (home, else hotspot)
    web: bool = False  # phone gallery / remote over Wi-Fi
    hotspot_password: str = ""  # generated on first use
    # Saved setting sets (Custom 1/2): {setting: value}
    custom1: Optional[dict] = None
    custom2: Optional[dict] = None
    # Storage
    photo_dir: str = "/media/photos"

    @classmethod
    def load(cls, path: Path = DEFAULT_CONFIG_PATH) -> "Settings":
        """Load settings, ignoring unknown keys and falling back to defaults on any error."""
        try:
            data = json.loads(Path(path).read_text())
        except (OSError, ValueError):
            return cls()
        if not isinstance(data, dict):
            return cls()
        settings = cls()
        defaults = cls()
        for f in fields(cls):
            if f.name in data:
                setattr(settings, f.name, data[f.name])
        settings.mode = {"Tv": "S", "Sv": "A"}.get(settings.mode, settings.mode)  # pre-0.3 names
        if settings.mode not in ("P", "A", "S", "M", "MOVIE", "SQ"):
            settings.mode = defaults.mode
        if not settings.shutter:  # older files stored None for AUTO
            settings.shutter = defaults.shutter
        from .exposure import LONG_SHUTTER_S, MAX_SHUTTER_S
        settings.shutter = min(settings.shutter,
                               LONG_SHUTTER_S if settings.long_exposure else MAX_SHUTTER_S)
        if not settings.iso:
            settings.iso = defaults.iso
        if isinstance(settings.grid, bool):  # pre-0.4: grid was on/off
            settings.grid = "thirds" if settings.grid else "off"
        if settings.grid not in ("off", "thirds", "diagonal"):
            settings.grid = defaults.grid
        return settings

    @property
    def exp_shutter(self) -> Optional[float]:
        """Shutter to use, or None when the mode leaves it to auto-exposure."""
        return self.shutter if self.mode in ("S", "M") else None

    @property
    def exp_iso(self) -> Optional[int]:
        """ISO to use, or None when the mode leaves it to auto-exposure."""
        return self.iso if self.mode in ("A", "M") else None

    @property
    def video_mode(self) -> bool:
        return self.mode in ("MOVIE", "SQ")

    def save(self, path: Path = DEFAULT_CONFIG_PATH) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2))
        os.replace(tmp, path)
