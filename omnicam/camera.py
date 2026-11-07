"""Camera backends: Picamera2 on the Pi, and a synthetic camera for --sim."""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw

from .config import Settings

PREVIEW_MAX_EXPOSURE_S = 1 / 15
log = logging.getLogger(__name__)
SCREEN_W, SCREEN_H = 320, 240


@dataclass
class CameraInfo:
    model: str
    resolution: tuple[int, int]
    max_exposure_s: float
    max_gain: float
    has_af: bool = False  # autofocus lens (Camera Module 3)


# Picture style levels -> libcamera control values (1.0 = normal).
STYLE_VALUES = {
    "sharpness": {"low": 0.5, "normal": 1.0, "high": 2.0},
    "contrast": {"low": 0.8, "normal": 1.0, "high": 1.25},
    "saturation": {"low": 0.7, "normal": 1.0, "high": 1.4},
}


def scaled_size(resolution: tuple[int, int], image_size: str) -> tuple[int, int]:
    """Capture size for L / M / S (M ≈ half the pixels, S ≈ a quarter)."""
    scale = {"L": 1.0, "M": 0.71, "S": 0.5}.get(image_size, 1.0)
    w, h = (int(v * scale) for v in resolution)
    return w - w % 2, h - h % 2


@dataclass
class FrameMeta:
    exposure_s: float = 0.0
    gain: float = 1.0
    colour_gains: Optional[tuple[float, float]] = None
    # Extra stops the saved photo will get compared with this preview frame
    # (the preview caps the shutter so it stays live during long exposures).
    ev_bias: float = 0.0


def preview_size(sensor: tuple[int, int]) -> tuple[int, int]:
    """Largest even size with the sensor's aspect ratio that fits 320x240."""
    sw, sh = sensor
    w, h = SCREEN_W, round(SCREEN_W * sh / sw)
    if h > SCREEN_H:
        w, h = round(SCREEN_H * sw / sh), SCREEN_H
    return w - w % 2, h - h % 2


def preview_exposure(settings: Settings, max_gain: float) -> tuple[Optional[float], Optional[float], float]:
    """(shutter_s, gain, ev_bias) to use for the live preview.

    Long shutters are capped so the preview stays live; the lost light is made
    up with extra gain where the sensor allows, and the rest reported as ev_bias.
    """
    shutter = settings.exp_shutter
    gain = settings.exp_iso / 100 if settings.exp_iso else None
    if shutter is None or shutter <= PREVIEW_MAX_EXPOSURE_S:
        return shutter, gain, 0.0
    lost = shutter / PREVIEW_MAX_EXPOSURE_S
    if gain is None:  # S mode: AE will raise the gain itself
        return PREVIEW_MAX_EXPOSURE_S, None, 0.0
    boosted = min(gain * lost, max_gain)
    return PREVIEW_MAX_EXPOSURE_S, boosted, math.log2(gain * lost / boosted)


