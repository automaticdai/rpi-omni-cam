# rpi-omni-cam

A pocket camera that behaves like a simplified DSLR, built from a Raspberry Pi
Zero 2 W, a Raspberry Pi camera and a Pimoroni Display HAT Mini.

- Live colour preview with shutter speed / ISO / white balance / filter readout,
  an exposure meter, optional grid and histogram
- P / A / S / M exposure modes (Sony naming, chosen in the menu), with the mode's values
  and exposure compensation adjusted straight from live view
- Full-resolution JPEG capture, optional RAW (DNG), shutter speeds 1/4000 – 1 s
- Drive modes: single, burst (×5 / ×10), exposure bracketing (3 or 5 frames)
- Focus aids: magnifier (2× / 4×), focus peaking, highlight warning (zebras);
  AF-C / AF-S / manual focus on autofocus cameras (Camera Module 3)
- Metering (centre-weighted / spot / matrix), picture style (sharpness,
  contrast, saturation, noise reduction), image size and aspect ratio
- Battery level (PiSugar, INA219 UPS HATs, or any Linux-reported battery)
- Filters: B&W, Sepia, High Contrast, Vivid, Cool, Warm, Vintage (baked into the JPEG)
- Self-timer, timelapse with exposure lock
- On-camera playback with 100 % zoom and delete
- Photos saved on the SD card; **USB export** turns the camera into a USB drive
  on your computer over the micro-USB port

## Hardware

- Raspberry Pi Zero 2 W
- Raspberry Pi camera module (v2, v3, or HQ camera — the sensor is detected at runtime)
- Pimoroni Display HAT Mini (320×240 LCD, buttons A/B/X/Y, RGB LED)
- microSD card (16 GB or more recommended)
- 3D printed case

## Controls

**On / off:** the camera starts **off** when the Pi boots (screen dark, camera
stopped). **Hold Y for 3 seconds** to turn it on — a welcome screen shows, then
live view. Hold Y for 3 seconds again to turn it off (not possible during USB
export). This is a standby; **Menu › SETUP › About › Shut down** powers the Pi off.

```
        ┌──────────────────────────────┐
   A ── │                              │ ── X   SHUTTER  (hold: start/stop timelapse)
        │        Display HAT Mini      │
   B ── │                              │ ── Y   adjust exposure (hold 3 s: on/off)
        └──────────────────────────────┘
   A: quick menu (hold: full menu)
   B: function button: press = playback, hold = magnify (Menu › SETUP › B press / B hold)
```

**Button rules everywhere:** **Y** = choose / confirm / change, **X** = back /
close / cancel, **A / B** = move (or change a value while editing), hold **A / B**
= jump faster, hold **Y** 3 s = on / off.

### Quick menu (A)

Eight tiles over the bottom of the live view: **Mode, Drive, EV, WB, Metering,
Focus, Filter, Aspect**. **A / B** move between tiles; **Y** changes the
highlighted one in place (`◀ value ▶`, **A / B** step it and the live view
updates at once); **Y** keeps the new value, **X** cancels back to the old one.
**X** closes the quick menu; **hold A** opens the full menu. It reopens on the
tile you used last.

### Full menu (hold A)

Six tabs, each fitting on one screen:

| Tab | Items |
|-----|-------|
| **SHOOT** | Mode, Drive, Self-timer, Exposure comp., Metering, White balance |
| **IMAGE** | Filter, Image size, Aspect ratio, JPEG quality, Save RAW |
| **STYLE** | Sharpness, Contrast, Saturation, Noise reduction |
| **FOCUS** | Focus mode, Focus peaking, Highlight warning, Grid, Histogram |
| **TL** | Interval, Shots, Exposure lock, Start timelapse |
| **SETUP** | B press, B hold, Brightness, Screen off, Storage › (Playback, USB export, Storage info, Erase all photos), About › (Camera info, Reset settings, Shut down) |

**A / B** move through the items; going past the end of a tab continues into the
next one, and **holding A / B** jumps a whole tab. **Y** on a setting changes it:
On/Off settings flip at once, short lists change in place (`◀ value ▶`, **Y**
keeps, **X** cancels), long lists (Filter, Interval, Shots, B press / B hold) open a
picker. **Y** on an action runs it; on *Storage* / *About* it opens that page.
**X** goes back. A help line at the bottom explains the highlighted item or
value. The menu reopens where you left it.

