#!/usr/bin/env bash
# Install omnicam on a Raspberry Pi Zero 2 W running Raspberry Pi OS (Bookworm or later).
# Run from the repo:  sudo ./scripts/install.sh
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Please run with sudo: sudo $0" >&2
    exit 1
fi

REPO="$(cd "$(dirname "$0")/.." && pwd)"
MOUNT=/media/photos

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

echo "==> Creating virtualenv (with system packages for picamera2)"
python3 -m venv --system-site-packages "$REPO/.venv"
# --no-deps: everything else comes from apt (pip would otherwise build its own RPi.GPIO).
"$REPO/.venv/bin/pip" install --upgrade --no-deps displayhatmini st7789 gpiodevice
if ! "$REPO/.venv/bin/python" -c "import gpiod; gpiod.request_lines" 2>/dev/null; then
    "$REPO/.venv/bin/pip" install --upgrade "gpiod>=2"   # older OS ships libgpiod v1 bindings
fi

echo "==> Configuring photo partition mount at $MOUNT"
mkdir -p "$MOUNT"
if ! grep -q 'LABEL=PHOTOS' /etc/fstab; then
    echo "LABEL=PHOTOS  $MOUNT  vfat  defaults,nofail,noatime,flush,umask=000,x-systemd.device-timeout=5s  0  2" >> /etc/fstab
fi
systemctl daemon-reload
mount "$MOUNT" 2>/dev/null || echo "    (no PHOTOS partition yet - run: sudo ./scripts/partition-on-pi.sh)"

echo "==> Installing systemd service"
mkdir -p /var/lib/omnicam
sed "s#@REPO@#$REPO#g" "$REPO/systemd/omnicam.service" > /etc/systemd/system/omnicam.service
systemctl daemon-reload
systemctl enable omnicam.service

echo
if (( REBOOT )); then
    echo "Done. SPI configuration changed: reboot to start the camera (sudo reboot)."
else
    echo "Done. Start now with:  sudo systemctl start omnicam"
fi
echo "Logs:                  journalctl -u omnicam -f"
