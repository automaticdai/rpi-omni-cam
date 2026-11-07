"""Main application: owns the hardware, the current screen and background jobs."""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from PIL import Image

from . import __version__, exif, filters, sysinfo, ui
from .buttons import LONG, POWER, ButtonTracker
from .camera import FrameMeta
from .config import DEFAULT_CONFIG_PATH, Settings
from .exposure import EV_STEPS, format_shutter, iso_steps, shutter_steps, step
from .screens import (BusyView, ConfirmView, ExportView, InfoView, LiveView,
                      MenuView, ReviewView, Screen, WelcomeView)
from .storage import PHOTO_IMAGE, Storage

SAVE_DEBOUNCE_S = 2.0
log = logging.getLogger("omnicam")


# ---------------------------------------------------------------------------
class Job:
    """A background task (capture, format...) so the UI keeps updating."""

    def __init__(self, kind: str, fn: Callable[[], Any], started: float,
                 expected_s: Optional[float] = None, on_done: Optional[Callable] = None):
        self.kind, self.started, self.expected_s, self.on_done = kind, started, expected_s, on_done
        self.result: Any = None
        self.error: Optional[BaseException] = None
        self._thread = threading.Thread(target=self._run, args=(fn,), daemon=True)
        self._thread.start()

    def _run(self, fn):
        try:
            self.result = fn()
        except BaseException as e:  # reported on the screen
            self.error = e

    @property
    def done(self) -> bool:
        return not self._thread.is_alive()

    def wait(self, timeout=None):
        self._thread.join(timeout)

    def remaining(self, now: float) -> Optional[float]:
        if not self.expected_s:
            return None
        return max(0.0, self.expected_s - (now - self.started))


@dataclass
class Timelapse:
    directory: Path
    interval: int
    count: int  # 0 = unlimited
    next_at: float
    done: int = 0


class System:
    """Things that need root on the real device."""

    GADGET = "g_mass_storage"

    def shutdown(self) -> None:
        subprocess.run(["systemctl", "poweroff"], check=False)

    @staticmethod
    def _run(*cmd: str) -> subprocess.CompletedProcess:
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError((res.stderr or res.stdout or f"{cmd[0]} failed").strip().splitlines()[-1])
        return res

    def usb_export_start(self, mount: Path, image: Path) -> None:
        """Unmount the photo image and offer it to a USB host as a mass-storage drive."""
        if not image.exists():
            raise RuntimeError("No photo image - run install.sh")
        os.sync()
        if os.path.ismount(mount):
            self._run("umount", str(mount))
        subprocess.run(["modprobe", "-r", self.GADGET], capture_output=True)
        try:
            self._run("modprobe", self.GADGET, f"file={image}", "removable=1", "ro=0", "stall=0")
        except RuntimeError:
            subprocess.run(["mount", str(mount)], capture_output=True)  # don't leave photos unmounted
            raise

    def usb_export_stop(self, mount: Path, image: Path) -> None:
        """Take the drive back from the USB host, check it and mount it again."""
        subprocess.run(["modprobe", "-r", self.GADGET], capture_output=True)
        # The computer may have been unplugged without ejecting: repair the FAT if needed.
        fsck = subprocess.run(["fsck.vfat", "-a", str(image)], capture_output=True, text=True)
        if fsck.returncode not in (0, 1):
            log.warning("fsck.vfat returned %d: %s", fsck.returncode, fsck.stdout[-300:])
        self._run("mount", str(mount))

    def usb_state(self) -> str:
        """USB device-port state: 'configured' when a computer is using the drive."""
        for state in sorted(Path("/sys/class/udc").glob("*/state")):
            try:
                return state.read_text().strip()
            except OSError:
                pass
        return "no-udc"


class SimSystem(System):
    def shutdown(self) -> None:
        pass

    def usb_export_start(self, mount: Path, image: Path) -> None:
        pass

    def usb_export_stop(self, mount: Path, image: Path) -> None:
        pass

    def usb_state(self) -> str:
        return "configured"


