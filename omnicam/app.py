"""Main application: owns the hardware, the current screen and background jobs."""

from __future__ import annotations

import logging
import os
import queue
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from PIL import Image

from . import __version__, exif, filters, stack, sysinfo, ui, video
from .buttons import LONG, POWER, ButtonTracker
from .battery import Battery
from .camera import FrameMeta, scaled_size
from .config import DEFAULT_CONFIG_PATH, Settings
from .exposure import (BRACKETS, BURSTS, EV_STEPS, FOCUS_STEPS, LONG_SHUTTER_S, MAX_SHUTTER_S,
                       NIGHT_FRAMES, SQ_PLAYBACK_FPS, SQ_SIZE, VIDEO_SIZES,
                       bracket_exposure, format_shutter, iso_steps, shutter_steps, step)
from .network import Network, NoNetwork, new_password
from .screens import (BusyView, ConfirmView, ConnectionView, DateTimeView, ExportView, InfoView,
                      LiveView, MenuView, PlaybackView, ReviewView, Screen, WelcomeView)
from .storage import PHOTO_IMAGE, Storage

SAVE_DEBOUNCE_S = 2.0
VIDEO_MIN_FREE = 50 * 1024 * 1024  # stop recording before the storage is full
VIDEO_MAX_BYTES = 3_900_000_000  # FAT32 file size limit is 4 GiB
STREAM_INTERVAL_S = 1 / 8  # phone live view frame rate
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


class PostWorker:
    """One background thread for slow follow-up work (making video files)
    that must not block shooting. Results are collected by the main loop."""

    def __init__(self):
        self._q: queue.Queue = queue.Queue()
        self._done: queue.Queue = queue.Queue()
        self.label = ""
        self.progress: Optional[float] = None
        self._pending = 0
        self._lock = threading.Lock()
        threading.Thread(target=self._loop, daemon=True, name="post").start()

    def add(self, label: str, fn: Callable[[Callable[[float], None]], Any],
            on_done: Optional[Callable[[Any], None]] = None) -> None:
        with self._lock:
            self._pending += 1
        self._q.put((label, fn, on_done))

    @property
    def busy(self) -> bool:
        return self._pending > 0

    def _set_progress(self, value: float) -> None:
        self.progress = value

    def _loop(self) -> None:
        while True:
            item = self._q.get()
            if item is None:
                self._q.task_done()
                return
            label, fn, on_done = item
            self.label, self.progress = label, None
            try:
                result, error = fn(self._set_progress), None
            except Exception as e:
                result, error = None, e
            self._done.put((label, result, error, on_done))
            self.label, self.progress = "", None
            with self._lock:
                self._pending -= 1
            self._q.task_done()

    def poll(self) -> list:
        out = []
        while True:
            try:
                out.append(self._done.get_nowait())
            except queue.Empty:
                return out

    def wait(self, timeout: Optional[float] = None) -> bool:
        """Wait until idle; False on timeout."""
        end = None if timeout is None else time.monotonic() + timeout
        while self.busy:
            if end is not None and time.monotonic() > end:
                return False
            time.sleep(0.05)
        return True

    def close(self) -> None:
        self._q.put(None)


@dataclass
class VideoClip:
    path: Path  # final MP4
    raw: Path  # H.264 being written
    started: float  # app clock
    playback_fps: Optional[float]  # None: the capture rate


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

    def set_time(self, when: datetime) -> None:
        """Set the system clock. The Pi has no RTC, so also record the time where
        the OS restores the clock from at boot (systemd-timesyncd / fake-hwclock)."""
        self._run("date", "-s", when.strftime("%Y-%m-%d %H:%M:%S"))
        stamp = Path("/var/lib/systemd/timesync/clock")
        if stamp.exists():
            os.utime(stamp, None)  # timesyncd won't start the clock earlier than this
        if shutil.which("fake-hwclock"):
            subprocess.run(["fake-hwclock", "save", "force"], capture_output=True)

    def time_synced(self) -> Optional[bool]:
        """True if the clock is synced from the network, None if unknown."""
        try:
            out = subprocess.run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"],
                                 capture_output=True, text=True, timeout=2).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return None
        return {"yes": True, "no": False}.get(out)

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

    def set_time(self, when: datetime) -> None:
        self.time_set_to = when

    def time_synced(self) -> Optional[bool]:
        return False


