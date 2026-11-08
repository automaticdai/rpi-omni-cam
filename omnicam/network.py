"""Wi-Fi through NetworkManager (nmcli): home network, camera hotspot, or off.

All nmcli calls run on a background thread (they can take seconds); the UI
reads the cached `status`. The home network is whatever NetworkManager already
has saved (set up with Raspberry Pi Imager or nmtui) - with four buttons there
is no way to type a new password on the camera.
"""

from __future__ import annotations

import logging
import queue
import secrets
import string
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)
HOTSPOT_CON = "omnicam-hotspot"
HOTSPOT_IP = "10.42.0.1"  # NetworkManager's address for shared (hotspot) connections
AUTO_WAIT_S = 60  # Auto: how long to wait for the home network before the hotspot


@dataclass
class WifiNetwork:
    ssid: str
    signal: int  # 0-100
    secure: bool
    saved: bool
    active: bool = False


@dataclass
class NetStatus:
    radio: bool = False
    connected: bool = False
    ssid: str = ""
    ip: str = ""
    hotspot: bool = False
    busy: str = ""  # what is being done right now, e.g. "Starting hotspot"

    @property
    def summary(self) -> str:
        if self.busy:
            return self.busy + "…"
        if not self.radio:
            return "Wi-Fi off"
        if self.hotspot:
            return f"Hotspot {self.ssid}"
        if self.connected:
            return f"Connected to {self.ssid}"
        return "Not connected"


def new_password() -> str:
    alphabet = string.ascii_lowercase.replace("l", "").replace("o", "") + "23456789"
    return "".join(secrets.choice(alphabet) for _ in range(10))


def hotspot_ssid() -> str:
    try:
        mac = Path("/sys/class/net/wlan0/address").read_text().strip().replace(":", "")
        return f"omnicam-{mac[-4:].upper()}"
    except OSError:
        return "omnicam"


