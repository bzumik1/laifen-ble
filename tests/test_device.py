from __future__ import annotations

import asyncio
from collections.abc import Callable, Generator
from datetime import datetime
import gc
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
import pytest

from laifen_ble import (
    BrushingSession,
    LaifenCommandError,
    LaifenConnectionError,
    LaifenMotorError,
    LaifenToothbrush,
    LaifenValueError,
    Mode,
    ModeSettings,
    MotorStatus,
    UnsupportedStateError,
)
from laifen_ble.const import CHARACTERISTIC_UUID
from laifen_ble.protocol import READ_STATE, READ_VERSION, Command, Kind, encode_frame

STATE_PAYLOAD = bytes.fromhex("00010a070a0a0a040a0a140101005d0100780100")
VERSION_FRAME = bytes.fromhex("aa010209020008590404030003f3")
ADDRESS = "C0:00:00:00:A2:20"
NAME = "LFTB01-P-A220"


class FakeClient:
    def __init__(self, payload: bytes = STATE_PAYLOAD) -> None:
        self.is_connected = True
        self.payload = bytearray(payload)
        self.answer_state = True
        self.acknowledge = True
        self.acknowledgement_payload: bytes | None = None
        self.status = 0x00
        self.stale_services = 0
        self.written: list[bytes] = []
        self.clear_cache = AsyncMock(return_value=True)
        self.notify_callback: Callable[[Any, bytearray], None] | None = None
        self.disconnected_callback: Callable[[Any], None] | None = None
        self.start_notify = AsyncMock(side_effect=self._start_notify)
        self.write_gatt_char = AsyncMock(side_effect=self._write)
        self.disconnect = AsyncMock(side_effect=self._disconnect)

    @property
    def services(self) -> MagicMock:
        services = MagicMock()
        if self.stale_services:
            self.stale_services -= 1
            services.get_characteristic.return_value = None
        return services

    async def _start_notify(
        self, _uuid: str, callback: Callable[[Any, bytearray], None]
    ) -> None:
        self.notify_callback = callback
        self.notify(VERSION_FRAME)

    async def _write(self, _uuid: str, data: bytes, response: bool) -> None:
        self.written.append(bytes(data))
        if data == READ_VERSION:
            self.notify(VERSION_FRAME)
            return
        if data == READ_STATE:
            if self.answer_state:
                self.notify_state()
            return
        if self.status == 0x00:
            self._apply(data[1], data[4:-1])
        if self.acknowledge:
            payload = self.acknowledgement_payload or bytes((self.status,))
            self.notify(encode_frame(Command(data[1]), Kind.REPORT, payload))

    def _apply(self, command: int, payload: bytes) -> None:
        if command == Command.MODE_SETTINGS:
            mode = payload[0]
            self.payload[0] = mode
            self.payload[1 + 3 * mode : 4 + 3 * mode] = payload[1:]
        elif command == Command.BRUSHING_TIME:
            self.payload[16:18] = payload
        elif command == Command.REMINDER_30S:
            self.payload[15] = payload[0]
        elif command == Command.HIGH_FREQUENCY:
            self.payload[18] = payload[0]
        elif command == Command.MOTOR:
            self.payload[19] = payload[0]

    async def _disconnect(self) -> None:
        self.is_connected = False

    def notify(self, data: bytes) -> None:
        assert self.notify_callback is not None
        self.notify_callback(MagicMock(), bytearray(data))

    def notify_state(self) -> None:
        self.notify(encode_frame(Command.STATE, Kind.REPORT, bytes(self.payload)))

    def set_running(self, running: bool) -> None:
        self.payload[19] = 0x01 if running else 0x00
        self.notify_state()

    def drop(self) -> None:
        self.is_connected = False
        assert self.disconnected_callback is not None
        self.disconnected_callback(self)


def frame(command: Command, payload: str) -> bytes:
    return encode_frame(command, Kind.WRITE, bytes.fromhex(payload))


