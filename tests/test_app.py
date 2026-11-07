"""End-to-end tests driving the App with the simulated camera and a fake display."""

from pathlib import Path

import pytest
from PIL import Image

from omnicam import exif
from omnicam.app import App, SimSystem
from omnicam.camera import SimCamera
from omnicam.config import Settings
from omnicam.screens import (BusyView, ConfirmView, ExportView, InfoView, LiveView,
                             MenuView, PlaybackView, QuickMenuView, ReviewView, WelcomeView)


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
                       system=SimSystem(), clock=self.clock, start_on=True)
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


def menu_go(rig, *labels):
    """Hold A for the full menu (if not open) and walk it by labels.

    An item label moves there with B (crossing tabs) and selects with Y. After a
    setting opens for in-place editing or in a picker, the next label is the
    value to choose: it is stepped to and confirmed with Y.
    """
    if not isinstance(rig.app.screen, MenuView):
        rig.long("A")
    for label in labels:
        m = rig.app.screen
        assert isinstance(m, MenuView), m
        if m.editing is not None:
            c = m.editing
            values = [v for v, _ in c.options]
            target = values.index(next(v for v, lbl in c.options if lbl == label))
            current = values.index(getattr(rig.settings, c.key))
            for _ in range(abs(target - current)):
                rig.press("B" if target > current else "A")
            rig.press("Y")
            continue
        if m.picker is not None:
            for _ in range(40):
                if m.picker.options[m.picker_row][1] == label:
                    break
                rig.press("B")
            else:
                raise AssertionError(f"option {label!r} not found")
            rig.press("Y")
            continue
        for _ in range(60):
            if m.current.label == label:
                break
            rig.press("B")
        else:
            raise AssertionError(f"menu item {label!r} not found")
        rig.press("Y")


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
    # P: Y starts adjusting EV, A/B step it, Y again stops (EV is the only value)
    assert s.mode == "P" and not live.adjusting
    rig.press("Y")
    assert live.adjusting and live.selected == "ev"
    rig.press("A")
    assert s.ev == 0.5
    rig.press("Y")
    assert not live.adjusting
    # M: Y -> shutter, Y -> ISO, Y -> done; B stops at the lowest value
    rig.app.set_setting("mode", "M")
    rig.press("Y")
    assert live.adjusting and live.selected == "shutter"
    before = s.shutter
    rig.press("A")
    assert s.shutter > before
    rig.press("Y")
    assert live.selected == "iso"
    for _ in range(8):
        rig.press("B")
    assert s.iso == 100 and s.exp_iso == 100
    rig.press("Y")
    assert not live.adjusting
    # S: shutter then EV; ISO is automatic
    rig.app.set_setting("mode", "S")
    rig.press("Y")
    assert live.selected == "shutter" and s.exp_iso is None
    rig.press("Y")
    assert live.selected == "ev"


def test_a_menu_b_function_when_not_adjusting(rig):
    live = rig.app.screen
    before = (rig.settings.shutter, rig.settings.iso, rig.settings.ev)
    rig.press("A")
    assert isinstance(rig.app.screen, QuickMenuView)
    rig.press("X")          # close
    assert rig.app.screen is live
    rig.long("A")
    assert isinstance(rig.app.screen, MenuView)
    rig.press("X")
    assert rig.app.screen is live
    rig.press("B")          # default function: playback
    assert isinstance(rig.app.screen, PlaybackView)
    rig.press("X")          # back to live
    assert (rig.settings.shutter, rig.settings.iso, rig.settings.ev) == before
    rig.app.set_setting("fn_button", "filter")
    rig.press("B")
    assert rig.settings.filter == "bw" and rig.app.screen is live
    rig.app.set_setting("fn_button", "grid")
    rig.press("B")
    assert rig.settings.grid == "thirds"
    rig.long("B")           # hold: second function (default magnify)
    assert rig.app.magnify == 2 and rig.settings.grid == "thirds"
    rig.app.set_setting("fn_button_long", "histogram")
    rig.long("B")
    assert rig.settings.histogram is True
    rig.app.set_setting("fn_button_long", "none")
    rig.long("B")
    assert rig.settings.histogram is True and rig.app.screen is live
    rig.press("B")
    assert rig.settings.grid == "diagonal"
    rig.press("B")
    assert rig.settings.grid == "off"


