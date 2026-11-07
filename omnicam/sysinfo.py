"""Board, processor and memory information for the camera info screen."""

from __future__ import annotations

import os
import platform
from pathlib import Path
from typing import Optional

# ARM "CPU part" numbers (implementer 0x41) seen on Raspberry Pi boards.
_ARM_CORES = {
    "0xb76": "ARM1176", "0xc07": "Cortex-A7", "0xd03": "Cortex-A53",
    "0xd08": "Cortex-A72", "0xd0b": "Cortex-A76",
}


def _read(path: str) -> Optional[str]:
    try:
        return Path(path).read_text(errors="replace").replace("\0", " ").strip()
    except OSError:
        return None


def board_model() -> str:
    return _read("/proc/device-tree/model") or platform.node() or "unknown"


def processor() -> str:
    """e.g. 'BCM2837, 4× Cortex-A53 @ 1.0 GHz'."""
    parts = []
    compatible = _read("/proc/device-tree/compatible") or ""
    soc = next((c.split(",", 1)[1] for c in compatible.split() if c.startswith("brcm,")), None)
    if soc:
        parts.append(soc.upper())
    core = None
    for line in (_read("/proc/cpuinfo") or "").splitlines():
        key, _, value = line.partition(":")
        if key.strip() == "CPU part":
            core = _ARM_CORES.get(value.strip().lower())
            break
        if key.strip() == "model name":
            core = value.strip()
            break
    desc = f"{os.cpu_count() or 1}× {core or platform.machine()}"
    khz = _read("/sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq")
    if khz and khz.isdigit():
        desc += f" @ {int(khz) / 1e6:.1f} GHz"
    parts.append(desc)
    return ", ".join(parts)


def ram() -> str:
    """Physical RAM (MemTotal rounded up to a power of two) and what Linux can use,
    e.g. '512 MB (415 MB usable)'. The GPU's reserved memory isn't in MemTotal."""
    for line in (_read("/proc/meminfo") or "").splitlines():
        if line.startswith("MemTotal:"):
            usable_mb = int(line.split()[1]) // 1024
            physical_mb = 1 << max(0, usable_mb - 1).bit_length()
            physical = f"{physical_mb // 1024} GB" if physical_mb >= 1024 else f"{physical_mb} MB"
            return f"{physical} ({usable_mb} MB usable)"
    return "unknown"