@pytest.fixture(autouse=True)
def short_timeouts() -> Generator[None]:
    with (
        patch("laifen_ble.device.STATE_TIMEOUT", 0.01),
        patch("laifen_ble.device.COMMAND_TIMEOUT", 0.01),
    ):
        yield


@pytest.fixture
def ble_device() -> BLEDevice:
    return BLEDevice(ADDRESS, NAME, details=None)


@pytest.fixture
def client() -> FakeClient:
    return FakeClient()


@pytest.fixture
def establish(client: FakeClient) -> Generator[AsyncMock]:
    async def _establish(*_args: Any, **kwargs: Any) -> FakeClient:
        client.disconnected_callback = kwargs["disconnected_callback"]
        client.is_connected = True
        return client

    with patch(
        "laifen_ble.device.establish_connection", side_effect=_establish
    ) as mock:
        yield mock


@pytest.fixture
async def toothbrush(
    ble_device: BLEDevice, client: FakeClient, establish: AsyncMock
) -> LaifenToothbrush:
    toothbrush = LaifenToothbrush(ble_device)
    await toothbrush.connect()
    client.written.clear()
    return toothbrush


async def test_connect_reads_state(
    ble_device: BLEDevice, client: FakeClient, establish: AsyncMock
) -> None:
    toothbrush = LaifenToothbrush(ble_device)
    callback = MagicMock()
    toothbrush.register_callback(callback)

    await toothbrush.connect()

    assert toothbrush.connected
    assert toothbrush.address == ADDRESS
    assert toothbrush.name == NAME
    assert toothbrush.firmware_version == "V2.0.8.Y44.303"
    assert toothbrush.firmware_version_raw == bytes.fromhex("020008590404030003")
    assert toothbrush.state is not None
    assert toothbrush.state.battery_level == 93
    assert toothbrush.state.mode is Mode.MODE_1
    assert toothbrush.last_session is None
    assert client.written == [READ_VERSION, READ_STATE]
    client.write_gatt_char.assert_awaited_with(
        CHARACTERISTIC_UUID, READ_STATE, response=True
    )
    assert callback.call_count == 4
    assert establish.call_args.kwargs["ble_device_callback"]() is ble_device


async def test_connect_when_connected_is_noop(
    ble_device: BLEDevice, establish: AsyncMock
) -> None:
    toothbrush = LaifenToothbrush(ble_device)
    await toothbrush.connect()
    await toothbrush.connect()
    assert establish.await_count == 1


async def test_connect_failure(ble_device: BLEDevice) -> None:
    toothbrush = LaifenToothbrush(ble_device)
    with (
        patch(
            "laifen_ble.device.establish_connection",
            side_effect=BleakError("out of slots"),
        ),
        pytest.raises(LaifenConnectionError),
    ):
        await toothbrush.connect()
    assert not toothbrush.connected


async def test_connect_state_timeout(
    ble_device: BLEDevice, client: FakeClient, establish: AsyncMock
) -> None:
    client.answer_state = False
    toothbrush = LaifenToothbrush(ble_device)

    with pytest.raises(LaifenConnectionError):
        await toothbrush.connect()

    assert not toothbrush.connected
    client.disconnect.assert_awaited_once()


@pytest.mark.parametrize("error", [BleakError("gone"), TimeoutError()])
async def test_connect_disconnect_error_is_ignored(
    ble_device: BLEDevice, client: FakeClient, establish: AsyncMock, error: Exception
) -> None:
    client.answer_state = False
    client.disconnect.side_effect = error
    toothbrush = LaifenToothbrush(ble_device)

    with pytest.raises(LaifenConnectionError):
        await toothbrush.connect()

    assert not toothbrush.connected


async def test_disconnect_timeout_is_ignored(
    toothbrush: LaifenToothbrush, client: FakeClient
) -> None:
    client.disconnect.side_effect = TimeoutError

    await toothbrush.disconnect()

    assert not toothbrush.connected