**Adjusting exposure:** press **Y** to start adjusting: the mode's first value is
highlighted and **A / B** step it up / down (hold to repeat; values stop at the
ends of their range). **Y** moves to the next value; after the last one,
adjusting ends. It also ends after 5 s without a press, or when you press **X**
to shoot. Outside adjusting, A and B are the menu and function buttons.

**Function button (B):** two functions — one for a short **press** (default
Playback) and one for a **hold** of 0.7 s (default Magnify) — chosen in
**Menu › SETUP › B press** and **B hold** (B hold can also be *None*). Choices:
Playback, Magnify (1× → 2× → 4×), Drive mode, Filter, White balance, Mode,
Metering, Focus mode, Focus peaking, Self-timer or Grid (each press cycles),
Highlight warning or Histogram (on/off), Timelapse start/stop, or USB export.
Playback is also in **Menu › SETUP › Storage**.

**Focus magnifier** zooms the live view into the centre (cropped on the sensor,
so it stays sharp) to check focus; it returns to 1× when you take the photo.
With **Focus mode › MF** on an autofocus camera, focus distance joins the values
that **Y** steps through (A = closer, B = farther).

**Grid** (Menu › FOCUS › Grid): Off, Thirds, or Thirds + diagonals.

### Shooting options

| Option | Choices |
|--------|---------|
| Drive | Single · Burst ×5 / ×10 (fast, ≈ ¼ resolution) · Bracket 3 ±1 EV / 3 ±2 EV / 5 ±1,2 EV |
| Metering | Centre-weighted · Spot · Matrix (the on-screen meter follows it) |
| Focus mode | AF-C · AF-S (focuses when you press X) · MF — autofocus cameras only |
| Picture style | Sharpness, Contrast, Saturation (Low / Normal / High) · Noise reduction (Off / Fast / High quality) |
| Image size | L (full) · M (≈ ½ the pixels) · S (≈ ¼) |
| Aspect ratio | Native (sensor) · 4:3 · 3:2 · 16:9 · 1:1 — the live view shades what will be cropped |

Bracketing centres on the current exposure and changes the shutter speed between
frames; past the 1 s limit it raises the ISO instead. White balance is held for
the series. The **FOCUS** tab adds **Focus peaking** (Off / Red / Yellow / Blue) and
**Highlight warning** (striped overlay on clipped areas, also in playback).

### Battery

The top-right corner of the live view shows the battery level when a supported
battery board is found: **PiSugar** (needs its `pisugar-server`), INA219-based UPS
HATs such as the **Waveshare UPS HAT (C)** for Pi Zero (I2C address 0x43; charge
estimated from voltage), or any battery Linux reports under
`/sys/class/power_supply`. Without a gauge the camera assumes it is on
external (USB) power and shows a full green battery with a charge bolt. The CPU temperature is in
**Menu › SETUP › About › Camera info**; the top bar shows a red **HOT** above 80 °C.

**Reset settings** (Menu › SETUP › About) restores all defaults (photos are untouched).

The exposure mode is chosen in the **quick menu** (first tile) or **Menu › SHOOT › Mode**. Values you set
are shown in white; values chosen by auto-exposure are shown in grey.

| Mode | Shutter | ISO  | Y steps through |
|------|---------|------|--------------------|
| **P**  (program)           | auto | auto | EV only |
| **A**  (aperture priority) | auto | set  | ISO ↔ EV |
| **S**  (shutter priority)  | set  | auto | shutter ↔ EV |
| **M**  (manual)            | set  | set  | shutter ↔ ISO |

EV is exposure compensation (−2 … +2): it tells auto-exposure to make the
picture darker or brighter. In **A** you set the aperture on the lens ring (HQ
camera; the other Pi cameras have a fixed aperture) and the ISO with A/B, and
the camera picks the shutter speed.

Confirmation dialogs (delete, erase, shut down): **Y** confirms, **X** cancels.
In playback: **A/B** browse, **Y** toggles 100 % zoom, **hold Y** deletes (asks to
confirm), **X** goes back (out of zoom, then to live view). Holding Y on the
delete prompt never powers the camera off.

The LED glows red while capturing, flashes green when a photo is saved, blinks
amber during the self-timer and blue for each timelapse frame.

## Setup

1. Flash **Raspberry Pi OS Lite** with Raspberry Pi Imager, setting up
   Wi-Fi/SSH as you like, and boot the Pi.
