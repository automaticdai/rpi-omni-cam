#!/usr/bin/env bash
# Install omnicam on a Raspberry Pi Zero 2 W running Raspberry Pi OS (Bookworm or later).
# Run from the repo:  sudo ./scripts/install.sh
#
# Photo storage size: half the free space, capped at 32 GB, unless set, e.g.
#   sudo PHOTOS_SIZE_GB=8 ./scripts/install.sh
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Please run with sudo: sudo $0" >&2
    exit 1
fi

REPO="$(cd "$(dirname "$0")/.." && pwd)"
MOUNT=/media/photos
IMAGE=/var/lib/omnicam/photos.img

echo "==> Installing system packages"
apt-get update
apt-get install -y --no-install-recommends \
    python3-picamera2 python3-pil python3-numpy python3-venv python3-pip \
    python3-spidev python3-libgpiod \
    fonts-dejavu-core dosfstools
# RPi.GPIO API (buttons, LED, backlight): rpi-lgpio on current releases.
apt-get install -y --no-install-recommends python3-rpi-lgpio \
    || apt-get install -y --no-install-recommends python3-rpi.gpio

echo "==> Configuring SPI for the Display HAT Mini"
# SPI0 with both chip selects (the HAT is on CE1) and MISO released: the HAT
# uses GPIO9 (MISO) as its D/C line, which newer kernels refuse to hand out
# while SPI owns it.
CONFIG=/boot/firmware/config.txt
[[ -f "$CONFIG" ]] || CONFIG=/boot/config.txt
REBOOT=0
raspi-config nonint do_spi 0
if ! grep -q '^dtoverlay=spi0-2cs,no_miso\s*$' "$CONFIG"; then
    if grep -qE '^dtoverlay=spi0-(0|1|2)?cs' "$CONFIG"; then
        sed -i -E 's/^dtoverlay=spi0-(0|1|2)?cs.*/dtoverlay=spi0-2cs,no_miso/' "$CONFIG"
    else
        printf '\n[all]\ndtoverlay=spi0-2cs,no_miso\n' >> "$CONFIG"
    fi
    REBOOT=1
fi

echo "==> Enabling USB device mode (USB export over the micro-USB data port)"
if ! grep -qE '^dtoverlay=dwc2(,dr_mode=(otg|peripheral))?\s*$' "$CONFIG"; then
    printf '\n[all]\ndtoverlay=dwc2,dr_mode=peripheral\n' >> "$CONFIG"
    REBOOT=1
fi
echo dwc2 > /etc/modules-load.d/omnicam.conf

echo "==> Creating virtualenv (with system packages for picamera2)"
python3 -m venv --system-site-packages "$REPO/.venv"
# --no-deps: everything else comes from apt (pip would otherwise build its own RPi.GPIO).
"$REPO/.venv/bin/pip" install --upgrade --no-deps displayhatmini st7789 gpiodevice
if ! "$REPO/.venv/bin/python" -c "import gpiod; gpiod.request_lines" 2>/dev/null; then
    "$REPO/.venv/bin/pip" install --upgrade "gpiod>=2"   # older OS ships libgpiod v1 bindings
fi

echo "==> Photo storage: FAT32 image $IMAGE mounted at $MOUNT"
mkdir -p "$(dirname "$IMAGE")" "$MOUNT"
if [[ ! -f "$IMAGE" ]]; then
    if [[ -z "${PHOTOS_SIZE_GB:-}" ]]; then
        FREE_GB=$(( $(df --output=avail -B1G "$(dirname "$IMAGE")" | tail -1) ))
        PHOTOS_SIZE_GB=$(( FREE_GB / 2 ))
        (( PHOTOS_SIZE_GB > 32 )) && PHOTOS_SIZE_GB=32
    fi
    (( PHOTOS_SIZE_GB >= 1 )) || { echo "error: not enough free space for photo storage" >&2; exit 1; }
    echo "    creating ${PHOTOS_SIZE_GB} GB image"
    fallocate -l "${PHOTOS_SIZE_GB}G" "$IMAGE"
    mkfs.vfat -F 32 -n PHOTOS "$IMAGE" >/dev/null
else
    echo "    keeping existing image ($(du -h --apparent-size "$IMAGE" | cut -f1))"
fi
sed -i '/LABEL=PHOTOS/d' /etc/fstab  # partition-based setup from older versions
if ! grep -q "^$IMAGE " /etc/fstab; then
    echo "$IMAGE  $MOUNT  vfat  loop,defaults,nofail,noatime,flush,umask=000  0  0" >> /etc/fstab
fi
systemctl daemon-reload
mountpoint -q "$MOUNT" || mount "$MOUNT"

echo "==> Installing systemd service"
sed "s#@REPO@#$REPO#g" "$REPO/systemd/omnicam.service" > /etc/systemd/system/omnicam.service
systemctl daemon-reload
systemctl enable omnicam.service

echo
if (( REBOOT )); then
    echo "Done. Boot configuration changed: reboot to start the camera (sudo reboot)."
else
    echo "Done. Start now with:  sudo systemctl start omnicam"
fi
echo "Logs:                  journalctl -u omnicam -f"