def read_temperature() -> Optional[float]:
    try:
        return int(Path("/sys/class/thermal/thermal_zone0/temp").read_text()) / 1000
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------------------
class App:
    def __init__(self, camera, display, settings: Settings, *,
                 settings_path: Path = DEFAULT_CONFIG_PATH, require_mount: bool = True,
                 system: Optional[System] = None, clock: Callable[[], float] = time.monotonic,
                 start_on: bool = False):
        self.camera, self.display, self.s = camera, display, settings
        # Standby: the camera starts off; holding Y for 3 s turns it on/off.
        self.powered = start_on
        self.settings_path = settings_path
        self.system = system or System()
        self.now = clock
        self._require_mount = require_mount
        self.photo_image = PHOTO_IMAGE
        self.storage = Storage(settings.photo_dir, require_mount)
        self.buttons = ButtonTracker()
        self.screen: Screen = LiveView(self)
        self._live = self.screen
        self.job: Optional[Job] = None
        self.timelapse: Optional[Timelapse] = None
        self.preview_raw: Optional[Image.Image] = None
        self.preview_filtered: Optional[Image.Image] = None
        self.preview_meta: Optional[FrameMeta] = None
        self.meter_ev: Optional[float] = None
        self.temperature: Optional[float] = read_temperature()
        self._temp_at = 0.0
        self._toast: Optional[tuple[str, tuple, float]] = None
        self._led_until: Optional[float] = None
        self._dirty_at: Optional[float] = None
        self._last_activity = self.now()
        self.screen_on = True
        self._swallow = False
        self._avg_jpeg: Optional[float] = None
        self._shots_cache: Optional[tuple[int, float]] = None
        self.running = True

    # ---- lifecycle ----------------------------------------------------
    def start(self) -> None:
        if self.powered:
            self.camera.start()
            self.camera.apply(self.s)
            self.display.set_backlight(self.s.brightness / 100)
        else:
            self.display.set_backlight(0)
            self.display.set_led(0, 0, 0)
            log.info("standby: hold Y for 3 s to turn on")
        i = self.camera.info
        log.info("omnicam %s: camera %s %s, max exposure %.2fs, max gain %.1f, storage %s (%s)",
                 __version__, i.model, i.resolution, i.max_exposure_s, i.max_gain,
                 self.storage.root, "RAM" if self.storage.in_ram else "photo image")
        if self.storage.in_ram:
            self.toast("No photo storage: saving to RAM", ui.RED, 4)

    def run(self) -> None:
        self.start()
        try:
            while self.running and not self.display.poll_quit():
                self.step()
        finally:
            self.close()

    def close(self) -> None:
        if self.job:
            self.job.wait(10)
        if self._dirty_at is not None:
            self.s.save(self.settings_path)
        self.camera.close()
        self.display.close()

    # ---- one iteration ------------------------------------------------
    def step(self) -> None:
        now = self.now()
        pressed = self.display.pressed()
        if not self.powered:
            self._step_standby(pressed, now)
            return
        if not self.screen_on and pressed:
            self._wake()
            self._swallow = True
        events = self.buttons.update(pressed, now)
        if self._swallow:
            events = [e for e in events if e.kind == POWER]
            if not self.buttons.any_down():
                self._swallow = False
        for ev in events:
            if ev.kind == POWER:
                self.power_off()
                return
            if ev.button == "Y" and ev.kind == LONG:
                self.toast("Keep holding Y to turn off", ui.ACCENT, 2.4)
            self._last_activity = now
            log.info("button %s %s on %s", ev.button, ev.kind, type(self.screen).__name__)
            self.screen.on_button(ev)

        self._finish_job()
        self._tick_timelapse(now)
        self.screen.tick(now)
        self._housekeeping(now)

        if not self.screen_on:
            time.sleep(0.05)
            return
        if self.screen.wants_preview and not self.busy:
            self._grab_preview()
        else:
            time.sleep(0.04)
        frame = self.screen.render()
        frame = self._draw_toast(frame, now)
        self.display.show(frame)

    # ---- standby / power -------------------------------------------------
    def _step_standby(self, pressed: set, now: float) -> None:
        for ev in self.buttons.update(pressed, now):
            if ev.kind == POWER:
                self.power_on()
                return
            if ev.button == "Y" and ev.kind == LONG:
                self.display.set_led(0.15, 0.15, 0.15)  # "keep holding" glow
        if not self.buttons.any_down():
            self.display.set_led(0, 0, 0)
        self._housekeeping(now)
        time.sleep(0.05)

    def power_on(self) -> None:
        log.info("power on")
        self.display.set_led(0, 0, 0)
        self.camera.start()
        self.camera.apply(self.s)
        self.powered = True
        self.screen_on = True
        self._last_activity = self.now()
        self.display.set_backlight(self.s.brightness / 100)
        self.reload_storage()
        self.open(WelcomeView(self))
        if self.storage.in_ram:
            self.toast("No photo storage: saving to RAM", ui.RED, 4)

    def power_off(self) -> None:
        if isinstance(self.screen, ExportView):
            self.toast("Stop USB export first", ui.RED, 2.5)
            return
        if self.busy:
            self.toast("Busy - try again", ui.RED, 2)
            return
        log.info("power off")
        if self.timelapse:
            self.stop_timelapse()
        img, d = ui.panel_screen("omnicam")
        ui.centered(d, 105, "Turning off…", ui.font(18, "bold"))
        self.display.show(img)
        time.sleep(0.8)
        self.camera.stop()
        self.display.set_backlight(0)
        self.display.set_led(0, 0, 0)
        self._led_until = None
        self._toast = None
        self.preview_raw = self.preview_filtered = self.preview_meta = None
        self.meter_ev = None
        self.powered = False
        self.back_to_live()
        if self._dirty_at is not None:
            self._dirty_at = None
            self.s.save(self.settings_path)

    def _grab_preview(self) -> None:
        img, meta = self.camera.preview()
        self.preview_raw, self.preview_meta = img, meta
        self.preview_filtered = filters.apply(self.s.filter, img)
        self.meter_ev = ui.mean_ev(img) + meta.ev_bias

    def _housekeeping(self, now: float) -> None:
        if self._led_until is not None and now >= self._led_until:
            self._led_until = None
            self.display.set_led(0, 0, 0)
        if self._dirty_at is not None and now - self._dirty_at >= SAVE_DEBOUNCE_S:
            self._dirty_at = None
            try:
                self.s.save(self.settings_path)
            except OSError:
                pass
        if now - self._temp_at > 5:
            self._temp_at = now
            self.temperature = read_temperature()
        countdown = isinstance(self.screen, LiveView) and self.screen.countdown_until is not None
        if (self.screen_on and self.s.screen_off and not countdown and not self.busy
                and now - self._last_activity > self.s.screen_off):
            self.screen_on = False
            self.display.set_backlight(0)

    def _wake(self) -> None:
        self.screen_on = True
        self._last_activity = self.now()
        self.display.set_backlight(self.s.brightness / 100)

    # ---- helpers for screens -------------------------------------------
    @property
    def busy(self) -> bool:
        return self.job is not None

    def open(self, screen: Screen) -> None:
        self.screen = screen

    def back_to_live(self) -> None:
        self.screen = self._live

    def toast(self, text: str, colour=ui.WHITE, seconds: float = 1.8) -> None:
        self._toast = (text, colour, self.now() + seconds)

    def _draw_toast(self, frame: Image.Image, now: float) -> Image.Image:
        if not self._toast:
            return frame
        text, colour, until = self._toast
        if now >= until:
            self._toast = None
            return frame
        layer, d = ui.overlay(frame)
        f = ui.font(14, "bold")
        tw = ui.text_w(d, text, f)
        x0, y0 = (ui.W - tw) // 2 - 10, 30
        d.rounded_rectangle([x0, y0, x0 + tw + 20, y0 + 26], 6, fill=(0, 0, 0, 200))
        d.text((x0 + 10, y0 + 5), text, font=f, fill=colour)
        return ui.flatten(frame, layer)

    def led(self, rgb: tuple, seconds: Optional[float]) -> None:
        self.display.set_led(*rgb)
        self._led_until = self.now() + seconds if seconds else None

    def mark_dirty(self) -> None:
        self._dirty_at = self.now()

    def set_setting(self, key: str, value: Any) -> None:
        setattr(self.s, key, value)
        self.mark_dirty()
        if key == "brightness":
            self.display.set_backlight(value / 100)
        self.camera.apply(self.s)

    def step_exposure(self, which: str, direction: int) -> None:
        """A/B in live view: step shutter, ISO or exposure compensation."""
        if self.timelapse:
            self.toast("Locked during timelapse")
            return
        info = self.camera.info
        if which == "shutter":
            self.s.shutter = step(shutter_steps(info.max_exposure_s), self.s.shutter, direction)
        elif which == "iso":
            self.s.iso = step(iso_steps(info.max_gain), self.s.iso, direction)
        else:
            self.s.ev = step(EV_STEPS, self.s.ev, direction, log=False)
        self.mark_dirty()
        self.camera.apply(self.s)

    def shot_bytes(self) -> float:
        """Estimated size of one shot (JPEG, plus DNG if RAW is on)."""
        w, h = self.camera.info.resolution
        est = self._avg_jpeg or w * h * {95: 0.55, 90: 0.4}.get(self.s.jpeg_quality, 0.25)
        if self.s.save_raw:
            est += w * h * 2
        return max(est, 1)

    def shots_left(self, fresh: bool = False) -> int:
        """Shots that still fit. Cached for a couple of seconds (it's drawn every frame)."""
        now = self.now()
        if fresh or self._shots_cache is None or now - self._shots_cache[1] > 2.0:
            self._shots_cache = (int(self.storage.free_bytes() // self.shot_bytes()), now)
        return self._shots_cache[0]

    def refresh_storage(self) -> None:
        self._shots_cache = None

    def _check_space(self) -> bool:
        if self.shots_left(fresh=True) >= 1:
            return True
        log.warning("no space: free=%d bytes, shot≈%d bytes", self.storage.free_bytes(), self.shot_bytes())
        self.toast("RAM full – shut down to clear" if self.storage.in_ram else "Storage full", ui.RED, 3)
        self.led((1, 0, 0), 1.0)
        return False

    # ---- capture ------------------------------------------------------
    def _capture_to(self, path: Path, settings: Settings) -> tuple[Path, Image.Image]:
        when = datetime.now()
        raw = path.with_suffix(".dng") if settings.save_raw else None
        img, meta = self.camera.capture(settings, raw)
        img = filters.apply(settings.filter, img)
        exif_bytes = exif.build(self.camera.info.model, meta.exposure_s, meta.gain, settings.ev, when)
        self.storage.save_jpeg(img, path, settings.jpeg_quality, exif_bytes)
        size = path.stat().st_size
        self._avg_jpeg = size if self._avg_jpeg is None else 0.8 * self._avg_jpeg + 0.2 * size
        self._shots_cache = None
        img.thumbnail((ui.W, ui.H))
        return path, img

    def _snapshot(self) -> Settings:
        return Settings(**vars(self.s))

    def take_photo(self) -> None:
        if self.busy or not self._check_space():
            return
        try:
            path = self.storage.next_photo_path()
        except OSError as e:
            self.toast(f"Storage error: {e.strerror}", ui.RED)
            return
        settings = self._snapshot()
        self.led((1, 0, 0), None)
        expected = settings.exp_shutter if settings.exp_shutter and settings.exp_shutter >= 1 else None
        log.info("capture -> %s", path)
        self.job = Job("photo", lambda: self._capture_to(path, settings), self.now(), expected)

    def _finish_job(self) -> None:
        job = self.job
        if job is None or not job.done:
            return
        self.job = None
        if job.error is not None:
            log.error("%s job failed", job.kind, exc_info=job.error)
            self.led((1, 0, 0), 1.0)
            self.toast(f"Error: {job.error}"[:40], ui.RED, 3)
            if job.kind == "frame":
                self.stop_timelapse(message="Timelapse stopped (error)")
            elif job.kind == "export_start":
                self.back_to_live()
            elif job.kind in ("export_stop", "erase"):
                self.reload_storage()
                self.back_to_live()
            return
        if job.kind == "photo":
            path, thumb = job.result
            log.info("saved %s (%d bytes) in %.1fs", path, path.stat().st_size, self.now() - job.started)
            self.led((0, 1, 0), 0.3)
            if self.screen is self._live:
                self.open(ReviewView(self, thumb, path.name))
        elif job.kind == "frame":
            tl = self.timelapse
            if tl:
                tl.done += 1
                self.led((0, 0, 1), 0.15)
                if tl.count and tl.done >= tl.count:
                    self.stop_timelapse(message=f"Timelapse done: {tl.done} frames")
        if job.on_done:
            job.on_done()

    # ---- timelapse ----------------------------------------------------
    def start_timelapse(self) -> None:
        if not self._check_space():
            return
        try:
            directory = self.storage.new_timelapse_dir()
        except OSError as e:
            self.toast(f"Storage error: {e.strerror}", ui.RED)
            return
        manual = self.s.mode == "M"
        if self.s.tl_lock_exposure and not manual and self.preview_meta:
            self.camera.lock(self.preview_meta)
            self.camera.apply(self.s)
        self.timelapse = Timelapse(directory, self.s.tl_interval, self.s.tl_count, self.now())
        self.toast("Timelapse started", ui.ACCENT)

    def stop_timelapse(self, message: str = "") -> None:
        tl = self.timelapse
        self.timelapse = None
        self.camera.lock(None)
        self.camera.apply(self.s)
        if tl:
            self.toast(message or f"Timelapse stopped: {tl.done} frames", ui.ACCENT, 2.5)

    def _tick_timelapse(self, now: float) -> None:
        tl = self.timelapse
        if tl is None or self.busy or now < tl.next_at:
            return
        if not self._check_space():
            self.stop_timelapse(message="Timelapse stopped: storage full")
            return
        tl.next_at = max(tl.next_at + tl.interval, now)
        path = self.storage.frame_path(tl.directory, tl.done + 1)
        settings = self._snapshot()
        self.led((1, 0, 0), None)
        log.info("timelapse frame -> %s", path)
        self.job = Job("frame", lambda: self._capture_to(path, settings), now)

    # ---- menu actions -------------------------------------------------
    def run_action(self, action: str) -> None:
        menu = self.screen
        if action == "storage_info":
            used, total = self.storage.usage()
            gb = 1024 ** 3
            where = (f"Photo storage: {self.photo_image}" if self.storage.mounted
                     else "No photo storage: in RAM, lost at power-off")
            lines = [where, f"Mounted at {self.storage.root}",
                     f"Used {used / gb:.1f} of {total / gb:.1f} GB",
                     f"Entries: {len(self.storage.entries())}",
                     f"About {self.shots_left()} shots left"]
            self.open(InfoView(self, "Storage", lines, back=menu))
        elif action == "camera_info":
            i = self.camera.info
            mp = i.resolution[0] * i.resolution[1] / 1e6
            shutters = shutter_steps(i.max_exposure_s)
            lines = [f"App: omnicam {__version__}",
                     f"Model: {sysinfo.board_model()}",
                     f"CPU: {sysinfo.processor()}",
                     f"RAM: {sysinfo.ram()}",
                     f"Sensor: {i.model}",
                     f"Resolution: {i.resolution[0]}×{i.resolution[1]} ({mp:.1f} MP)",
                     f"Shutter: {format_shutter(shutters[0])} – {format_shutter(shutters[-1])}",
                     f"ISO: {iso_steps(i.max_gain)[0]} – {iso_steps(i.max_gain)[-1]}"]
            self.open(InfoView(self, "Camera", lines, back=menu))
        elif action == "usb_export":
            if self.storage.in_ram:
                self.open(InfoView(self, "USB export", [
                    "Photos are in RAM because the photo storage isn't mounted, so "
                    "there is nothing to export over USB."], back=menu))
                return
            self.start_export()
        elif action == "erase":
            self.open(ConfirmView(self, "Erase ALL photos?", "Erase", self._erase, back=menu))
        elif action == "shutdown":
            msg = "Shut down the camera?"
            if self.storage.in_ram and self.storage.entries():
                msg = "Photos in RAM will be lost. Shut down anyway?"
            self.open(ConfirmView(self, msg, "Shut down", self._shutdown, back=menu))

    # ---- storage --------------------------------------------------------
    def reload_storage(self) -> None:
        self.storage = Storage(self.s.photo_dir, self._require_mount)
        self.refresh_storage()

    def start_export(self) -> None:
        log.info("USB export: start")
        self.open(ExportView(self))
        self.job = Job("export_start", lambda: self.system.usb_export_start(
            self.storage.requested, self.photo_image), self.now())

    def stop_export(self) -> None:
        if self.busy:
            return
        log.info("USB export: stop")
        self.open(BusyView(self, "Checking photos"))

        def done():
            self.reload_storage()
            self.toast("Photos back on the camera", ui.GREEN)
            self.back_to_live()

        self.job = Job("export_stop", lambda: self.system.usb_export_stop(
            self.storage.requested, self.photo_image), self.now(), on_done=done)

    def _erase(self) -> None:
        import shutil

        self.open(BusyView(self, "Erasing"))

        def done():
            self.reload_storage()
            self.toast("All photos erased", ui.GREEN)
            self.back_to_live()

        self.job = Job("erase", lambda: shutil.rmtree(self.storage.dcim, ignore_errors=True),
                       self.now(), on_done=done)

    def _shutdown(self) -> None:
        img, d = ui.panel_screen("Goodbye")
        ui.centered(d, 105, "Shutting down…", ui.font(18, "bold"))
        self.display.show(img)
        self.running = False
        self.s.save(self.settings_path)
        self._dirty_at = None
        self.system.shutdown()