async def test_connect_link_drops_after_state(
    ble_device: BLEDevice, client: FakeClient, establish: AsyncMock
) -> None:
    async def answer_then_drop(_uuid: str, data: bytes, response: bool) -> None:
        if data == READ_STATE:
            client.notify_state()
            client.drop()

    client.write_gatt_char.side_effect = answer_then_drop
    toothbrush = LaifenToothbrush(ble_device)

    with pytest.raises(LaifenConnectionError):
        await toothbrush.connect()

    assert not toothbrush.connected


TRANSPORT_ERRORS = [
    pytest.param(EOFError(), id="eof"),
    pytest.param(BrokenPipeError(), id="broken_pipe"),
    pytest.param(AttributeError(), id="attribute"),
]


@pytest.mark.parametrize("error", TRANSPORT_ERRORS)
async def test_connect_transport_error(
    ble_device: BLEDevice, client: FakeClient, establish: AsyncMock, error: Exception
) -> None:
    client.start_notify.side_effect = error
    toothbrush = LaifenToothbrush(ble_device)

    with pytest.raises(LaifenConnectionError):
        await toothbrush.connect()

    assert not toothbrush.connected


@pytest.mark.parametrize("error", TRANSPORT_ERRORS)
@pytest.mark.parametrize(
    ("method", "args"),
    [
        pytest.param("set_reminder_30s", (True,), id="command"),
        pytest.param("update", (), id="update"),
    ],
)
async def test_operation_transport_error(
    toothbrush: LaifenToothbrush,
    client: FakeClient,
    error: Exception,
    method: str,
    args: tuple[Any, ...],
) -> None:
    client.write_gatt_char.side_effect = error

    with pytest.raises(LaifenConnectionError):
        await getattr(toothbrush, method)(*args)


@pytest.mark.parametrize("error", TRANSPORT_ERRORS)
async def test_disconnect_transport_error_is_ignored(
    toothbrush: LaifenToothbrush, client: FakeClient, error: Exception
) -> None:
    client.disconnect.side_effect = error

    await toothbrush.disconnect()

    assert not toothbrush.connected


@pytest.mark.parametrize(
    ("method", "args"),
    [
        pytest.param("set_reminder_30s", (True,), id="command"),
        pytest.param("update", (), id="update"),
    ],
)
async def test_stuck_write_times_out(
    toothbrush: LaifenToothbrush,
    client: FakeClient,
    method: str,
    args: tuple[Any, ...],
) -> None:
    async def never_answer(_uuid: str, data: bytes, response: bool) -> None:
        await asyncio.Event().wait()

    client.write_gatt_char.side_effect = never_answer

    with pytest.raises(LaifenConnectionError):
        async with asyncio.timeout(1):
            await getattr(toothbrush, method)(*args)


async def test_stuck_notify_times_out(
    ble_device: BLEDevice, client: FakeClient, establish: AsyncMock
) -> None:
    async def never_answer(
        _uuid: str, callback: Callable[[Any, bytearray], None]
    ) -> None:
        await asyncio.Event().wait()

    client.start_notify.side_effect = never_answer
    toothbrush = LaifenToothbrush(ble_device)

    with pytest.raises(LaifenConnectionError):
        async with asyncio.timeout(1):
            await toothbrush.connect()

    assert not toothbrush.connected
    client.disconnect.assert_awaited_once()


async def test_failed_connect_does_not_keep_a_session(
    ble_device: BLEDevice, client: FakeClient, establish: AsyncMock
) -> None:
    async def answer_then_drop(_uuid: str, data: bytes, response: bool) -> None:
        if data == READ_STATE:
            client.notify_state()
            client.drop()

    client.payload[19] = 0x01
    client.write_gatt_char.side_effect = answer_then_drop
    toothbrush = LaifenToothbrush(ble_device)

    with patch("laifen_ble.device.monotonic", side_effect=[100.0, 3700.0]):
        with pytest.raises(LaifenConnectionError):
            await toothbrush.connect()

        client.payload[19] = 0x00
        client.write_gatt_char.side_effect = client._write
        await toothbrush.connect()

    assert toothbrush.connected
    assert toothbrush.last_session is None


