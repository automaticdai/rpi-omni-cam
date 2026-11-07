"""Battery level from common Pi Zero battery boards, auto-detected.

Supported: PiSugar (via pisugar-server on TCP 8423), anything Linux exposes in
/sys/class/power_supply (type Battery), and INA219-based UPS HATs such as the
Waveshare UPS HAT (C) for Pi Zero (I2C 0x43, voltage -> charge estimate).
"""

from __future__ import annotations

import logging
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger(__name__)


@dataclass
class BatteryState:
    percent: int
    charging: Optional[bool] = None


def _pisugar() -> Optional[BatteryState]:
    def ask(cmd: str) -> str:
        with socket.create_connection(("127.0.0.1", 8423), timeout=0.5) as s:
            s.sendall(cmd.encode() + b"\n")
            return s.recv(256).decode(errors="replace")

    try:
        pct = float(ask("get battery").split(":", 1)[1])
    except (OSError, ValueError, IndexError):
        return None
    charging = None
    try:
        charging = "true" in ask("get battery_charging").lower()
    except (OSError, ValueError):
        pass
    return BatteryState(round(max(0.0, min(100.0, pct))), charging)


def _sysfs() -> Optional[BatteryState]:
    for supply in Path("/sys/class/power_supply").glob("*"):
        try:
            if (supply / "type").read_text().strip() != "Battery":
                continue
            pct = int((supply / "capacity").read_text().strip())
            status = (supply / "status").read_text().strip().lower()
        except (OSError, ValueError):
            continue
        return BatteryState(pct, status == "charging")
    return None


def lipo_percent(volts: float) -> int:
    """Rough single-cell LiPo charge from resting voltage."""
    return round(max(0.0, min(1.0, (volts - 3.3) / (4.15 - 3.3))) * 100)


def _ina219(address: int = 0x43, bus_no: int = 1) -> Optional[BatteryState]:
    try:
        try:
            from smbus2 import SMBus
        except ImportError:
            from smbus import SMBus  # type: ignore
        with SMBus(bus_no) as bus:
            raw = bus.read_word_data(address, 0x02)  # bus voltage register
    except Exception:
        return None
    raw = ((raw & 0xFF) << 8) | (raw >> 8)  # SMBus is little-endian, INA219 big-endian
    volts = (raw >> 3) * 0.004
    if volts < 2.5:  # nothing plausible connected
        return None
    return BatteryState(lipo_percent(volts))


class Battery:
    """Finds a working source on first read, then keeps using it."""

    SOURCES: tuple[tuple[str, Callable[[], Optional[BatteryState]]], ...] = (
        ("PiSugar", _pisugar), ("power_supply", _sysfs), ("INA219 UPS HAT", _ina219),
    )

    def __init__(self):
        self._source: Optional[Callable[[], Optional[BatteryState]]] = None
        self.name: Optional[str] = None
        self._searched = False

    def read(self) -> Optional[BatteryState]:
        if self._source is not None:
            return self._source()
        if self._searched:
            return None
        self._searched = True
        for name, fn in self.SOURCES:
            state = fn()
            if state is not None:
                self._source, self.name = fn, name
                log.info("battery: %s (%d%%)", name, state.percent)
                return state
        log.info("battery: no battery gauge found")
        return None