2. Clone this repo on the Pi and install:
   ```bash
   git clone <this repo> ~/rpi-omni-cam
   cd ~/rpi-omni-cam
   sudo ./scripts/install.sh      # or: sudo PHOTOS_SIZE_GB=8 ./scripts/install.sh
   sudo reboot
   ```
   This installs `picamera2` and the display driver, configures SPI for the
   Display HAT Mini, enables USB device mode, creates the photo storage and
   enables the `omnicam` service.

### Photo storage

Photos are saved on the SD card, inside a FAT32 disk-image file
`/var/lib/omnicam/photos.img` that is mounted at `/media/photos`. By default it
gets half of the free space (at most 32 GB); set `PHOTOS_SIZE_GB` when running
`install.sh` to choose. It is an image file rather than a plain folder because
USB storage mode can only share a whole disk with the computer, not a folder.

If the photo storage isn't mounted, the camera still works, but photos are kept
**in RAM only** (`/dev/shm/omnicam-photos`) and are **lost at power-off**. The
status bar shows a red **RAM**, and shutting down warns you if RAM photos would
be lost. RAM storage is capped at 128 MB and always leaves 192 MB of memory free
for capturing; when it is full the camera says so instead of taking the shot.

### Getting photos off the camera (USB export)

1. On the camera: **Menu › SETUP › Storage › USB export** (or set the B button to USB export).
2. Connect the Pi's **USB data port** — the inner micro-USB, marked `USB`, not
   `PWR IN` — to your computer. A drive named **PHOTOS** appears. The computer
   can also power the camera through this port.
3. Copy (or delete) photos on the computer, then **eject** the drive there.
4. On the camera press **X** to stop the export. The camera checks the drive,
   takes it back and returns to live view.

No photos can be taken while exporting, because the computer and the camera
must not write to the same drive at the same time. If the cable is pulled
without ejecting, the camera repairs the drive when the export stops.

The drive contains:

```
DCIM/2026-11-07/IMG_0001.jpg
DCIM/2026-11-07/IMG_0001.dng          (if RAW is on)
DCIM/2026-11-07/TL_0001/FRAME_00001.jpg
```

To turn a timelapse into a video on your computer:

```bash
ffmpeg -framerate 25 -i DCIM/2026-11-07/TL_0001/FRAME_%05d.jpg -c:v libx264 -pix_fmt yuv420p timelapse.mp4
```

## Service

```bash
sudo systemctl status omnicam
journalctl -u omnicam -f
sudo systemctl stop omnicam      # e.g. to run it by hand: sudo .venv/bin/python -m omnicam
```

Settings persist in `/var/lib/omnicam/settings.json`.

## Development (no Pi needed)

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m omnicam --sim --photo-dir /tmp/omnicam-photos   # keys a/b/x/y, Esc quits
# starts off: hold y for 3 s to turn it on
.venv/bin/python -m pytest
```

`--sim` opens a window showing the 320×240 screen and uses a synthetic camera
whose brightness responds to the exposure settings.

## Layout

```
omnicam/
  app.py        main loop, capture/timelapse jobs, menu actions
  screens.py    live view, menu, playback, confirm/info screens
  camera.py     Picamera2 backend + simulated camera
  display.py    Display HAT Mini backend + pygame simulator
  buttons.py    short / long / repeat press detection
  exposure.py   modes, shutter/ISO/EV steps, formatting
  filters.py    image filters
  storage.py    photo paths, listing, deletion, RAM fallback
  menu.py       menu tree
  ui.py         drawing helpers
  exif.py       EXIF read/write
scripts/        install.sh
systemd/        omnicam.service
```

## Troubleshooting

- **"Camera not found"** — check the ribbon cable orientation and run `rpicam-hello --list-cameras`.
- **Full-resolution capture fails with an out-of-memory error** — the Zero 2 has
  512 MB; close other services, or raise the CMA size via the `vc4-kms-v3d`
  overlay in `/boot/firmware/config.txt`.
- **Screen stays black / `Invalid argument` on GPIO9 in the log** — the Display HAT
  Mini uses GPIO9 (SPI MISO) as its D/C line, which newer kernels won't release
  while SPI owns it. `/boot/firmware/config.txt` needs `dtparam=spi=on` and
  `dtoverlay=spi0-2cs,no_miso` (install.sh sets this), then reboot.
- **USB export says "USB device mode not enabled"** — `/boot/firmware/config.txt`
  needs `dtoverlay=dwc2,dr_mode=peripheral` under `[all]` (install.sh adds it),
  then reboot.
- **No PHOTOS drive appears on the computer** — check you used the data port
  (marked `USB`) and a data cable, not a charge-only one.