async def test_unsupported_state_while_connected(
    toothbrush: LaifenToothbrush, client: FakeClient
) -> None:
    state = toothbrush.state
    client.payload = bytearray(STATE_PAYLOAD[:15])

    with pytest.raises(LaifenConnectionError):
        await toothbrush.update()

    assert toothbrush.state is state


async def test_pushed_state_updates(
    toothbrush: LaifenToothbrush, client: FakeClient
) -> None:
    callback = MagicMock()
    toothbrush.register_callback(callback)

    client.set_running(True)

    assert toothbrush.state is not None
    assert toothbrush.state.running is True
    callback.assert_called_once()


@pytest.mark.parametrize(
    "data",
    [
        bytes.fromhex("aa05020100ac"),
        bytes.fromhex("aa0a0000a0"),
        bytes.fromhex("0102030405060702ff2a"),
        encode_frame(Command.STATE, Kind.REPORT, bytes(15)),
        encode_frame(Command.STATE, Kind.REPORT, b"\x04" + STATE_PAYLOAD[1:]),
    ],
    ids=[
        "unexpected_acknowledgement",
        "request_echo",
        "filler",
        "unsupported_state",
        "invalid_state",
    ],
)
async def test_ignored_frames(
    toothbrush: LaifenToothbrush, client: FakeClient, data: bytes
) -> None:
    state = toothbrush.state
    callback = MagicMock()
    toothbrush.register_callback(callback)

    client.notify(data)

    assert toothbrush.state is state
    callback.assert_not_called()


async def test_unexpected_disconnect(
    toothbrush: LaifenToothbrush, client: FakeClient
) -> None:
    callback = MagicMock()
    toothbrush.register_callback(callback)

    client.drop()

    assert not toothbrush.connected
    assert toothbrush.state is not None
    callback.assert_called_once()


async def test_stale_disconnect_is_ignored(
    toothbrush: LaifenToothbrush, client: FakeClient
) -> None:
    callback = MagicMock()
    toothbrush.register_callback(callback)

    assert client.disconnected_callback is not None
    client.disconnected_callback(FakeClient())

    assert toothbrush.connected
    callback.assert_not_called()


async def test_disconnect(toothbrush: LaifenToothbrush, client: FakeClient) -> None:
    await toothbrush.disconnect()
    await toothbrush.disconnect()

    assert not toothbrush.connected
    client.disconnect.assert_awaited_once()


async def test_unregister_callback(ble_device: BLEDevice, establish: AsyncMock) -> None:
    toothbrush = LaifenToothbrush(ble_device)
    callback = MagicMock()
    unregister = toothbrush.register_callback(callback)
    unregister()

    await toothbrush.connect()

    callback.assert_not_called()


async def test_connect_without_ble_device(establish: AsyncMock) -> None:
    toothbrush = LaifenToothbrush(ADDRESS)
    assert toothbrush.address == ADDRESS
    assert toothbrush.name == ADDRESS

    with pytest.raises(LaifenConnectionError):
        await toothbrush.connect()

    establish.assert_not_called()


async def test_connect_after_ble_device_is_set(
    ble_device: BLEDevice, establish: AsyncMock
) -> None:
    toothbrush = LaifenToothbrush(ADDRESS)
    toothbrush.set_ble_device(ble_device)

    await toothbrush.connect()

    assert toothbrush.connected
    assert toothbrush.name == NAME


def test_set_ble_device(ble_device: BLEDevice) -> None:
    toothbrush = LaifenToothbrush(ble_device)
    toothbrush.set_ble_device(BLEDevice(ADDRESS, None, details=None))
    assert toothbrush.name == ADDRESS


async def test_set_mode(toothbrush: LaifenToothbrush, client: FakeClient) -> None:
    callback = MagicMock()
    toothbrush.register_callback(callback)

    await toothbrush.set_mode(Mode.MODE_2)

    assert client.written == [frame(Command.MODE_SETTINGS, "010a0a0a"), READ_STATE]
    assert toothbrush.state is not None
    assert toothbrush.state.mode is Mode.MODE_2
    callback.assert_called()