def ui_format_ev(ev: float) -> str:
    from .exposure import format_ev

    return format_ev(ev)


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
                 start_on: bool = False, network: Optional[Network] = None,
                 web_port: int = 80, web_host: str = "0.0.0.0"):
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
        self.battery = Battery()
        self.battery_state = None
        self._battery_at = -1e9
        self.magnify = 1  # focus magnifier: 1, 2 or 4
        self.menu_pos = (0, 0)  # last (tab, row) in the main menu
        self.quick_pos = 0  # last quick-menu tile
        self._toast: Optional[tuple[str, tuple, float]] = None
        self._led_until: Optional[float] = None
        self._dirty_at: Optional[float] = None
        self._last_activity = self.now()
        self.screen_on = True
        self._swallow = False
        self._avg_jpeg: Optional[float] = None
        self._shots_cache: Optional[tuple[int, float]] = None
        self.running = True
        # video, night/double exposure, background work
        self.clip: Optional[VideoClip] = None
        self._clip_check_at = 0.0
        self.double_first: Optional[tuple[Image.Image, FrameMeta]] = None
        self.double_ghost: Optional[Image.Image] = None
        self.post = PostWorker()
        # Wi-Fi and the phone page
        self.network = network or (Network() if shutil.which("nmcli") else NoNetwork())
        self.web = None
        self._web_port, self._web_host = web_port, web_host
        self._web_q: queue.Queue = queue.Queue()
        self.stream_clients = 0
        self.stream_jpeg: Optional[bytes] = None
        self._stream_at = 0.0
        self.keymap: dict[str, str] = {}
        self.update_keymap()

    # ---- lifecycle ----------------------------------------------------
    def start(self) -> None:
        if self.s.rotate180:
            self.camera.set_rotation(True)
        if not self.s.hotspot_password:
            self.s.hotspot_password = new_password()
            self.mark_dirty()
        self.network.start(self.s.wifi, self.s.hotspot_password)
        if self.s.web:
            self._set_web(True)
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
        if self.camera.recording:
            self.stop_recording()
        if self.job:
            self.job.wait(10)
        self.post.wait(60)  # e.g. finish wrapping a just-stopped video
        self.post.close()
        self._set_web(False)
        self.network.close()
        if self._dirty_at is not None:
            self.s.save(self.settings_path)
        self.camera.close()
        self.display.close()

    # ---- one iteration ------------------------------------------------
    def step(self) -> None:
        now = self.now()
        pressed = {self.keymap.get(b, b) for b in self.display.pressed()}
        self._process_web()
        self._finish_post()
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
        home = self.screen is self._live
        for ev in events:
            if ev.kind == POWER:
                if home:  # the 3 s power hold only works from the live view
                    self.power_off()
                    return
                continue
            if ev.button == "Y" and ev.kind == LONG and home:
                self.toast("Keep holding Y to turn off", ui.ACCENT, 2.4)
            self._last_activity = now
            log.info("button %s %s on %s", ev.button, ev.kind, type(self.screen).__name__)
            self.screen.on_button(ev)

        self._finish_job()
        self._tick_timelapse(now)
        self._tick_recording(now)
        self.screen.tick(now)
        self._housekeeping(now)

        streaming = self.stream_clients > 0
        if streaming:
            self._last_activity = now  # keep the preview running for the phone
            if not self.screen_on:
                self._wake()
        if not self.screen_on:
            time.sleep(0.05)
            return
        if (self.screen.wants_preview or streaming) and not self.busy:
            self._grab_preview()
            if streaming and now - self._stream_at >= STREAM_INTERVAL_S:
                self._stream_at = now
                self._encode_stream()
        else:
            time.sleep(0.04)
        frame = self.screen.render()
        frame = self._draw_toast(frame, now)
        self.show(frame)

    def show(self, frame: Image.Image) -> None:
        self.display.show(frame.rotate(180) if self.s.rotate180 else frame)

    def update_keymap(self) -> None:
        """Physical -> logical buttons for the left-handed / upside-down settings."""
        m = {b: b for b in "ABXY"}
        if self.s.rotate180:  # turned round: top-left is now the Y button
            m = {"A": "Y", "B": "X", "X": "B", "Y": "A"}
        if self.s.left_handed:  # mirrored: shutter (X) and Y on the left
            mirror = {"A": "X", "B": "Y", "X": "A", "Y": "B"}
            m = {phys: mirror[logical] for phys, logical in m.items()}
        self.keymap = m
        ui.KEY_LABELS.clear()
        ui.KEY_LABELS.update({logical: phys for phys, logical in m.items()})
        ui.MIRRORED = self.s.left_handed

    # ---- standby / power -------------------------------------------------
    def _encode_stream(self) -> None:
        import io

        img = self.preview_filtered
        if img is None:
            return
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=75)
        self.stream_jpeg = buf.getvalue()

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
        if self.camera.recording:
            self.stop_recording()
        if self.timelapse:
            self.stop_timelapse()
        self.cancel_double()
        self.set_magnify(1)
        img, d = ui.panel_screen("omnicam")
        ui.centered(d, 105, "Turning off…", ui.font(18, "bold"))
        self.show(img)
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
        self.meter_ev = ui.mean_ev(img, self.s.metering) + meta.ev_bias

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
        if now - self._battery_at > 30:
            self._battery_at = now
            self.battery_state = self.battery.read()
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
        if isinstance(self.screen, PlaybackView):
            self.screen.stop_video()
        self.screen = self._live

    def toast(self, text: str, colour=ui.WHITE, seconds: float = 1.8) -> None:
        self._toast = (ui.keys(text), colour, self.now() + seconds)

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
        if self.camera.recording and key in ("mode", "video_size", "sq_fps", "rotate180"):
            self.toast("Stop recording first", ui.RED)
            return
        setattr(self.s, key, value)
        self.mark_dirty()
        if key == "brightness":
            self.display.set_backlight(value / 100)
        elif key == "long_exposure" and not value:
            self.s.shutter = min(self.s.shutter, MAX_SHUTTER_S)
        elif key == "drive" and value != "double":
            self.cancel_double()
        elif key in ("left_handed", "rotate180"):
            self.update_keymap()
            if key == "rotate180":
                self.camera.set_rotation(bool(value))
        elif key == "wifi":
            self.network.apply(value, self.s.hotspot_password)
        elif key == "web":
            self._set_web(bool(value))
        self.camera.apply(self.s)

    def step_exposure(self, which: str, direction: int) -> None:
        """A/B in live view: step shutter, ISO or exposure compensation."""
        if self.timelapse:
            self.toast("Locked during timelapse")
            return
        info = self.camera.info
        if which == "shutter":
            self.s.shutter = step(shutter_steps(info.max_exposure_s, self.s.long_exposure),
                                  self.s.shutter, direction)
        elif which == "iso":
            self.s.iso = step(iso_steps(info.max_gain), self.s.iso, direction)
        elif which == "focus":  # A = closer (more dioptres), B = farther
            self.s.lens_position = step(FOCUS_STEPS, self.s.lens_position, direction, log=False)
        else:
            self.s.ev = step(EV_STEPS, self.s.ev, direction, log=False)
        self.mark_dirty()
        self.camera.apply(self.s)

    def shot_bytes(self) -> float:
        """Estimated size of one shot (JPEG, plus DNG if RAW is on)."""
        rw, rh = self.camera.info.resolution
        x0, y0, x1, y1 = ui.aspect_crop(scaled_size((rw, rh), self.s.image_size), self.s.aspect)
        pixels = (x1 - x0) * (y1 - y0)
        est = self._avg_jpeg or pixels * {95: 0.55, 90: 0.4}.get(self.s.jpeg_quality, 0.25)
        if self.s.save_raw:
            est += rw * rh * 2
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
    def _save(self, img: Image.Image, meta: FrameMeta, path: Path, settings: Settings,
              when: datetime) -> Image.Image:
        """Crop to the aspect ratio, filter, write with EXIF; returns a screen-size thumbnail."""
        box = ui.aspect_crop(img.size, settings.aspect)
        if box != (0, 0, *img.size):
            img = img.crop(box)
        img = filters.apply(settings.filter, img)
        exif_bytes = exif.build(self.camera.info.model, meta.exposure_s, meta.gain, settings.ev, when)
        self.storage.save_jpeg(img, path, settings.jpeg_quality, exif_bytes)
        size = path.stat().st_size
        self._avg_jpeg = size if self._avg_jpeg is None else 0.8 * self._avg_jpeg + 0.2 * size
        self._shots_cache = None
        img.thumbnail((ui.W, ui.H))
        return img

    def _capture_to(self, path: Path, settings: Settings) -> tuple[Path, Image.Image, int]:
        when = datetime.now()
        raw = path.with_suffix(".dng") if settings.save_raw else None
        size = scaled_size(self.camera.info.resolution, settings.image_size)
        img, meta = self.camera.capture(settings, raw, size)
        return path, self._save(img, meta, path, settings, when), 1

    def _burst(self, settings: Settings, count: int) -> tuple[Path, Image.Image, int]:
        """Back-to-back frames at reduced size (≈ a quarter of the pixels)."""
        size = scaled_size(self.camera.info.resolution, "S")
        frames = self.camera.burst(settings, count, size)
        when = datetime.now()
        path = thumb = None
        for img, meta in frames:
            path = self.storage.next_photo_path(when)
            thumb = self._save(img, meta, path, settings, when)
        return path, thumb, len(frames)

    def _bracket(self, settings: Settings, offsets: tuple[float, ...],
                 base: FrameMeta) -> tuple[Path, Image.Image, int]:
        """Same scene at several exposures: shutter changes first, ISO once the
        shutter reaches its limit. White balance is held for the series."""
        info = self.camera.info
        longest = LONG_SHUTTER_S if settings.long_exposure else MAX_SHUTTER_S
        max_shutter = min(longest, info.max_exposure_s)
        path = thumb = None
        try:
            for ev in offsets:
                shutter, gain = bracket_exposure(base.exposure_s, base.gain, ev, max_shutter, info.max_gain)
                self.camera.lock(FrameMeta(shutter, gain, base.colour_gains))
                path = self.storage.next_photo_path()
                path, thumb, _ = self._capture_to(path, settings)
        finally:
            self.camera.lock(None)
            self.camera.apply(self.s)
        return path, thumb, len(offsets)

    def _night(self, settings: Settings, count: int) -> tuple[Path, Image.Image, int]:
        """Average `count` frames (aligned) into one photo with less noise."""
        size = scaled_size(self.camera.info.resolution, settings.image_size)
        stacker = stack.Stacker()
        metas: list[FrameMeta] = []

        def add(img: Image.Image, meta: FrameMeta) -> None:
            stacker.add(img)
            metas.append(meta)

        self.camera.burst(settings, count, size, on_frame=add, buffers=1)
        when = datetime.now()
        path = self.storage.next_photo_path(when)
        log.info("night: stacked %d frames, crop margin %s", stacker.count, stacker.margin)
        return path, self._save(stacker.result(), metas[0], path, settings, when), 1

    def _double(self, settings: Settings, first: tuple[Image.Image, FrameMeta]) -> tuple[Path, Image.Image, int]:
        """Second exposure, blended 50/50 with the first."""
        size = scaled_size(self.camera.info.resolution, settings.image_size)
        img, meta = self.camera.capture(settings, None, size)
        base = first[0] if first[0].size == img.size else first[0].resize(img.size)
        when = datetime.now()
        path = self.storage.next_photo_path(when)
        return path, self._save(Image.blend(base, img, 0.5), meta, path, settings, when), 1

    def cancel_double(self) -> None:
        if self.double_first is not None:
            self.double_first = self.double_ghost = None
            self.toast("Double exposure cancelled")

    def _bracket_base(self, settings: Settings) -> Optional[FrameMeta]:
        """Exposure the bracket is centred on: what the photo would get now."""
        meta = self.preview_meta
        if meta is None or not meta.exposure_s:
            return None
        total = meta.exposure_s * meta.gain * 2 ** meta.ev_bias
        shutter = settings.exp_shutter or meta.exposure_s
        gain = settings.exp_iso / 100 if settings.exp_iso else total / shutter
        if settings.exp_iso and not settings.exp_shutter:
            shutter = total / gain
        return FrameMeta(shutter, max(1.0, min(gain, self.camera.info.max_gain)), meta.colour_gains)

    def _snapshot(self) -> Settings:
        return Settings(**vars(self.s))

    def set_magnify(self, level: int) -> None:
        self.magnify = level
        self.camera.set_zoom(level)

    def take_photo(self) -> None:
        if self.s.video_mode:
            self.toggle_recording()
            return
        if self.busy or not self._check_space():
            return
        if self.magnify != 1:  # the magnifier is for focusing; shoot the full frame
            self.set_magnify(1)
        settings = self._snapshot()
        drive = settings.drive
        expected = settings.exp_shutter if settings.exp_shutter and settings.exp_shutter >= 1 else None
        kind = "photo"
        try:
            if drive in NIGHT_FRAMES:
                n = NIGHT_FRAMES[drive]
                fn = lambda: self._night(settings, n)
                if expected:
                    expected *= n
            elif drive == "double":
                if self.double_first is None:
                    size = scaled_size(self.camera.info.resolution, settings.image_size)
                    kind = "double1"
                    fn = lambda: self.camera.capture(settings, None, size)
                else:
                    first = self.double_first
                    fn = lambda: self._double(settings, first)
            elif drive in BURSTS:
                fn = lambda: self._burst(settings, BURSTS[drive])
            elif drive in BRACKETS:
                base = self._bracket_base(settings)
                if base is None:
                    self.toast("No exposure reading yet", ui.RED)
                    return
                fn = lambda: self._bracket(settings, BRACKETS[drive], base)
            else:
                path = self.storage.next_photo_path()
                fn = lambda: self._capture_to(path, settings)
        except OSError as e:
            self.toast(f"Storage error: {e.strerror}", ui.RED)
            return
        self.led((1, 0, 0), None)
        log.info("capture (%s)", drive)
        self.job = Job(kind, fn, self.now(), expected)

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
        if job.kind == "double1":
            img, meta = job.result
            self.double_first = (img, meta)
            ghost = img.copy()
            ghost.thumbnail((ui.W, ui.H))
            self.double_ghost = ghost
            self.led((0, 1, 0), 0.3)
            self.toast("First exposure done: shoot the second", ui.ACCENT, 2.5)
        elif job.kind == "photo":
            if self.double_first is not None and self.s.drive == "double":
                self.double_first = self.double_ghost = None
            path, thumb, count = job.result
            log.info("saved %d photo(s), last %s (%d bytes) in %.1fs", count, path,
                     path.stat().st_size, self.now() - job.started)
            self.led((0, 1, 0), 0.3)
            if self.screen is self._live:
                name = path.name if count == 1 else f"{count} photos, last {path.name}"
                self.open(ReviewView(self, thumb, name))
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
            if self.s.tl_video and tl.done >= 2:
                self.make_timelapse_video(tl.directory, self.s.tl_video_fps)

    def make_timelapse_video(self, directory: Path, fps: int) -> None:
        frames = sorted(directory.glob("FRAME_*.jpg"))
        dst = directory / f"{directory.name}.mp4"

        def work(progress):
            codec = video.frames_to_mp4(frames, dst, fps, progress)
            log.info("timelapse video %s: %d frames at %d fps (%s)", dst, len(frames), fps, codec)
            return dst

        self.post.add("Timelapse video", work,
                      lambda _: self.toast("Timelapse video saved", ui.GREEN, 2.5))

    # ---- video ----------------------------------------------------------
    def toggle_recording(self) -> None:
        if self.camera.recording:
            self.stop_recording()
        else:
            self.start_recording()

    def start_recording(self) -> None:
        if self.busy:
            return
        if self.timelapse:
            self.toast("Timelapse running")
            return
        if self.storage.free_bytes() < VIDEO_MIN_FREE * 2:
            self.toast("Storage full", ui.RED, 3)
            return
        if self.magnify != 1:
            self.set_magnify(1)
        s = self.s
        if s.mode == "SQ":
            size, fps, playback = SQ_SIZE, s.sq_fps, float(SQ_PLAYBACK_FPS)
        else:
            w, h, fps = VIDEO_SIZES.get(s.video_size, VIDEO_SIZES["1080p"])
            size, playback = (w, h), None
        try:
            path = self.storage.next_video_path()
            raw = path.with_suffix(".h264")
            if self.preview_raw is not None:  # poster frame for playback and the phone page
                self.storage.save_jpeg(self.preview_raw, path.with_suffix(".THM"), 85)
            self.camera.start_recording(raw, size, fps, s)
        except Exception as e:
            log.exception("recording failed to start")
            self.toast(f"Video error: {e}"[:40], ui.RED, 3)
            return
        self.clip = VideoClip(path, raw, self.now(), playback)
        self.led((1, 0, 0), None)
        self.toast("Recording", ui.RED)

    def stop_recording(self, message: str = "") -> None:
        rec = self.camera.stop_recording()
        clip, self.clip = self.clip, None
        self.led((0, 0, 0), 0.01)
        if clip is None or rec is None:
            return
        fps = clip.playback_fps or rec.fps
        seconds = self.now() - clip.started
        log.info("recording stopped after %.1f s: %s (%d bytes)", seconds, clip.raw,
                 clip.raw.stat().st_size if clip.raw.exists() else -1)

        def work(progress):
            frames = video.remux_h264(clip.raw, clip.path, fps)
            clip.raw.unlink(missing_ok=True)
            log.info("video %s: %d frames at %.1f fps", clip.path, frames, fps)
            return frames

        self.toast(message or "Saving video…", ui.ACCENT, 2)
        self.post.add("Video", work, lambda _: self.toast(f"Saved {clip.path.name}", ui.GREEN))

    def _tick_recording(self, now: float) -> None:
        clip = self.clip
        if clip is None or not self.camera.recording or now - self._clip_check_at < 1.0:
            return
        self._clip_check_at = now
        try:
            size = clip.raw.stat().st_size
        except OSError:
            size = 0
        if size > VIDEO_MAX_BYTES:
            self.stop_recording("Video stopped: 4 GB file limit")
        elif self.storage.free_bytes() < VIDEO_MIN_FREE:
            self.stop_recording("Video stopped: storage full")

    def video_minutes_left(self) -> int:
        s = self.s
        if s.mode == "SQ":
            w, h, fps = *SQ_SIZE, s.sq_fps
        else:
            w, h, fps = VIDEO_SIZES.get(s.video_size, VIDEO_SIZES["1080p"])
        bytes_per_s = min(17e6, max(4e6, w * h * fps * 0.16)) / 8
        return int(max(0, self.storage.free_bytes() - VIDEO_MIN_FREE) / bytes_per_s / 60)

    def _finish_post(self) -> None:
        for label, result, error, on_done in self.post.poll():
            if error is not None:
                log.error("%s failed", label, exc_info=error)
                self.toast(f"{label} failed: {error}"[:40], ui.RED, 3)
            elif on_done:
                on_done(result)
            self.refresh_storage()
            if isinstance(self.screen, PlaybackView):
                self.screen.reload()

    # ---- phone page -------------------------------------------------------
    def _set_web(self, on: bool) -> None:
        from .web import WebServer

        if on and self.web is None:
            try:
                web = WebServer(self, self._web_port, self._web_host)
                web.start()
                self.web = web
            except Exception as e:
                log.exception("web server")
                self.toast(f"Phone access failed: {e}"[:40], ui.RED, 3)
        elif not on and self.web is not None:
            self.web.stop()
            self.web = None
            self.stream_jpeg = None

    @property
    def web_url(self) -> str:
        ip = self.network.status.ip
        if not (self.web and ip):
            return ""
        port = "" if self.web.port == 80 else f":{self.web.port}"
        return f"http://{ip}{port}/"

    def web_command(self, name: str, **kw) -> tuple[bool, str]:
        """Called from the web server's threads: run on the main loop and wait."""
        done = threading.Event()
        box: dict = {}
        self._web_q.put((name, kw, done, box))
        if not done.wait(10):
            return False, "The camera did not respond"
        return box["result"]

    def _process_web(self) -> None:
        while True:
            try:
                name, kw, done, box = self._web_q.get_nowait()
            except queue.Empty:
                return
            try:
                box["result"] = self._web_action(name, **kw)
            except Exception as e:
                log.exception("web %s", name)
                box["result"] = (False, str(e))
            done.set()

    def _web_action(self, name: str, key: str = "", direction: int = 0) -> tuple[bool, str]:
        if name == "delete":
            entry = self.storage.entry(key)
            if entry is None:
                return False, "Not found"
            if self.clip and entry.video == self.clip.path:
                return False, "Still recording"
            self.storage.delete(entry)
            self.refresh_storage()
            if isinstance(self.screen, PlaybackView):
                self.screen.reload()
            log.info("web: deleted %s", key)
            return True, "Deleted"
        if not self.powered:
            return False, "The camera is off (hold Y for 3 s)"
        if isinstance(self.screen, ExportView):
            return False, "USB export is on"
        if name == "ev":
            if self.s.mode == "M":
                return False, "No EV in M mode"
            self.step_exposure("ev", 1 if direction > 0 else -1)
            return True, "EV " + ui_format_ev(self.s.ev)
        if name == "shutter":
            self._last_activity = self.now()
            if not self.screen_on:
                self._wake()
            if self.busy:
                return False, "Busy"
            if self.timelapse:
                return False, "Timelapse running"
            if self.screen is not self._live:
                self.back_to_live()
            if self.s.video_mode:
                self.toggle_recording()
                return True, "Recording" if self.camera.recording else "Recording stopped"
            self.take_photo()
            return (True, "Shooting…") if self.busy else (False, "Not taken")
        return False, "Unknown command"

    def web_status(self) -> dict:
        s = self.s
        rec = self.clip.started if self.clip else None
        out = {"powered": self.powered, "mode": s.mode, "recording": rec is not None,
               "busy": self.busy, "battery": self.battery_state.percent if self.battery_state else None}
        if not self.powered:
            out["summary"] = "Camera is off"
            return out
        mode = {"MOVIE": "Movie", "SQ": "S&Q"}.get(s.mode, s.mode)
        parts = [mode]
        if rec is not None:
            secs = int(self.now() - rec)
            parts.append(f"● REC {secs // 60}:{secs % 60:02d}")
        elif s.video_mode:
            parts.append(f"{self.video_minutes_left()} min left")
        else:
            parts.append(f"{self.shots_left()} shots left")
        if s.mode != "M":
            parts.append("EV " + ui_format_ev(s.ev))
        if self.busy:
            parts.append("busy")
        if self.timelapse:
            parts.append(f"timelapse {self.timelapse.done}")
        out["summary"] = " · ".join(parts)
        return out

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

    # ---- custom sets ------------------------------------------------------
    def save_custom(self, n: int) -> None:
        from .menu import CUSTOM_KEYS

        setattr(self.s, f"custom{n}", {k: getattr(self.s, k) for k in CUSTOM_KEYS})
        self.mark_dirty()
        log.info("saved custom %d", n)
        self.toast(f"Saved as Custom {n}", ui.GREEN)

    def recall_custom(self, n: int) -> None:
        saved = getattr(self.s, f"custom{n}")
        if not saved:
            self.toast(f"Custom {n} is empty: save it first", ui.RED, 2.5)
            return
        if self.camera.recording:
            self.toast("Stop recording first", ui.RED)
            return
        valid = {f for f in vars(Settings())}
        for k, v in saved.items():
            if k in valid:
                setattr(self.s, k, v)
        if not self.s.long_exposure:
            self.s.shutter = min(self.s.shutter, MAX_SHUTTER_S)
        if self.s.drive != "double":
            self.double_first = self.double_ghost = None
        self.mark_dirty()
        self.camera.apply(self.s)
        log.info("recalled custom %d", n)
        self.toast(f"Custom {n} recalled", ui.GREEN)

    # ---- menu actions -------------------------------------------------
    def run_function(self, long: bool = False) -> None:
        """B in live view: the user-chosen function for a press or a hold
        (Menu › SETUP › B press / B hold)."""
        from . import menu as m

        fn = self.s.fn_button_long if long else self.s.fn_button
        if fn == "none":
            return

        def cycle(key: str, options: list) -> None:
            values = [v for v, _ in options]
            cur = getattr(self.s, key)
            i = (values.index(cur) + 1) % len(values) if cur in values else 0
            self.set_setting(key, values[i])
            self.toast(dict(options)[values[i]], ui.ACCENT)

        def choice(key: str) -> list:
            return m.choice_for(key).options

        if fn == "playback":
            self.open(PlaybackView(self))
        elif fn == "magnify":
            if self.camera.recording:
                self.toast("Not while recording")
                return
            level = {1: 2, 2: 4}.get(self.magnify, 1)
            self.set_magnify(level)
            self.toast(f"Magnify {level}×", ui.ACCENT)
        elif fn == "zebras":
            self.set_setting("zebras", not self.s.zebras)
            self.toast(f"Highlight warning {'on' if self.s.zebras else 'off'}", ui.ACCENT)
        elif fn == "usb_export":
            self.run_action("usb_export")
        elif fn in ("custom1", "custom2"):
            self.recall_custom(int(fn[-1]))
        elif fn == "web":
            self.set_setting("web", not self.s.web)
            self.toast(f"Phone access {'on' if self.s.web else 'off'}", ui.ACCENT)
        elif fn == "wifi_info":
            self.open(ConnectionView(self))
        elif fn == "timelapse":
            if self.timelapse:
                self.stop_timelapse()
            elif not self.busy:
                self.start_timelapse()
        elif fn == "histogram":
            self.set_setting(fn, not self.s.histogram)
            self.toast(f"Histogram {'on' if self.s.histogram else 'off'}", ui.ACCENT)
        elif fn in ("filter", "wb", "mode", "self_timer", "grid", "drive", "metering",
                    "peaking", "af_mode"):
            cycle(fn, choice(fn))

    def run_action(self, action: str) -> None:
        menu = self.screen
        if action == "playback":
            self.open(PlaybackView(self))
        elif action == "timelapse_start":
            self.back_to_live()
            if self.timelapse:
                self.toast("Timelapse already running")
            elif not self.busy:
                self.start_timelapse()
        elif action == "storage_info":
            used, total = self.storage.usage()
            gb = 1024 ** 3
            where = (f"Photo storage: {self.photo_image}" if self.storage.mounted
                     else "No photo storage: in RAM, lost at power-off")
            lines = [where, f"Mounted at {self.storage.root}",
                     f"Used {used / gb:.1f} of {total / gb:.1f} GB",
                     f"Entries: {len(self.storage.entries())}",
                     f"About {self.shots_left()} shots left"]
            self.open(InfoView(self, "Storage", lines, back=menu))
        elif action == "datetime":
            self.open(DateTimeView(self, back=menu))
        elif action == "wifi_info":
            self.open(ConnectionView(self, back=menu))
        elif action in ("save1", "save2"):
            self.save_custom(int(action[-1]))
        elif action in ("recall1", "recall2"):
            self.recall_custom(int(action[-1]))
        elif action == "camera_info":
            i = self.camera.info
            mp = i.resolution[0] * i.resolution[1] / 1e6
            shutters = shutter_steps(i.max_exposure_s, long=True)
            lines = [f"App: omnicam {__version__}",
                     f"Model: {sysinfo.board_model()}",
                     f"CPU: {sysinfo.processor()}",
                     f"RAM: {sysinfo.ram()}",
                     f"Sensor: {i.model}",
                     f"Resolution: {i.resolution[0]}×{i.resolution[1]} ({mp:.1f} MP)",
                     f"Shutter: {format_shutter(shutters[0])} – {format_shutter(shutters[-1])}",
                     f"ISO: {iso_steps(i.max_gain)[0]} – {iso_steps(i.max_gain)[-1]}",
                     "Temperature: " + (f"{self.temperature:.0f} °C" if self.temperature else "n/a"),
                     "Battery: " + (f"{self.battery.name}, {self.battery_state.percent}%"
                                    if self.battery_state else "external power (no gauge)")]
            self.open(InfoView(self, "Camera", lines, back=menu))
        elif action == "usb_export":
            if self.storage.in_ram:
                self.open(InfoView(self, "USB export", [
                    "Photos are in RAM because the photo storage isn't mounted, so "
                    "there is nothing to export over USB."], back=menu))
                return
            self.start_export()
        elif action == "reset":
            self.open(ConfirmView(self, "Reset all settings to defaults?", "Reset",
                                  self._reset_settings, back=menu))
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
        if self.camera.recording or self.post.busy:
            self.toast("Wait for the video to finish", ui.RED, 2.5)
            return
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

    def set_clock(self, when: datetime) -> None:
        try:
            self.system.set_time(when)
        except Exception as e:
            log.error("setting the clock failed: %s", e)
            self.toast(f"Clock not set: {e}"[:40], ui.RED, 3)
            return
        log.info("clock set to %s", when.isoformat(sep=" "))
        self.refresh_storage()
        self.toast(f"Clock set: {when:%Y-%m-%d %H:%M}", ui.GREEN)

    def _reset_settings(self) -> None:
        keep = {k: getattr(self.s, k) for k in
                ("photo_dir", "custom1", "custom2", "hotspot_password", "wifi", "web")}
        for k, v in vars(Settings(**keep)).items():
            setattr(self.s, k, v)  # in place: screens hold a reference to self.s
        self.set_magnify(1)
        self.cancel_double()
        self.update_keymap()
        self.camera.set_rotation(self.s.rotate180)
        self.camera.apply(self.s)
        self.display.set_backlight(self.s.brightness / 100)
        self.mark_dirty()
        log.info("settings reset to defaults")
        self.toast("Settings reset", ui.GREEN)
        self.back_to_live()

    def _erase(self) -> None:
        import shutil

        if self.camera.recording or self.post.busy:
            self.toast("Wait for the video to finish", ui.RED, 2.5)
            self.back_to_live()
            return
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
        self.show(img)
        self.running = False
        self.s.save(self.settings_path)
        self._dirty_at = None
        self.system.shutdown()
