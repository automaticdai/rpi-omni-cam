#!/usr/bin/env bash
# Create the FAT32 'PHOTOS' partition on the Pi itself, while it is running.
#
#   sudo ./scripts/partition-on-pi.sh [ROOT_SIZE_GB] [--yes]     (default 8 GB)
#
# Requirement: the root filesystem must NOT have been expanded over the whole
# card on first boot (a mounted ext4 filesystem can grow but never shrink).
# Before the first boot, edit cmdline.txt on the card's boot partition:
#   - Trixie:   delete the word  resize
#   - Bookworm: delete  init=/usr/lib/raspberrypi-sys-mods/firstboot
# The script checks the filesystem size itself and refuses if it is too big;
# in that case use scripts/partition-from-pc.sh instead.
#
# What it does (all online, no reboot):
#   1. sets partition 2 (root) to ROOT_SIZE_GB and grows the ext4 filesystem into it
#   2. adds partition 3 over the rest of the card, formats it FAT32 'PHOTOS'
#   3. mounts it at /media/photos (fstab entry added if missing)
set -euo pipefail

ROOT_GB=8
ASSUME_YES=0
for arg in "$@"; do
    case "$arg" in
        --yes) ASSUME_YES=1 ;;
        *) ROOT_GB="$arg" ;;
    esac
done
MOUNT=/media/photos
MIN_PHOTOS_GB=1

