from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from .exceptions import InvalidFrameError, UnsupportedStateError
from .models import LaifenState, Mode, ModeSettings

HEADER = 0xAA
MINIMUM_FRAME_LENGTH = 5
SUPPORTED_STATE_LENGTHS = (20, 21, 23)
VERSION_LENGTH = 9
VERSION_LETTER_INDEX = 3


class Command(IntEnum):
    VERSION = 0x01
    STATE = 0x0A


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
        raise InvalidFrameError(f"Unknown mode {payload[0]}") from err
    return LaifenState(
        mode=mode,
        mode_settings={
            Mode.MODE_1: _mode_settings(payload, 1),
            Mode.MODE_2: _mode_settings(payload, 4),
            Mode.MODE_3: _mode_settings(payload, 7),
            Mode.HIGH_FREQUENCY: _mode_settings(payload, 10),
        },
        airplane_mode=_flag(payload[13]),
        battery_level=payload[14],
        reminder_30s=_flag(payload[15]),
        brushing_time=int.from_bytes(payload[16:18], "big"),
        high_frequency=_flag(payload[18]),
        running=_flag(payload[19]),
    )


def _signed(value: int) -> int:
    return value - 0x100 if value > 0x7F else value


def decode_version(payload: bytes) -> str | None:
    if len(payload) != VERSION_LENGTH:
        return None
    parts = [
        chr(byte) if index == VERSION_LETTER_INDEX else str(_signed(byte))
        for index, byte in enumerate(payload)
    ]
    return (
        f"V{parts[0]}.{parts[1]}.{parts[2]}."
        f"{parts[3]}{parts[4]}{parts[5]}.{parts[6]}{parts[7]}{parts[8]}"
    )


READ_STATE = encode_frame(Command.STATE, Kind.READ)
READ_VERSION = encode_frame(Command.VERSION, Kind.READ)
