"""End-to-end tests driving the App with the simulated camera and a fake display."""

from pathlib import Path

import pytest
from PIL import Image

from omnicam import exif
from omnicam.app import App, SimSystem
from omnicam.camera import SimCamera
from omnicam.config import Settings
from omnicam.screens import (BusyView, ConfirmView, ExportView, InfoView, LiveView,
                             MenuView, PlaybackView, ReviewView)


class FakeDisplay:
    def __init__(self):
        self.held: set = set()
        self.frames: list = []
        self.led = (0, 0, 0)
        self.backlight = 1.0

    def show(self, img):
        assert img.size == (320, 240)
        self.frames.append(img)

    def pressed(self):
        return set(self.held)

    def set_led(self, r, g, b):
        self.led = (r, g, b)

    def set_backlight(self, v):
        self.backlight = v

    def poll_quit(self):
        return False

    def close(self):
        pass


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class Rig:
    def __init__(self, tmp_path: Path, **settings):
        self.clock = Clock()
        self.display = FakeDisplay()
        self.settings = Settings(photo_dir=str(tmp_path / "photos"), screen_off=0, **settings)
        self.app = App(SimCamera(), self.display, self.settings,
                       settings_path=tmp_path / "settings.json", require_mount=False,
                       system=SimSystem(), clock=self.clock)
        self.app.start()
        self.app.step()

    def tick(self, dt=0.05):
        self.clock.t += dt
        self.app.step()

    def press(self, button, hold=0.1):
        self.display.held = {button}
        self.tick(0.01)
        self.tick(hold)
        self.display.held = set()
        self.tick(0.01)

    def long(self, button):
        self.press(button, hold=0.8)

    def finish_job(self):
        if self.app.job:
            self.app.job.wait(15)
        self.tick()

    def photos(self):
        return sorted((Path(self.settings.photo_dir) / "DCIM").rglob("*.jpg")) \
            if (Path(self.settings.photo_dir) / "DCIM").exists() else []


@pytest.fixture
def rig(tmp_path, monkeypatch):
    monkeypatch.setattr("omnicam.storage.FALLBACK_DIR", tmp_path / "fallback")
    return Rig(tmp_path)


def test_take_photo_saves_full_res_jpeg_with_exif(rig):
    rig.press("X")
    assert rig.app.busy
    rig.finish_job()
    photos = rig.photos()
    assert len(photos) == 1 and photos[0].name == "IMG_0001.jpg"
    with Image.open(photos[0]) as im:
        assert im.size == (2028, 1520)
        info = exif.read(im)
    assert info.get("iso") and info.get("exposure_s")
    assert isinstance(rig.app.screen, ReviewView)
    rig.tick(2)
    assert isinstance(rig.app.screen, LiveView)


