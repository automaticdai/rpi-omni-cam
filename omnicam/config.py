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
    # Exposure. mode: P (all auto), Tv (shutter set), Sv (ISO set), M (both set).
    # shutter/iso hold the manual values; they only apply in modes that use them.
    mode: str = "P"
    shutter: float = 1 / 125  # seconds
    iso: int = 200
    ev: float = 0.0
    wb: str = "auto"
    # Image
    filter: str = "none"
    jpeg_quality: int = 90
    save_raw: bool = False
    self_timer: int = 0  # seconds, 0 = off
    # Timelapse
    tl_interval: int = 10  # seconds
    tl_count: int = 0  # 0 = unlimited
    tl_lock_exposure: bool = True
    # Display
    grid: bool = False
    histogram: bool = False
    brightness: int = 100  # percent
    screen_off: int = 60  # seconds, 0 = never
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
        if settings.mode not in ("P", "Tv", "Sv", "M"):
            settings.mode = defaults.mode
        if not settings.shutter:  # older files stored None for AUTO
            settings.shutter = defaults.shutter
        if not settings.iso:
            settings.iso = defaults.iso
        return settings

    @property
    def exp_shutter(self) -> Optional[float]:
        """Shutter to use, or None when the mode leaves it to auto-exposure."""
        return self.shutter if self.mode in ("Tv", "M") else None

    @property
    def exp_iso(self) -> Optional[int]:
        """ISO to use, or None when the mode leaves it to auto-exposure."""
        return self.iso if self.mode in ("Sv", "M") else None

    def save(self, path: Path = DEFAULT_CONFIG_PATH) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2))
        os.replace(tmp, path)
