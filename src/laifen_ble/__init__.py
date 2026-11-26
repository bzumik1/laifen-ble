from __future__ import annotations

from .const import LOCAL_NAME_PREFIX, MANUFACTURER_ID, SERVICE_UUID
from .device import LaifenToothbrush
from .exceptions import (
    InvalidFrameError,
    LaifenConnectionError,
    LaifenError,
    UnsupportedStateError,
)
from .models import LaifenState, Mode, ModeSettings


def is_supported(local_name: str | None) -> bool:
    return local_name is not None and local_name.startswith(LOCAL_NAME_PREFIX)


__all__ = [
    "LOCAL_NAME_PREFIX",
    "MANUFACTURER_ID",
    "SERVICE_UUID",
    "InvalidFrameError",
    "LaifenConnectionError",
    "LaifenError",
    "LaifenState",
    "LaifenToothbrush",
    "Mode",
    "ModeSettings",
    "UnsupportedStateError",
    "is_supported",
]
