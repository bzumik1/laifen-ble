from __future__ import annotations

from collections.abc import Callable, Generator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
import pytest

from laifen_ble import LaifenConnectionError, LaifenToothbrush, Mode
from laifen_ble.const import CHARACTERISTIC_UUID
from laifen_ble.protocol import READ_STATE

STATE_FRAME = bytes.fromhex("aa0a021400010a070a0a0a040a0a140101005d010078010085")
VERSION_FRAME = bytes.fromhex("aa010209020008590404030003f3")
ACK_FRAME = bytes.fromhex("aa05020100ac")
READ_REQUEST_FRAME = bytes.fromhex("aa0a0000a0")
ADDRESS = "C0:00:00:00:A2:20"
NAME = "LFTB01-P-A220"


class FakeClient:
    def __init__(self, respond: bool = True) -> None:
        self.is_connected = True
        self.respond = respond
        self.notify_callback: Callable[[Any, bytearray], None] | None = None
        self.disconnected_callback: Callable[[Any], None] | None = None
        self.start_notify = AsyncMock(side_effect=self._start_notify)
        self.write_gatt_char = AsyncMock(side_effect=self._write)
        self.disconnect = AsyncMock(side_effect=self._disconnect)

    async def _start_notify(
        self, _uuid: str, callback: Callable[[Any, bytearray], None]
    ) -> None:
        self.notify_callback = callback
        self.notify(VERSION_FRAME)

    async def _write(self, _uuid: str, data: bytes, response: bool) -> None:
        if self.respond and data == READ_STATE:
            self.notify(STATE_FRAME)

    async def _disconnect(self) -> None:
        self.is_connected = False

    def notify(self, data: bytes) -> None:
        assert self.notify_callback is not None
        self.notify_callback(MagicMock(), bytearray(data))

    def drop(self) -> None:
        self.is_connected = False
        assert self.disconnected_callback is not None
        self.disconnected_callback(self)


@pytest.fixture(autouse=True)
def short_state_timeout() -> Generator[None]:
    with patch("laifen_ble.device.STATE_TIMEOUT", 0.01):
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
        return client

    with patch(
        "laifen_ble.device.establish_connection", side_effect=_establish
    ) as mock:
        yield mock


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
    assert toothbrush.state is not None
    assert toothbrush.state.battery_level == 93
    assert toothbrush.state.mode is Mode.MODE_1
    client.write_gatt_char.assert_awaited_once_with(
        CHARACTERISTIC_UUID, READ_REQUEST_FRAME, response=True
    )
    assert callback.call_count == 3
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
    ble_device: BLEDevice, establish: AsyncMock
) -> None:
    silent_client = FakeClient(respond=False)

    async def _establish(*_args: Any, **kwargs: Any) -> FakeClient:
        silent_client.disconnected_callback = kwargs["disconnected_callback"]
        return silent_client

    establish.side_effect = _establish
    toothbrush = LaifenToothbrush(ble_device)

    with pytest.raises(LaifenConnectionError):
        await toothbrush.connect()

    assert not toothbrush.connected
    silent_client.disconnect.assert_awaited_once()


async def test_connect_disconnect_error_is_ignored(
    ble_device: BLEDevice, establish: AsyncMock
) -> None:
    silent_client = FakeClient(respond=False)
    silent_client.disconnect.side_effect = BleakError("gone")

    async def _establish(*_args: Any, **kwargs: Any) -> FakeClient:
        return silent_client

    establish.side_effect = _establish
    toothbrush = LaifenToothbrush(ble_device)

    with pytest.raises(LaifenConnectionError):
        await toothbrush.connect()

    assert not toothbrush.connected


async def test_pushed_state_updates(
    ble_device: BLEDevice, client: FakeClient, establish: AsyncMock
) -> None:
    toothbrush = LaifenToothbrush(ble_device)
    await toothbrush.connect()
    callback = MagicMock()
    toothbrush.register_callback(callback)

    running = bytearray(STATE_FRAME)
    running[23] = 0x01
    running[-1] ^= 0x01
    client.notify(bytes(running))

    assert toothbrush.state is not None
    assert toothbrush.state.running is True
    callback.assert_called_once()


@pytest.mark.parametrize(
    "frame",
    [
        ACK_FRAME,
        bytes.fromhex("aa0a0000a0"),
        bytes.fromhex("0102030405060702ff2a"),
        bytes.fromhex("aa0a020f") + bytes(15) + bytes((0xAA ^ 0x0A ^ 0x02 ^ 0x0F,)),
    ],
    ids=["ack", "request_echo", "filler", "unsupported_state"],
)
async def test_ignored_frames(
    ble_device: BLEDevice, client: FakeClient, establish: AsyncMock, frame: bytes
) -> None:
    toothbrush = LaifenToothbrush(ble_device)
    await toothbrush.connect()
    state = toothbrush.state
    callback = MagicMock()
    toothbrush.register_callback(callback)

    client.notify(frame)

    assert toothbrush.state is state
    callback.assert_not_called()


async def test_unexpected_disconnect(
    ble_device: BLEDevice, client: FakeClient, establish: AsyncMock
) -> None:
    toothbrush = LaifenToothbrush(ble_device)
    await toothbrush.connect()
    callback = MagicMock()
    toothbrush.register_callback(callback)

    client.drop()

    assert not toothbrush.connected
    assert toothbrush.state is not None
    callback.assert_called_once()


async def test_stale_disconnect_is_ignored(
    ble_device: BLEDevice, client: FakeClient, establish: AsyncMock
) -> None:
    toothbrush = LaifenToothbrush(ble_device)
    await toothbrush.connect()
    callback = MagicMock()
    toothbrush.register_callback(callback)

    assert client.disconnected_callback is not None
    client.disconnected_callback(FakeClient())

    assert toothbrush.connected
    callback.assert_not_called()


async def test_disconnect(
    ble_device: BLEDevice, client: FakeClient, establish: AsyncMock
) -> None:
    toothbrush = LaifenToothbrush(ble_device)
    await toothbrush.connect()

    await toothbrush.disconnect()
    await toothbrush.disconnect()

    assert not toothbrush.connected
    client.disconnect.assert_awaited_once()


async def test_unregister_callback(
    ble_device: BLEDevice, client: FakeClient, establish: AsyncMock
) -> None:
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