class Network:
    """Owns a worker thread; `apply(mode, password)` queues a change."""

    POLL_S = 5.0

    def __init__(self, iface: str = "wlan0"):
        self.iface = iface
        self.status = NetStatus()
        self.hotspot_ssid = hotspot_ssid()
        self._mode = "home"
        self._password = ""
        self._mode_since = time.monotonic()
        self._q: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._retry = None  # mode change still to apply
        self._warned = False
        # Outcome of the last join request: (ok, message, ssid); read by the App.
        self.join_result: Optional[tuple[bool, str, str]] = None

    # ---- public (UI thread) ----
    def start(self, mode: str, password: str) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, daemon=True, name="network")
            self._thread.start()
        self.apply(mode, password, initial=True)

    def apply(self, mode: str, password: str, initial: bool = False) -> None:
        self._q.put((mode, password, initial))

    def join(self, ssid: str, password: str) -> None:
        """Leave the hotspot and join (and save) a network. If that fails, the
        hotspot comes back so the phone can reconnect. Result in join_result."""
        self.join_result = None
        self._q.put(("__join__", ssid, password))

    def setup_allowed(self, client_ip: str) -> bool:
        """Wi-Fi changes only from a phone on the camera's own hotspot."""
        return self.status.hotspot and client_ip.startswith(HOTSPOT_IP.rsplit(".", 1)[0] + ".")

    def scan(self) -> list[WifiNetwork]:
        """Networks in range, strongest first (called from the web server)."""
        saved = {ssid for _, ssid in self.saved_networks()}
        seen: dict[str, WifiNetwork] = {}
        try:
            out = self._nmcli("-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "dev", "wifi", "list",
                              "--rescan", "auto", timeout=20)
        except (RuntimeError, OSError, subprocess.SubprocessError) as e:
            log.info("wi-fi scan: %s", e)
            return []
        for line in out.splitlines():
            parts = _split_terse(line)
            if len(parts) < 4 or not parts[1].strip() or parts[1] == self.hotspot_ssid:
                continue
            ssid, signal = parts[1], int(parts[2] or 0)
            net = WifiNetwork(ssid, signal, parts[3] not in ("", "--"), ssid in saved, parts[0] == "*")
            if ssid not in seen or seen[ssid].signal < signal:
                seen[ssid] = net
        return sorted(seen.values(), key=lambda n: -n.signal)

    def saved(self) -> list[str]:
        """Saved Wi-Fi connections (names), not counting the camera's hotspot."""
        try:
            out = self._nmcli("-t", "-f", "NAME,TYPE", "con", "show", timeout=10)
        except (RuntimeError, OSError, subprocess.SubprocessError):
            return []
        names = []
        for line in out.splitlines():
            name, typ = (_split_terse(line) + ["", ""])[:2]
            if typ == "802-11-wireless" and name != HOTSPOT_CON:
                names.append(name)
        return names

    def saved_networks(self) -> list[tuple[str, str]]:
        """(connection name, network name) of each saved Wi-Fi connection. The
        names differ e.g. for networks set up by Raspberry Pi Imager (netplan-...)."""
        out = []
        for name in self.saved():
            try:
                ssid = self._nmcli("-g", "802-11-wireless.ssid", "con", "show", "id", name, timeout=10).strip()
            except (RuntimeError, OSError, subprocess.SubprocessError):
                ssid = name
            out.append((name, ssid or name))
        return out

    def forget(self, name: str) -> None:
        if name == HOTSPOT_CON or name not in self.saved():
            raise RuntimeError("No such saved network")
        self._nmcli("con", "delete", "id", name)
        log.info("wi-fi: forgot %s", name)

    def close(self) -> None:
        self._stop.set()
        self._q.put(None)

    # ---- worker ----
    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                item = self._q.get(timeout=self.POLL_S)
            except queue.Empty:
                item = False
            if item is None:
                break
            if item and item[0] == "__join__":
                self._join(item[1], item[2])
                continue
            if item:
                self._retry = item
            try:
                if self._retry:
                    self._set_mode(*self._retry)
                    self._retry = None
                self._refresh()
                self._auto_fallback()
            except Exception as e:  # e.g. NetworkManager not started yet at boot: retry
                if self._retry and not self._warned:
                    log.warning("wi-fi: %s (will retry)", e)
                    self._warned = True
                elif not self._retry:
                    log.exception("network")

    def _nmcli(self, *args: str, timeout: float = 30) -> str:
        res = subprocess.run(["nmcli", *args], capture_output=True, text=True, timeout=timeout)
        if res.returncode != 0:
            raise RuntimeError((res.stderr or res.stdout).strip() or f"nmcli {args[0]} failed")
        return res.stdout

    def _set_mode(self, mode: str, password: str, initial: bool) -> None:
        self._mode, self._password = mode, password
        self._mode_since = time.monotonic()
        self._refresh()
        st = self.status
        log.info("wi-fi mode %s (now: %s)", mode, st.summary)
        if mode == "off":
            if st.radio:
                self._busy("Turning Wi-Fi off")
                self._nmcli("radio", "wifi", "off")
            return
        if not st.radio:
            self._busy("Turning Wi-Fi on")
            self._nmcli("radio", "wifi", "on")
            time.sleep(2)
        if mode == "hotspot":
            if not st.hotspot:
                self._start_hotspot()
        elif st.hotspot:  # home / auto: leave the hotspot, rejoin the saved network
            self._stop_hotspot()
        # 'home' when already connected: nothing to do (don't disturb the link)

    def _join(self, ssid: str, password: str) -> None:
        before = set(self.saved())
        self._busy(f"Joining {ssid}")
        log.info("wi-fi: joining %s", ssid)
        try:
            if self.status.hotspot:
                time.sleep(2)  # let the phone page's reply get out first
                try:
                    self._nmcli("con", "down", HOTSPOT_CON)
                except RuntimeError:
                    pass
            args = ["dev", "wifi", "connect", ssid, "ifname", self.iface]
            if password:
                args += ["password", password]
            self._nmcli(*args, timeout=60)
            self._mode = "home"
            self.join_result = (True, f"Joined {ssid}", ssid)
            log.info("wi-fi: joined %s", ssid)
        except (RuntimeError, OSError, subprocess.SubprocessError) as e:
            msg = str(e).replace("Error: ", "")
            log.warning("wi-fi: joining %s failed: %s", ssid, msg)
            self.join_result = (False, msg, ssid)
            for name in set(self.saved()) - before:  # don't keep a profile with a wrong password
                try:
                    self._nmcli("con", "delete", "id", name)
                except RuntimeError:
                    pass
            if self._mode in ("hotspot", "auto"):  # back to the hotspot so the phone can retry
                try:
                    self._start_hotspot()
                except RuntimeError as e2:
                    log.error("wi-fi: hotspot did not come back: %s", e2)
        self._refresh()

    def _busy(self, text: str) -> None:
        self.status = NetStatus(**{**vars(self.status), "busy": text})

    def _start_hotspot(self) -> None:
        self._busy("Starting hotspot")
        self._nmcli("dev", "wifi", "hotspot", "ifname", self.iface, "con-name", HOTSPOT_CON,
                    "ssid", self.hotspot_ssid, "password", self._password)
        try:  # only ever started by the camera, never at boot by NetworkManager
            self._nmcli("con", "modify", HOTSPOT_CON, "connection.autoconnect", "no")
        except RuntimeError:
            pass

    def _stop_hotspot(self) -> None:
        self._busy("Leaving hotspot")
        try:
            self._nmcli("con", "down", HOTSPOT_CON)
        except RuntimeError:
            pass
        try:
            self._nmcli("dev", "connect", self.iface, timeout=45)
        except RuntimeError as e:
            log.info("no home network to rejoin: %s", e)

    def _auto_fallback(self) -> None:
        st = self.status
        if (self._mode == "auto" and st.radio and not st.connected and not st.hotspot
                and time.monotonic() - self._mode_since > AUTO_WAIT_S):
            log.info("auto: no home network after %d s, starting the hotspot", AUTO_WAIT_S)
            self._start_hotspot()
            self._refresh()

    def _refresh(self) -> None:
        st = NetStatus()
        try:
            st.radio = self._nmcli("radio", "wifi", timeout=5).strip() == "enabled"
            for line in self._nmcli("-t", "-f", "DEVICE,STATE,CONNECTION", "dev", timeout=5).splitlines():
                dev, _, rest = line.partition(":")
                state, _, con = rest.partition(":")
                if dev == self.iface and state == "connected":
                    st.connected = True
                    st.hotspot = con == HOTSPOT_CON
            if st.connected:
                for line in self._nmcli("-t", "-f", "IP4.ADDRESS,GENERAL.CONNECTION", "dev", "show",
                                        self.iface, timeout=5).splitlines():
                    k, _, v = line.partition(":")
                    if k.startswith("IP4.ADDRESS") and not st.ip:
                        st.ip = v.split("/")[0]
                if st.hotspot:
                    st.ssid = self.hotspot_ssid
                else:
                    for line in self._nmcli("-t", "-f", "ACTIVE,SSID", "dev", "wifi", "list",
                                            "--rescan", "no", timeout=5).splitlines():
                        if line.startswith("yes:"):
                            st.ssid = line[4:].replace("\\:", ":")
        except (RuntimeError, OSError, subprocess.SubprocessError) as e:
            log.debug("wi-fi status: %s", e)
        self.status = st


