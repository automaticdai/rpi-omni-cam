"""Photo storage.

Photos live in a FAT32 disk-image file on the SD card (PHOTO_IMAGE), loop-mounted
at the photo directory (/media/photos, see install.sh). Being a disk image, it can
be handed to a computer as a USB drive over the micro-USB port (USB export).

Layout:  <root>/DCIM/YYYY-MM-DD/IMG_NNNN.jpg
         <root>/DCIM/YYYY-MM-DD/TL_NNNN/FRAME_NNNNN.jpg
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from PIL import Image

# Without the mounted photo image, photos go to RAM (tmpfs) rather than the root
# filesystem. They are lost at power-off. The Zero 2 has only 512 MB and a
# full-resolution capture needs a lot of it, so RAM storage is capped twice:
# a fixed budget, and always leaving RAM_RESERVE_BYTES of memory available.
PHOTO_IMAGE = Path("/var/lib/omnicam/photos.img")
FALLBACK_DIR = Path("/dev/shm/omnicam-photos")
RAM_BUDGET_BYTES = 128 * 1024 * 1024
RAM_RESERVE_BYTES = 192 * 1024 * 1024


def mem_available() -> Optional[int]:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


def tree_size(path: Path) -> int:
    total = 0
    for p in path.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            pass
    return total

_IMG_RE = re.compile(r"^IMG_(\d+)\.jpg$", re.IGNORECASE)
_TL_RE = re.compile(r"^TL_(\d+)$")


@dataclass
class Entry:
    """One item in playback: a single photo or a whole timelapse sequence."""
    path: Path            # the image to show
    delete_paths: list    # files/dirs removed when deleting this entry
    title: str
    frames: int = 1


class Storage:
    def __init__(self, photo_dir: str, require_mount: bool = True):
        self.requested = Path(photo_dir)
        self.mounted = (not require_mount) or os.path.ismount(self.requested)
        self.in_ram = not self.mounted
        self.root = self.requested if self.mounted else FALLBACK_DIR
        self.dcim = self.root / "DCIM"

    # ---- naming -------------------------------------------------------
    def day_dir(self, when: Optional[datetime] = None) -> Path:
        d = self.dcim / (when or datetime.now()).strftime("%Y-%m-%d")
        d.mkdir(parents=True, exist_ok=True)
        return d

    @staticmethod
    def _next_number(directory: Path, pattern: re.Pattern) -> int:
        nums = [int(m.group(1)) for p in directory.iterdir() if (m := pattern.match(p.name))]
        return max(nums, default=0) + 1

    def next_photo_path(self, when: Optional[datetime] = None) -> Path:
        d = self.day_dir(when)
        return d / f"IMG_{self._next_number(d, _IMG_RE):04d}.jpg"

    def new_timelapse_dir(self, when: Optional[datetime] = None) -> Path:
        d = self.day_dir(when)
        tl = d / f"TL_{self._next_number(d, _TL_RE):04d}"
        tl.mkdir()
        return tl

    @staticmethod
    def frame_path(tl_dir: Path, index: int) -> Path:
        return tl_dir / f"FRAME_{index:05d}.jpg"

    # ---- saving -------------------------------------------------------
    @staticmethod
    def save_jpeg(img: Image.Image, path: Path, quality: int, exif: bytes = b"") -> None:
        tmp = path.with_name(path.name + ".part")
        with open(tmp, "wb") as f:
            kwargs = {"quality": quality}
            if exif:
                kwargs["exif"] = exif
            img.save(f, "JPEG", **kwargs)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)

    # ---- listing ------------------------------------------------------
    def entries(self) -> list[Entry]:
        """Playback entries, newest first."""
        out: list[Entry] = []
        if not self.dcim.is_dir():
            return out
        for day in sorted((p for p in self.dcim.iterdir() if p.is_dir()), reverse=True):
            items = []
            for p in day.iterdir():
                if p.is_file() and _IMG_RE.match(p.name):
                    extra = [q for q in (p.with_suffix(".dng"),) if q.exists()]
                    items.append((p.name, Entry(p, [p, *extra], f"{day.name}/{p.name}")))
                elif p.is_dir() and _TL_RE.match(p.name):
                    frames = sorted(p.glob("FRAME_*.jpg"))
                    if frames:
                        items.append((p.name, Entry(frames[-1], [p], f"{day.name}/{p.name}",
                                                    frames=len(frames))))
            # IMG_ and TL_ sort independently; order within a day by mtime instead.
            items.sort(key=lambda t: t[1].path.stat().st_mtime, reverse=True)
            out.extend(e for _, e in items)
        return out

    @staticmethod
    def delete(entry: Entry) -> None:
        for p in entry.delete_paths:
            if p.is_dir():
                shutil.rmtree(p)
            elif p.exists():
                p.unlink()
        day = entry.delete_paths[0].parent
        if day.is_dir() and not any(day.iterdir()):
            day.rmdir()

    # ---- space --------------------------------------------------------
    def free_bytes(self) -> int:
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            free = shutil.disk_usage(self.root).free
        except OSError:
            return 0
        if self.in_ram:
            free = min(free, RAM_BUDGET_BYTES - tree_size(self.root))
            avail = mem_available()
            if avail is not None:
                free = min(free, avail - RAM_RESERVE_BYTES)
        return max(0, free)

    def usage(self) -> tuple[int, int]:
        """(used, total) bytes."""
        if self.in_ram:
            return tree_size(self.root) if self.root.exists() else 0, RAM_BUDGET_BYTES
        try:
            u = shutil.disk_usage(self.root)
            return u.used, u.total
        except OSError:
            return 0, 0
