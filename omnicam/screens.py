"""Screens: each handles button events and renders a 320x240 image."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Callable, Optional

from PIL import Image, ImageDraw

from . import exif, filters, ui
from .buttons import LONG, PRESS, REPEAT, ButtonEvent
from .exposure import (BRACKETS, BURSTS, MODE_PARAMS, NIGHT_FRAMES, SQ_SIZE, VIDEO_SIZES, format_ev,
                       format_focus, format_iso, format_shutter)
from .menu import QUICK, TABS, Action, Choice, Section
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
    """Live view.

    Normally A opens the quick menu (hold A: full menu) and B runs the function button. Pressing Y starts
    adjusting: the mode's values are highlighted one at a time (Y moves on, past
    the last one adjusting ends) and A/B step the highlighted value up/down.
    Adjusting also ends after ADJUST_TIMEOUT_S without a press, or with X.
    """

    wants_preview = True
    ADJUST_TIMEOUT_S = 5.0

    def __init__(self, app: "App"):
        super().__init__(app)
        self._param: Optional[str] = None
        self.adjusting = False
        self._adjust_until = 0.0
        self.countdown_until: Optional[float] = None
        self._last_beep = 0

    @property
    def params(self) -> tuple[str, ...]:
        """Values A/B can adjust: the mode's, plus focus in manual-focus mode."""
        ps = MODE_PARAMS.get(self.app.s.mode, ("ev",))
        if self.app.camera.info.has_af and self.app.s.af_mode == "manual":
            ps = ps + ("focus",)
        return ps

    @property
    def selected(self) -> str:
        """Highlighted value; falls back to the mode's first one after a mode change."""
        return self._param if self._param in self.params else self.params[0]

    def _touch(self) -> None:
        self._adjust_until = self.app.now() + self.ADJUST_TIMEOUT_S

    def on_button(self, ev: ButtonEvent) -> None:
        app = self.app
        b, kind = ev.button, ev.kind
        if self.countdown_until is not None:
            if kind == PRESS:  # any press cancels the self-timer
                self.countdown_until = None
                app.toast("Self-timer cancelled")
            return
        if b == "X":
            self.adjusting = False
            if kind == PRESS:
                if app.timelapse:
                    app.toast("Hold X to stop timelapse")
                elif app.s.video_mode:
                    app.toggle_recording()
                elif not app.busy:
                    if app.s.self_timer:
                        self.countdown_until = app.now() + app.s.self_timer
                    else:
                        app.take_photo()
            elif kind == LONG:
                if app.double_first is not None:
                    app.cancel_double()
                elif app.camera.recording:
                    pass
                elif app.timelapse:
                    app.stop_timelapse()
                elif not app.busy:
                    app.start_timelapse()
        elif b == "Y" and kind == PRESS:
            ps = self.params
            if not self.adjusting:
                self.adjusting, self._param = True, ps[0]
            else:
                i = ps.index(self.selected) + 1
                if i >= len(ps):
                    self.adjusting = False
                else:
                    self._param = ps[i]
            self._touch()
        elif b in "AB" and self.adjusting:
            if kind in (PRESS, LONG, REPEAT):  # hold to keep stepping
                app.step_exposure(self.selected, 1 if b == "A" else -1)
                self._touch()
        elif b in "AB" and kind in (PRESS, LONG):
            if app.timelapse:
                app.toast("Timelapse running")
            elif app.camera.recording:
                app.toast("Recording: X stops")
            elif b == "A":
                app.open(MenuView(app) if kind == LONG else QuickMenuView(app))
            else:
                app.run_function(long=kind == LONG)

    def tick(self, now: float) -> None:
        if self.adjusting and now >= self._adjust_until:
            self.adjusting = False
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
        if app.double_ghost is not None:  # double exposure: first shot over the view
            frame = Image.blend(frame, ui.fit(app.double_ghost), 0.45)
        if app.preview_raw is not None and not app.busy:
            # computed on the un-letterboxed preview, then placed like the image
            raw = app.preview_raw
            if s.zebras:
                frame = ui.flatten(frame, ui.place(ui.zebras(raw, int(time.monotonic() * 8) % 8)))
            if s.peaking != "off":
                frame = ui.flatten(frame, ui.place(ui.peaking(raw, s.peaking)))
        if app.busy:
            frame = frame.point(lambda v: v // 3)
        layer, d = ui.overlay(frame)
        if s.mode == "MOVIE":  # the video frame, until recording shows it for real
            aspect = "native" if app.camera.recording else "16:9"
        else:
            aspect = "native" if s.video_mode else s.aspect
        if aspect != "native" and app.preview_raw is not None:
            ui.aspect_guides(d, app.preview_raw.size, aspect)
        if s.grid != "off":
            ui.grid(d, diagonal=s.grid == "diagonal")
        if s.histogram and app.preview_raw:
            ui.histogram(d, app.preview_raw)

        # top strip
        ui.bar(d, 0, 22)
        f = ui.font(13, "bold")
        top = [({"MOVIE": "MOVIE", "SQ": "S&Q"}.get(s.mode, s.mode), False, ui.ACCENT)]
        if app.clip is not None:
            secs = int(app.now() - app.clip.started)
            top.append((f"● REC {secs // 60}:{secs % 60:02d}", False, ui.RED))
        elif s.video_mode:
            if s.mode == "SQ":
                top.append((f"{SQ_SIZE[1]}p {s.sq_fps}fps", False, ui.WHITE))
            else:
                top.append((f"{s.video_size} {VIDEO_SIZES[s.video_size][2]}", False, ui.WHITE))
            top.append((f"{app.video_minutes_left()} min", False, ui.WHITE))
        elif app.timelapse:
            tl = app.timelapse
            total = f"/{tl.count}" if tl.count else ""
            nxt = max(0, int(tl.next_at - app.now()))
            top.append((f"TL {tl.done}{total} · {nxt}s", False, ui.RED))
        else:
            left = app.shots_left()
            top.append((f"● {left if left < 10000 else '9999+'}", False,
                        ui.RED if left < 10 else ui.WHITE))
        if s.video_mode:
            pass
        elif s.drive in BURSTS:
            top.append((f"BURST{BURSTS[s.drive]}", False, ui.WHITE))
        elif s.drive in BRACKETS:
            top.append((f"BKT{len(BRACKETS[s.drive])}", False, ui.WHITE))
        elif s.drive in NIGHT_FRAMES:
            top.append((f"NIGHT{NIGHT_FRAMES[s.drive]}", False, ui.WHITE))
        elif s.drive == "double":
            top.append(("DBL 2/2" if app.double_first else "DBL 1/2", False,
                        ui.ACCENT if app.double_first else ui.WHITE))
        if s.self_timer and not s.video_mode:
            top.append((f"T{s.self_timer}s", False, ui.WHITE))
        if s.save_raw and not s.video_mode:
            top.append(("RAW", False, ui.WHITE))
        if app.post.label:
            pct = f" {app.post.progress * 100:.0f}%" if app.post.progress is not None else "…"
            top.append(("MP4" + pct, False, ui.GREY))
        if app.web is not None:
            top.append(("WEB", False, ui.GREEN if app.web_url else ui.GREY))
        if app.camera.info.has_af and s.af_mode != "manual":
            top.append(({"continuous": "AF-C", "single": "AF-S"}.get(s.af_mode, "AF"), False, ui.GREY))
        if app.magnify > 1:
            top.append((f"{app.magnify}×", False, ui.ACCENT))
        if app.storage.in_ram:
            top.append(("RAM", False, ui.RED))
        ui.items_row(d, 4, top, f)
        bat = app.battery_state
        left_edge = ui.battery_icon(d, ui.W - 4, 4, bat.percent if bat else None,
                                    bat.charging if bat else None)
        if app.temperature and app.temperature >= 80:
            d.text((left_edge - 6 - ui.text_w(d, "HOT", f), 4), "HOT", font=f, fill=ui.RED)

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
        adj = self.adjusting
        bottom = [
            (sh, adj and sel == "shutter", sh_col),
            (iso, adj and sel == "iso", iso_col),
        ]
        if "ev" in params or s.ev:
            bottom.append(("EV " + format_ev(s.ev), adj and sel == "ev", ui.WHITE))
        if "focus" in params:
            bottom.append((format_focus(s.lens_position), adj and sel == "focus", ui.WHITE))
        bottom.append((s.wb.upper()[:4] if s.wb != "auto" else "AWB", False, ui.GREY))
        if s.filter != "none":
            bottom.append((filters.label(s.filter), False, ui.ACCENT))
        ui.items_row(d, ui.H - 21, bottom, fb, gap=12)

        # centre messages
        if self.countdown_until is not None:
            n = max(1, int(self.countdown_until - app.now() + 0.999))
            ui.centered(d, 80, str(n), ui.font(64, "bold"), ui.ACCENT)
        elif app.job and app.job.kind in ("photo", "double1"):
            remaining = app.job.remaining(app.now())
            if remaining and remaining >= 1:
                msg = f"Exposing… {remaining:.0f}s"
            elif s.drive in NIGHT_FRAMES:
                msg = "Night shot: hold still…"
            else:
                msg = "Saving…"
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
def _at_ends(choice: Choice, value) -> tuple[bool, bool]:
    """(no previous option, no next option) for the current value."""
    values = [v for v, _ in choice.options]
    i = values.index(value) if value in values else 0
    return i == 0, i == len(values) - 1


def _step_option(choice: Choice, value, direction: int):
    """Next/previous option value, stopping at the ends."""
    values = [v for v, _ in choice.options]
    i = values.index(value) if value in values else 0
    return values[max(0, min(len(values) - 1, i + direction))]


class MenuView(Screen):
    """Tabbed main menu.

    A/B move through the items; moving past the end of a tab continues into the
    next one, holding A/B jumps a whole tab. Y on a setting changes it in place
    (◀ value ▶, A/B step, Y keeps, X cancels); On/Off settings toggle; long lists
    open a picker. Y on an action runs it, on a section opens its page.
    X goes back (page → tab → live view). The position is remembered.
    """

    def __init__(self, app: "App"):
        super().__init__(app)
        self.tab, self.row = app.menu_pos
        self.page: Optional[Section] = None
        self.page_row = 0
        self.editing: Optional[Choice] = None
        self._orig = None
        self.picker: Optional[Choice] = None
        self.picker_row = 0

    # ---- state helpers ----
    @property
    def items(self) -> list:
        return self.page.items if self.page else TABS[self.tab].items

    @property
    def index(self) -> int:
        return self.page_row if self.page else self.row

    @property
    def current(self):
        return self.items[self.index]

    def _remember(self) -> None:
        self.app.menu_pos = (self.tab, self.row)

    def _move(self, d: int) -> None:
        if self.page:
            self.page_row = (self.page_row + d) % len(self.page.items)
            return
        self.row += d
        if self.row < 0:
            self.tab = (self.tab - 1) % len(TABS)
            self.row = len(TABS[self.tab].items) - 1
        elif self.row >= len(TABS[self.tab].items):
            self.tab = (self.tab + 1) % len(TABS)
            self.row = 0
        self._remember()

    def _jump_tab(self, d: int) -> None:
        if self.page:
            return
        self.tab, self.row = (self.tab + d) % len(TABS), 0
        self._remember()

    # ---- buttons ----
    def on_button(self, ev: ButtonEvent) -> None:
        app, b, kind = self.app, ev.button, ev.kind
        if self.picker is not None:
            if b in "AB":
                self.picker_row = (self.picker_row + _moves(ev)) % len(self.picker.options)
            elif kind == PRESS and b == "Y":
                app.set_setting(self.picker.key, self.picker.options[self.picker_row][0])
                self.picker = None
            elif kind == PRESS and b == "X":
                self.picker = None
            return
        if self.editing is not None:
            c = self.editing
            if b in "AB":
                app.set_setting(c.key, _step_option(c, getattr(app.s, c.key), -1 if b == "A" else 1))
            elif kind == PRESS and b == "Y":
                self.editing = None
            elif kind == PRESS and b == "X":  # cancel: back to the value before editing
                app.set_setting(c.key, self._orig)
                self.editing = None
            return
        if b in "AB":
            if kind == LONG:
                self._jump_tab(-1 if b == "A" else 1)
            elif kind == PRESS:
                self._move(-1 if b == "A" else 1)
            return
        if kind != PRESS:
            return
        if b == "X":
            if self.page:
                self.page = None
            else:
                app.back_to_live()
            return
        # Y
        item = self.current
        if isinstance(item, Section):
            self.page, self.page_row = item, 0
        elif isinstance(item, Action):
            app.run_action(item.action)
        elif isinstance(item, Choice):
            value = getattr(app.s, item.key)
            if item.is_toggle:
                app.set_setting(item.key, not value)
            elif item.uses_picker:
                self.picker = item
                values = [v for v, _ in item.options]
                self.picker_row = values.index(value) if value in values else 0
            else:
                self.editing, self._orig = item, value

    # ---- drawing ----
    def _tab_bar(self, d: ImageDraw.ImageDraw) -> None:
        d.rectangle([0, 0, ui.W, 24], fill=(40, 40, 46))
        if self.page:
            d.text((10, 4), f"{TABS[self.tab].name} › {self.page.label}", font=ui.font(14, "bold"),
                   fill=ui.WHITE)
            return
        f = ui.font(11, "bold")
        slot = ui.W / len(TABS)
        for i, tab in enumerate(TABS):
            x0 = int(i * slot)
            if i == self.tab:
                d.rectangle([x0 + 1, 2, int(x0 + slot) - 2, 22], fill=ui.ACCENT)
            tw = ui.text_w(d, tab.name, f)
            d.text((x0 + (slot - tw) / 2, 6), tab.name, font=f,
                   fill=(0, 0, 0) if i == self.tab else ui.GREY)

    def _help(self, d: ImageDraw.ImageDraw, text: str) -> None:
        f = ui.font(11)
        lines = ui.wrap(d, ui.keys(text), f, ui.W - 16)[:2]
        y = ui.H - 18 - 4 - 15 * len(lines)
        d.line([(8, y - 4), (ui.W - 8, y - 4)], fill=(60, 60, 66))
        for i, line in enumerate(lines):
            d.text((8, y + i * 15), line, font=f, fill=ui.GREY)

    def render(self) -> Image.Image:
        if self.picker is not None:
            return self._render_picker()
        img = Image.new("RGB", (ui.W, ui.H), ui.BG)
        d = ImageDraw.Draw(img)
        self._tab_bar(d)
        f, fv = ui.font(15), ui.font(14)
        top, row_h = 28, 25
        for i, item in enumerate(self.items):
            y = top + i * row_h
            sel = i == self.index
            label = item.label
            if isinstance(item, Choice):
                value = item.value_label(getattr(self.app.s, item.key))
            elif isinstance(item, Section):
                value = "›"
            else:
                value = ""
            if sel and self.editing is item:
                d.rounded_rectangle([4, y, ui.W - 4, y + row_h - 3], 4, outline=ui.ACCENT, width=2)
                d.text((12, y + 3), label, font=f, fill=ui.WHITE)
                first, last = _at_ends(item, getattr(self.app.s, item.key))
                x = ui.W - 14
                for part, col in (("▶", ui.GREY if last else ui.ACCENT), (f" {value} ", ui.ACCENT),
                                  ("◀", ui.GREY if first else ui.ACCENT)):
                    x -= ui.text_w(d, part, fv)
                    d.text((x, y + 4), part, font=fv, fill=col)
                continue
            if sel:
                d.rounded_rectangle([4, y, ui.W - 4, y + row_h - 3], 4, fill=ui.ACCENT)
            d.text((12, y + 3), label, font=f, fill=(0, 0, 0) if sel else ui.WHITE)
            if value:
                d.text((ui.W - 14 - ui.text_w(d, value, fv), y + 4), value, font=fv,
                       fill=(40, 40, 40) if sel else ui.GREY)
        item = self.current
        if isinstance(item, Choice):
            self._help(d, item.help_for(getattr(self.app.s, item.key)))
        else:
            self._help(d, item.help)
        if self.editing is not None:
            ui.hints(d, "A ◀  B ▶", "Y keep · X cancel")
        elif self.page:
            ui.hints(d, "A/B move", "Y select · X back")
        else:
            ui.hints(d, "A/B move · hold: tab", "Y select · X back")
        return img

    def _render_picker(self) -> Image.Image:
        c = self.picker
        img, d = ui.panel_screen(c.label)
        cur = getattr(self.app.s, c.key)
        rows = [(lbl, "●" if v == cur else "") for v, lbl in c.options]
        ui.menu_list(d, rows, self.picker_row, top=30, row_h=24, visible=6)
        self._help(d, c.help_for(c.options[self.picker_row][0]))
        ui.hints(d, "A/B move", "Y choose · X back")
        return img


class QuickMenuView(Screen):
    """Eight tiles over the live view; Y changes the highlighted one in place."""

    wants_preview = True
    COLS, TILE_W, TILE_H, TOP = 4, 80, 50, 120

    def __init__(self, app: "App"):
        super().__init__(app)
        self.index = app.quick_pos
        self.editing = False
        self._orig = None

    @property
    def choice(self) -> Choice:
        return QUICK[self.index]

    def on_button(self, ev: ButtonEvent) -> None:
        app, b, kind = self.app, ev.button, ev.kind
        if self.editing:
            c = self.choice
            if b in "AB":
                app.set_setting(c.key, _step_option(c, getattr(app.s, c.key), -1 if b == "A" else 1))
            elif kind == PRESS and b == "Y":
                self.editing = False
            elif kind == PRESS and b == "X":
                app.set_setting(c.key, self._orig)
                self.editing = False
            return
        if b == "A" and kind == LONG:
            app.open(MenuView(app))
        elif b in "AB" and kind in (PRESS, REPEAT):
            self.index = (self.index + (-1 if b == "A" else 1)) % len(QUICK)
            app.quick_pos = self.index
        elif kind == PRESS and b == "Y":
            self.editing, self._orig = True, getattr(app.s, self.choice.key)
        elif kind == PRESS and b == "X":
            app.back_to_live()

    @staticmethod
    def _fit(d: ImageDraw.ImageDraw, text: str, width: int):
        for size in (14, 13, 12, 11, 10):
            f = ui.font(size, "bold")
            if ui.text_w(d, text, f) <= width:
                return f, text
        f = ui.font(10, "bold")
        while text and ui.text_w(d, text + "…", f) > width:
            text = text[:-1]
        return f, text + "…"

    def render(self) -> Image.Image:
        base = self.app._live.render()
        layer, d = ui.overlay(base)
        d.rectangle([0, self.TOP - 6, ui.W, ui.H], fill=(12, 12, 14, 255))
        for i, c in enumerate(QUICK):
            col, row = i % self.COLS, i // self.COLS
            x0 = col * self.TILE_W + 2
            y0 = self.TOP + row * (self.TILE_H + 2)
            x1, y1 = x0 + self.TILE_W - 4, y0 + self.TILE_H
            sel = i == self.index
            if sel and self.editing:
                d.rounded_rectangle([x0, y0, x1, y1], 5, outline=ui.ACCENT, width=2, fill=(30, 30, 34, 255))
            elif sel:
                d.rounded_rectangle([x0, y0, x1, y1], 5, fill=ui.ACCENT + (255,))
            else:
                d.rounded_rectangle([x0, y0, x1, y1], 5, fill=(36, 36, 42, 255))
            name = c.label.upper().replace("EXPOSURE COMP.", "EV").replace("WHITE BALANCE", "WB") \
                .replace("FOCUS MODE", "FOCUS").replace("ASPECT RATIO", "ASPECT")
            fl = ui.font(9, "bold")
            tc = (0, 0, 0) if sel and not self.editing else ui.GREY
            d.text((x0 + (x1 - x0 - ui.text_w(d, name, fl)) / 2, y0 + 5), name, font=fl, fill=tc)
            value = c.short_label(getattr(self.app.s, c.key))
            inner = (x1 - x0) - (20 if sel and self.editing else 8)
            fv, value = self._fit(d, value, inner)
            vc = (0, 0, 0) if sel and not self.editing else (ui.ACCENT if sel else ui.WHITE)
            d.text((x0 + (x1 - x0 - ui.text_w(d, value, fv)) / 2, y0 + 23), value, font=fv, fill=vc)
            if sel and self.editing:
                fa = ui.font(9, "bold")
                first, last = _at_ends(c, getattr(self.app.s, c.key))
                d.text((x0 + 3, y0 + 26), "◀", font=fa, fill=ui.GREY if first else ui.ACCENT)
                d.text((x1 - 3 - ui.text_w(d, "▶", fa), y0 + 26), "▶", font=fa,
                       fill=ui.GREY if last else ui.ACCENT)
        hint = ("A ◀  B ▶   Y keep   X cancel" if self.editing
                else "A/B move   Y change   X close   hold A: menu")
        ui.centered(d, ui.H - 15, ui.keys(hint), ui.font(10, "bold"), ui.GREY)
        return ui.flatten(base, layer)


# ---------------------------------------------------------------------------
class PlaybackView(Screen):
    """A/B: previous/next. Y: cycle the information shown (basic → details with
    histogram → none); on a video or timelapse with an MP4, Y plays/pauses and
    A/B skip 5 s while it plays. Hold Y: delete. X: stop the video, or back."""

    SKIP_S = 5.0

    INFO_MODES = ("basic", "details", "none")

    def __init__(self, app: "App"):
        super().__init__(app)
        self.entries: list[Entry] = app.storage.entries()
        self.index = 0
        self.info_mode = 0
        self._cache: tuple = (None, None, None)  # (key, image, info)
        self._durations: dict = {}
        self.player = None

    def stop_video(self) -> None:
        if self.player is not None:
            self.player.close()
            self.player = None

    def reload(self) -> None:
        self.stop_video()
        current = self.entries[self.index].key if self.entries else None
        self.entries = self.app.storage.entries()
        keys = [e.key for e in self.entries]
        self.index = keys.index(current) if current in keys else min(self.index, max(0, len(keys) - 1))
        self._cache = (None, None, None)

    def _load(self) -> tuple[Optional[Image.Image], dict]:
        if not self.entries:
            return None, {}
        entry = self.entries[self.index]
        key = entry.path
        if self._cache[0] == key:
            return self._cache[1], self._cache[2]
        try:
            with Image.open(entry.path) as im:
                info = exif.read(im)
                info["size"] = im.size
                im.draft("RGB", (ui.W, ui.H))  # fast reduced-size JPEG decode
                img = ui.fit(im.convert("RGB")).convert("RGB")
            if not entry.is_video:
                info["bytes"] = entry.path.stat().st_size
        except (OSError, ValueError):
            img, info = None, {"error": True}
        self._cache = (key, img, info)
        return img, info

    def _duration(self, entry: Entry) -> Optional[float]:
        if entry.video is None:
            return None
        if entry.video not in self._durations:
            from . import video

            self._durations[entry.video] = video.duration_s(entry.video)
        return self._durations[entry.video]

    def on_button(self, ev: ButtonEvent) -> None:
        player = self.player
        if ev.button in "AB" and player is not None and player.playing:
            player.seek(self.SKIP_S * (-1 if ev.button == "A" else 1))
        elif ev.button in "AB" and self.entries:
            self.stop_video()
            self.index = (self.index + _moves(ev)) % len(self.entries)
        elif ev.button == "X" and ev.kind == PRESS:
            if player is not None:
                self.stop_video()
            else:
                self.app.back_to_live()
        elif ev.button == "Y" and self.entries:
            entry = self.entries[self.index]
            if ev.kind == PRESS and entry.video is not None:
                if player is None:
                    from .video import Player

                    self.player = Player(entry.video, (ui.W, ui.H))
                else:
                    player.toggle()
            elif ev.kind == PRESS:
                self.info_mode = (self.info_mode + 1) % len(self.INFO_MODES)
            elif ev.kind == LONG:
                self.stop_video()
                if entry.frames > 1:
                    what = f"timelapse ({entry.frames} frames)"
                else:
                    what = entry.path.with_suffix(".mp4").name if entry.is_video else entry.path.name
                self.app.open(ConfirmView(self.app, f"Delete {what}?", "Delete",
                                          lambda: self._delete(entry), back=self))

    def _render_video(self) -> Image.Image:
        p = self.player
        frame = p.current()
        entry = self.entries[self.index]
        if frame is None:
            img, _ = self._load()
            frame = img if img is not None else Image.new("RGB", (ui.W, ui.H))
        img = ui.fit(frame)
        layer, d = ui.overlay(img)
        f = ui.font(13, "bold")
        if p.error:
            ui.centered(d, 100, "Can't play this video", ui.font(16, "bold"), ui.RED)
        ui.bar(d, ui.H - 26, ui.H)
        if p.playing:  # pause bars
            d.rectangle([10, ui.H - 20, 13, ui.H - 7], fill=ui.ACCENT)
            d.rectangle([18, ui.H - 20, 21, ui.H - 7], fill=ui.ACCENT)
        else:  # play triangle
            d.polygon([(10, ui.H - 20), (10, ui.H - 7), (22, ui.H - 13)], fill=ui.ACCENT)
        dur = p.duration or self._duration(entry)
        times = f"{self._clock(p.position)} / {self._clock(dur)}"
        tw = ui.text_w(d, times, f)
        d.text((ui.W - 8 - tw, ui.H - 21), times, font=f, fill=ui.WHITE)
        x0, x1, y = 34, ui.W - 16 - tw, ui.H - 13
        d.line([(x0, y), (x1, y)], fill=(90, 90, 96), width=3)
        if dur:
            xp = x0 + int((x1 - x0) * min(1.0, p.position / dur))
            d.line([(x0, y), (xp, y)], fill=ui.ACCENT, width=3)
            d.ellipse([xp - 4, y - 4, xp + 4, y + 4], fill=ui.ACCENT)
        if not p.playing and not p.error:
            hint = "Y play · X stop · A/B next" if not p.ended else "Y replay · X stop"
            ui.centered(d, 4, ui.keys(hint), ui.font(11, "bold"), ui.GREY)
        return ui.flatten(img, layer)

    def _delete(self, entry: Entry) -> None:
        if self.app.clip and entry.video == self.app.clip.path:
            self.app.toast("Still recording", ui.RED)
            self.app.open(self)
            return
        self.app.storage.delete(entry)
        self.app.refresh_storage()
        self.reload()
        self.app.toast("Deleted")
        self.app.open(self)

    @staticmethod
    def _bytes(n: int) -> str:
        return f"{n / 1e6:.1f} MB" if n >= 1e6 else f"{n / 1e3:.0f} kB"

    def render(self) -> Image.Image:
        if self.player is not None and self.entries:
            return self._render_video()
        img, info = self._load()
        if img is None:
            canvas, d = ui.panel_screen("Playback")
            msg = "No photos yet" if not self.entries else "Can't open this file"
            ui.centered(d, 105, msg, ui.font(16), ui.GREY)
            ui.hints(d, "", "X back")
            return canvas
        mode = self.INFO_MODES[self.info_mode]
        if self.app.s.zebras and mode != "none":
            img = ui.flatten(img, ui.zebras(img, int(time.monotonic() * 8) % 8))
        if mode == "none":
            return img
        entry = self.entries[self.index]
        layer, d = ui.overlay(img)
        f, fs = ui.font(13, "bold"), ui.font(12)
        ui.bar(d, 0, 20)
        d.text((6, 3), f"{self.index + 1}/{len(self.entries)}  {entry.title}", font=f, fill=ui.WHITE)
        duration = self._duration(entry)
        if entry.is_video or entry.frames > 1:
            label = "▶ VIDEO" if entry.is_video else (f"TL {entry.frames}" + (" ▶" if entry.video else ""))
            d.text((ui.W - 6 - ui.text_w(d, label, f), 3), label, font=f, fill=ui.ACCENT)
        if mode == "details":
            self._details(d, img, entry, info, duration)
        else:
            ui.bar(d, ui.H - 20, ui.H)
            parts = []
            if entry.is_video:
                parts.append((f"Video {self._clock(duration)}" if duration else "Video") + ui.keys(" · Y play"))
            elif entry.frames > 1:
                parts.append(f"Timelapse · {entry.frames} frames" + (ui.keys(" · Y play") if entry.video else ""))
            if not entry.is_video:
                if "exposure_s" in info:
                    parts.append(format_shutter(info["exposure_s"]))
                if "iso" in info:
                    parts.append(f"ISO {info['iso']}")
            if "datetime" in info:
                parts.append(info["datetime"].strftime("%H:%M:%S"))
            d.text((6, ui.H - 17), "  ".join(parts), font=fs, fill=ui.WHITE)
        return ui.flatten(img, layer)

    @staticmethod
    def _clock(seconds: Optional[float]) -> str:
        if not seconds:
            return "--:--"
        s = int(round(seconds))
        return f"{s // 60}:{s % 60:02d}"

    def _details(self, d: ImageDraw.ImageDraw, img: Image.Image, entry: Entry, info: dict,
                 duration: Optional[float]) -> None:
        d.rectangle([0, 20, 150, ui.H], fill=(0, 0, 0, 170))
        lines = []
        if "datetime" in info:
            lines.append(info["datetime"].strftime("%Y-%m-%d %H:%M:%S"))
        if entry.is_video:
            lines.append(f"Length {self._clock(duration)}")
            try:
                lines.append(self._bytes(entry.video.stat().st_size))
            except (OSError, AttributeError):
                pass
        else:
            if "exposure_s" in info:
                lines.append(f"Shutter {format_shutter(info['exposure_s'])}")
            if "iso" in info:
                lines.append(f"ISO {info['iso']}")
            if "ev" in info:
                lines.append(f"EV {format_ev(info['ev'])}")
            if "size" in info:
                w, h = info["size"]
                lines.append(f"{w}×{h} ({w * h / 1e6:.1f} MP)")
            if "bytes" in info:
                lines.append(self._bytes(info["bytes"]))
            if any(p.suffix.lower() == ".dng" for p in entry.delete_paths):
                lines.append("+ RAW (DNG)")
        if entry.frames > 1:
            lines.append(f"{entry.frames} frames" + (" + MP4" if entry.video else ""))
        fs = ui.font(12)
        for i, line in enumerate(lines[:9]):
            d.text((8, 28 + i * 19), line, font=fs, fill=ui.WHITE)
        ui.histogram(d, img, box=(ui.W - 104, 26, ui.W - 6, 80))


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
        top, bottom = 34, ui.H - 20
        # Use the largest font whose wrapped lines fit between title and hints.
        for size, step in ((14, 21), (13, 19), (12, 17), (11, 15), (10, 13)):
            f = ui.font(size)
            parts = [p for line in self.lines for p in (ui.wrap(d, line, f, ui.W - 24) or [""])]
            if top + len(parts) * step <= bottom:
                break
        y = top
        for part in parts:
            d.text((12, y), part, font=f, fill=ui.WHITE)
            y += step
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
        ui.centered(d, 210, ui.keys("Hold Y for 3 s to turn off"), ui.font(11), ui.GREY)
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
        for line in ui.wrap(d, ui.keys(body), f, ui.W - 30):
            ui.centered(d, y, line, f)
            y += 20
        ui.centered(d, ui.H - 46, "No photos can be taken during export.", ui.font(12), ui.GREY)
        ui.hints(d, "", "X stop export")
        return img


class DateTimeView(Screen):
    """Set the clock: Y moves through year, month, day, hour, minute; A/B change
    the highlighted field (hold to repeat); Y on the last field saves, X cancels."""

    FIELDS = ("year", "month", "day", "hour", "minute")
    NAMES = ("Year", "Month", "Day", "Hour", "Minute")

    def __init__(self, app: "App", back: Optional[Screen] = None):
        super().__init__(app)
        from datetime import datetime

        now = datetime.now()
        self.values = {"year": max(now.year, 2024), "month": now.month, "day": now.day,
                       "hour": now.hour, "minute": now.minute}
        self.field = 0
        self.back = back
        self.synced = app.system.time_synced()

    @staticmethod
    def _days_in(year: int, month: int) -> int:
        import calendar

        return calendar.monthrange(year, month)[1]

    def _bounds(self, name: str) -> tuple[int, int]:
        v = self.values
        return {"year": (2024, 2099), "month": (1, 12),
                "day": (1, self._days_in(v["year"], v["month"])),
                "hour": (0, 23), "minute": (0, 59)}[name]

    def _step(self, d: int) -> None:
        name = self.FIELDS[self.field]
        lo, hi = self._bounds(name)
        v = self.values[name] + d
        self.values[name] = hi if v < lo else lo if v > hi else v  # wraps like a clock
        self.values["day"] = min(self.values["day"], self._bounds("day")[1])  # 31 Jan -> Feb

    def selected_datetime(self):
        from datetime import datetime

        return datetime(**self.values)

    def _leave(self) -> None:
        if self.back:
            self.app.open(self.back)
        else:
            self.app.back_to_live()

    def on_button(self, ev: ButtonEvent) -> None:
        b, kind = ev.button, ev.kind
        if b in "AB" and kind in (PRESS, LONG, REPEAT):
            self._step(-1 if b == "A" else 1)
        elif kind == PRESS and b == "Y":
            if self.field < len(self.FIELDS) - 1:
                self.field += 1
            else:
                self.app.set_clock(self.selected_datetime())
                self._leave()
        elif kind == PRESS and b == "X":
            self._leave()

    def render(self) -> Image.Image:
        img, d = ui.panel_screen("Date & time")
        v = self.values
        parts = [(f"{v['year']:04d}", 0), ("-", None), (f"{v['month']:02d}", 1), ("-", None),
                 (f"{v['day']:02d}", 2), ("  ", None), (f"{v['hour']:02d}", 3), (":", None),
                 (f"{v['minute']:02d}", 4)]
        f = ui.font(28, "bold")
        total = sum(ui.text_w(d, t, f) for t, _ in parts)
        x, y = (ui.W - total) // 2, 66
        for text, idx in parts:
            w = ui.text_w(d, text, f)
            if idx == self.field:
                d.rounded_rectangle([x - 3, y - 2, x + w + 3, y + 34], 4, fill=ui.ACCENT)
                d.text((x, y), text, font=f, fill=(0, 0, 0))
            else:
                d.text((x, y), text, font=f, fill=ui.WHITE if idx is not None else ui.GREY)
            x += w
        ui.centered(d, 114, f"Setting: {self.NAMES[self.field]}", ui.font(13), ui.ACCENT)
        if self.synced:
            note, col = "Clock is synced from the network", ui.GREEN
        else:
            note, col = "No network time - set the clock here", ui.GREY
        ui.centered(d, 148, note, ui.font(12), col)
        ui.centered(d, 168, "Seconds are set to 00", ui.font(11), ui.GREY)
        last = self.field == len(self.FIELDS) - 1
        ui.hints(d, "A −  B +", "Y save · X cancel" if last else "Y next · X cancel")
        return img


class ConnectionView(Screen):
    """Wi-Fi status and QR codes for the phone page (A/B switch the code)."""

    def __init__(self, app: "App", back: Optional[Screen] = None):
        super().__init__(app)
        self.back = back
        self.page = 0

    def _codes(self) -> list[tuple[str, str]]:
        """(caption, QR text) pages for the current state."""
        app, st = self.app, self.app.network.status
        out = []
        if st.hotspot:
            pw = app.s.hotspot_password
            out.append((f"1. Join {st.ssid}", f"WIFI:T:WPA;S:{st.ssid};P:{pw};;"))
        if app.web_url:
            out.append(("2. Open the camera page" if st.hotspot else "Open on your phone", app.web_url))
        return out

    def on_button(self, ev: ButtonEvent) -> None:
        n = max(1, len(self._codes()))
        if ev.button in "AB" and ev.kind == PRESS:
            self.page = (self.page + (1 if ev.button == "B" else -1)) % n
        elif ev.kind == PRESS and ev.button in "XY":
            if self.back:
                self.app.open(self.back)
            else:
                self.app.back_to_live()

    def render(self) -> Image.Image:
        app = self.app
        st = app.network.status
        img, d = ui.panel_screen("Wi-Fi & Remote")
        f, fb = ui.font(12), ui.font(13, "bold")
        lines = [(st.summary, fb, ui.GREEN if st.connected else ui.ACCENT if st.busy else ui.GREY)]
        if st.hotspot:
            lines.append((f"Password: {app.s.hotspot_password}", f, ui.WHITE))
        if st.ip:
            lines.append((f"Address: {st.ip}", f, ui.WHITE))
        if not app.s.web:
            lines.append(("Phone access is off", fb, ui.RED))
            lines.append(("Turn it on in this menu", f, ui.GREY))
        elif app.web_url:
            lines.append((app.web_url, fb, ui.ACCENT))
        else:
            lines.append(("Waiting for the network…", f, ui.GREY))
        codes = self._codes()
        text_w = ui.W - 12 if not codes else 164
        y = 32
        for text, font_, col in lines:
            for part in ui.wrap(d, text, font_, text_w):
                d.text((10, y), part, font=font_, fill=col)
                y += 18
        if codes:
            self.page %= len(codes)
            caption, data = codes[self.page]
            if ui.qr(d, data, ui.W - 146, 30, 140):
                for i, part in enumerate(ui.wrap(d, caption, ui.font(11), 140)[:2]):
                    tw = ui.text_w(d, part, ui.font(11))
                    d.text((ui.W - 76 - tw // 2, 174 + i * 14), part, font=ui.font(11), fill=ui.GREY)
            else:
                d.text((10, y + 6), "Install python3-segno for QR codes", font=f, fill=ui.GREY)
        ui.hints(d, "A/B next code" if len(codes) > 1 else "", "X back")
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