async def test_set_mode_settings_of_active_mode(
    toothbrush: LaifenToothbrush, client: FakeClient
) -> None:
    await toothbrush.set_mode_settings(Mode.MODE_1, ModeSettings(5, 6, 7))

    assert client.written == [frame(Command.MODE_SETTINGS, "00050607"), READ_STATE]
    assert toothbrush.state is not None
    assert toothbrush.state.active_mode_settings == ModeSettings(5, 6, 7)


async def test_set_mode_settings_of_other_mode(
    toothbrush: LaifenToothbrush, client: FakeClient
) -> None:
    await toothbrush.set_mode_settings(Mode.MODE_3, ModeSettings(5, 6, 7))

    assert client.written == [
        frame(Command.MODE_SETTINGS, "02050607"),
        READ_STATE,
        frame(Command.MODE_SETTINGS, "00010a07"),
        READ_STATE,
    ]
    assert toothbrush.state is not None
    assert toothbrush.state.mode is Mode.MODE_1
    assert toothbrush.state.mode_settings[Mode.MODE_3] == ModeSettings(5, 6, 7)


async def test_set_high_frequency_mode_when_off(
    ble_device: BLEDevice, establish: AsyncMock, client: FakeClient
) -> None:
    client.payload[18] = 0x00
    toothbrush = LaifenToothbrush(ble_device)
    await toothbrush.connect()

    with pytest.raises(LaifenValueError):
        await toothbrush.set_mode(Mode.HIGH_FREQUENCY)
    with pytest.raises(LaifenValueError):
        await toothbrush.set_mode_settings(Mode.HIGH_FREQUENCY, ModeSettings(15, 1, 1))


async def test_set_brushing_time(
    toothbrush: LaifenToothbrush, client: FakeClient
) -> None:
    await toothbrush.set_brushing_time(150)

    assert client.written == [bytes.fromhex("aa05010200963a"), READ_STATE]
    assert toothbrush.state is not None
    assert toothbrush.state.brushing_time == 150


@pytest.mark.parametrize(
    ("method", "argument", "expected", "attribute", "value"),
    [
        pytest.param(
            "set_reminder_30s",
            False,
            "aa0b010100a1",
            "reminder_30s",
            False,
            id="reminder_off",
        ),
        pytest.param(
            "set_high_frequency",
            True,
            "aa0e010101a5",
            "high_frequency",
            True,
            id="high_frequency_on",
        ),
        pytest.param(
            "set_high_frequency",
            False,
            "aa0e010100a4",
            "high_frequency",
            False,
            id="high_frequency_off",
        ),
    ],
)
async def test_switches(
    toothbrush: LaifenToothbrush,
    client: FakeClient,
    method: str,
    argument: bool,
    expected: str,
    attribute: str,
    value: bool,
) -> None:
    await getattr(toothbrush, method)(argument)

    assert client.written == [bytes.fromhex(expected), READ_STATE]
    assert getattr(toothbrush.state, attribute) is value


async def test_start_and_stop(toothbrush: LaifenToothbrush, client: FakeClient) -> None:
    await toothbrush.start()
    assert toothbrush.state is not None
    assert toothbrush.state.running is True

    await toothbrush.stop()
    assert toothbrush.state.running is False

    assert client.written == [
        bytes.fromhex("aa0f010101a4"),
        READ_STATE,
        bytes.fromhex("aa0f010100a5"),
        READ_STATE,
    ]


async def test_turn_off_high_frequency_while_active(
    ble_device: BLEDevice, establish: AsyncMock, client: FakeClient
) -> None:
    client.payload[0] = Mode.HIGH_FREQUENCY
    toothbrush = LaifenToothbrush(ble_device)
    await toothbrush.connect()
    client.written.clear()

    await toothbrush.set_high_frequency(False)

    assert client.written == [
        frame(Command.MODE_SETTINGS, "02040a0a"),
        READ_STATE,
        bytes.fromhex("aa0e010100a4"),
        READ_STATE,
    ]
    assert toothbrush.state is not None
    assert toothbrush.state.mode is Mode.MODE_3
    assert toothbrush.state.high_frequency is False


