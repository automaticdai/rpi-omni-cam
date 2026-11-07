#!/usr/bin/env bash
# Split a Raspberry Pi OS SD card into: boot | root (fixed size) | PHOTOS (FAT32, rest of card).
#
# Run on a Linux machine with the card in a card reader, AFTER the Pi has booted
# from the card once (first boot expands root over the whole card; this script
# shrinks it back and puts the photo partition after it).
#
#   sudo ./scripts/partition-from-pc.sh /dev/sdX [ROOT_SIZE_GB]   (default 8)
#
# The PHOTOS partition is readable by Windows, macOS and Linux card readers.
set -euo pipefail

DEV="${1:-}"
ROOT_GB="${2:-8}"

die() { echo "error: $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "run with sudo"
[[ -n "$DEV" && -b "$DEV" ]] || die "usage: $0 /dev/sdX [ROOT_SIZE_GB]"
[[ "$ROOT_GB" =~ ^[0-9]+$ ]] || die "ROOT_SIZE_GB must be a whole number"
for tool in sfdisk e2fsck resize2fs mkfs.vfat partprobe lsblk; do
    command -v "$tool" >/dev/null || die "missing tool: $tool"
done

part() { if [[ "$DEV" =~ [0-9]$ ]]; then echo "${DEV}p$1"; else echo "${DEV}$1"; fi; }
P1="$(part 1)"; P2="$(part 2)"; P3="$(part 3)"

# --- sanity checks: must look like an untouched Pi OS card -------------------
[[ "$(sfdisk -d "$DEV" | awk '/^label:/{print $2}')" == "dos" ]] \
    || die "$DEV does not have an MBR (dos) partition table"
[[ "$(lsblk -no FSTYPE "$P1")" == "vfat" ]] || die "$P1 is not the vfat boot partition"
[[ "$(lsblk -no FSTYPE "$P2")" == "ext4" ]] || die "$P2 is not the ext4 root partition"
[[ ! -b "$P3" ]] || die "$DEV already has a third partition"

echo "About to repartition this card:"
lsblk -o NAME,SIZE,FSTYPE,LABEL,MOUNTPOINTS "$DEV"
echo
echo "Root will be shrunk to ${ROOT_GB} GB; the rest becomes a FAT32 'PHOTOS' partition."
read -r -p "Type YES to continue: " answer
[[ "$answer" == "YES" ]] || die "aborted"

# --- unmount anything on the card --------------------------------------------
for p in $(lsblk -lnpo NAME "$DEV" | tail -n +2); do
    umount "$p" 2>/dev/null || true
done

# --- shrink root ---------------------------------------------------------------
echo "==> Checking and shrinking root filesystem"
e2fsck -f -y "$P2" || [[ $? -le 1 ]]
resize2fs "$P2" "${ROOT_GB}G"

SECTORS=$(( ROOT_GB * 1024 * 1024 * 1024 / 512 ))
echo "==> Shrinking partition 2 to ${SECTORS} sectors"
echo ",${SECTORS}" | sfdisk --no-reread -N 2 "$DEV"

# --- add photos partition ------------------------------------------------------
echo "==> Adding FAT32 partition in the remaining space"
# Explicit start: without it sfdisk may pick the small gap before the boot partition.
P2_START=$(sfdisk -d "$DEV" | awk -v p="$P2" '$1 == p { gsub(/[^0-9]/, "", $4); print $4 }')
[[ -n "$P2_START" ]] || die "could not read the start of $P2"
P3_START=$(( (P2_START + SECTORS + 8191) / 8192 * 8192 ))   # 4 MiB aligned
echo "${P3_START},,c" | sfdisk --no-reread --append "$DEV"
partprobe "$DEV"
sleep 2
[[ -b "$P3" ]] || die "new partition $P3 did not appear"
mkfs.vfat -F 32 -n PHOTOS "$P3"

# --- make root fill its (now exact) partition ----------------------------------
e2fsck -f -y "$P2" || [[ $? -le 1 ]]
resize2fs "$P2"

echo
lsblk -o NAME,SIZE,FSTYPE,LABEL "$DEV"
echo "Done. Put the card back in the Pi; it mounts PHOTOS at /media/photos (see scripts/install.sh)."
