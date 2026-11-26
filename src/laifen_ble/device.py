from __future__ import annotations

import asyncio
from collections.abc import Callable
import logging

from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from .const import CHARACTERISTIC_UUID, STATE_TIMEOUT
from .exceptions import InvalidFrameError, LaifenConnectionError
from .models import LaifenState
from .protocol import (
    READ_STATE,
    Command,
    Kind,
    decode_frame,
    decode_state,
    decode_version,
)

_LOGGER = logging.getLogger(__name__)


class LaifenToothbrush:
    def __init__(self, address_or_ble_device: BLEDevice | str) -> None:
        if isinstance(address_or_ble_device, BLEDevice):
            self._address = address_or_ble_device.address
            self._ble_device: BLEDevice | None = address_or_ble_device
        else:
            self._address = address_or_ble_device
            self._ble_device = None
        self._client: BleakClientWithServiceCache | None = None
        self._connect_lock = asyncio.Lock()
        self._callbacks: list[Callable[[], None]] = []
        self._state: LaifenState | None = None
        self._firmware_version: str | None = None
        self._state_received = asyncio.Event()

    @property
    def address(self) -> str:
        return self._address

    @property
    def name(self) -> str:
        if self._ble_device is not None and self._ble_device.name:
            return self._ble_device.name
        return self._address

    @property
    def connected(self) -> bool:
        return self._client is not None and self._client.is_connected

    @property
    def state(self) -> LaifenState | None:
        return self._state

    @property
    def firmware_version(self) -> str | None:
        return self._firmware_version

    def set_ble_device(self, ble_device: BLEDevice) -> None:
        self._ble_device = ble_device

    def register_callback(self, callback: Callable[[], None]) -> Callable[[], None]:
        self._callbacks.append(callback)

        def unregister() -> None:
            self._callbacks.remove(callback)

        return unregister

    async def connect(self) -> None:
        async with self._connect_lock:
            if self.connected:
                return
            ble_device = self._ble_device
            if ble_device is None:
                raise LaifenConnectionError(f"{self.name} has not been seen yet")
            try:
                client = await establish_connection(
                    BleakClientWithServiceCache,
                    ble_device,
                    self.name,
                    disconnected_callback=self._handle_disconnect,
                    ble_device_callback=lambda: self._ble_device or ble_device,
                )
            except (BleakError, TimeoutError) as err:
                raise LaifenConnectionError(
                    f"Failed to connect to {self.name}: {err}"
                ) from err
            self._client = client
            self._state_received.clear()
            try:
                await client.start_notify(
                    CHARACTERISTIC_UUID, self._handle_notification
                )
                await client.write_gatt_char(
                    CHARACTERISTIC_UUID, READ_STATE, response=True
                )
                async with asyncio.timeout(STATE_TIMEOUT):
                    await self._state_received.wait()
            except (BleakError, TimeoutError) as err:
                await self._disconnect_client()
                raise LaifenConnectionError(
                    f"Failed to read the state of {self.name}: {err}"
                ) from err
        self._notify_callbacks()

    async def disconnect(self) -> None:
        async with self._connect_lock:
            await self._disconnect_client()

    async def _disconnect_client(self) -> None:
        client = self._client
        self._client = None
        if client is None:
            return
        try:
            await client.disconnect()
        except BleakError as err:
            _LOGGER.debug("%s: Error while disconnecting: %s", self.name, err)

    def _handle_disconnect(self, client: BleakClientWithServiceCache) -> None:
        if client is not self._client:
            return
        _LOGGER.debug("%s: Disconnected", self.name)
        self._client = None
        self._notify_callbacks()

    def _handle_notification(
        self, _characteristic: BleakGATTCharacteristic, data: bytearray
    ) -> None:
        try:
            frame = decode_frame(bytes(data))
        except InvalidFrameError as err:
            _LOGGER.debug("%s: Ignoring frame: %s", self.name, err)
            return
        if frame.kind != Kind.REPORT:
            return
        if frame.command == Command.STATE:
            try:
                self._state = decode_state(frame.payload)
            except InvalidFrameError as err:
                _LOGGER.debug("%s: Ignoring state: %s", self.name, err)
                return
            self._state_received.set()
        elif frame.command == Command.VERSION:
            self._firmware_version = decode_version(frame.payload)
        else:
            return
        self._notify_callbacks()

    def _notify_callbacks(self) -> None:
        for callback in list(self._callbacks):
            callback()