def test_filter_is_baked_into_saved_photo(rig):
    rig.app.set_setting("filter", "bw")
    rig.press("X")
    rig.finish_job()
    with Image.open(rig.photos()[0]) as im:
        r, g, b = im.convert("RGB").getpixel((im.width // 5, im.height // 2))
    assert abs(r - g) <= 3 and abs(g - b) <= 3


def test_exposure_buttons_follow_mode(rig):
    live = rig.app.screen
    s = rig.settings
    # P: A/B change exposure compensation only, Y has nothing else to select
    assert s.mode == "P" and live.selected == "ev"
    rig.press("A")
    assert s.ev == 0.5
    rig.press("Y")
    assert live.selected == "ev"
    # M: Y switches shutter <-> ISO; B stops at the lowest value, never AUTO
    rig.app.set_setting("mode", "M")
    assert live.selected == "shutter"
    before = s.shutter
    rig.press("A")
    assert s.shutter > before
    rig.press("Y")
    assert live.selected == "iso"
    for _ in range(8):
        rig.press("B")
    assert s.iso == 100 and s.exp_iso == 100
    # S: shutter and EV; ISO is automatic
    rig.app.set_setting("mode", "S")
    assert live.selected == "shutter" and s.exp_iso is None
    rig.press("Y")
    assert live.selected == "ev"


def test_mode_is_set_from_menu(rig):
    rig.long("A")
    rig.press("Y")          # Mode (first item of the top menu)
    rig.press("B")          # A
    rig.press("B")          # S
    rig.press("B")          # M
    rig.press("Y")          # choose
    assert rig.settings.mode == "M"


def test_menu_change_setting_and_persist(rig, tmp_path):
    rig.long("A")
    assert isinstance(rig.app.screen, MenuView)
    rig.press("B")          # Shooting
    rig.press("Y")
    rig.press("B")          # Exposure comp. (White balance is first)
    rig.press("B")          # Filter
    rig.press("Y")          # open filter options
    rig.press("B")          # B&W
    rig.press("Y")          # choose
    assert rig.settings.filter == "bw"
    rig.press("X")          # back to top menu
    rig.press("X")          # back to live view
    assert isinstance(rig.app.screen, LiveView)
    rig.tick(3)             # debounce save
    assert Settings.load(tmp_path / "settings.json").filter == "bw"


def test_timelapse_runs_for_count_and_stops(rig):
    rig.app.set_setting("tl_interval", 1)
    rig.app.set_setting("tl_count", 3)
    rig.long("X")
    assert rig.app.timelapse is not None
    for _ in range(40):
        rig.finish_job()
        rig.tick(0.5)
        if rig.app.timelapse is None:
            break
    assert rig.app.timelapse is None
    frames = [p for p in rig.photos() if p.name.startswith("FRAME_")]
    assert len(frames) == 3


def test_playback_and_delete(rig):
    for _ in range(2):
        rig.press("X")
        rig.finish_job()
        rig.tick(2)
    rig.long("B")
    pb = rig.app.screen
    assert isinstance(pb, PlaybackView) and len(pb.entries) == 2
    rig.press("X")
    assert pb.zoom
    rig.press("X")
    rig.long("X")
    assert isinstance(rig.app.screen, ConfirmView)
    rig.press("Y")          # confirm
    assert rig.app.screen is pb and len(pb.entries) == 1
    assert len(rig.photos()) == 1
    rig.press("Y")
    assert isinstance(rig.app.screen, LiveView)


def test_self_timer(rig):
    rig.app.set_setting("self_timer", 2)
    rig.press("X")
    assert rig.app.screen.countdown_until is not None and not rig.app.busy
    rig.tick(2.1)
    assert rig.app.busy
    rig.finish_job()
    assert len(rig.photos()) == 1


def test_system_info_screens(rig):
    rig.long("A")
    for _ in range(5):
        rig.press("B")      # System
    rig.press("Y")
    rig.press("Y")          # Storage info
    assert isinstance(rig.app.screen, InfoView)
    rig.press("X")
    assert isinstance(rig.app.screen, MenuView)
    rig.press("B")          # Erase all photos
    rig.press("B")          # Camera info
    rig.press("Y")
    info = rig.app.screen
    assert isinstance(info, InfoView)
    from omnicam import __version__
    assert info.lines[0] == f"App: omnicam {__version__}"
    assert any(line.startswith("Shutter: 1/4000 – 1\"") for line in info.lines)


def test_ram_fallback_saves_to_ram_and_refuses_when_full(tmp_path, monkeypatch):
    import omnicam.storage as storage

    monkeypatch.setattr(storage, "FALLBACK_DIR", tmp_path / "ram")
    monkeypatch.setattr(storage, "RAM_RESERVE_BYTES", 0)
    monkeypatch.setattr(storage, "mem_available", lambda: 10**12)
    clock, display = Clock(), FakeDisplay()
    s = Settings(photo_dir=str(tmp_path / "not-mounted"), screen_off=0)
    app = App(SimCamera(), display, s, settings_path=tmp_path / "s.json",
              require_mount=True, system=SimSystem(), clock=clock)
    assert app.storage.in_ram
    app.start()
    app._avg_jpeg = 50_000
    monkeypatch.setattr(storage, "RAM_BUDGET_BYTES", 10**9)
    app.take_photo()
    assert app.busy
    app.job.wait(15)
    app.step()
    assert list((tmp_path / "ram" / "DCIM").rglob("IMG_*.jpg"))
    monkeypatch.setattr(storage, "RAM_BUDGET_BYTES", 1)  # now full
    clock.t += 5
    app.back_to_live()
    app.take_photo()
    assert not app.busy
    assert "RAM full" in app._toast[0]


def test_usb_export_round_trip(rig):
    calls = []
    sysm = rig.app.system
    sysm.usb_export_start = lambda mount, image: calls.append(("start", mount, image))
    sysm.usb_export_stop = lambda mount, image: calls.append(("stop", mount, image))
    rig.press("X")
    rig.finish_job()
    rig.tick(2)
    rig.long("A")
    for _ in range(4):
        rig.press("B")      # USB export
    rig.press("Y")
    assert isinstance(rig.app.screen, ExportView)
    rig.finish_job()
    assert calls and calls[0][0] == "start"
    rig.press("A")          # ignored: no shooting during export
    rig.press("X")          # stop export
    rig.finish_job()
    assert [c[0] for c in calls] == ["start", "stop"]
    assert isinstance(rig.app.screen, LiveView)
    assert len(rig.photos()) == 1


def test_usb_export_refused_when_photos_are_in_ram(tmp_path, monkeypatch):
    import omnicam.storage as storage

    monkeypatch.setattr(storage, "FALLBACK_DIR", tmp_path / "ram")
    app = App(SimCamera(), FakeDisplay(), Settings(photo_dir=str(tmp_path / "nope"), screen_off=0),
              settings_path=tmp_path / "s.json", require_mount=True, system=SimSystem(),
              clock=Clock())
    app.run_action("usb_export")
    assert isinstance(app.screen, InfoView) and not app.busy


def test_erase_all_photos(rig):
    rig.press("X")
    rig.finish_job()
    rig.tick(2)
    assert rig.photos()
    rig.app.run_action("erase")
    rig.press("Y")          # confirm
    rig.finish_job()
    assert not rig.photos()
    assert isinstance(rig.app.screen, LiveView)


def test_screen_off_and_wake(tmp_path, monkeypatch):
    monkeypatch.setattr("omnicam.storage.FALLBACK_DIR", tmp_path / "fallback")
    rig = Rig(tmp_path)
    rig.app.set_setting("screen_off", 30)
    rig.tick(31)
    assert not rig.app.screen_on and rig.display.backlight == 0
    rig.press("X")          # wakes, does not take a photo
    assert rig.app.screen_on and not rig.app.busy