def test_adjusting_times_out_and_x_ends_it(rig):
    live = rig.app.screen
    rig.press("Y")
    assert live.adjusting
    rig.tick(5.5)
    assert not live.adjusting
    rig.press("Y")
    rig.press("X")          # shutter ends adjusting and shoots
    assert not live.adjusting and rig.app.busy
    rig.finish_job()


def test_mode_is_set_from_menu(rig):
    menu_go(rig, "Mode", "M · Manual")
    assert rig.settings.mode == "M"


def test_menu_change_setting_and_persist(rig, tmp_path):
    menu_go(rig, "Filter", "B&W")       # 8 filters: opens a picker
    assert rig.settings.filter == "bw"
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
    rig.press("B")          # function button: playback (default)
    pb = rig.app.screen
    assert isinstance(pb, PlaybackView) and len(pb.entries) == 2
    rig.press("Y")          # zoom
    assert pb.zoom
    rig.press("X")          # back: leaves zoom first
    assert not pb.zoom and rig.app.screen is pb
    rig.long("Y")           # hold Y: delete
    assert isinstance(rig.app.screen, ConfirmView)
    rig.press("Y")          # confirm
    assert rig.app.screen is pb and len(pb.entries) == 1
    assert len(rig.photos()) == 1
    rig.press("X")          # back to live view
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
    menu_go(rig, "Storage", "Storage info")
    assert isinstance(rig.app.screen, InfoView)
    rig.press("X")
    assert isinstance(rig.app.screen, MenuView) and rig.app.screen.page.label == "Storage"
    rig.press("X")          # leave the Storage page
    menu_go(rig, "About", "Camera info")
    info = rig.app.screen
    assert isinstance(info, InfoView)
    from omnicam import __version__
    assert info.lines[0] == f"App: omnicam {__version__}"
    assert any(line.startswith("Shutter: 1/4000 – 1\"") for line in info.lines)
    assert any(line.startswith("Temperature:") for line in info.lines)
    assert any(line.startswith("Battery:") for line in info.lines)


def test_ram_fallback_saves_to_ram_and_refuses_when_full(tmp_path, monkeypatch):
    import omnicam.storage as storage

    monkeypatch.setattr(storage, "FALLBACK_DIR", tmp_path / "ram")
    monkeypatch.setattr(storage, "RAM_RESERVE_BYTES", 0)
    monkeypatch.setattr(storage, "mem_available", lambda: 10**12)
    clock, display = Clock(), FakeDisplay()
    s = Settings(photo_dir=str(tmp_path / "not-mounted"), screen_off=0)
    app = App(SimCamera(), display, s, settings_path=tmp_path / "s.json",
              require_mount=True, system=SimSystem(), clock=clock, start_on=True)
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
    menu_go(rig, "Storage", "USB export")
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
              clock=Clock(), start_on=True)
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


class CountingCamera(SimCamera):
    def __init__(self):
        super().__init__()
        self.starts = self.stops = 0

    def start(self):
        self.starts += 1

    def stop(self):
        self.stops += 1


