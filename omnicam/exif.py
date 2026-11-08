"""Minimal EXIF writing/reading for saved photos."""

from __future__ import annotations

from datetime import datetime
from fractions import Fraction
from typing import Optional

from PIL import Image

_EXIF_IFD = 0x8769
_MAKE, _MODEL, _DATETIME, _SOFTWARE = 0x010F, 0x0110, 0x0132, 0x0131
_EXPOSURE, _ISO, _ORIGINAL, _EV_BIAS = 0x829A, 0x8827, 0x9003, 0x9204


def _rational(value: float):
    from PIL.TiffImagePlugin import IFDRational

    fr = Fraction(value).limit_denominator(100000)
    return IFDRational(fr.numerator, fr.denominator)


def build(model: str, exposure_s: float, gain: float, ev: float, when: datetime) -> bytes:
    """EXIF bytes for Image.save(exif=...). Returns b'' if this Pillow can't do it."""
    try:
        exif = Image.Exif()
        stamp = when.strftime("%Y:%m:%d %H:%M:%S")
        exif[_MAKE] = "Raspberry Pi"
        exif[_MODEL] = model
        exif[_SOFTWARE] = "omnicam"
        exif[_DATETIME] = stamp
        ifd = exif.get_ifd(_EXIF_IFD)
        ifd[_EXPOSURE] = _rational(exposure_s)
        ifd[_ISO] = int(round(gain * 100))
        ifd[_ORIGINAL] = stamp
        ifd[_EV_BIAS] = _rational(ev)
        return exif.tobytes()
    except Exception:  # older Pillow without writable sub-IFDs
        return b""


def read(img: Image.Image) -> dict:
    """{'exposure_s', 'iso', 'datetime'} where available."""
    out: dict = {}
    try:
        exif = img.getexif()
        ifd = exif.get_ifd(_EXIF_IFD)
        if _EXPOSURE in ifd:
            out["exposure_s"] = float(ifd[_EXPOSURE])
        if _ISO in ifd:
            out["iso"] = int(ifd[_ISO])
        if _EV_BIAS in ifd:
            out["ev"] = float(ifd[_EV_BIAS])
        if exif.get(_MODEL):
            out["model"] = str(exif[_MODEL]).strip()
        stamp: Optional[str] = ifd.get(_ORIGINAL) or exif.get(_DATETIME)
        if stamp:
            out["datetime"] = datetime.strptime(stamp, "%Y:%m:%d %H:%M:%S")
    except Exception:
        pass
    return out
