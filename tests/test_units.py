import json

import pytest
from PIL import Image

from omnicam import filters
from omnicam.buttons import LONG, POWER, PRESS, REPEAT, ButtonEvent, ButtonTracker
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


def test_power_event_only_for_y_after_3s():
    t = ButtonTracker()
    t.update({"Y", "A"}, 0.0)
    events = []
    for i in range(1, 32):
        events += t.update({"Y", "A"}, i * 0.1)
    assert events.count(ButtonEvent("Y", POWER)) == 1
    assert ButtonEvent("A", POWER) not in events
    assert t.update(set(), 3.3) == []    # no short press after a hold


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
    expected = {"P": (None, None), "A": (None, 400), "S": (1 / 60, None), "M": (1 / 60, 400)}
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
    p.write_text(json.dumps({"grid": True}))      # pre-0.4 on/off grid
    assert Settings.load(p).grid == "thirds"
    p.write_text(json.dumps({"grid": False}))
    assert Settings.load(p).grid == "off"
    p.write_text(json.dumps({"mode": "Tv"}))      # pre-0.3 mode names
    assert Settings.load(p).mode == "S"
    p.write_text(json.dumps({"mode": "Sv"}))
    assert Settings.load(p).mode == "A"
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


def test_sysinfo_reads_pi_files(monkeypatch):
    from omnicam import sysinfo

    files = {
        "/proc/device-tree/model": "Raspberry Pi Zero 2 W Rev 1.0\0",
        "/proc/device-tree/compatible": "raspberrypi,model-zero-2-w\0brcm,bcm2837\0",
        "/proc/cpuinfo": "processor\t: 0\nCPU implementer\t: 0x41\nCPU part\t: 0xd03\n",
        "/sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq": "1000000\n",
        "/proc/meminfo": "MemTotal:         425108 kB\nMemFree: 1 kB\n",
    }
    monkeypatch.setattr(sysinfo, "_read", lambda path: files[path].replace("\0", " ").strip())
    monkeypatch.setattr(sysinfo.os, "cpu_count", lambda: 4)
    assert sysinfo.board_model() == "Raspberry Pi Zero 2 W Rev 1.0"
    assert sysinfo.processor() == "BCM2837, 4× Cortex-A53 @ 1.0 GHz"
    assert sysinfo.ram() == "512 MB (415 MB usable)"


def test_bracket_exposure_moves_shutter_then_gain():
    from omnicam.exposure import bracket_exposure
    assert bracket_exposure(1 / 100, 2.0, 1.0, 1.0, 16) == (1 / 50, 2.0)
    s, g = bracket_exposure(0.5, 2.0, 2.0, 1.0, 16)        # wants 2 s, capped at 1 s
    assert s == 1.0 and abs(g - 4.0) < 1e-9
    s, g = bracket_exposure(1 / 100, 1.0, -2.0, 1.0, 16)
    assert abs(s - 1 / 400) < 1e-12 and g == 1.0


def test_zebras_mark_only_clipped_pixels():
    from omnicam import ui
    img = Image.new("RGB", (40, 20), (100, 100, 100))
    img.paste((255, 255, 255), (0, 0, 20, 20))
    alpha = ui.zebras(img).getchannel("A")
    assert alpha.crop((0, 0, 20, 20)).getbbox() is not None
    assert alpha.crop((20, 0, 40, 20)).getbbox() is None


def test_peaking_finds_edges():
    from omnicam import ui
    img = Image.new("RGB", (40, 20), (0, 0, 0))
    img.paste((255, 255, 255), (20, 0, 40, 20))
    assert ui.peaking(img, "red").getchannel("A").getbbox() is not None
    flat = Image.new("RGB", (40, 20), (90, 90, 90))
    assert ui.peaking(flat, "red").getchannel("A").getbbox() is None


def test_metering_modes_differ_on_bright_centre():
    from omnicam import ui
    img = Image.new("RGB", (64, 48), (20, 20, 20))
    img.paste((240, 240, 240), (28, 20, 36, 28))
    assert ui.mean_ev(img, "spot") > ui.mean_ev(img, "centre") > ui.mean_ev(img, "matrix")


def test_aspect_crop():
    from omnicam import ui
    assert ui.aspect_crop((2028, 1520), "4:3") == (0, 0, 2028, 1520)   # within 1 %
    assert ui.aspect_crop((2028, 1520), "native") == (0, 0, 2028, 1520)
    x0, y0, x1, y1 = ui.aspect_crop((3000, 2000), "1:1")
    assert (x1 - x0, y1 - y0) == (2000, 2000)
    x0, y0, x1, y1 = ui.aspect_crop((4000, 3000), "16:9")
    assert x1 - x0 == 4000 and y1 - y0 == 2250


