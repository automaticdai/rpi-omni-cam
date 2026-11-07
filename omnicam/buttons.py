"""Turn raw button states into short / long / repeat events."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

BUTTONS = ("A", "B", "X", "Y")

PRESS = "press"    # short press, fired on release
LONG = "long"      # fired once when held past LONG_PRESS_S
REPEAT = "repeat"  # fired repeatedly while still held after LONG
POWER = "power"    # fired once when a power button is held past POWER_HOLD_S

LONG_PRESS_S = 0.7
REPEAT_S = 0.15
POWER_HOLD_S = 3.0
POWER_BUTTONS = ("Y",)


@dataclass(frozen=True)
class ButtonEvent:
    button: str
    kind: str


@dataclass
class _State:
    down_since: float | None = None
    long_fired: bool = False
    power_fired: bool = False
    last_repeat: float = 0.0


@dataclass
class ButtonTracker:
    long_press_s: float = LONG_PRESS_S
    repeat_s: float = REPEAT_S
    power_hold_s: float = POWER_HOLD_S
    _states: dict = field(default_factory=lambda: {b: _State() for b in BUTTONS})

    def update(self, pressed: Iterable[str], now: float) -> list[ButtonEvent]:
        pressed = set(pressed)
        events: list[ButtonEvent] = []
        for name, st in self._states.items():
            if name in pressed:
                if st.down_since is None:
                    st.down_since = now
                    st.long_fired = st.power_fired = False
                    continue
                if (name in POWER_BUTTONS and not st.power_fired
                        and now - st.down_since >= self.power_hold_s):
                    st.power_fired = True
                    events.append(ButtonEvent(name, POWER))
                if not st.long_fired and now - st.down_since >= self.long_press_s:
                    st.long_fired = True
                    st.last_repeat = now
                    events.append(ButtonEvent(name, LONG))
                elif st.long_fired and now - st.last_repeat >= self.repeat_s:
                    st.last_repeat = now
                    events.append(ButtonEvent(name, REPEAT))
            elif st.down_since is not None:
                if not st.long_fired:
                    events.append(ButtonEvent(name, PRESS))
                st.down_since = None
                st.long_fired = False
        return events

    def any_down(self) -> bool:
        return any(st.down_since is not None for st in self._states.values())
