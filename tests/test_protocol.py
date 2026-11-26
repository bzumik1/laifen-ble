from __future__ import annotations

import pytest

from laifen_ble import InvalidFrameError, Mode, ModeSettings, UnsupportedStateError
from laifen_ble.protocol import (
    READ_STATE,
    READ_VERSION,
    Command,
    Kind,
    checksum,
    decode_frame,
    decode_state,
    decode_version,
    encode_frame,
)

STATE_FRAME = bytes.fromhex("aa0a021400010a070a0a0a040a0a140101005d010078010085")
VERSION_FRAME = bytes.fromhex("aa010209020008590404030003f3")
ACK_FRAME = bytes.fromhex("aa05020100ac")
STATE_21_FRAME = bytes.fromhex("aa0a0215020205050406060a0a0a140a0a004f00012c000000cf")
STATE_21_RUNNING_FRAME = bytes.fromhex(
    "aa0a0215020205050406060a0a0a140a0a004f00012c000100ce"
)
VERSION_19_FRAME = bytes.fromhex("aa01021302000903585858540001420000590404030003a4")


def test_checksum() -> None:
    assert checksum(bytes.fromhex("aa0a0000")) == 0xA0


def test_read_commands() -> None:
    assert bytes.fromhex("aa0a0000a0") == READ_STATE
    assert bytes.fromhex("aa010000ab") == READ_VERSION


def test_encode_frame_with_payload() -> None:
    assert encode_frame(Command.STATE, Kind.WRITE, b"\x01") == bytes.fromhex(
        "aa0a010101a1"
    )


def test_decode_frame() -> None:
    frame = decode_frame(ACK_FRAME)
    assert frame.command == 0x05
    assert frame.kind == Kind.REPORT
    assert frame.payload == b"\x00"


@pytest.mark.parametrize(
    "data",
    [
        bytes.fromhex("aa0a02"),
        bytes.fromhex("bb05020100ac"),
        bytes.fromhex("aa05020200ac"),
        bytes.fromhex("aa05020100ad"),
    ],
    ids=["too_short", "bad_header", "bad_length", "bad_checksum"],
)
def test_decode_frame_invalid(data: bytes) -> None:
    with pytest.raises(InvalidFrameError):
        decode_frame(data)


def test_decode_state() -> None:
    state = decode_state(decode_frame(STATE_FRAME).payload)
    assert state.mode is Mode.MODE_1
    assert state.mode_settings == {
        Mode.MODE_1: ModeSettings(strength=1, range=10, speed=7),
        Mode.MODE_2: ModeSettings(strength=10, range=10, speed=10),
        Mode.MODE_3: ModeSettings(strength=4, range=10, speed=10),
        Mode.HIGH_FREQUENCY: ModeSettings(strength=20, range=1, speed=1),
    }
    assert state.active_mode_settings == ModeSettings(strength=1, range=10, speed=7)
    assert state.airplane_mode is False
    assert state.battery_level == 93
    assert state.reminder_30s is True
    assert state.brushing_time == 120
    assert state.high_frequency is True
    assert state.running is False


def test_decode_state_running_in_high_frequency() -> None:
    payload = bytearray(decode_frame(STATE_FRAME).payload)
    payload[0] = Mode.HIGH_FREQUENCY
    payload[19] = 0x01
    state = decode_state(bytes(payload))
    assert state.mode is Mode.HIGH_FREQUENCY
    assert state.running is True
    assert state.active_mode_settings == ModeSettings(strength=20, range=1, speed=1)


@pytest.mark.parametrize("length", [21, 23])
def test_decode_state_longer_layouts(length: int) -> None:
    payload = decode_frame(STATE_FRAME).payload + bytes(length - 20)
    assert decode_state(payload).battery_level == 93


def test_decode_state_flag_requires_exact_one() -> None:
    payload = bytearray(decode_frame(STATE_FRAME).payload)
    payload[19] = 0x02
    assert decode_state(bytes(payload)).running is False


def test_decode_state_unsupported_length() -> None:
    with pytest.raises(UnsupportedStateError):
        decode_state(bytes(15))


def test_decode_state_unknown_mode() -> None:
    payload = bytearray(decode_frame(STATE_FRAME).payload)
    payload[0] = 0x04
    with pytest.raises(InvalidFrameError):
        decode_state(bytes(payload))


def test_decode_version() -> None:
    assert decode_version(decode_frame(VERSION_FRAME).payload) == "V2.0.8.Y44.303"


def test_decode_version_signed_bytes() -> None:
    assert decode_version(bytes.fromhex("ff0008590404030003")) == "V-1.0.8.Y44.303"


def test_decode_version_unknown_format() -> None:
    assert decode_version(decode_frame(VERSION_19_FRAME).payload) is None


def test_decode_state_21_byte_payload() -> None:
    state = decode_state(decode_frame(STATE_21_FRAME).payload)
    assert state.mode is Mode.MODE_3
    assert state.mode_settings == {
        Mode.MODE_1: ModeSettings(strength=2, range=5, speed=5),
        Mode.MODE_2: ModeSettings(strength=4, range=6, speed=6),
        Mode.MODE_3: ModeSettings(strength=10, range=10, speed=10),
        Mode.HIGH_FREQUENCY: ModeSettings(strength=20, range=10, speed=10),
    }
    assert state.airplane_mode is False
    assert state.battery_level == 79
    assert state.reminder_30s is False
    assert state.brushing_time == 300
    assert state.high_frequency is False
    assert state.running is False


def test_decode_state_21_byte_payload_running() -> None:
    assert decode_state(decode_frame(STATE_21_RUNNING_FRAME).payload).running is True