def test_starts_off_and_y_held_3s_turns_on_then_off(tmp_path, monkeypatch):
    monkeypatch.setattr("omnicam.storage.FALLBACK_DIR", tmp_path / "fallback")
    monkeypatch.setattr("omnicam.app.time.sleep", lambda s: None)
    clock, display, cam = Clock(), FakeDisplay(), CountingCamera()
    app = App(cam, display, Settings(photo_dir=str(tmp_path / "p"), screen_off=0),
              settings_path=tmp_path / "s.json", require_mount=False, system=SimSystem(),
              clock=clock)
    app.start()
    app.step()
    assert not app.powered and display.backlight == 0 and cam.starts == 0
    assert display.frames == []                       # nothing drawn while off

    def hold_y(seconds):
        display.held = {"Y"}
        t_end = clock.t + seconds
        while clock.t < t_end:
            clock.t += 0.1
            app.step()
        display.held = set()
        clock.t += 0.05
        app.step()

    hold_y(1.5)                                       # too short: stays off
    assert not app.powered
    hold_y(3.2)
    assert app.powered and cam.starts == 1 and display.backlight > 0
    assert isinstance(app.screen, WelcomeView)
    clock.t += 2.5
    app.step()
    assert isinstance(app.screen, LiveView)           # welcome times out
    live = app.screen
    before = live.selected
    hold_y(3.2)                                       # hold Y: no param switch, turns off
    assert not app.powered and cam.stops == 1 and display.backlight == 0
    assert live.selected == before


def test_power_off_refused_during_usb_export(rig):
    rig.app.start_export()
    rig.finish_job()
    rig.app.power_off()
    assert rig.app.powered
    assert "USB export" in rig.app._toast[0]


# ---- shooting functions ----------------------------------------------------
def test_bracket_saves_three_different_exposures(rig):
    rig.app.set_setting("drive", "bracket3_1")
    rig.press("X")
    rig.finish_job()
    photos = rig.photos()
    assert len(photos) == 3
    exposures = []
    for p in photos:
        with Image.open(p) as im:
            exposures.append(exif.read(im)["exposure_s"])
    base = exposures[0]
    assert sorted(exposures) == sorted([base / 2, base, base * 2]) or \
        len({round(e, 6) for e in exposures}) == 3
    assert rig.app.camera._lock is None       # lock released afterwards


def test_burst_saves_reduced_size_frames(rig):
    rig.app.set_setting("drive", "burst5")
    rig.press("X")
    rig.finish_job()
    photos = rig.photos()
    assert len(photos) == 5
    with Image.open(photos[0]) as im:
        assert im.size == (1014, 760)          # "S" size of the 2028x1520 sim sensor


def test_aspect_and_size(rig):
    rig.app.set_setting("aspect", "1:1")
    rig.app.set_setting("image_size", "M")
    rig.press("X")
    rig.finish_job()
    with Image.open(rig.photos()[0]) as im:
        w, h = im.size
    assert w == h and h < 1520


def test_magnifier_function_and_reset_on_shot(rig):
    rig.app.set_setting("fn_button", "magnify")
    rig.app.set_setting("fn_button_long", "none")
    rig.press("B")
    assert rig.app.magnify == 2 and rig.app.camera._zoom == 2
    rig.press("B")
    assert rig.app.magnify == 4
    rig.press("X")
    assert rig.app.magnify == 1 and rig.app.camera._zoom == 1
    rig.finish_job()


def test_manual_focus_is_adjustable_with_y(rig):
    live = rig.app.screen
    rig.app.set_setting("af_mode", "manual")
    assert live.params[-1] == "focus"
    rig.press("Y")          # EV (P mode)
    rig.press("Y")          # focus
    assert live.selected == "focus"
    before = rig.settings.lens_position
    rig.press("A")
    assert rig.settings.lens_position > before


def test_reset_settings(rig):
    rig.app.set_setting("filter", "sepia")
    rig.app.set_setting("mode", "M")
    menu_go(rig, "About", "Reset settings")
    rig.press("Y")          # confirm
    assert rig.settings.filter == "none" and rig.settings.mode == "P"
    assert isinstance(rig.app.screen, LiveView)


