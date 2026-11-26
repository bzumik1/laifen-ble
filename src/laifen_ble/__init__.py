from __future__ import annotations

from .const import (
    BRUSHING_TIME_STEP,
    LOCAL_NAME_PREFIX,
    MANUFACTURER_ID,
    MAX_BRUSHING_TIME,
    MAX_SETTING,
    MIN_BRUSHING_TIME,
    MIN_SETTING,
    SERVICE_UUID,
)
from .device import LaifenToothbrush
from .exceptions import (
    InvalidFrameError,
    LaifenCommandError,
    LaifenConnectionError,
    LaifenError,
    LaifenMotorError,
    UnsupportedStateError,
)
from .models import BrushingSession, LaifenState, Mode, ModeSettings, MotorStatus


def is_supported(local_name: str | None) -> bool:
    return local_name is not None and local_name.startswith(LOCAL_NAME_PREFIX)


__all__ = [
    "BRUSHING_TIME_STEP",
    "LOCAL_NAME_PREFIX",
    "MANUFACTURER_ID",
    "MAX_BRUSHING_TIME",
    "MAX_SETTING",
    "MIN_BRUSHING_TIME",
    "MIN_SETTING",
    "SERVICE_UUID",
    "BrushingSession",
    "InvalidFrameError",
    "LaifenCommandError",
    "LaifenConnectionError",
    "LaifenError",
    "LaifenMotorError",
    "LaifenState",
    "LaifenToothbrush",
    "Mode",
    "ModeSettings",
    "MotorStatus",
    "UnsupportedStateError",
    "is_supported",
]
