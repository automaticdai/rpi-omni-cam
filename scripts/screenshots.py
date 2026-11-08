#!/usr/bin/env python3
"""Regenerate the README screenshots with the simulator (no Pi needed).

    .venv/bin/python scripts/screenshots.py            # writes docs/screenshots/*.png

Drives the real app (SimCamera + a fake display) by "pressing" buttons and saves
the screen at 2x size.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from omnicam import storage, sysinfo  # noqa: E402
from omnicam.app import App, SimSystem  # noqa: E402
from omnicam.camera import SimCamera  # noqa: E402
from omnicam.config import Settings  # noqa: E402


class Display:
    def __init__(self):
        self.held: set = set()
        self.frame: Image.Image | None = None

    def show(self, img):
        self.frame = img

    def pressed(self):
        return set(self.held)

    def set_led(self, *rgb):
        pass

    def set_backlight(self, level):
        pass

    def poll_quit(self):
        return False

    def close(self):
        pass


class Clock:
    t = 1000.0

    def __call__(self):
        return self.t


class Driver:
    def __init__(self, out: Path, scale: int):
        tmp = Path(tempfile.mkdtemp())
        storage.FALLBACK_DIR = tmp / "ram"
        # Board info as a Pi Zero 2 W reports it (values read on the real device),
        # so Camera info doesn't show the machine the simulator runs on.
        sysinfo.board_model = lambda: "Raspberry Pi Zero 2 W Rev 1.0"
        sysinfo.processor = lambda: "BCM2837, 4× Cortex-A53 @ 1.0 GHz"
        sysinfo.ram = lambda: "512 MB (415 MB usable)"
        self.out, self.scale = out, scale
        self.clock, self.display = Clock(), Display()
        self.app = App(SimCamera(), self.display,
                       Settings(photo_dir=str(tmp / "photos"), screen_off=0),
                       settings_path=tmp / "settings.json", require_mount=False,
                       system=SimSystem(), clock=self.clock)
        self.app.temperature = None
        self.app.start()

    def tick(self, dt=0.05):
        self.clock.t += dt
        self.app.step()
        self.app.temperature = None
        self.app.battery_state = None  # external power (no gauge) in screenshots

    def press(self, button, hold=0.1):
        self.display.held = {button}
        self.tick(0.01)
        self.tick(hold)
        self.display.held = set()
        self.tick(0.01)

    def long(self, button):
        self.press(button, hold=0.8)

    def wait_job(self):
        if self.app.job:
            self.app.job.wait(30)
        self.tick()

    def shot(self, name: str):
        self.tick(0.01)
        img = self.display.frame
        if self.scale != 1:
            img = img.resize((img.width * self.scale, img.height * self.scale), Image.NEAREST)
        path = self.out / f"{name}.png"
        img.save(path, optimize=True)
        print(path)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parent.parent / "docs" / "screenshots")
    ap.add_argument("--scale", type=int, default=2)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    d = Driver(args.out, args.scale)
    app, s = d.app, d.app.s

    # standby -> on: welcome screen
    app.power_on()
    d.shot("welcome")
    d.tick(2.5)

    # live view
    app.set_setting("grid", "thirds")
    app.set_setting("histogram", True)
    d.shot("live-view")
    app.set_setting("histogram", False)

    # manual mode, adjusting the shutter
    app.set_setting("mode", "M")
    d.press("Y")
    d.shot("adjust")
    d.press("X")
    d.wait_job()
    d.tick(2)
    app.set_setting("mode", "P")

    # quick menu
    d.press("A")
    d.press("B")
    d.shot("quick-menu")
    d.press("Y")
    d.press("B")
    d.press("B")
    d.press("B")
    d.shot("quick-menu-edit")
    d.press("X")
    d.press("X")

    # full menu
    d.long("A")
    d.press("B")
    d.shot("menu-shoot")
    d.press("Y")
    d.press("B")
    d.shot("menu-edit")
    d.press("X")
    menu = app.screen
    menu.tab, menu.row = 1, 0
    d.press("Y")
    d.press("B")
    d.press("B")
    d.press("B")
    d.press("B")
    d.press("B")
    d.press("B")
    d.press("B")
    d.shot("menu-picker")
    d.press("X")
    menu.tab, menu.row = 5, 0
    d.shot("menu-setup")
    menu.row = 2  # Date & time
    d.press("Y")
    view = app.screen
    view.values = {"year": 2026, "month": 11, "day": 7, "hour": 14, "minute": 30}
    d.press("Y")
    d.press("Y")
    d.press("Y")  # on the hour field
    d.shot("date-time")
    d.press("X")
    menu.row = 5  # About
    d.press("Y")
    d.press("Y")
    d.shot("camera-info")
    d.press("X")
    d.press("X")
    d.press("X")

    # focus aids, aspect ratio, magnifier
    app.set_setting("grid", "diagonal")
    app.set_setting("peaking", "red")
    app.set_setting("zebras", True)
    app.set_setting("aspect", "16:9")
    app.set_setting("mode", "M")
    app.set_setting("iso", 400)
    d.shot("focus-aids")
    for k, v in (("grid", "off"), ("peaking", "off"), ("zebras", False), ("aspect", "native"),
                 ("mode", "P")):
        app.set_setting(k, v)
    app.set_magnify(2)
    d.shot("magnify")
    app.set_magnify(1)

    # filter + capture + review
    app.set_setting("filter", "vintage")
    d.press("X")
    d.wait_job()
    d.shot("review")
    d.tick(2)
    app.set_setting("filter", "none")

    # bracketing, then playback
    app.set_setting("drive", "bracket3_1")
    d.press("X")
    d.wait_job()
    d.tick(2)
    app.set_setting("drive", "single")
    d.press("B")  # function button: playback
    for _ in range(3):  # newest first: step back to the Vintage-filter photo
        d.press("B")
    d.shot("playback")
    d.press("X")

    # timelapse
    app.set_setting("tl_interval", 10)
    d.long("X")
    d.wait_job()
    d.tick(3)
    d.shot("timelapse")
    d.long("X")
    d.tick(3)

    # USB export
    app.start_export()
    d.wait_job()
    d.shot("usb-export")
    d.press("X")
    d.wait_job()


if __name__ == "__main__":
    main()