async def test_command_refused(
    toothbrush: LaifenToothbrush, client: FakeClient
) -> None:
    client.status = MotorStatus.CHARGING

    with pytest.raises(LaifenMotorError) as err:
        await toothbrush.start()

    assert err.value.command == Command.MOTOR
    assert err.value.status == MotorStatus.CHARGING
    assert err.value.reason is MotorStatus.CHARGING
    assert toothbrush.state is not None
    assert toothbrush.state.running is False


async def test_command_when_not_connected(ble_device: BLEDevice) -> None:
    toothbrush = LaifenToothbrush(ble_device)

    with pytest.raises(LaifenConnectionError):
        await toothbrush.set_mode(Mode.MODE_2)
    with pytest.raises(LaifenConnectionError):
        await toothbrush.set_brushing_time(120)


@pytest.mark.parametrize(
    "attribute",
    ["acknowledge", "answer_state"],
    ids=["no_acknowledgement", "no_state"],
)
async def test_command_timeout(
    toothbrush: LaifenToothbrush, client: FakeClient, attribute: str
) -> None:
    setattr(client, attribute, False)

    with pytest.raises(LaifenConnectionError):
        await toothbrush.set_reminder_30s(True)


async def test_command_invalid_acknowledgement(
    toothbrush: LaifenToothbrush, client: FakeClient
) -> None:
    client.acknowledgement_payload = bytes.fromhex("0000")

    with pytest.raises(LaifenConnectionError):
        await toothbrush.set_reminder_30s(True)


async def test_command_write_error(
    toothbrush: LaifenToothbrush, client: FakeClient
) -> None:
    client.write_gatt_char.side_effect = BleakError("disconnected")

    with pytest.raises(LaifenConnectionError):
        await toothbrush.stop()


async def test_duplicate_acknowledgement(
    toothbrush: LaifenToothbrush, client: FakeClient
) -> None:
    async def write_twice(_uuid: str, data: bytes, response: bool) -> None:
        await FakeClient._write(client, _uuid, data, response)
        if data != READ_STATE:
            client.notify(encode_frame(Command(data[1]), Kind.REPORT, b"\x00"))

    client.write_gatt_char.side_effect = write_twice

    await toothbrush.set_reminder_30s(True)

    assert toothbrush.state is not None
    assert toothbrush.state.reminder_30s is True


async def test_brushing_session(
    toothbrush: LaifenToothbrush, client: FakeClient
) -> None:
    with patch("laifen_ble.device.monotonic", side_effect=[100.0, 160.4]):
        client.set_running(True)
        client.set_running(True)
        client.set_running(False)

    session = toothbrush.last_session
    assert isinstance(session, BrushingSession)
    assert session.duration == 60
    assert isinstance(session.started_at, datetime)
    assert session.started_at.tzinfo is not None


async def test_short_run_is_not_a_session(
    toothbrush: LaifenToothbrush, client: FakeClient
) -> None:
    with patch("laifen_ble.device.monotonic", side_effect=[100.0, 101.0]):
        client.set_running(True)
        client.set_running(False)

    assert toothbrush.last_session is None


async def test_session_is_dropped_on_disconnect(
    toothbrush: LaifenToothbrush, client: FakeClient
) -> None:
    with patch("laifen_ble.device.monotonic", side_effect=[100.0]):
        client.set_running(True)
    client.drop()
    client.is_connected = True
    client.set_running(False)

    assert toothbrush.last_session is None


async def test_unknown_motor_refusal(
    toothbrush: LaifenToothbrush, client: FakeClient
) -> None:
    client.status = 0x09

    with pytest.raises(LaifenMotorError) as err:
        await toothbrush.stop()

    assert err.value.reason is None


async def test_other_command_refused(
    toothbrush: LaifenToothbrush, client: FakeClient
) -> None:
    client.status = 0x01

    with pytest.raises(LaifenCommandError) as err:
        await toothbrush.set_brushing_time(120)

    assert not isinstance(err.value, LaifenMotorError)


