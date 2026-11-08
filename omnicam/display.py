"""Display + button + LED backends: Pimoroni Display HAT Mini, or a pygame window for --sim."""

from __future__ import annotations

from typing import Optional

from PIL import Image

WIDTH, HEIGHT = 320, 240


def rgb565(img: Image.Image, rotate180: bool = True):
    """RGB image -> (rows x cols) big-endian RGB565 array, as the ST7789 wants it."""
    import numpy as np

    a = np.asarray(img.convert("RGB") if img.mode != "RGB" else img)
    if rotate180:  # the HAT's panel is mounted upside down
        a = a[::-1, ::-1]
    r = a[..., 0].astype(np.uint16)
    g = a[..., 1].astype(np.uint16)
    c = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (a[..., 2] >> 3)
    return c.astype(">u2")


def changed_rows(new, old) -> Optional[tuple[int, int]]:
    """(first, last) row that differs, or None if the frames are the same."""
    import numpy as np

    if old is None or old.shape != new.shape:
        return 0, new.shape[0] - 1
    rows = np.flatnonzero((new != old).any(axis=1))
    if rows.size == 0:
        return None
    return int(rows[0]), int(rows[-1])


class HatDisplay:
    """Pimoroni Display HAT Mini via the `displayhatmini` library.

    Needs SPI0 with both chip selects and MISO released, i.e. in config.txt:
        dtparam=spi=on
        dtoverlay=spi0-2cs,no_miso
    The HAT uses GPIO9 (SPI MISO) as its D/C line; newer kernels won't hand
    that pin to user space while SPI owns it, hence `no_miso`.

    Frames are sent by a background thread (the SPI write doesn't hold the
    GIL), so the next frame is prepared while this one is on its way. Only the
    rows that changed since the last frame are sent, in one SPI write.
    """

    def __init__(self):
        import threading

        from displayhatmini import DisplayHATMini  # only exists on the Pi

        self._buffer = Image.new("RGB", (WIDTH, HEIGHT))
        self._hat = DisplayHATMini(self._buffer, backlight_pwm=True)
        self._pins = {
            "A": DisplayHATMini.BUTTON_A, "B": DisplayHATMini.BUTTON_B,
            "X": DisplayHATMini.BUTTON_X, "Y": DisplayHATMini.BUTTON_Y,
        }
        self._last = None  # RGB565 array of the frame on the panel
        self._job = None  # (array, first row, last row) waiting to be sent
        self._cv = threading.Condition()
        self._closing = False
        self.timings: dict = {}
        self._thread = threading.Thread(target=self._sender, daemon=True, name="display")
        self._thread.start()
        self.set_led(0, 0, 0)

    def show(self, img: Image.Image) -> None:
        import time

        t0 = time.perf_counter()
        data = rgb565(img)
        rows = changed_rows(data, self._last)
        t1 = time.perf_counter()
        with self._cv:
            while self._job is not None and not self._closing:  # previous frame still going
                self._cv.wait(0.5)
            t2 = time.perf_counter()
            if rows is not None:
                self._job = (data, *rows)
                self._last = data
                self._cv.notify_all()
        self.timings = {"convert": t1 - t0, "wait": t2 - t1}

    def _sender(self) -> None:
        st = self._hat.st7789
        while True:
            with self._cv:
                while self._job is None and not self._closing:
                    self._cv.wait()
                if self._closing:
                    return
                data, y0, y1 = self._job
            try:
                st.set_window(0, y0, WIDTH - 1, y1)
                st.set_pin(st._dc, True)
                st._spi.writebytes2(data[y0:y1 + 1].tobytes())
            except Exception:
                self._last = None  # resend everything next time
            with self._cv:
                self._job = None
                self._cv.notify_all()

    def pressed(self) -> set[str]:
        return {name for name, pin in self._pins.items() if self._hat.read_button(pin)}

    def set_led(self, r: float, g: float, b: float) -> None:
        clamp = lambda v: min(1.0, max(0.0, float(v)))  # the library raises outside 0..1
        self._hat.set_led(clamp(r), clamp(g), clamp(b))

    def set_backlight(self, level: float) -> None:
        self._hat.set_backlight(min(1.0, max(0.0, float(level))))

    def poll_quit(self) -> bool:
        return False

    def close(self) -> None:
        with self._cv:  # let the last frame finish, then stop the sender
            while self._job is not None:
                if not self._cv.wait(1.0):
                    break
            self._closing = True
            self._cv.notify_all()
        self._thread.join(2)
        self.set_led(0, 0, 0)
        self.set_backlight(0)
        # Release the PWM objects while the GPIO chip is still open. Otherwise
        # displayhatmini's GPIO.cleanup() runs first and each PWM.__del__ then
        # fails on the closed chip (rpi-lgpio), spamming tracebacks at exit.
        for attr in ("led_r_pwm", "led_g_pwm", "led_b_pwm", "backlight_pwm"):
            pwm = getattr(self._hat, attr, None)
            if pwm is not None:
                try:
                    pwm.stop()
                except Exception:
                    pass
                setattr(self._hat, attr, None)
            del pwm
        try:
            import RPi.GPIO as GPIO

            GPIO.cleanup()
        except Exception:
            pass


class SimDisplay:
    """pygame window at 2x scale. Keys a/b/x/y are the buttons, Esc quits."""

    SCALE = 2

    def __init__(self):
        import pygame

        self._pg = pygame
        pygame.init()
        self._screen = pygame.display.set_mode((WIDTH * self.SCALE, HEIGHT * self.SCALE + 24))
        pygame.display.set_caption("omnicam (sim) — a/b/x/y buttons, hold for long press, Esc quits")
        self._keys = {"A": pygame.K_a, "B": pygame.K_b, "X": pygame.K_x, "Y": pygame.K_y}
        self._led = (0, 0, 0)
        self._backlight = 1.0
        self._quit = False
        self._font = pygame.font.SysFont(None, 20)

    def show(self, img: Image.Image) -> None:
        pg = self._pg
        img = img.resize((WIDTH * self.SCALE, HEIGHT * self.SCALE), Image.NEAREST)
        if self._backlight < 1:
            img = img.point(lambda v: int(v * self._backlight))
        surf = pg.image.fromstring(img.tobytes(), img.size, "RGB")
        self._screen.fill((30, 30, 30))
        self._screen.blit(surf, (0, 0))
        y = HEIGHT * self.SCALE + 4
        pg.draw.circle(self._screen, tuple(int(c * 255) for c in self._led), (12, y + 8), 7)
        self._screen.blit(self._font.render("LED   A/B left · X/Y right", True, (200, 200, 200)), (26, y))
        pg.display.flip()

    def pressed(self) -> set[str]:
        self.poll_quit()
        keys = self._pg.key.get_pressed()
        return {name for name, k in self._keys.items() if keys[k]}

    def set_led(self, r: float, g: float, b: float) -> None:
        self._led = (r, g, b)

    def set_backlight(self, level: float) -> None:
        self._backlight = level
        if level == 0:
            self.show(Image.new("RGB", (WIDTH, HEIGHT)))

    def poll_quit(self) -> bool:
        for ev in self._pg.event.get():
            if ev.type == self._pg.QUIT or (ev.type == self._pg.KEYDOWN and ev.key == self._pg.K_ESCAPE):
                self._quit = True
        return self._quit

    def close(self) -> None:
        self._pg.quit()
