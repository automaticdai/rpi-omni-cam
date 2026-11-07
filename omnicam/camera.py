"""Camera backends: Picamera2 on the Pi, and a synthetic camera for --sim."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw

from .config import Settings

PREVIEW_MAX_EXPOSURE_S = 1 / 15
SCREEN_W, SCREEN_H = 320, 240


@dataclass
class CameraInfo:
    model: str
    resolution: tuple[int, int]
    max_exposure_s: float
    max_gain: float


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
        )
        self.preview_size = preview_size(sensor)
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

    def close(self) -> None:
        try:
            self.cam.stop()
        finally:
            self.cam.close()

    def _controls(self, shutter: Optional[float], gain: Optional[float], settings: Settings) -> dict:
        lc = self._lc
        c: dict = {
            "AwbEnable": True,
            "AwbMode": getattr(lc.AwbModeEnum, self.WB_MODES.get(settings.wb, "Auto")),
            "ExposureValue": float(settings.ev),
        }
        if self._lock is not None:
            shutter, gain = self._lock.exposure_s, self._lock.gain
            if self._lock.colour_gains:
                c = {"AwbEnable": False, "ColourGains": self._lock.colour_gains}
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
        return c

    def apply(self, settings: Settings) -> None:
        shutter, gain, self._ev_bias = preview_exposure(settings, self.info.max_gain)
        c = self._controls(shutter, gain, settings)
        if not shutter:
            c["FrameDurationLimits"] = (100, 66666)  # keep preview >= 15 fps under AE
        self.cam.set_controls(c)

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

    def capture(self, settings: Settings, raw_path: Optional[Path]) -> tuple[Image.Image, FrameMeta]:
        gain = settings.exp_iso / 100 if settings.exp_iso else None
        cfg = self.cam.create_still_configuration(
            main={"size": self.info.resolution, "format": "RGB888"},
            raw={} if raw_path else None,
            buffer_count=1,
            controls=self._controls(settings.exp_shutter, gain, settings),
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


# ---------------------------------------------------------------------------
class SimCamera:
    """Synthetic scene whose brightness responds to the exposure settings."""

    SCENE_EXPOSURE = 1 / 125 * 2.0  # shutter*gain giving a correct exposure

    def __init__(self):
        self.info = CameraInfo("simulated", (2028, 1520), 60.0, 16.0)
        self.preview_size = preview_size(self.info.resolution)
        self._settings = Settings()
        self._lock: Optional[FrameMeta] = None
        self._t0 = time.monotonic()

    def start(self) -> None:
        pass

    def close(self) -> None:
        pass

    def apply(self, settings: Settings) -> None:
        self._settings = settings

    def lock(self, meta: Optional[FrameMeta]) -> None:
        self._lock = meta

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
        return self._render(self.preview_size, meta), meta

    def capture(self, settings: Settings, raw_path: Optional[Path]) -> tuple[Image.Image, FrameMeta]:
        meta = self._exposure(settings, preview=False)
        time.sleep(min(meta.exposure_s, 5) + 0.3)
        if raw_path:
            Path(raw_path).write_bytes(b"simulated raw")
        return self._render(self.info.resolution, meta), meta