async def test_update(toothbrush: LaifenToothbrush, client: FakeClient) -> None:
    client.payload[14] = 80
    callback = MagicMock()
    toothbrush.register_callback(callback)

    await toothbrush.update()

    assert client.written == [READ_STATE]
    assert toothbrush.state is not None
    assert toothbrush.state.battery_level == 80
    callback.assert_called_once()


async def test_update_errors(toothbrush: LaifenToothbrush, client: FakeClient) -> None:
    client.answer_state = False
    with pytest.raises(LaifenConnectionError):
        await toothbrush.update()

    client.is_connected = False
    with pytest.raises(LaifenConnectionError):
        await toothbrush.update()


async def test_connect_with_stale_services(
    ble_device: BLEDevice, client: FakeClient, establish: AsyncMock
) -> None:
    client.stale_services = 1
    toothbrush = LaifenToothbrush(ble_device)

    await toothbrush.connect()

    assert toothbrush.connected
    client.clear_cache.assert_awaited_once()
    assert [call.kwargs["use_services_cache"] for call in establish.call_args_list] == [
        True,
        False,
    ]


@pytest.mark.parametrize("error", [BleakError("not connected"), TimeoutError()])
async def test_connect_when_clearing_the_services_fails(
    ble_device: BLEDevice,
    client: FakeClient,
    establish: AsyncMock,
    error: Exception,
) -> None:
    client.stale_services = 1
    client.clear_cache.side_effect = error
    toothbrush = LaifenToothbrush(ble_device)

    await toothbrush.connect()

    assert toothbrush.connected
    assert establish.await_count == 2


async def test_connect_unsupported_state(
    ble_device: BLEDevice, establish: AsyncMock
) -> None:
    client = FakeClient(STATE_PAYLOAD[:15])
    establish.side_effect = None
    establish.return_value = client
    toothbrush = LaifenToothbrush(ble_device)

    with (
        patch("laifen_ble.device.STATE_TIMEOUT", 30),
        pytest.raises(UnsupportedStateError),
    ):
        async with asyncio.timeout(1):
            await toothbrush.connect()

    assert not toothbrush.connected
    client.disconnect.assert_awaited_once()


async def test_unknown_version_keeps_the_known_one(
    toothbrush: LaifenToothbrush, client: FakeClient
) -> None:
    client.notify(encode_frame(Command.VERSION, Kind.REPORT, bytes.fromhex("0102")))

    assert toothbrush.firmware_version == "V2.0.8.Y44.303"
    assert toothbrush.firmware_version_raw == bytes.fromhex("0102")


async def test_connect_without_characteristic(
    ble_device: BLEDevice, client: FakeClient, establish: AsyncMock
) -> None:
    client.stale_services = 2
    toothbrush = LaifenToothbrush(ble_device)

    with pytest.raises(LaifenConnectionError):
        await toothbrush.connect()

    assert not toothbrush.connected
    assert client.disconnect.await_count == 2


@pytest.mark.parametrize(
    ("method", "expected"),
    [
        pytest.param("set_strength", ModeSettings(3, 10, 7), id="strength"),
        pytest.param("set_range", ModeSettings(1, 3, 7), id="range"),
        pytest.param("set_speed", ModeSettings(1, 10, 3), id="speed"),
    ],
)
async def test_set_active_mode_setting(
    toothbrush: LaifenToothbrush,
    client: FakeClient,
    method: str,
    expected: ModeSettings,
) -> None:
    await getattr(toothbrush, method)(3)

    assert toothbrush.state is not None
    assert toothbrush.state.active_mode_settings == expected


async def test_concurrent_commands_do_not_interleave(
    toothbrush: LaifenToothbrush, client: FakeClient
) -> None:
    await asyncio.gather(
        toothbrush.set_mode_settings(Mode.MODE_3, ModeSettings(5, 6, 7)),
        toothbrush.set_mode(Mode.MODE_2),
    )

    assert [data for data in client.written if data != READ_STATE] == [
        frame(Command.MODE_SETTINGS, "02050607"),
        frame(Command.MODE_SETTINGS, "00010a07"),
        frame(Command.MODE_SETTINGS, "010a0a0a"),
    ]
    assert toothbrush.state is not None
    assert toothbrush.state.mode is Mode.MODE_2