def test_battery_sources_and_lipo_curve(monkeypatch):
    from omnicam import battery
    assert battery.lipo_percent(4.2) == 100 and battery.lipo_percent(3.2) == 0
    calls = []
    fake = (("none", lambda: calls.append(1)), ("ok", lambda: battery.BatteryState(80, False)))
    monkeypatch.setattr(battery.Battery, "SOURCES", fake)
    b = battery.Battery()
    assert b.read().percent == 80 and b.name == "ok"
    assert b.read().percent == 80 and len(calls) == 1   # source remembered
    monkeypatch.setattr(battery.Battery, "SOURCES", (("none", lambda: None),))
    b = battery.Battery()
    assert b.read() is None and b.read() is None


def test_picamera_zoom_crop_is_centred_on_measured_full_view():
    from omnicam.camera import PiCamera
    cam = PiCamera.__new__(PiCamera)        # no hardware: test the crop maths only
    cam._crop_max = (0, 0, 3280, 2464)
    cam._zoom = 1
    assert cam._crop() == (0, 0, 3280, 2464)
    cam._zoom = 2
    assert cam._crop() == (820, 616, 1640, 1232)
    cam._zoom = 4
    assert cam._crop() == (1230, 924, 820, 616)


def test_edit_arrows_know_the_ends():
    from omnicam.menu import MODE
    from omnicam.screens import _at_ends
    assert _at_ends(MODE, "P") == (True, False)
    assert _at_ends(MODE, "A") == (False, False)
    assert _at_ends(MODE, "SQ") == (False, True)


# ---- night stacking ----------------------------------------------------------
def test_stacker_aligns_shifted_frames():
    import numpy as np
    from PIL import Image
    from omnicam.stack import Stacker
    base = np.asarray(Image.effect_noise((480, 360), 60).convert("RGB"))
    st = Stacker()
    for dx in (0, 2, -1):
        st.add(Image.fromarray(np.roll(base, dx * 8, axis=1)))
    assert st.count == 3 and st.margin == [0, 16]
    out = np.asarray(st.result()).astype(int)
    # aligned: the average matches the original (cropped by the margin)
    assert np.abs(out - base[:, 16:-16].astype(int)).mean() < 2


# ---- video files -------------------------------------------------------------
def test_video_size_fits_1080p():
    from omnicam.video import video_size
    assert video_size((3280, 2464)) == (1436, 1080)
    assert video_size((2464, 3280)) == (1080, 1436)
    assert video_size((640, 480)) == (640, 480)


def test_remux_and_timelapse_video(tmp_path):
    av = pytest.importorskip("av")
    from PIL import Image
    from omnicam import video
    raw = tmp_path / "clip.h264"
    with av.open(str(raw), "w", format="h264") as out:
        s = out.add_stream("libx264", rate=120)
        s.width, s.height, s.pix_fmt = 320, 240, "yuv420p"
        for i in range(60):
            for pkt in s.encode(av.VideoFrame.from_image(Image.new("RGB", (320, 240), (i * 4, 0, 0)))):
                out.mux(pkt)
        for pkt in s.encode():
            out.mux(pkt)
    mp4 = tmp_path / "clip.mp4"
    assert video.remux_h264(raw, mp4, 30) == 60
    assert abs(video.duration_s(mp4) - 2.0) < 0.1   # 60 frames at 30 fps: slow motion
    frames = []
    for i in range(6):
        p = tmp_path / f"FRAME_{i:05d}.jpg"
        Image.new("RGB", (800, 600), (0, i * 40, 0)).save(p)
        frames.append(p)
    progress = []
    tl = tmp_path / "tl.mp4"
    video.frames_to_mp4(frames, tl, 12, progress.append)
    assert tl.exists() and progress[-1] == 1.0
    assert abs(video.duration_s(tl) - 0.5) < 0.1


# ---- storage -----------------------------------------------------------------
def test_storage_lists_videos_and_refuses_paths_outside(tmp_path):
    from omnicam.storage import Storage
    st = Storage(str(tmp_path), require_mount=False)
    p = st.next_video_path()
    p.write_bytes(b"x")
    p.with_suffix(".THM").write_bytes(b"x")
    assert st.next_video_path().name == "VID_0002.mp4"
    (e,) = st.entries()
    assert e.is_video and e.video == p and st.entry(e.key) is not None
    assert st.resolve(e.key) == p.resolve()
    assert st.resolve("../../etc/passwd") is None
    (tmp_path / "secret.txt").write_text("x")
    assert st.resolve("../secret.txt") is None
    st.delete(e)
    assert not p.exists() and not p.with_suffix(".THM").exists()


