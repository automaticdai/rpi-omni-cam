import json

import pytest
from PIL import Image

from omnicam import filters
from omnicam.buttons import LONG, PRESS, REPEAT, ButtonEvent, ButtonTracker
from omnicam.camera import PREVIEW_MAX_EXPOSURE_S, preview_exposure, preview_size
from omnicam.config import Settings
from omnicam.exposure import EV_STEPS, format_shutter, iso_steps, shutter_steps, step
from omnicam.storage import Storage


# ---- buttons -----------------------------------------------------------
def test_short_press_fires_on_release():
    t = ButtonTracker()
    assert t.update({"A"}, 0.0) == []
    assert t.update({"A"}, 0.2) == []
    assert t.update(set(), 0.3) == [ButtonEvent("A", PRESS)]


def test_long_press_fires_once_then_repeats_and_no_press_on_release():
    t = ButtonTracker(long_press_s=0.7, repeat_s=0.15)
    t.update({"B"}, 0.0)
    assert t.update({"B"}, 0.71) == [ButtonEvent("B", LONG)]
    assert t.update({"B"}, 0.8) == []
    assert t.update({"B"}, 0.87) == [ButtonEvent("B", REPEAT)]
    assert t.update(set(), 1.0) == []


# ---- exposure ----------------------------------------------------------
def test_step_clamps_at_both_ends_and_never_goes_auto():
    steps = iso_steps(16)
    assert step(steps, 100, -1) == 100
    assert step(steps, 1600, +1) == 1600
    assert step(steps, 200, +1) == 400
    shutters = shutter_steps(60)
    assert step(shutters, 1 / 4000, -1) == 1 / 4000
    assert step(shutters, 1 / 70, +1) == 1 / 30   # off-table value snaps to 1/60 first


def test_ev_steps_linearly_including_negative():
    assert step(EV_STEPS, 0.0, -1, log=False) == -0.5
    assert step(EV_STEPS, -2.0, -1, log=False) == -2.0
    assert step(EV_STEPS, 2.0, +1, log=False) == 2.0


def test_steps_are_limited_by_sensor():
    assert max(shutter_steps(11.7)) == 1          # capped at 1 s
    assert max(shutter_steps(0.5)) == 0.5         # ...or the sensor's limit if lower
    assert max(iso_steps(10.66)) == 800
    assert max(iso_steps(22.26)) == 1600          # HQ camera


def test_mode_decides_which_values_are_manual():
    s = Settings(shutter=1 / 60, iso=400)
    expected = {"P": (None, None), "Tv": (1 / 60, None), "Sv": (None, 400), "M": (1 / 60, 400)}
    for mode, values in expected.items():
        s.mode = mode
        assert (s.exp_shutter, s.exp_iso) == values


def test_labels():
    assert format_shutter(1 / 125) == "1/125"
    assert format_shutter(2) == '2"'
    assert format_shutter(0.5) == '0.5"'


def test_preview_caps_long_shutter_and_compensates_with_gain():
    s = Settings(mode="M", shutter=1.0, iso=100)
    shutter, gain, bias = preview_exposure(s, max_gain=16)
    assert shutter == PREVIEW_MAX_EXPOSURE_S
    assert gain == 15.0
    assert bias == 0
    s = Settings(mode="M", shutter=8.0, iso=400)
    _, gain, bias = preview_exposure(s, max_gain=16)
    assert gain == 16 and bias > 0


def test_preview_size_keeps_aspect():
    assert preview_size((4056, 3040)) == (320, 240)
    assert preview_size((4608, 2592)) == (320, 180)


# ---- filters -----------------------------------------------------------
@pytest.mark.parametrize("name", list(filters.FILTERS))
def test_every_filter_returns_same_size_rgb(name):
    img = Image.new("RGB", (64, 48), (120, 80, 40))
    out = filters.apply(name, img)
    assert out.mode == "RGB" and out.size == img.size


def test_bw_is_grey():
    out = filters.apply("bw", Image.new("RGB", (4, 4), (200, 50, 10)))
    r, g, b = out.getpixel((0, 0))
    assert r == g == b


# ---- settings ----------------------------------------------------------
def test_settings_roundtrip_and_bad_file(tmp_path):
    p = tmp_path / "s.json"
    Settings(shutter=0.5, iso=200, filter="sepia").save(p)
    s = Settings.load(p)
    assert (s.shutter, s.iso, s.filter) == (0.5, 200, "sepia")
    p.write_text("{not json")
    assert Settings.load(p) == Settings()
    p.write_text(json.dumps({"iso": 800, "unknown": 1}))
    assert Settings.load(p).iso == 800
    # older files stored None for AUTO; must load as usable manual values
    p.write_text(json.dumps({"shutter": None, "iso": None, "mode": "bogus"}))
    s = Settings.load(p)
    assert s.shutter and s.iso and s.mode == "P"
    p.write_text(json.dumps({"shutter": 8}))       # from before the 1 s cap
    assert Settings.load(p).shutter == 1


# ---- storage -----------------------------------------------------------
def test_storage_naming_listing_and_delete(tmp_path):
    st = Storage(str(tmp_path), require_mount=False)
    p1 = st.next_photo_path()
    st.save_jpeg(Image.new("RGB", (8, 8)), p1, 90)
    p2 = st.next_photo_path()
    assert p1.name == "IMG_0001.jpg" and p2.name == "IMG_0002.jpg"
    st.save_jpeg(Image.new("RGB", (8, 8)), p2, 90)
    tl = st.new_timelapse_dir()
    for i in range(1, 4):
        st.save_jpeg(Image.new("RGB", (8, 8)), st.frame_path(tl, i), 90)
    entries = st.entries()
    assert len(entries) == 3
    tl_entry = next(e for e in entries if e.frames == 3)
    st.delete(tl_entry)
    assert not tl.exists()
    assert len(st.entries()) == 2


def test_storage_falls_back_to_ram_when_not_mounted(tmp_path):
    st = Storage(str(tmp_path / "not-a-mount"), require_mount=True)
    assert not st.mounted and st.in_ram
    assert str(st.root).startswith("/dev/shm/")


def test_ram_storage_is_capped_by_budget_and_reserve(tmp_path, monkeypatch):
    import omnicam.storage as storage

    monkeypatch.setattr(storage, "FALLBACK_DIR", tmp_path / "ram")
    monkeypatch.setattr(storage, "RAM_BUDGET_BYTES", 100_000)
    monkeypatch.setattr(storage, "RAM_RESERVE_BYTES", 0)
    monkeypatch.setattr(storage, "mem_available", lambda: 10**12)
    st = Storage(str(tmp_path / "not-a-mount"))
    assert st.free_bytes() == 100_000
    (st.day_dir() / "IMG_0001.jpg").write_bytes(b"x" * 60_000)
    assert st.free_bytes() == 40_000
    assert st.usage() == (60_000, 100_000)
    monkeypatch.setattr(storage, "mem_available", lambda: 10_000)  # memory pressure wins
    assert st.free_bytes() == 10_000
    monkeypatch.setattr(storage, "mem_available", lambda: 0)
    assert st.free_bytes() == 0
