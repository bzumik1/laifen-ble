from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum

from .const import (
    MAX_HIGH_FREQUENCY_STRENGTH,
    MAX_SETTING,
    MIN_HIGH_FREQUENCY_STRENGTH,
    MIN_SETTING,
)


class Mode(IntEnum):
    MODE_1 = 0
    MODE_2 = 1
    MODE_3 = 2
    HIGH_FREQUENCY = 3

    @property
    def strength_limits(self) -> tuple[int, int]:
        if self is Mode.HIGH_FREQUENCY:
            return MIN_HIGH_FREQUENCY_STRENGTH, MAX_HIGH_FREQUENCY_STRENGTH
        return MIN_SETTING, MAX_SETTING


class MotorStatus(IntEnum):
    CHARGING = 0x01
    MALFUNCTION = 0x02
    LOW_BATTERY = 0x03


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


@dataclass(frozen=True, slots=True)
class BrushingSession:
    started_at: datetime
    duration: int
