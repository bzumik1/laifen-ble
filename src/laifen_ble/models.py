from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


class Mode(IntEnum):
    MODE_1 = 0
    MODE_2 = 1
    MODE_3 = 2
    HIGH_FREQUENCY = 3


@dataclass(frozen=True, slots=True)
class ModeSettings:
    strength: int
    range: int
    speed: int


@dataclass(frozen=True, slots=True)
class LaifenState:
    mode: Mode
    mode_settings: dict[Mode, ModeSettings]
    airplane_mode: bool
    battery_level: int
    reminder_30s: bool
    brushing_time: int
    high_frequency: bool
    running: bool

    @property
    def active_mode_settings(self) -> ModeSettings:
        return self.mode_settings[self.mode]
