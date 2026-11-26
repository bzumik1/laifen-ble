from __future__ import annotations

import pytest

from laifen_ble import (
    InvalidFrameError,
    LaifenValueError,
    Mode,
    ModeSettings,
    UnsupportedStateError,
)
from laifen_ble.protocol import (
    READ_STATE,
    READ_VERSION,
    Command,
    Kind,
    checksum,
    decode_acknowledgement,
    decode_frame,
    decode_state,
    decode_version,
    encode_brushing_time,
    encode_frame,
    encode_mode_settings,
    encode_switch,
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
    with pytest.raises(UnsupportedStateError):
        decode_state(bytes(payload))


def test_decode_version() -> None:
    assert decode_version(decode_frame(VERSION_FRAME).payload) == "V2.0.8.Y44.303"


def test_decode_version_signed_bytes() -> None:
    assert decode_version(bytes.fromhex("ff0008590404030003")) == "V-1.0.8.Y44.303"


def test_decode_version_19_bytes() -> None:
    assert (
        decode_version(decode_frame(VERSION_19_FRAME).payload)
        == "V2.0.93.XXX.T01.B00.Y44.303"
    )


def test_decode_version_unprintable_letter() -> None:
    assert decode_version(bytes.fromhex("020008070404030003")) is None


def test_decode_version_unknown_format() -> None:
    assert decode_version(bytes.fromhex("0102")) is None


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


def test_encode_mode_settings() -> None:
    assert encode_mode_settings(Mode.MODE_2, ModeSettings(10, 10, 9)) == bytes.fromhex(
        "aa040104010a0a09a3"
    )


def test_encode_mode_settings_high_frequency() -> None:
    assert encode_mode_settings(
        Mode.HIGH_FREQUENCY, ModeSettings(20, 1, 1)
    ) == encode_frame(Command.MODE_SETTINGS, Kind.WRITE, bytes((3, 20, 1, 1)))


@pytest.mark.parametrize(
    ("mode", "settings"),
    [
        pytest.param(Mode.MODE_1, ModeSettings(11, 5, 5), id="strength_too_high"),
        pytest.param(Mode.MODE_1, ModeSettings(0, 5, 5), id="strength_too_low"),
        pytest.param(
            Mode.HIGH_FREQUENCY, ModeSettings(10, 5, 5), id="high_frequency_strength"
        ),
        pytest.param(Mode.MODE_1, ModeSettings(5, 0, 5), id="range_too_low"),
        pytest.param(Mode.MODE_1, ModeSettings(5, 5, 11), id="speed_too_high"),
    ],
)
def test_encode_mode_settings_invalid(mode: Mode, settings: ModeSettings) -> None:
    with pytest.raises(ValueError):
        encode_mode_settings(mode, settings)


def test_encode_brushing_time() -> None:
    assert encode_brushing_time(120) == bytes.fromhex("aa0501020078d4")


@pytest.mark.parametrize("seconds", [30, 330, 75])
def test_encode_brushing_time_invalid(seconds: int) -> None:
    with pytest.raises(ValueError):
        encode_brushing_time(seconds)


@pytest.mark.parametrize(
    ("enabled", "expected"),
    [(True, "aa0f010101a4"), (False, "aa0f010100a5")],
)
def test_encode_switch(enabled: bool, expected: str) -> None:
    assert encode_switch(Command.MOTOR, enabled) == bytes.fromhex(expected)


def test_decode_acknowledgement() -> None:
    assert decode_acknowledgement(decode_frame(ACK_FRAME).payload) == 0x00


def test_decode_acknowledgement_invalid() -> None:
    with pytest.raises(InvalidFrameError):
        decode_acknowledgement(b"\x00\x00")


def test_strength_limits() -> None:
    assert Mode.MODE_1.strength_limits == (1, 10)
    assert Mode.HIGH_FREQUENCY.strength_limits == (11, 20)


def test_state_is_hashable() -> None:
    state = decode_state(decode_frame(STATE_FRAME).payload)

    assert hash(state) == hash(decode_state(decode_frame(STATE_FRAME).payload))


def test_decode_state_full_battery() -> None:
    payload = bytearray(decode_frame(STATE_FRAME).payload)
    payload[14] = 100
    assert decode_state(bytes(payload)).battery_level == 100


def test_decode_state_battery_out_of_range() -> None:
    payload = bytearray(decode_frame(STATE_FRAME).payload)
    payload[14] = 0xFF
    assert decode_state(bytes(payload)).battery_level is None


@pytest.mark.parametrize(
    ("high_frequency", "modes"),
    [
        (True, (Mode.MODE_1, Mode.MODE_2, Mode.MODE_3, Mode.HIGH_FREQUENCY)),
        (False, (Mode.MODE_1, Mode.MODE_2, Mode.MODE_3)),
    ],
)
def test_available_modes(high_frequency: bool, modes: tuple[Mode, ...]) -> None:
    payload = bytearray(decode_frame(STATE_FRAME).payload)
    payload[18] = 0x01 if high_frequency else 0x00
    assert decode_state(bytes(payload)).available_modes == modes


def test_invalid_values_raise_laifen_value_error() -> None:
    with pytest.raises(LaifenValueError):
        encode_brushing_time(75)
    with pytest.raises(ValueError):
        encode_mode_settings(Mode.MODE_1, ModeSettings(0, 1, 1))