def _split_terse(line: str) -> list[str]:
    """Split an `nmcli -t` line on ':' (colons in values are escaped as '\\:')."""
    out, cur, esc = [], "", False
    for ch in line:
        if esc:
            cur += ch
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == ":":
            out.append(cur)
            cur = ""
        else:
            cur += ch
    out.append(cur)
    return out


class SimNetwork(Network):
    """No nmcli: pretends to switch instantly."""

    def __init__(self):
        super().__init__()
        self.hotspot_ssid = "omnicam-SIM"
        self.status = NetStatus(radio=True, connected=True, ssid="HomeWiFi", ip="127.0.0.1")
        self._saved = ["HomeWiFi"]
        self._in_range = [("HomeWiFi", 80, True), ("Cafe Guest", 55, False), ("Neighbour", 30, True)]

    def setup_allowed(self, client_ip: str) -> bool:
        return self.status.hotspot

    def scan(self) -> list[WifiNetwork]:
        return [WifiNetwork(n, sig, sec, n in self._saved, n == self.status.ssid and not self.status.hotspot)
                for n, sig, sec in self._in_range]

    def saved(self) -> list[str]:
        return list(self._saved)

    def saved_networks(self) -> list[tuple[str, str]]:
        return [(n, n) for n in self._saved]

    def forget(self, name: str) -> None:
        if name not in self._saved:
            raise RuntimeError("No such saved network")
        self._saved.remove(name)

    def join(self, ssid: str, password: str) -> None:
        secure = next((sec for n, _, sec in self._in_range if n == ssid), True)
        if secure and ssid not in self._saved and len(password) < 8:
            self.join_result = (False, "Secrets were required, but not provided", ssid)
            return
        if ssid not in self._saved:
            self._saved.append(ssid)
        self._mode = "home"
        self.status = NetStatus(radio=True, connected=True, ssid=ssid, ip="127.0.0.1")
        self.join_result = (True, f"Joined {ssid}", ssid)

    def start(self, mode: str, password: str) -> None:
        self.apply(mode, password)

    def apply(self, mode: str, password: str, initial: bool = False) -> None:
        self._mode, self._password = mode, password
        if mode == "off":
            self.status = NetStatus()
        elif mode == "hotspot":
            self.status = NetStatus(radio=True, connected=True, ssid=self.hotspot_ssid,
                                    ip=HOTSPOT_IP, hotspot=True)
        else:
            self.status = NetStatus(radio=True, connected=True, ssid="HomeWiFi", ip="127.0.0.1")

    def close(self) -> None:
        pass


class NoNetwork(Network):
    """No NetworkManager on this machine: Wi-Fi settings do nothing."""

    def start(self, mode: str, password: str) -> None:
        pass

    def apply(self, mode: str, password: str, initial: bool = False) -> None:
        pass

    def close(self) -> None:
        pass