# ---------------------------------------------------------------------------
class PiCamera:
    """Picamera2 / libcamera backend."""

    WB_MODES = {
        "auto": "Auto", "daylight": "Daylight", "cloudy": "Cloudy",
        "tungsten": "Tungsten", "fluorescent": "Fluorescent",
    }

    def __init__(self):
        from picamera2 import Picamera2  # imported lazily: only exists on the Pi
        from libcamera import controls as lc

        self._lc = lc
        self.cam = Picamera2()
        props = self.cam.camera_properties
        ctrls = self.cam.camera_controls
        sensor = tuple(props["PixelArraySize"])
        # camera_controls["ExposureTime"] only reflects the default frame
        # duration (e.g. 66 ms = 1/15 s), not what the sensor can do; the
        # per-mode exposure_limits do.
        _, max_exp_us, _ = ctrls["ExposureTime"]
        try:
            mode_max = max(m["exposure_limits"][1] for m in self.cam.sensor_modes
                           if m.get("exposure_limits"))
            max_exp_us = max(max_exp_us, mode_max)
        except (KeyError, TypeError, ValueError):
            pass
        _, max_gain, _ = ctrls["AnalogueGain"]
        self.info = CameraInfo(
            model=str(props.get("Model", "unknown")),
            resolution=sensor,
            max_exposure_s=max_exp_us / 1e6,
            max_gain=float(max_gain),
            has_af="AfMode" in ctrls,
        )
        self.preview_size = preview_size(sensor)
        self._ctrl_names = set(ctrls)
        # Full-view crop of the *preview* mode; measured in start() from the
        # frame metadata. (The ScalerCropMaximum property reflects the camera's
        # default mode, which can be a centre crop, e.g. IMX219 640x480.)
        self._crop_max: tuple = (0, 0, *sensor)
        self._zoom = 1
        self._new_style = "ExposureTimeMode" in ctrls  # libcamera >= 0.5
        self._lock: Optional[FrameMeta] = None
        # Ask for a 2x2-binned full-sensor mode: left alone, libcamera picks
        # the mode closest to 320x240, which on some sensors (e.g. IMX219's
        # 640x480) is a centre crop, so the preview would not match the photo.
        binned = (sensor[0] // 2, sensor[1] // 2)
        main = {"size": self.preview_size, "format": "RGB888"}
        try:
            self._preview_cfg = self.cam.create_preview_configuration(
                main=main, sensor={"output_size": binned}, buffer_count=3)
        except TypeError:  # older picamera2 without the sensor= argument
            self._preview_cfg = self.cam.create_preview_configuration(
                main=main, raw={"size": binned}, buffer_count=3)
        self._ev_bias = 0.0

    def start(self) -> None:
        self.cam.configure(self._preview_cfg)
        self.cam.start()
        self._zoom = 1
        try:
            crop = self.cam.capture_metadata().get("ScalerCrop")
            if crop and crop[2] > 0 and crop[3] > 0:
                self._crop_max = tuple(crop)
        except Exception:
            log.exception("could not read the preview crop")
        log.info("preview full-view crop: %s", self._crop_max)

    def stop(self) -> None:
        self.cam.stop()

    def close(self) -> None:
        try:
            self.cam.stop()
        finally:
            self.cam.close()

    def _controls(self, shutter: Optional[float], gain: Optional[float], settings: Settings,
                  still: bool = False) -> dict:
        lc = self._lc
        c: dict = {
            "AwbEnable": True,
            "AwbMode": getattr(lc.AwbModeEnum, self.WB_MODES.get(settings.wb, "Auto")),
            "ExposureValue": float(settings.ev),
            "AeMeteringMode": getattr(lc.AeMeteringModeEnum, {
                "centre": "CentreWeighted", "spot": "Spot", "matrix": "Matrix"}.get(settings.metering, "CentreWeighted")),
            "Sharpness": STYLE_VALUES["sharpness"].get(settings.sharpness, 1.0),
            "Contrast": STYLE_VALUES["contrast"].get(settings.contrast, 1.0),
            "Saturation": STYLE_VALUES["saturation"].get(settings.saturation, 1.0),
        }
        if still:  # the preview keeps libcamera's fast default
            nr = getattr(getattr(lc, "draft", lc), "NoiseReductionModeEnum", None)
            if nr is not None:
                c["NoiseReductionMode"] = getattr(nr, {
                    "off": "Off", "fast": "Fast", "hq": "HighQuality"}.get(settings.noise_reduction, "HighQuality"))
        if self.info.has_af:
            if settings.af_mode == "manual":
                c["AfMode"] = lc.AfModeEnum.Manual
                c["LensPosition"] = float(settings.lens_position)
            elif settings.af_mode == "single":
                c["AfMode"] = lc.AfModeEnum.Auto
            else:
                c["AfMode"] = lc.AfModeEnum.Continuous
        if self._lock is not None:
            shutter, gain = self._lock.exposure_s, self._lock.gain
            if self._lock.colour_gains:
                c.update(AwbEnable=False, ColourGains=self._lock.colour_gains)
        exp_us = int(shutter * 1e6) if shutter else 0
        if self._new_style:
            c["ExposureTimeMode"] = 1 if shutter else 0  # 1 = manual
            c["AnalogueGainMode"] = 1 if gain else 0
            if shutter:
                c["ExposureTime"] = exp_us
            if gain:
                c["AnalogueGain"] = float(gain)
        else:
            # Raspberry Pi AGC: a non-zero value fixes that parameter, 0 hands it back to AE.
            c["AeEnable"] = not (shutter and gain)
            c["ExposureTime"] = exp_us
            c["AnalogueGain"] = float(gain or 0.0)
        if shutter:
            frame_us = max(exp_us + 20000, 33333)
            c["FrameDurationLimits"] = (100, frame_us)
        if self._lock is not None and self._lock.colour_gains:
            c.pop("AwbMode", None)
        # Only send what this camera/libcamera supports (e.g. NoiseReductionMode, AfMode).
        return {k: v for k, v in c.items() if k in self._ctrl_names}

    def apply(self, settings: Settings) -> None:
        self._settings = settings
        shutter, gain, self._ev_bias = preview_exposure(settings, self.info.max_gain)
        c = self._controls(shutter, gain, settings)
        if not shutter:
            c["FrameDurationLimits"] = (100, 66666)  # keep preview >= 15 fps under AE
        if "ScalerCrop" in self._ctrl_names and self._zoom > 1:
            c["ScalerCrop"] = self._crop()
        self.cam.set_controls(c)

    def _crop(self) -> tuple[int, int, int, int]:
        x, y, w, h = self._crop_max
        z = self._zoom
        cw, ch = w // z, h // z
        return (x + (w - cw) // 2, y + (h - ch) // 2, cw, ch)

    def set_zoom(self, level: int) -> None:
        """Focus magnifier: crop the preview on the sensor (1, 2 or 4x)."""
        self._zoom = level
        if "ScalerCrop" in self._ctrl_names:
            log.info("zoom %dx: ScalerCrop %s (full %s)", level, self._crop(), self._crop_max)
            self.cam.set_controls({"ScalerCrop": self._crop()})

    def lock(self, meta: Optional[FrameMeta]) -> None:
        self._lock = meta

    @staticmethod
    def _meta(md: dict) -> FrameMeta:
        cg = md.get("ColourGains")
        return FrameMeta(
            exposure_s=md.get("ExposureTime", 0) / 1e6,
            gain=md.get("AnalogueGain", 1.0),
            colour_gains=tuple(cg) if cg else None,
        )

    def preview(self) -> tuple[Image.Image, FrameMeta]:
        req = self.cam.capture_request()
        try:
            img = req.make_image("main").convert("RGB")
            meta = self._meta(req.get_metadata())
        finally:
            req.release()
        meta.ev_bias = self._ev_bias
        return img, meta

    def _autofocus(self, settings: Settings) -> None:
        if self.info.has_af and settings.af_mode == "single":
            try:
                self.cam.autofocus_cycle()
            except Exception:  # focus failures shouldn't stop the shot
                pass

    def capture(self, settings: Settings, raw_path: Optional[Path],
                size: Optional[tuple[int, int]] = None) -> tuple[Image.Image, FrameMeta]:
        self._autofocus(settings)
        gain = settings.exp_iso / 100 if settings.exp_iso else None
        cfg = self.cam.create_still_configuration(
            main={"size": size or self.info.resolution, "format": "RGB888"},
            raw={} if raw_path else None,
            buffer_count=1,
            controls=self._controls(settings.exp_shutter, gain, settings, still=True),
        )
        req = self.cam.switch_mode_and_capture_request(cfg, wait=True)
        try:
            img = req.make_image("main").convert("RGB")
            meta = self._meta(req.get_metadata())
            if raw_path:
                req.save_dng(str(raw_path))
        finally:
            req.release()
        # Picamera2 switches back to the preview configuration, whose controls
        # don't include what we set at runtime, so re-apply them.
        self.apply(settings)
        return img, meta

    def burst(self, settings: Settings, count: int,
              size: tuple[int, int]) -> list[tuple[Image.Image, FrameMeta]]:
        """Capture `count` frames back to back in one still mode (fast, reduced size)."""
        self._autofocus(settings)
        gain = settings.exp_iso / 100 if settings.exp_iso else None
        cfg = self.cam.create_still_configuration(
            main={"size": size, "format": "RGB888"}, raw=None, buffer_count=2,
            controls=self._controls(settings.exp_shutter, gain, settings, still=True))
        frames = []
        self.cam.switch_mode(cfg)
        try:
            for _ in range(count):
                req = self.cam.capture_request()
                try:
                    frames.append((req.make_image("main").convert("RGB"), self._meta(req.get_metadata())))
                finally:
                    req.release()
        finally:
            self.cam.switch_mode(self._preview_cfg)
            self.apply(settings)
        return frames


# ---------------------------------------------------------------------------
class SimCamera:
    """Synthetic scene whose brightness responds to the exposure settings."""

    SCENE_EXPOSURE = 1 / 125 * 2.0  # shutter*gain giving a correct exposure

    def __init__(self):
        self.info = CameraInfo("simulated", (2028, 1520), 60.0, 16.0, has_af=True)
        self.preview_size = preview_size(self.info.resolution)
        self._zoom = 1
        self._settings = Settings()
        self._lock: Optional[FrameMeta] = None
        self._t0 = time.monotonic()

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def close(self) -> None:
        pass

    def apply(self, settings: Settings) -> None:
        self._settings = settings

    def lock(self, meta: Optional[FrameMeta]) -> None:
        self._lock = meta

    def set_zoom(self, level: int) -> None:
        self._zoom = level

    def _exposure(self, settings: Settings, preview: bool) -> FrameMeta:
        if self._lock:
            return FrameMeta(self._lock.exposure_s, self._lock.gain)
        target = self.SCENE_EXPOSURE * 2 ** settings.ev
        if preview:
            shutter, gain, bias = preview_exposure(settings, self.info.max_gain)
        else:
            shutter = settings.exp_shutter
            gain, bias = (settings.exp_iso / 100 if settings.exp_iso else None), 0.0
        if shutter is None and gain is None:
            shutter, gain = 1 / 125, target / (1 / 125)
        elif shutter is None:
            shutter = min(target / gain, self.info.max_exposure_s)
        elif gain is None:
            gain = min(max(target / shutter, 1.0), self.info.max_gain)
        return FrameMeta(shutter, gain, ev_bias=bias)

    def _render(self, size: tuple[int, int], meta: FrameMeta) -> Image.Image:
        w, h = size
        img = Image.new("RGB", size)
        d = ImageDraw.Draw(img)
        for y in range(h):  # sky / ground gradient
            t = y / h
            col = (int(90 + 80 * t), int(140 + 60 * t), int(210 - 40 * t)) if t < 0.6 else \
                  (int(70 - 30 * t), int(110 - 40 * t), int(50 - 20 * t))
            d.line([(0, y), (w, y)], fill=col)
        phase = (time.monotonic() - self._t0) / 4
        cx = int(w * (0.5 + 0.3 * math.sin(phase)))
        r = h // 10
        d.ellipse([cx - r, h * 0.25 - r, cx + r, h * 0.25 + r], fill=(255, 235, 170))
        d.rectangle([w * 0.1, h * 0.45, w * 0.3, h * 0.8], fill=(180, 60, 50))
        d.rectangle([w * 0.65, h * 0.5, w * 0.85, h * 0.85], fill=(60, 90, 170))
        scale = (meta.exposure_s * meta.gain) / self.SCENE_EXPOSURE
        return img.point(lambda v: min(255, int(v * scale)))

    def preview(self) -> tuple[Image.Image, FrameMeta]:
        time.sleep(1 / 30)
        meta = self._exposure(self._settings, preview=True)
        img = self._render(self.preview_size, meta)
        if self._zoom > 1:
            w, h = img.size
            cw, ch = w // self._zoom, h // self._zoom
            img = img.crop(((w - cw) // 2, (h - ch) // 2, (w + cw) // 2, (h + ch) // 2)).resize((w, h))
        return img, meta

    def capture(self, settings: Settings, raw_path: Optional[Path],
                size: Optional[tuple[int, int]] = None) -> tuple[Image.Image, FrameMeta]:
        meta = self._exposure(settings, preview=False)
        time.sleep(min(meta.exposure_s, 5) + 0.3)
        if raw_path:
            Path(raw_path).write_bytes(b"simulated raw")
        return self._render(size or self.info.resolution, meta), meta

    def burst(self, settings: Settings, count: int,
              size: tuple[int, int]) -> list[tuple[Image.Image, FrameMeta]]:
        meta = self._exposure(settings, preview=False)
        time.sleep(0.1 * count)
        return [(self._render(size, meta), meta) for _ in range(count)]
