from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
import logging
from time import monotonic

from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from .const import (
    CHARACTERISTIC_UUID,
    COMMAND_TIMEOUT,
    MIN_SESSION_DURATION,
    STATE_TIMEOUT,
)
from .exceptions import (
    InvalidFrameError,
    LaifenCommandError,
    LaifenConnectionError,
    LaifenMotorError,
)
from .models import BrushingSession, LaifenState, Mode, ModeSettings
from .protocol import (
    ACCEPTED,
    READ_STATE,
    Command,
    Frame,
    Kind,
    decode_acknowledgement,
    decode_frame,
    decode_state,
    decode_version,
    encode_brushing_time,
    encode_mode_settings,
    encode_switch,
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
        self._command_lock = asyncio.Lock()
        self._callbacks: list[Callable[[], None]] = []
        self._state: LaifenState | None = None
        self._firmware_version: str | None = None
        self._firmware_version_raw: bytes | None = None
        self._state_received = asyncio.Event()
        self._acknowledgements: dict[int, asyncio.Future[int]] = {}
        self._session_start: tuple[float, datetime] | None = None
        self._last_session: BrushingSession | None = None

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

    @property
    def firmware_version_raw(self) -> bytes | None:
        return self._firmware_version_raw

    @property
    def last_session(self) -> BrushingSession | None:
        return self._last_session

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
            client = await self._establish_connection(ble_device, True)
            if client.services.get_characteristic(CHARACTERISTIC_UUID) is None:
                _LOGGER.debug("%s: Cached services are stale, discovering", self.name)
                await client.clear_cache()
                await self._disconnect(client)
                client = await self._establish_connection(ble_device, False)
                if client.services.get_characteristic(CHARACTERISTIC_UUID) is None:
                    await self._disconnect(client)
                    raise LaifenConnectionError(
                        f"{self.name} does not offer {CHARACTERISTIC_UUID}"
                    )
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

    async def _establish_connection(
        self, ble_device: BLEDevice, use_services_cache: bool
    ) -> BleakClientWithServiceCache:
        try:
            return await establish_connection(
                BleakClientWithServiceCache,
                ble_device,
                self.name,
                disconnected_callback=self._handle_disconnect,
                ble_device_callback=lambda: self._ble_device or ble_device,
                use_services_cache=use_services_cache,
            )
        except (BleakError, TimeoutError) as err:
            raise LaifenConnectionError(
                f"Failed to connect to {self.name}: {err}"
            ) from err

    async def set_mode(self, mode: Mode) -> None:
        state = self._connected_state()
        self._check_mode_available(state, mode)
        await self._send_command(encode_mode_settings(mode, state.mode_settings[mode]))

    async def set_mode_settings(self, mode: Mode, settings: ModeSettings) -> None:
        state = self._connected_state()
        self._check_mode_available(state, mode)
        await self._send_command(encode_mode_settings(mode, settings))
        if mode is not state.mode:
            current = self._connected_state()
            await self._send_command(
                encode_mode_settings(state.mode, current.mode_settings[state.mode])
            )

    async def set_brushing_time(self, seconds: int) -> None:
        await self._send_command(encode_brushing_time(seconds))

    async def set_reminder_30s(self, enabled: bool) -> None:
        await self._send_command(encode_switch(Command.REMINDER_30S, enabled))

    async def set_high_frequency(self, enabled: bool) -> None:
        state = self._connected_state()
        if not enabled and state.mode is Mode.HIGH_FREQUENCY:
            await self._send_command(
                encode_mode_settings(Mode.MODE_3, state.mode_settings[Mode.MODE_3])
            )
        await self._send_command(encode_switch(Command.HIGH_FREQUENCY, enabled))

    async def start(self) -> None:
        await self._send_motor_command(True)

    async def stop(self) -> None:
        await self._send_motor_command(False)

    async def _send_motor_command(self, running: bool) -> None:
        try:
            await self._send_command(encode_switch(Command.MOTOR, running))
        except LaifenCommandError as err:
            raise LaifenMotorError(err.command, err.status) from err

    def _connected_state(self) -> LaifenState:
        if not self.connected or self._state is None:
            raise LaifenConnectionError(f"{self.name} is not connected")
        return self._state

    @staticmethod
    def _check_mode_available(state: LaifenState, mode: Mode) -> None:
        if mode is Mode.HIGH_FREQUENCY and not state.high_frequency:
            raise ValueError("High frequency must be turned on first")

    async def update(self) -> None:
        async with self._command_lock:
            client = self._connected_client()
            try:
                await self._read_state(client)
            except (BleakError, TimeoutError) as err:
                raise LaifenConnectionError(
                    f"Failed to read the state of {self.name}: {err}"
                ) from err

    def _connected_client(self) -> BleakClientWithServiceCache:
        client = self._client
        if client is None or not client.is_connected:
            raise LaifenConnectionError(f"{self.name} is not connected")
        return client

    async def _read_state(self, client: BleakClientWithServiceCache) -> None:
        self._state_received.clear()
        await client.write_gatt_char(CHARACTERISTIC_UUID, READ_STATE, response=True)
        async with asyncio.timeout(COMMAND_TIMEOUT):
            await self._state_received.wait()

    async def _send_command(self, frame: bytes) -> None:
        command = frame[1]
        async with self._command_lock:
            client = self._connected_client()
            acknowledgement = asyncio.get_running_loop().create_future()
            self._acknowledgements[command] = acknowledgement
            try:
                await client.write_gatt_char(CHARACTERISTIC_UUID, frame, response=True)
                async with asyncio.timeout(COMMAND_TIMEOUT):
                    status = await acknowledgement
                if status != ACCEPTED:
                    raise LaifenCommandError(command, status)
                await self._read_state(client)
            except (BleakError, TimeoutError) as err:
                raise LaifenConnectionError(
                    f"Failed to send a command to {self.name}: {err}"
                ) from err
            finally:
                self._acknowledgements.pop(command, None)

    async def _disconnect_client(self) -> None:
        client = self._client
        self._client = None
        self._session_start = None
        if client is not None:
            await self._disconnect(client)

    async def _disconnect(self, client: BleakClientWithServiceCache) -> None:
        try:
            await client.disconnect()
        except BleakError as err:
            _LOGGER.debug("%s: Error while disconnecting: %s", self.name, err)

    def _handle_disconnect(self, client: BleakClientWithServiceCache) -> None:
        if client is not self._client:
            return
        _LOGGER.debug("%s: Disconnected", self.name)
        self._client = None
        self._session_start = None
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
                state = decode_state(frame.payload)
            except InvalidFrameError as err:
                _LOGGER.debug("%s: Ignoring state: %s", self.name, err)
                return
            self._track_session(state.running)
            self._state = state
            self._state_received.set()
        elif frame.command == Command.VERSION:
            self._firmware_version_raw = frame.payload
            self._firmware_version = decode_version(frame.payload)
        else:
            self._handle_acknowledgement(frame)
            return
        self._notify_callbacks()

    def _handle_acknowledgement(self, frame: Frame) -> None:
        acknowledgement = self._acknowledgements.get(frame.command)
        if acknowledgement is None or acknowledgement.done():
            return
        try:
            acknowledgement.set_result(decode_acknowledgement(frame.payload))
        except InvalidFrameError as err:
            _LOGGER.debug("%s: Ignoring acknowledgement: %s", self.name, err)

    def _track_session(self, running: bool) -> None:
        if running:
            if self._session_start is None:
                self._session_start = (monotonic(), datetime.now(UTC))
            return
        if self._session_start is None:
            return
        started, started_at = self._session_start
        self._session_start = None
        duration = monotonic() - started
        if duration >= MIN_SESSION_DURATION:
            self._last_session = BrushingSession(
                started_at=started_at, duration=round(duration)
            )

    def _notify_callbacks(self) -> None:
        for callback in list(self._callbacks):
            callback()
