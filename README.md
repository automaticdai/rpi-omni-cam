# rpi-omni-cam

A pocket camera that behaves like a simplified DSLR, built from a Raspberry Pi
Zero 2 W, a Raspberry Pi camera and a Pimoroni Display HAT Mini.

- Live colour preview with shutter speed / ISO / white balance / filter readout,
  an exposure meter, optional grid and histogram
- P / Tv / Sv / M exposure modes (chosen in the menu), with the mode's values
  and exposure compensation adjusted straight from live view
- Full-resolution JPEG capture, optional RAW (DNG), shutter speeds 1/4000 – 1 s
- Filters: B&W, Sepia, High Contrast, Vivid, Cool, Warm, Vintage (baked into the JPEG)
- Self-timer, timelapse with exposure lock
- On-camera playback with 100 % zoom and delete
- Photos stored on a separate FAT32 partition you can read with any card reader

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
| **P**  (program)          | auto | auto | EV only |
| **Tv** (shutter priority) | set  | auto | shutter ↔ EV |
| **Sv** (ISO priority)     | auto | set  | ISO ↔ EV |
| **M**  (manual)           | set  | set  | shutter ↔ ISO |

EV is exposure compensation (−2 … +2): it tells auto-exposure to make the
picture darker or brighter. There is no aperture mode, since the Pi cameras
have no electronically controlled aperture.

In the menu: **A/B** move (hold to scroll), **Y** selects, **X** goes back.
Confirmation dialogs (delete, format, shut down): **Y** confirms, **X** cancels.
In playback: **A/B** browse, **X** toggles 100 % zoom, **hold X** deletes (asks to
confirm), **Y** returns to live view.

The LED glows red while capturing, flashes green when a photo is saved, blinks
amber during the self-timer and blue for each timelapse frame.

## Setup

1. Flash **Raspberry Pi OS Lite** with Raspberry Pi Imager, setting up
   Wi-Fi/SSH as you like.
2. **Before the first boot**, stop the OS from expanding itself over the whole
   card, so there is room left for the photo partition. Open the card's boot
   drive on your computer (it shows up as `bootfs` on Windows/macOS/Linux) and
   edit `cmdline.txt` — it is a single line:
   - **Trixie** (current release): delete the word `resize`
   - **Bookworm**: delete `init=/usr/lib/raspberrypi-sys-mods/firstboot`

   Save, eject, and boot the Pi.
3. Clone this repo on the Pi, install, and create the photo partition:
   ```bash
   git clone <this repo> ~/rpi-omni-cam
   cd ~/rpi-omni-cam
   sudo ./scripts/install.sh            # picamera2, Display HAT Mini, SPI, service
   sudo ./scripts/partition-on-pi.sh 8  # root grows to 8 GB, the rest becomes PHOTOS
   sudo reboot
   ```
   `partition-on-pi.sh` works on the running system: it grows the root
   filesystem to the given size and creates a FAT32 `PHOTOS` partition in the
   rest of the card, mounted at `/media/photos`. It checks the root filesystem
   first and refuses to do anything if step 2 was missed.

**If you missed step 2** (root already fills the card), a running Pi can't
shrink it. Either re-flash and do step 2, or put the card in a reader on a
Linux machine (on Windows, WSL2 can reach a USB card reader via `usbipd-win`)
and run:
```bash
lsblk                                            # find the card, e.g. /dev/sdb
sudo ./scripts/partition-from-pc.sh /dev/sdb 8   # 8 GB for the OS, rest for photos
```

Until the photo partition exists the camera still works, but photos are kept
**in RAM only** (`/dev/shm/omnicam-photos`) and are **lost at power-off**. The
status bar shows a red **RAM**, and shutting down warns you if RAM photos would
be lost. You can still copy them off over the network before shutting down
(`scp -r pi@<camera>:/dev/shm/omnicam-photos/DCIM .`). RAM storage is capped at
128 MB and always leaves 192 MB of memory free for capturing; when it is full
the camera says so instead of taking the shot.

### Getting photos off the camera

Shut the camera down (Menu › System › Shut down), take the card out and put it in a
card reader. The `PHOTOS` drive contains:

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
  storage.py    photo paths, listing, deletion
  menu.py       menu tree
  ui.py         drawing helpers
  exif.py       EXIF read/write
scripts/        install.sh, partition-on-pi.sh, partition-from-pc.sh
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