def test_live_view_renders_with_all_overlays(rig):
    for k, v in (("peaking", "red"), ("zebras", True), ("aspect", "16:9"), ("grid", "diagonal"),
                 ("histogram", True), ("drive", "bracket5_1"), ("af_mode", "manual")):
        rig.app.set_setting(k, v)
    rig.app.battery_state = None
    rig.tick(0.01)
    from omnicam.battery import BatteryState
    rig.app.battery_state = BatteryState(12, True)
    rig.app.temperature = 85
    rig.tick(0.01)
    assert rig.display.frames[-1].size == (320, 240)



# ---- menu redesign ----------------------------------------------------------
def test_quick_menu_changes_in_place_and_cancels(rig):
    rig.press("A")
    q = rig.app.screen
    assert isinstance(q, QuickMenuView) and q.choice.key == "mode"
    rig.press("B")                      # DRIVE tile
    assert q.choice.key == "drive"
    rig.press("Y")                      # change in place
    rig.press("B")
    assert rig.settings.drive == "burst5"   # applied immediately
    rig.press("Y")                      # keep
    assert not q.editing
    rig.press("Y")
    rig.press("B")
    assert rig.settings.drive == "burst10"
    rig.press("X")                      # cancel -> back to the kept value
    assert rig.settings.drive == "burst5" and not q.editing
    rig.press("X")                      # close
    assert isinstance(rig.app.screen, LiveView)
    rig.press("A")
    assert rig.app.screen.choice.key == "drive"   # remembers the tile
    rig.long("A")
    assert isinstance(rig.app.screen, MenuView)


def test_menu_tabs_continuous_scroll_and_jump(rig):
    from omnicam.menu import TABS
    rig.long("A")
    m = rig.app.screen
    assert (m.tab, m.row) == (0, 0)
    for _ in range(len(TABS[0].items)):
        rig.press("B")
    assert (m.tab, m.row) == (1, 0)    # past the last SHOOT item into IMAGE
    rig.press("A")
    assert m.tab == 0 and m.row == len(TABS[0].items) - 1
    rig.long("B")
    assert (m.tab, m.row) == (1, 0)    # hold: jump a tab
    rig.long("A")
    rig.long("A")
    assert m.tab == len(TABS) - 1       # wraps round
    rig.press("X")
    rig.long("A")
    assert rig.app.screen.tab == len(TABS) - 1   # position remembered


def test_menu_toggle_and_in_place_edit_cancel(rig):
    menu_go(rig, "Save RAW (DNG)")      # On/Off toggles directly
    assert rig.settings.save_raw is True
    m = rig.app.screen
    menu_go(rig, "JPEG quality")        # 3 options: edit in place
    assert m.editing is not None
    rig.press("A")
    assert rig.settings.jpeg_quality == 95
    rig.press("X")                      # cancel
    assert rig.settings.jpeg_quality == 90 and m.editing is None


def test_every_menu_screen_renders(rig):
    from omnicam.menu import TABS
    rig.long("A")
    m = rig.app.screen
    for t in range(len(TABS)):
        m.tab, m.row = t, 0
        for r in range(len(TABS[t].items)):
            m.row = r
            assert m.render().size == (320, 240)
    rig.press("A")


def test_holding_y_on_delete_prompt_does_not_power_off(rig):
    rig.press("X")
    rig.finish_job()
    rig.tick(2)
    rig.press("B")          # playback
    rig.display.held = {"Y"}
    for _ in range(40):     # hold Y for 4 s: delete prompt at 0.7 s, power hold at 3 s
        rig.tick(0.1)
    rig.display.held = set()
    rig.tick(0.05)
    assert rig.app.powered and isinstance(rig.app.screen, ConfirmView)


def test_info_screen_fits_long_text(rig):
    lines = [f"Line {i}: some information about the camera" for i in range(10)]
    view = InfoView(rig.app, "Info", lines)
    img = view.render()
    # the last line must be drawn above the hint bar (rows 222+ are the bar)
    last = img.crop((0, 180, 320, 221)).convert("L")    # 10th line, above the hint bar
    assert last.getextrema()[1] > 150