async def test_disconnect_during_command_fails_immediately(
    toothbrush: LaifenToothbrush, client: FakeClient
) -> None:
    async def drop_instead_of_acknowledging(
        _uuid: str, data: bytes, response: bool
    ) -> None:
        client.drop()

    client.write_gatt_char.side_effect = drop_instead_of_acknowledging

    with (
        patch("laifen_ble.device.COMMAND_TIMEOUT", 30),
        pytest.raises(LaifenConnectionError),
    ):
        async with asyncio.timeout(1):
            await toothbrush.set_reminder_30s(True)


@pytest.mark.parametrize(
    ("method", "args"),
    [
        pytest.param("set_reminder_30s", (True,), id="command"),
        pytest.param("update", (), id="update"),
    ],
)
async def test_disconnect_while_writing_retrieves_errors(
    toothbrush: LaifenToothbrush,
    client: FakeClient,
    method: str,
    args: tuple[Any, ...],
) -> None:
    unhandled: list[dict[str, Any]] = []
    asyncio.get_running_loop().set_exception_handler(
        lambda _loop, context: unhandled.append(context)
    )

    async def drop_and_fail(_uuid: str, data: bytes, response: bool) -> None:
        client.drop()
        raise BleakError("disconnected")

    client.write_gatt_char.side_effect = drop_and_fail

    with pytest.raises(LaifenConnectionError):
        await getattr(toothbrush, method)(*args)
    gc.collect()

    assert unhandled == []


async def test_disconnect_during_connect(
    ble_device: BLEDevice, client: FakeClient, establish: AsyncMock
) -> None:
    async def drop_instead_of_answering(
        _uuid: str, data: bytes, response: bool
    ) -> None:
        if data == READ_STATE:
            client.drop()

    client.write_gatt_char.side_effect = drop_instead_of_answering
    toothbrush = LaifenToothbrush(ble_device)
    callback = MagicMock()
    toothbrush.register_callback(callback)

    with (
        patch("laifen_ble.device.STATE_TIMEOUT", 30),
        pytest.raises(LaifenConnectionError),
    ):
        async with asyncio.timeout(1):
            await toothbrush.connect()

    assert not toothbrush.connected
    client.disconnect.assert_awaited_once()


async def test_cancelled_connect_disconnects(
    ble_device: BLEDevice, client: FakeClient, establish: AsyncMock
) -> None:
    client.start_notify.side_effect = asyncio.CancelledError
    toothbrush = LaifenToothbrush(ble_device)

    with pytest.raises(asyncio.CancelledError):
        await toothbrush.connect()

    assert not toothbrush.connected
    client.disconnect.assert_awaited_once()


async def test_second_connection_attempt_fails(
    ble_device: BLEDevice, client: FakeClient, establish: AsyncMock
) -> None:
    client.stale_services = 1
    first = establish.side_effect

    async def fail_second(*args: Any, **kwargs: Any) -> FakeClient:
        if establish.await_count > 1:
            raise BleakError("gone")
        result: FakeClient = await first(*args, **kwargs)
        return result

    establish.side_effect = fail_second
    toothbrush = LaifenToothbrush(ble_device)

    with pytest.raises(LaifenConnectionError):
        await toothbrush.connect()

    client.clear_cache.assert_awaited_once()


async def test_disconnect_notifies_callbacks(toothbrush: LaifenToothbrush) -> None:
    callback = MagicMock()
    toothbrush.register_callback(callback)

    await toothbrush.disconnect()

    callback.assert_called_once()


async def test_unregister_twice(toothbrush: LaifenToothbrush) -> None:
    unregister = toothbrush.register_callback(MagicMock())

    unregister()
    unregister()


async def test_command_without_state(
    toothbrush: LaifenToothbrush, client: FakeClient
) -> None:
    toothbrush._state = None

    with pytest.raises(LaifenConnectionError):
        await toothbrush.set_mode(Mode.MODE_2)
