from __future__ import annotations

from .const import (
    BRUSHING_TIME_STEP,
    LOCAL_NAME_PREFIX,
    MAX_BRUSHING_TIME,
    MAX_SETTING,
    MIN_BRUSHING_TIME,
    MIN_SETTING,
)
from .device import LaifenToothbrush
from .exceptions import (
    InvalidFrameError,
    LaifenCommandError,
    LaifenConnectionError,
    LaifenError,
    LaifenMotorError,
    LaifenValueError,
    UnsupportedStateError,
)
from .models import BrushingSession, LaifenState, Mode, ModeSettings, MotorStatus


def is_supported(local_name: str | None) -> bool:
    return local_name is not None and local_name.startswith(LOCAL_NAME_PREFIX)


__all__ = [
    "BRUSHING_TIME_STEP",
    "LOCAL_NAME_PREFIX",
    "MAX_BRUSHING_TIME",
    "MAX_SETTING",
    "MIN_BRUSHING_TIME",
    "MIN_SETTING",
    "BrushingSession",
    "InvalidFrameError",
    "LaifenCommandError",
    "LaifenConnectionError",
    "LaifenError",
    "LaifenMotorError",
    "LaifenState",
    "LaifenToothbrush",
    "LaifenValueError",
    "Mode",
    "ModeSettings",
    "MotorStatus",
    "UnsupportedStateError",
    "is_supported",
]
