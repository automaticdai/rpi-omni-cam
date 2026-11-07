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

Power (added after hardware testing, decided): the camera starts in standby
when the Pi boots (backlight and LED off, camera stopped). Holding **Y** for
3 s anywhere turns it on (welcome screen with name and version, ~2 s, any
button skips) or off (refused during USB export or while saving; a running
timelapse is stopped). Menu › Shut down still powers off the Pi.

Live view:
- **X** short: take photo (honours self-timer). **X** hold: start/stop timelapse.
- **Y** short: move highlight between the values the current mode lets you set.
- **A** short: highlighted value +1 step. **A** hold: open menu.
- **B** short: highlighted value −1 step. **B** hold: open playback.

Menu: A/B move (hold auto-repeats), Y selects, X goes back (revised after
hardware testing). Confirmation dialogs: Y confirms, X cancels. Playback: A/B
browse, X zoom, hold X delete, Y back to live view.

Exposure (revised after hardware testing, decided): the mode — P (both auto),
A (aperture priority: ISO set, aperture on the lens ring), S (shutter set),
M (both set), Sony naming and order (renamed from Tv/Sv) — is chosen in the menu (first item of the top level, not a sub-menu). A/B adjust
only the mode's values and stop at the ends of the range (no AUTO step): P → EV
compensation; A → ISO ↔ EV; S → shutter ↔ EV; M → shutter ↔ ISO. Shutter steps are full stops 1/4000 … 1 s (capped at 1 s after hardware testing), clipped to the sensor's
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
  info, erase all photos, camera info, shut down), USB export.
- **Playback:** newest first; filename, date, exposure info from EXIF. X
  toggles 100 % centre crop, hold X deletes (with confirmation). A timelapse
  sequence appears as one entry; deleting it deletes the whole sequence.

## Storage (decided, revised after hardware testing)

No partitioning. Photos are saved on the SD card's root filesystem inside a
FAT32 disk-image file (`/var/lib/omnicam/photos.img`, created by install.sh,
default half the free space up to 32 GB), loop-mounted at `/media/photos` via
fstab. Layout:
`DCIM/YYYY-MM-DD/IMG_NNNN.jpg`, timelapses `DCIM/YYYY-MM-DD/TL_NNNN/FRAME_NNNNN.jpg`.

Export is over the Pi Zero 2's micro-USB data port as a USB mass-storage device
(`dtoverlay=dwc2,dr_mode=peripheral`, `g_mass_storage`). USB mass storage shares
a block device, not a folder — hence the image file. Menu › USB export unmounts
the image and loads the gadget with it; X stops the export: unload gadget,
`fsck.vfat -a` (the host may have been unplugged without ejecting), remount.
The camera cannot shoot during export (two writers on one FAT would corrupt it).
"Erase all photos" deletes DCIM instead of reformatting.

If the image is not mounted the app saves to RAM (`/dev/shm`, decided):
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
- Partitioning was dropped in favour of the image file + USB export (see Storage).
