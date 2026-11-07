# rpi-omni-cam redesign — design

Date: 2026-11-07

## Intent

Rebuild the project as a small pocket camera that behaves like a simplified
DSLR: live colour preview, direct shutter/ISO control, full-resolution capture,
on-device review, filters and timelapse — all driven by four buttons. The old
`src/picam.py` (legacy `picamera`, SSD1306 OLED) is deleted.

## Hardware

- Raspberry Pi Zero 2 W
- Raspberry Pi camera module now; HQ camera later. Code must not assume a
  sensor: resolution, max exposure and max gain are read from the camera.
- Pimoroni Display HAT Mini: 320×240 ST7789 LCD, buttons A/B (left edge),
  X/Y (right edge), RGB LED.

## Stack (builder's call)

Python 3 on current Raspberry Pi OS, `picamera2` for the camera, Pimoroni
Pimoroni `displayhatmini` for the Display HAT Mini (needs
`dtoverlay=spi0-2cs,no_miso`: newer kernels won't release GPIO9/MISO, the HAT's
D/C line, while SPI owns it), Pillow + numpy for rendering and
filters. Runs as a systemd service at boot. A `--sim` mode (pygame window,
keyboard a/b/x/y, synthetic camera) allows development off the Pi.

## Controls (decided: option A, "pick a setting, then nudge it")

Live view:
- **X** short: take photo (honours self-timer). **X** hold: start/stop timelapse.
- **Y** short: move highlight between the values the current mode lets you set.
- **A** short: highlighted value +1 step. **A** hold: open menu.
- **B** short: highlighted value −1 step. **B** hold: open playback.

Menu: A/B move (hold auto-repeats), Y selects, X goes back (revised after
hardware testing). Confirmation dialogs: Y confirms, X cancels. Playback: A/B
browse, X zoom, hold X delete, Y back to live view.

Exposure (revised after hardware testing, decided): the mode — P (both auto),
Tv (shutter set), Sv (ISO set), M (both set) — is chosen in the menu (first item of the top level, not a sub-menu). A/B adjust
only the mode's values and stop at the ends of the range (no AUTO step): P → EV
compensation; Tv → shutter ↔ EV; Sv → ISO ↔ EV; M → shutter ↔ ISO. Shutter steps are full stops 1/4000 … 1 s (capped at 1 s after hardware testing), clipped to the sensor's
maximum. ISO = 100 × analogue gain, full stops, clipped to the sensor's max
gain. A 1 s exposure shows a countdown.

## Screens

- **Live view:** full-screen preview (with the active filter applied); top
  strip: mode, shots left, warnings, timelapse progress, temperature; bottom
  strip: shutter, ISO, WB, filter, EV; exposure meter (−2…+2) above the bottom
  strip computed from preview luminance; optional grid and histogram.
- **Menu:** Mode (top-level choice), Shooting (white balance, exposure compensation, filter, JPEG
  quality, save RAW, self-timer), Timelapse (interval, shots, exposure lock),
  Display (grid, histogram, brightness, screen-off timer), System (storage
  info, format photo partition, camera info, shut down).
- **Playback:** newest first; filename, date, exposure info from EXIF. X
  toggles 100 % centre crop, hold X deletes (with confirmation). A timelapse
  sequence appears as one entry; deleting it deletes the whole sequence.

## Storage (decided)

Photos live on a separate FAT32 partition (label `PHOTOS`) readable by any
card reader, mounted at `/media/photos`. Layout:
`DCIM/YYYY-MM-DD/IMG_NNNN.jpg`, timelapses `DCIM/YYYY-MM-DD/TL_NNNN/FRAME_NNNNN.jpg`.
If the partition is not mounted the app saves to RAM (`/dev/shm`, decided):
photos are lost at power-off, a red RAM marker is shown, shutdown warns, and
RAM use is capped (128 MB budget, 192 MB of memory always left available) so
capture can't run the Zero 2 out of memory. It never writes into the root
filesystem at the mount path.

Filters are baked into the saved JPEG (decided); no unfiltered copy. Optional
RAW `.dng` (menu, off by default) is unfiltered by nature. Timelapse saves
frames only; video assembly happens on a computer.

Settings persist in a JSON file between power cycles.

## Non-goals for v1

Video recording, Wi-Fi transfer, focus peaking, battery gauge.

## Builder's calls

- Filters: None, B&W, Sepia, High Contrast, Vivid, Cool, Warm, Vintage.
- Long-press threshold 0.7 s; short presses fire on release.
- Preview exposure is capped at 1/15 s; a longer shutter is simulated in the
  preview with extra gain where possible.
- Timelapse exposure lock freezes exposure and white-balance gains at start.
- Partition creation runs on the Pi itself (scripts/partition-on-pi.sh): the user
  disables the first-boot root expansion in cmdline.txt, then the script grows
  root to a fixed size online and adds PHOTOS. Fallback for already-expanded
  cards: scripts/partition-from-pc.sh, run on a Linux machine against the
  card after the Pi's first boot (root fs already expanded), because Pi OS
  expands the root partition over the whole card on first boot.
