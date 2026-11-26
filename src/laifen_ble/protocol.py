from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from types import MappingProxyType

from .const import (
    BRUSHING_TIME_STEP,
    MAX_BRUSHING_TIME,
    MAX_SETTING,
    MIN_BRUSHING_TIME,
    MIN_SETTING,
)
from .exceptions import InvalidFrameError, LaifenValueError, UnsupportedStateError
from .models import LaifenState, Mode, ModeSettings

HEADER = 0xAA
MINIMUM_FRAME_LENGTH = 5
SUPPORTED_STATE_LENGTHS = (20, 21, 23)
ACCEPTED = 0x00
MAX_BATTERY_LEVEL = 100
FIRST_PRINTABLE_CHARACTER = 0x21
LAST_PRINTABLE_CHARACTER = 0x7E


@dataclass(frozen=True, slots=True)
class VersionLayout:
    letter_indexes: frozenset[int]
    group_sizes: tuple[int, ...]


VERSION_LAYOUTS = {
    9: VersionLayout(frozenset({3}), (1, 1, 1, 3, 3)),
    19: VersionLayout(frozenset({4, 5, 6, 7, 10, 13}), (1, 1, 2, 3, 3, 3, 3, 3)),
}


class Command(IntEnum):
    VERSION = 0x01
    MODE_SETTINGS = 0x04
    BRUSHING_TIME = 0x05
    STATE = 0x0A
    REMINDER_30S = 0x0B
    HIGH_FREQUENCY = 0x0E
    MOTOR = 0x0F


class Kind(IntEnum):
    READ = 0x00
    WRITE = 0x01
    REPORT = 0x02


@dataclass(frozen=True, slots=True)
class Frame:
    command: int
    kind: int
    payload: bytes


def checksum(data: bytes) -> int:
    result = 0
    for byte in data:
        result ^= byte
    return result


def encode_frame(command: Command, kind: Kind, payload: bytes = b"") -> bytes:
    body = bytes((HEADER, command, kind, len(payload))) + payload
    return body + bytes((checksum(body),))


def decode_frame(data: bytes) -> Frame:
    if len(data) < MINIMUM_FRAME_LENGTH:
        raise InvalidFrameError(f"Frame too short: {data.hex()}")
    if data[0] != HEADER:
        raise InvalidFrameError(f"Unexpected header: {data.hex()}")
    if len(data) != MINIMUM_FRAME_LENGTH + data[3]:
        raise InvalidFrameError(f"Length mismatch: {data.hex()}")
    if checksum(data[:-1]) != data[-1]:
        raise InvalidFrameError(f"Checksum mismatch: {data.hex()}")
    return Frame(command=data[1], kind=data[2], payload=bytes(data[4:-1]))


def _flag(value: int) -> bool:
    return value == 0x01


def _mode_settings(payload: bytes, offset: int) -> ModeSettings:
    return ModeSettings(
        strength=payload[offset],
        range=payload[offset + 1],
        speed=payload[offset + 2],
    )


def decode_state(payload: bytes) -> LaifenState:
    if len(payload) not in SUPPORTED_STATE_LENGTHS:
        raise UnsupportedStateError(f"Unsupported state length {len(payload)}")
    try:
        mode = Mode(payload[0])
    except ValueError as err:
        raise UnsupportedStateError(f"Unknown mode {payload[0]}") from err
    if payload[14] > MAX_BATTERY_LEVEL:
        raise UnsupportedStateError(f"Battery level {payload[14]} out of range")
    return LaifenState(
        mode=mode,
        mode_settings=MappingProxyType(
            {
                Mode.MODE_1: _mode_settings(payload, 1),
                Mode.MODE_2: _mode_settings(payload, 4),
                Mode.MODE_3: _mode_settings(payload, 7),
                Mode.HIGH_FREQUENCY: _mode_settings(payload, 10),
            }
        ),
        airplane_mode=_flag(payload[13]),
        battery_level=payload[14],
        reminder_30s=_flag(payload[15]),
        brushing_time=int.from_bytes(payload[16:18], "big"),
        high_frequency=_flag(payload[18]),
        running=_flag(payload[19]),
    )


def decode_acknowledgement(payload: bytes) -> int:
    if len(payload) != 1:
        raise InvalidFrameError(f"Unexpected acknowledgement: {payload.hex()}")
    return payload[0]


def _is_printable_letter(value: int) -> bool:
    return FIRST_PRINTABLE_CHARACTER <= value <= LAST_PRINTABLE_CHARACTER


def _signed(value: int) -> int:
    return value - 0x100 if value > 0x7F else value


def decode_version(payload: bytes) -> str | None:
    layout = VERSION_LAYOUTS.get(len(payload))
    if layout is None:
        return None
    if not all(_is_printable_letter(payload[index]) for index in layout.letter_indexes):
        return None
    parts = [
        chr(byte) if index in layout.letter_indexes else str(_signed(byte))
        for index, byte in enumerate(payload)
    ]
    groups = []
    start = 0
    for size in layout.group_sizes:
        groups.append("".join(parts[start : start + size]))
        start += size
    return "V" + ".".join(groups)


def _check_limits(name: str, value: int, minimum: int, maximum: int) -> None:
    if not minimum <= value <= maximum:
        raise LaifenValueError(f"{name} must be between {minimum} and {maximum}")


def encode_mode_settings(mode: Mode, settings: ModeSettings) -> bytes:
    _check_limits("Strength", settings.strength, *mode.strength_limits)
    _check_limits("Range", settings.range, MIN_SETTING, MAX_SETTING)
    _check_limits("Speed", settings.speed, MIN_SETTING, MAX_SETTING)
    return encode_frame(
        Command.MODE_SETTINGS,
        Kind.WRITE,
        bytes((mode, settings.strength, settings.range, settings.speed)),
    )


def encode_brushing_time(seconds: int) -> bytes:
    _check_limits("Brushing time", seconds, MIN_BRUSHING_TIME, MAX_BRUSHING_TIME)
    if seconds % BRUSHING_TIME_STEP:
        raise LaifenValueError(
            f"Brushing time must be a multiple of {BRUSHING_TIME_STEP}"
        )
    return encode_frame(Command.BRUSHING_TIME, Kind.WRITE, seconds.to_bytes(2, "big"))


def encode_switch(command: Command, enabled: bool) -> bytes:
    return encode_frame(command, Kind.WRITE, bytes((0x01 if enabled else 0x00,)))


READ_STATE = encode_frame(Command.STATE, Kind.READ)
READ_VERSION = encode_frame(Command.VERSION, Kind.READ)
