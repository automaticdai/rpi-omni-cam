# rpi-omni-cam

A pocket camera that behaves like a simplified DSLR, built from a Raspberry Pi
Zero 2 W, a Raspberry Pi camera and a Pimoroni Display HAT Mini.

- Live colour preview with shutter speed / ISO / white balance / filter readout,
  an exposure meter, optional grid and histogram
- P / A / S / M exposure modes (Sony naming, chosen in the menu), with the mode's values
  and exposure compensation adjusted straight from live view
- Full-resolution JPEG capture, optional RAW (DNG), shutter speeds 1/4000 – 1 s
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

```
        ┌──────────────────────────────┐
   A ── │                              │ ── X   SHUTTER  (hold: start/stop timelapse)
        │        Display HAT Mini      │
   B ── │                              │ ── Y   select which value A/B change
        └──────────────────────────────┘
   A: value up   (hold: menu)
   B: value down (hold: playback)
```

The exposure mode is chosen in **Menu › Mode** (the first item). In live view, **Y**
picks which of the mode's values is highlighted and **A/B** step it; values stop
at the ends of their range. Values you set are shown in white; values chosen by
auto-exposure are shown in grey.

| Mode | Shutter | ISO  | Y switches between |
|------|---------|------|--------------------|
| **P**  (program)           | auto | auto | EV only |
| **A**  (aperture priority) | auto | set  | ISO ↔ EV |
| **S**  (shutter priority)  | set  | auto | shutter ↔ EV |
| **M**  (manual)            | set  | set  | shutter ↔ ISO |

EV is exposure compensation (−2 … +2): it tells auto-exposure to make the
picture darker or brighter. In **A** you set the aperture on the lens ring (HQ
camera; the other Pi cameras have a fixed aperture) and the ISO with A/B, and
the camera picks the shutter speed.

In the menu: **A/B** move (hold to scroll), **Y** selects, **X** goes back.
Confirmation dialogs (delete, format, shut down): **Y** confirms, **X** cancels.
In playback: **A/B** browse, **X** toggles 100 % zoom, **hold X** deletes (asks to
confirm), **Y** returns to live view.

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

1. On the camera: **Menu › USB export**.
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