die() { echo "error: $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "run with sudo"
[[ "$ROOT_GB" =~ ^[0-9]+$ ]] || die "ROOT_SIZE_GB must be a whole number"
for tool in sfdisk partx resize2fs dumpe2fs mkfs.vfat blockdev findmnt lsblk udevadm; do
    command -v "$tool" >/dev/null || die "missing tool: $tool (sudo apt install dosfstools util-linux e2fsprogs)"
done

ensure_fstab() {
    mkdir -p "$MOUNT"
    if ! grep -q 'LABEL=PHOTOS' /etc/fstab; then
        echo "LABEL=PHOTOS  $MOUNT  vfat  defaults,nofail,noatime,flush,umask=000,x-systemd.device-timeout=5s  0  2" >> /etc/fstab
        systemctl daemon-reload
    fi
    mountpoint -q "$MOUNT" || mount "$MOUNT"
}

# --- find the boot disk ------------------------------------------------------
ROOT_PART="$(findmnt -n -o SOURCE /)"
ROOT_NAME="$(basename "$ROOT_PART")"
[[ -e "/sys/class/block/$ROOT_NAME/partition" ]] || die "root ($ROOT_PART) is not a disk partition"
PARTNUM="$(cat "/sys/class/block/$ROOT_NAME/partition")"
DISK="/dev/$(lsblk -no PKNAME "$ROOT_PART")"
[[ "$PARTNUM" == "2" ]] || die "expected root on partition 2, found partition $PARTNUM"
[[ "$(sfdisk -d "$DISK" | awk '/^label:/{print $2}')" == "dos" ]] || die "$DISK does not use an MBR (dos) partition table"

if [[ "$DISK" =~ [0-9]$ ]]; then P3="${DISK}p3"; else P3="${DISK}3"; fi

# Already done?
if [[ -b "$P3" ]]; then
    if [[ "$(lsblk -no LABEL "$P3")" == "PHOTOS" ]]; then
        echo "PHOTOS partition already exists ($P3); making sure it is mounted."
        ensure_fstab
        exit 0
    fi
    if [[ -z "$(lsblk -no FSTYPE "$P3")" && "$(sfdisk --part-type "$DISK" 3 | tr -d "[:space:]")" =~ ^(c|C|b|B)$ ]]; then
        echo "Found an unformatted FAT partition $P3 (left over from an earlier run); formatting it."
        resize2fs "$ROOT_PART"   # in case the earlier run stopped before growing root
        mkfs.vfat -F 32 -n PHOTOS "$P3"
        udevadm settle
        ensure_fstab
        exit 0
    fi
    die "$DISK already has a third partition that isn't PHOTOS; not touching it"
fi

# --- sizes (512-byte sectors) -------------------------------------------------
FS_BLOCKS=$(dumpe2fs -h "$ROOT_PART" 2>/dev/null | awk -F: '/^Block count/{gsub(/ /,"",$2); print $2}')
FS_BSIZE=$(dumpe2fs -h "$ROOT_PART" 2>/dev/null | awk -F: '/^Block size/{gsub(/ /,"",$2); print $2}')
FS_BYTES=$(( FS_BLOCKS * FS_BSIZE ))
TARGET_BYTES=$(( ROOT_GB * 1024 * 1024 * 1024 ))
DISK_SECTORS=$(blockdev --getsz "$DISK")
P2_START=$(sfdisk -d "$DISK" | awk -v p="$ROOT_PART" '$1 == p { gsub(/[^0-9]/, "", $4); print $4 }')
[[ -n "$P2_START" ]] || die "could not read the start of $ROOT_PART"
P2_SECTORS=$(( TARGET_BYTES / 512 ))
P3_START=$(( (P2_START + P2_SECTORS + 8191) / 8192 * 8192 ))   # 4 MiB aligned
PHOTOS_SECTORS=$(( DISK_SECTORS - P3_START ))

gb() { awk -v b="$1" 'BEGIN { printf "%.1f GB", b / 1073741824 }'; }

if (( FS_BYTES > TARGET_BYTES )); then
    cat >&2 <<EOF
error: the root filesystem is already $(gb "$FS_BYTES"), bigger than the ${ROOT_GB} GB requested.
It was most likely expanded over the whole card on first boot, and a running
system can't shrink it. Either:
  - re-flash the card and, before the first boot, edit cmdline.txt on the boot
    partition (Trixie: delete 'resize'; Bookworm: delete
    'init=/usr/lib/raspberrypi-sys-mods/firstboot'), then run this again; or
  - shrink it from another Linux machine: scripts/partition-from-pc.sh
EOF
    exit 1
fi
(( PHOTOS_SECTORS * 512 >= MIN_PHOTOS_GB * 1024 * 1024 * 1024 )) \
    || die "card too small: only $(gb $(( PHOTOS_SECTORS * 512 ))) would be left for photos"

echo "Disk:             $DISK ($(gb $(( DISK_SECTORS * 512 ))))"
echo "Root filesystem:  $(gb "$FS_BYTES") now -> ${ROOT_GB} GB"
echo "PHOTOS partition: $(gb $(( PHOTOS_SECTORS * 512 ))) (FAT32)"
if (( ! ASSUME_YES )); then
    read -r -p "Type YES to repartition: " answer
    [[ "$answer" == "YES" ]] || die "aborted"
fi

# --- partition table (disk is in use, so don't let sfdisk re-read it) --------
echo "==> Setting root partition size"
echo ",${P2_SECTORS}" | sfdisk --no-reread --no-tell-kernel -N 2 "$DISK"
echo "==> Adding PHOTOS partition"
echo "${P3_START},,c" | sfdisk --no-reread --no-tell-kernel --append "$DISK"

# Tell the kernel about the changes one partition at a time (works while mounted).
partx -u --nr 2 "$DISK"
partx -a --nr 3 "$DISK"
udevadm settle
[[ -b "$P3" ]] || die "kernel did not pick up $P3; reboot and run this script again"

echo "==> Growing root filesystem online"
resize2fs "$ROOT_PART"

echo "==> Formatting $P3"
mkfs.vfat -F 32 -n PHOTOS "$P3"
udevadm settle

ensure_fstab
echo
lsblk -o NAME,SIZE,FSTYPE,LABEL,MOUNTPOINTS "$DISK"
echo "Done. Photos will be saved to $MOUNT."
