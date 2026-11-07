"""Screens: each handles button events and renders a 320x240 image."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Callable, Optional

from PIL import Image, ImageDraw

from . import exif, filters, ui
from .buttons import LONG, PRESS, REPEAT, ButtonEvent
from .exposure import MODE_PARAMS, format_ev, format_iso, format_shutter
from .menu import MENU, Action, Choice, Section
from .storage import Entry

if TYPE_CHECKING:
    from .app import App


class Screen:
    wants_preview = False

    def __init__(self, app: "App"):
        self.app = app

    def on_button(self, ev: ButtonEvent) -> None:
        pass

    def tick(self, now: float) -> None:
        pass

    def render(self) -> Image.Image:
        raise NotImplementedError


def _moves(ev: ButtonEvent) -> int:
    """A/B as up/down in lists: press, long and repeat all move."""
    if ev.button == "A":
        return -1
    if ev.button == "B":
        return 1
    return 0


# ---------------------------------------------------------------------------
class LiveView(Screen):
    wants_preview = True

    def __init__(self, app: "App"):
        super().__init__(app)
        self._param: Optional[str] = None
        self.countdown_until: Optional[float] = None
        self._last_beep = 0

    @property
    def params(self) -> tuple[str, ...]:
        """Values A/B can adjust in the current mode."""
        return MODE_PARAMS.get(self.app.s.mode, ("ev",))

    @property
    def selected(self) -> str:
        """Highlighted value; falls back to the mode's first one after a mode change."""
        return self._param if self._param in self.params else self.params[0]

    def on_button(self, ev: ButtonEvent) -> None:
        app = self.app
        b, kind = ev.button, ev.kind
        if self.countdown_until is not None:
            if kind == PRESS:  # any press cancels the self-timer
                self.countdown_until = None
                app.toast("Self-timer cancelled")
            return
        if b == "X":
            if kind == PRESS:
                if app.timelapse:
                    app.toast("Hold X to stop timelapse")
                elif not app.busy:
                    if app.s.self_timer:
                        self.countdown_until = app.now() + app.s.self_timer
                    else:
                        app.take_photo()
            elif kind == LONG:
                if app.timelapse:
                    app.stop_timelapse()
                elif not app.busy:
                    app.start_timelapse()
        elif b == "Y" and kind == PRESS:
            ps = self.params
            self._param = ps[(ps.index(self.selected) + 1) % len(ps)]
        elif b in "AB" and kind == PRESS:
            app.step_exposure(self.selected, 1 if b == "A" else -1)
        elif b in "AB" and kind == LONG:
            if app.timelapse:
                app.toast("Timelapse running")
            elif b == "A":
                app.open(MenuView(app))
            else:
                app.open(PlaybackView(app))

    def tick(self, now: float) -> None:
        if self.countdown_until is None:
            return
        remaining = self.countdown_until - now
        if remaining <= 0:
            self.countdown_until = None
            self.app.take_photo()
        elif int(remaining) != self._last_beep:
            self._last_beep = int(remaining)
            self.app.led((1, 0.5, 0), 0.15)

    def render(self) -> Image.Image:
        app = self.app
        s = app.s
        frame = ui.fit(app.preview_filtered) if app.preview_filtered else Image.new("RGB", (ui.W, ui.H))
        if app.busy:
            frame = frame.point(lambda v: v // 3)
        layer, d = ui.overlay(frame)
        if s.grid:
            ui.grid(d)
        if s.histogram and app.preview_raw:
            ui.histogram(d, app.preview_raw)

        # top strip
        ui.bar(d, 0, 22)
        f = ui.font(13, "bold")
        top = [(s.mode, False, ui.ACCENT)]
        if app.timelapse:
            tl = app.timelapse
            total = f"/{tl.count}" if tl.count else ""
            nxt = max(0, int(tl.next_at - app.now()))
            top.append((f"TL {tl.done}{total} · {nxt}s", False, ui.RED))
        else:
            left = app.shots_left()
            top.append((f"● {left if left < 10000 else '9999+'}", False,
                        ui.RED if left < 10 else ui.WHITE))
        if s.self_timer:
            top.append((f"TIMER {s.self_timer}s", False, ui.WHITE))
        if s.save_raw:
            top.append(("RAW", False, ui.WHITE))
        if app.storage.in_ram:
            top.append(("RAM", False, ui.RED))
        ui.items_row(d, 4, top, f)
        if app.temperature:
            t = f"{app.temperature:.0f}°C"
            d.text((ui.W - 6 - ui.text_w(d, t, f), 4), t, font=f,
                   fill=ui.RED if app.temperature >= 75 else ui.GREY)

        # meter + bottom strip
        if app.meter_ev is not None:
            ui.meter(d, app.meter_ev, ui.H - 46)
        ui.bar(d, ui.H - 26, ui.H)
        meta = app.preview_meta
        params, sel = self.params, self.selected
        # Values set by the user are white; values chosen by auto-exposure are
        # grey (what the camera is currently using).
        if "shutter" in params:
            sh, sh_col = format_shutter(s.shutter), ui.WHITE
        else:
            sh = format_shutter(meta.exposure_s) if meta and meta.exposure_s else "--"
            sh_col = ui.GREY
        if "iso" in params:
            iso, iso_col = format_iso(s.iso), ui.WHITE
        else:
            iso = format_iso(meta.gain * 100) if meta else "ISO --"
            iso_col = ui.GREY
        fb = ui.font(14, "bold")
        bottom = [
            (sh, sel == "shutter", sh_col),
            (iso, sel == "iso", iso_col),
        ]
        if "ev" in params or s.ev:
            bottom.append(("EV " + format_ev(s.ev), sel == "ev", ui.WHITE))
        bottom.append((s.wb.upper()[:4] if s.wb != "auto" else "AWB", False, ui.GREY))
        if s.filter != "none":
            bottom.append((filters.label(s.filter), False, ui.ACCENT))
        ui.items_row(d, ui.H - 21, bottom, fb, gap=12)

        # centre messages
        if self.countdown_until is not None:
            n = max(1, int(self.countdown_until - app.now() + 0.999))
            ui.centered(d, 80, str(n), ui.font(64, "bold"), ui.ACCENT)
        elif app.job and app.job.kind == "photo":
            remaining = app.job.remaining(app.now())
            msg = f"Exposing… {remaining:.0f}s" if remaining and remaining >= 1 else "Saving…"
            ui.centered(d, 100, msg, ui.font(20, "bold"))
        return ui.flatten(frame, layer)


# ---------------------------------------------------------------------------
class ReviewView(Screen):
    """Shows the photo just taken for a moment."""

    SECONDS = 1.5

    def __init__(self, app: "App", thumb: Image.Image, name: str):
        super().__init__(app)
        self.thumb, self.name = thumb, name
        self.until = app.now() + self.SECONDS

    def on_button(self, ev: ButtonEvent) -> None:
        if ev.kind == PRESS:
            self.app.back_to_live()

    def tick(self, now: float) -> None:
        if now >= self.until:
            self.app.back_to_live()

    def render(self) -> Image.Image:
        img = ui.fit(self.thumb)
        layer, d = ui.overlay(img)
        ui.bar(d, ui.H - 24, ui.H)
        d.text((8, ui.H - 20), "✓ Saved " + self.name, font=ui.font(14, "bold"), fill=ui.GREEN)
        return ui.flatten(img, layer)


# ---------------------------------------------------------------------------
class MenuView(Screen):
    def __init__(self, app: "App"):
        super().__init__(app)
        # stack of (title, node, selected index); node is the list being shown
        self.stack: list[list] = [["Menu", MENU, 0]]

    def _rows(self, title: str, node) -> list[tuple[str, str]]:
        if isinstance(node, Choice):
            cur = getattr(self.app.s, node.key)
            return [(lbl, "●" if v == cur else "") for v, lbl in node.options]
        rows = []
        for item in node:
            if isinstance(item, Choice):
                rows.append((item.label, item.value_label(getattr(self.app.s, item.key))))
            elif isinstance(item, Section):
                rows.append((item.label, "›"))
            else:
                rows.append((item.label, ""))
        return rows

    def on_button(self, ev: ButtonEvent) -> None:
        frame = self.stack[-1]
        title, node, sel = frame
        n = len(node.options) if isinstance(node, Choice) else len(node)
        if ev.button in "AB":
            frame[2] = (sel + _moves(ev)) % n
            return
        if ev.kind != PRESS:
            return
        if ev.button == "X":  # back
            self.stack.pop()
            if not self.stack:
                self.app.back_to_live()
            return
        # Y: select
        if isinstance(node, Choice):
            self.app.set_setting(node.key, node.options[sel][0])
            self.stack.pop()
            return
        item = node[sel]
        if isinstance(item, Section):
            self.stack.append([item.label, item.items, 0])
        elif isinstance(item, Choice):
            cur = getattr(self.app.s, item.key)
            idx = next((i for i, (v, _) in enumerate(item.options) if v == cur), 0)
            self.stack.append([item.label, item, idx])
        elif isinstance(item, Action):
            self.app.run_action(item.action)

    def render(self) -> Image.Image:
        title, node, sel = self.stack[-1]
        crumbs = " › ".join(f[0] for f in self.stack[1:]) or "Menu"
        img, d = ui.panel_screen(crumbs)
        ui.menu_list(d, self._rows(title, node), sel)
        ui.hints(d, "A/B move", "Y select · X back")
        return img


# ---------------------------------------------------------------------------
class PlaybackView(Screen):
    def __init__(self, app: "App"):
        super().__init__(app)
        self.entries: list[Entry] = app.storage.entries()
        self.index = 0
        self.zoom = False
        self._cache: tuple = (None, None, None)  # (key, image, info)

    def _load(self) -> tuple[Optional[Image.Image], dict]:
        if not self.entries:
            return None, {}
        entry = self.entries[self.index]
        key = (entry.path, self.zoom)
        if self._cache[0] == key:
            return self._cache[1], self._cache[2]
        try:
            with Image.open(entry.path) as im:
                info = exif.read(im)
                if self.zoom:  # 100 % centre crop
                    cx, cy = im.width // 2, im.height // 2
                    img = im.crop((cx - ui.W // 2, cy - ui.H // 2, cx + ui.W // 2, cy + ui.H // 2))
                else:
                    im.draft("RGB", (ui.W, ui.H))  # fast reduced-size JPEG decode
                    img = ui.fit(im.convert("RGB"))
                img = img.convert("RGB")
        except (OSError, ValueError):
            img, info = None, {"error": True}
        self._cache = (key, img, info)
        return img, info

    def on_button(self, ev: ButtonEvent) -> None:
        if ev.button in "AB" and self.entries:
            self.index = (self.index + _moves(ev)) % len(self.entries)
            self.zoom = False
        elif ev.button == "Y" and ev.kind == PRESS:
            if self.zoom:
                self.zoom = False
            else:
                self.app.back_to_live()
        elif ev.button == "X" and self.entries:
            if ev.kind == PRESS:
                self.zoom = not self.zoom
            elif ev.kind == LONG:
                entry = self.entries[self.index]
                what = f"timelapse ({entry.frames} frames)" if entry.frames > 1 else entry.path.name
                self.app.open(ConfirmView(self.app, f"Delete {what}?", "Delete",
                                          lambda: self._delete(entry), back=self))

    def _delete(self, entry: Entry) -> None:
        self.app.storage.delete(entry)
        self.app.refresh_storage()
        self.entries = self.app.storage.entries()
        self.index = min(self.index, max(0, len(self.entries) - 1))
        self._cache = (None, None, None)
        self.app.toast("Deleted")
        self.app.open(self)

    def render(self) -> Image.Image:
        img, info = self._load()
        if img is None:
            canvas, d = ui.panel_screen("Playback")
            msg = "No photos yet" if not self.entries else "Can't open this file"
            ui.centered(d, 105, msg, ui.font(16), ui.GREY)
            ui.hints(d, "", "Y back")
            return canvas
        layer, d = ui.overlay(img)
        entry = self.entries[self.index]
        f, fs = ui.font(13, "bold"), ui.font(12)
        ui.bar(d, 0, 20)
        d.text((6, 3), f"{self.index + 1}/{len(self.entries)}  {entry.title}", font=f, fill=ui.WHITE)
        if self.zoom:
            d.text((ui.W - 46, 3), "100%", font=f, fill=ui.ACCENT)
        ui.bar(d, ui.H - 20, ui.H)
        parts = []
        if entry.frames > 1:
            parts.append(f"Timelapse · {entry.frames} frames")
        if "exposure_s" in info:
            parts.append(format_shutter(info["exposure_s"]))
        if "iso" in info:
            parts.append(f"ISO {info['iso']}")
        if "datetime" in info:
            parts.append(info["datetime"].strftime("%H:%M:%S"))
        d.text((6, ui.H - 17), "  ".join(parts), font=fs, fill=ui.WHITE)
        return ui.flatten(img, layer)


# ---------------------------------------------------------------------------
class ConfirmView(Screen):
    def __init__(self, app: "App", message: str, verb: str, on_yes: Callable[[], None],
                 back: Optional[Screen] = None):
        super().__init__(app)
        self.message, self.verb, self.on_yes, self.back = message, verb, on_yes, back

    def on_button(self, ev: ButtonEvent) -> None:
        if ev.kind != PRESS:
            return
        if ev.button == "Y":
            self.on_yes()
        elif ev.button == "X":
            if self.back:
                self.app.open(self.back)
            else:
                self.app.back_to_live()

    def render(self) -> Image.Image:
        img, d = ui.panel_screen("Confirm")
        f = ui.font(16, "bold")
        for i, line in enumerate(ui.wrap(d, self.message, f, ui.W - 30)):
            ui.centered(d, 80 + i * 24, line, f)
        ui.hints(d, "X cancel", f"Y {self.verb.lower()}")
        return img


class InfoView(Screen):
    def __init__(self, app: "App", title: str, lines: list[str], back: Optional[Screen] = None):
        super().__init__(app)
        self.title, self.lines, self.back = title, lines, back

    def on_button(self, ev: ButtonEvent) -> None:
        if ev.kind == PRESS and ev.button in "XY":
            if self.back:
                self.app.open(self.back)
            else:
                self.app.back_to_live()

    def render(self) -> Image.Image:
        img, d = ui.panel_screen(self.title)
        f = ui.font(14)
        y = 38
        for line in self.lines:
            for part in ui.wrap(d, line, f, ui.W - 24) or [""]:
                d.text((12, y), part, font=f, fill=ui.WHITE)
                y += 21
        ui.hints(d, "", "X back")
        return img


class WelcomeView(Screen):
    """Shown for a moment after turning on."""

    SECONDS = 2.0

    def __init__(self, app: "App"):
        super().__init__(app)
        self.until = app.now() + self.SECONDS

    def on_button(self, ev: ButtonEvent) -> None:
        if ev.kind == PRESS:
            self.app.back_to_live()

    def tick(self, now: float) -> None:
        if now >= self.until:
            self.app.back_to_live()

    def render(self) -> Image.Image:
        from . import __version__

        img = Image.new("RGB", (ui.W, ui.H), ui.BG)
        d = ImageDraw.Draw(img)
        # camera glyph
        cx, cy = ui.W // 2, 82
        d.rounded_rectangle([cx - 46, cy - 26, cx + 46, cy + 30], 8, outline=ui.WHITE, width=3)
        d.rectangle([cx - 18, cy - 36, cx + 18, cy - 26], fill=ui.WHITE)
        d.ellipse([cx - 19, cy - 17, cx + 19, cy + 21], outline=ui.ACCENT, width=4)
        d.ellipse([cx - 7, cy - 5, cx + 7, cy + 9], fill=ui.ACCENT)
        d.ellipse([cx + 30, cy - 18, cx + 37, cy - 11], fill=ui.RED)
        ui.centered(d, 128, "omnicam", ui.font(34, "bold"), ui.WHITE)
        ui.centered(d, 172, f"version {__version__}", ui.font(13), ui.GREY)
        ui.centered(d, 210, "Hold Y for 3 s to turn off", ui.font(11), ui.GREY)
        return img


class ExportView(Screen):
    """USB export: the photo storage is a USB drive on the connected computer."""

    def on_button(self, ev: ButtonEvent) -> None:
        if ev.kind == PRESS and ev.button == "X":
            self.app.stop_export()

    def render(self) -> Image.Image:
        app = self.app
        img, d = ui.panel_screen("USB export")
        f, fb = ui.font(14), ui.font(16, "bold")
        if app.job and app.job.kind == "export_start":
            ui.centered(d, 100, "Preparing…", fb)
            return img
        state = app.system.usb_state()
        if state == "configured":
            head, col = "Connected", ui.GREEN
            body = ("Your computer shows a drive named PHOTOS. Eject it there "
                    "before pressing X.")
        elif state == "no-udc":
            head, col = "USB device mode not enabled", ui.RED
            body = "Add dtoverlay=dwc2 to config.txt (install.sh does this) and reboot."
        else:
            head, col = "Waiting for a computer", ui.ACCENT
            body = ("Connect the camera's USB data port (the inner micro-USB, "
                    "marked USB) to a computer.")
        ui.centered(d, 44, head, fb, col)
        y = 76
        for line in ui.wrap(d, body, f, ui.W - 30):
            ui.centered(d, y, line, f)
            y += 20
        ui.centered(d, ui.H - 46, "No photos can be taken during export.", ui.font(12), ui.GREY)
        ui.hints(d, "", "X stop export")
        return img


class BusyView(Screen):
    def __init__(self, app: "App", message: str):
        super().__init__(app)
        self.message = message

    def render(self) -> Image.Image:
        img, d = ui.panel_screen("Please wait")
        dots = "." * (int(time.monotonic() * 2) % 4)
        ui.centered(d, 105, self.message + dots, ui.font(18, "bold"))
        return img
