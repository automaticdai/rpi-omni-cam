"""Display + button + LED backends: Pimoroni Display HAT Mini, or a pygame window for --sim."""

from __future__ import annotations

from PIL import Image

WIDTH, HEIGHT = 320, 240


class HatDisplay:
    """Pimoroni Display HAT Mini via the `displayhatmini` library.

    Needs SPI0 with both chip selects and MISO released, i.e. in config.txt:
        dtparam=spi=on
        dtoverlay=spi0-2cs,no_miso
    The HAT uses GPIO9 (SPI MISO) as its D/C line; newer kernels won't hand
    that pin to user space while SPI owns it, hence `no_miso`.
    """

    def __init__(self):
        from displayhatmini import DisplayHATMini  # only exists on the Pi

        self._buffer = Image.new("RGB", (WIDTH, HEIGHT))
        self._hat = DisplayHATMini(self._buffer, backlight_pwm=True)
        self._pins = {
            "A": DisplayHATMini.BUTTON_A, "B": DisplayHATMini.BUTTON_B,
            "X": DisplayHATMini.BUTTON_X, "Y": DisplayHATMini.BUTTON_Y,
        }
        self.set_led(0, 0, 0)

    def show(self, img: Image.Image) -> None:
        self._buffer.paste(img)
        self._hat.display()

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
