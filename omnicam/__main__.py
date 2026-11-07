"""Entry point: python -m omnicam [--sim]"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from pathlib import Path

from .config import DEFAULT_CONFIG_PATH, Settings


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="omnicam", description="Pi Zero 2 DSLR-style camera")
    p.add_argument("--sim", action="store_true", help="run in a desktop window with a fake camera")
    p.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH, help="settings file")
    p.add_argument("--photo-dir", help="override the photo directory")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    settings = Settings.load(args.config)
    if args.photo_dir:
        settings.photo_dir = args.photo_dir

    from .app import App, SimSystem

    if args.sim:
        from .camera import SimCamera
        from .display import SimDisplay

        display, camera = SimDisplay(), SimCamera()
        app = App(camera, display, settings, settings_path=args.config,
                  require_mount=False, system=SimSystem())
    else:
        from .display import HatDisplay

        display = HatDisplay()
        try:
            from .camera import PiCamera

            camera = PiCamera()
        except Exception as e:  # no camera connected, ribbon loose...
            logging.exception("camera init failed")
            _show_error(display, f"Camera not found: {e}")
            return 1
        app = App(camera, display, settings, settings_path=args.config)

    signal.signal(signal.SIGTERM, lambda *_: setattr(app, "running", False))
    app.run()
    return 0


def _show_error(display, message: str) -> None:
    from . import ui

    img, d = ui.panel_screen("Error")
    f = ui.font(14)
    for i, line in enumerate(ui.wrap(d, message, f, ui.W - 24)[:7]):
        d.text((12, 40 + i * 22), line, font=f, fill=ui.RED)
    d.text((12, ui.H - 24), "Retrying shortly…", font=ui.font(12), fill=ui.GREY)
    display.show(img)
    display.set_led(1, 0, 0)
    time.sleep(10)  # systemd restarts us


if __name__ == "__main__":
    sys.exit(main())