def test_settings_accept_video_modes(tmp_path):
    import json
    from omnicam.config import Settings
    f = tmp_path / "s.json"
    f.write_text(json.dumps({"mode": "SQ", "shutter": 8, "long_exposure": True}))
    s = Settings.load(f)
    assert s.mode == "SQ" and s.video_mode and s.shutter == 8 and s.exp_shutter is None
    f.write_text(json.dumps({"shutter": 8}))
    assert Settings.load(f).shutter == 1        # long exposures off: capped at 1 s


def test_long_shutter_steps():
    from omnicam.exposure import shutter_steps
    assert shutter_steps(11.77)[-1] == 1
    assert shutter_steps(11.77, long=True)[-3:] == [2, 4, 8]


def _make_mp4(path, seconds=3, fps=30, size=(640, 360)):
    av = pytest.importorskip("av")
    from PIL import Image
    with av.open(str(path), "w", format="mp4") as out:
        s = out.add_stream("libx264", rate=fps)
        s.width, s.height, s.pix_fmt = size[0], size[1], "yuv420p"
        for i in range(seconds * fps):
            img = Image.new("RGB", size, (i * 255 // (seconds * fps), 40, 200))
            for pkt in s.encode(av.VideoFrame.from_image(img)):
                out.mux(pkt)
        for pkt in s.encode():
            out.mux(pkt)
    return path


def test_player_plays_seeks_and_ends(tmp_path):
    import time
    from omnicam.video import Player
    mp4 = _make_mp4(tmp_path / "v.mp4")
    p = Player(mp4, (320, 240))

    def wait(cond, timeout=10):
        end = time.monotonic() + timeout
        while not cond() and time.monotonic() < end:
            p.current()  # the screen keeps looking
            time.sleep(0.02)
        return cond()

    assert wait(lambda: p.frame is not None)
    assert p.frame.size == (320, 180) and abs(p.duration - 3) < 0.2
    p.toggle()                                   # pause
    assert wait(lambda: not p.playing)
    paused_at = p.position
    p.seek(1.0)
    assert wait(lambda: abs(p.position - (paused_at + 1.0)) < 0.05)
    p.toggle()                                   # resume to the end
    assert wait(lambda: p.ended, 8) and not p.playing
    p.toggle()                                   # replay from the start
    assert wait(lambda: p.playing and p.position < 1.0)
    p.close()


def test_player_pauses_when_not_watched(tmp_path):
    import time
    from omnicam.video import Player
    p = Player(_make_mp4(tmp_path / "v.mp4"), (320, 240))
    time.sleep(1.6)                              # nobody calls current()
    assert not p.playing and not p.ended
    p.close()


def test_player_reports_a_bad_file(tmp_path):
    import time
    from omnicam.video import Player
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"not a video")
    p = Player(bad)
    for _ in range(100):
        if p.error:
            break
        time.sleep(0.02)
    assert p.error and not p.playing


# ---- display output ------------------------------------------------------------
def test_rgb565_matches_the_st7789_library():
    import numpy as np
    from PIL import Image
    from omnicam.display import rgb565
    img = Image.effect_noise((320, 240), 60).convert("RGB")
    pb = np.rot90(np.array(img), 2).astype("uint16")  # what st7789.image_to_data does
    ref = (((pb[..., [0]] & 0xF8) << 8) | ((pb[..., [1]] & 0xFC) << 3) | ((pb[..., [2]] & 0xF8) >> 3))
    assert rgb565(img).tobytes() == ref.byteswap().tobytes()


def test_changed_rows():
    import numpy as np
    from omnicam.display import changed_rows
    a = np.zeros((240, 320), dtype=">u2")
    assert changed_rows(a, None) == (0, 239)
    assert changed_rows(a, a.copy()) is None
    b = a.copy()
    b[30, 5] = 1
    b[100, 300] = 7
    assert changed_rows(b, a) == (30, 100)


def test_nmcli_terse_split_and_scan_parsing(monkeypatch):
    from omnicam.network import Network, _split_terse
    assert _split_terse(r"*:My\:Net:70:WPA2") == ["*", "My:Net", "70", "WPA2"]
    net = Network()
    out = {"con": "Home:802-11-wireless\nomnicam-hotspot:802-11-wireless\nWired:802-3-ethernet\n",
           "dev": "*:Home:80:WPA2\n :Cafe:40:\n :Home:60:WPA2\n :omnicam-ABCD:90:WPA2\n : :20:WPA2\n"}
    def fake(*a, **k):
        if a[0] == "-g":  # the network name of a saved connection
            return "Home\n"
        return out["con" if "con" in a else "dev"]
    monkeypatch.setattr(net, "_nmcli", fake)
    net.hotspot_ssid = "omnicam-ABCD"
    assert net.saved() == ["Home"] and net.saved_networks() == [("Home", "Home")]
    nets = net.scan()
    assert [(n.ssid, n.signal, n.secure, n.saved, n.active) for n in nets] == [
        ("Home", 80, True, True, True), ("Cafe", 40, False, False, False)]
