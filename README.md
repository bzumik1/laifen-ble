# laifen-ble

Async Bluetooth LE library for the **Laifen Wave** electric toothbrush (`LFTB01-P`).

It reads the toothbrush state (active mode, per-mode settings, battery level, brushing time,
running state and feature flags), tracks brushing sessions, and controls the toothbrush: mode and
per-mode strength, range and speed, brushing time, 30-second reminder, high frequency, and
starting or stopping the motor. It reads the firmware version when its format is known. It is
built on [bleak](https://github.com/hbldh/bleak) and
[bleak-retry-connector](https://github.com/Bluetooth-Devices/bleak-retry-connector) and is
intended to be used by the Home Assistant `laifen` integration.

## Supported devices

| Model | Advertised name | Status |
|---|---|---|
| Laifen Wave (tested with firmware V2.0.93) | `LFTB01-P-xxxx` | Supported |
| Other Laifen models | other names | Not supported |

## Usage

```python
import asyncio

from bleak import BleakScanner

from laifen_ble import LaifenToothbrush, Mode, is_supported


async def main() -> None:
    device = await BleakScanner.find_device_by_filter(
        lambda device, advertisement: is_supported(advertisement.local_name)
    )
    toothbrush = LaifenToothbrush(device)
    toothbrush.register_callback(lambda: print(toothbrush.state))
    await toothbrush.connect()
    await toothbrush.set_mode(Mode.MODE_2)
    await toothbrush.set_brushing_time(150)
    await asyncio.sleep(30)
    print(toothbrush.last_session)
    await toothbrush.disconnect()


asyncio.run(main())
```

The toothbrush only advertises while it is awake (after a button press) and disconnects on its
own about a minute after the last activity. While connected it pushes its state on every change,
so registered callbacks are called whenever the state, the firmware version or the connection
changes. Every command waits for the acknowledgement of the toothbrush and reads the state back.
The battery level is not pushed, so call `update()` to read the state again, for example while
the toothbrush stays awake on its charger.

`state.battery_level` is `None` when the toothbrush reports a value above 100.

A brushing session is a period in which the motor runs for at least five seconds; the short
vibrations that confirm button presses are ignored. `last_session` holds the start time and
duration of the last completed session. Sessions are measured while the toothbrush is connected:

- A session that was already running when the toothbrush was connected starts at the moment of
  the connection.
- When the connection drops while the motor runs and the toothbrush is connected again within a
  minute while it still runs, the session continues. If it would then last longer than the
  longest brushing time, the toothbrush must have been stopped and started again in between, and
  the session starts at the moment of the new connection.
- When the toothbrush is connected again later, only the rest of the session is recorded. When
  it has stopped by then, the interrupted session is not recorded.

## Errors

- `LaifenConnectionError`: the toothbrush could not be reached, the connection dropped, or it
  did not answer in time.
- `UnsupportedStateError`: raised by `connect()` when the toothbrush reports its state in a
  layout this library does not know.
- `LaifenValueError`: a value is outside what the toothbrush accepts in its current state.
- `LaifenCommandError`: the toothbrush refused a command. `LaifenMotorError.reason` tells why it
  refused to start or stop the motor, for example because it is charging.

All of them derive from `LaifenError`.

## Protocol

The protocol was documented by
[marc-alexis-com/laifen-toolbox](https://github.com/marc-alexis-com/laifen-toolbox/blob/main/docs/PROTOCOL.md)
and earlier explored by [UrbanTechIO/Laifen](https://github.com/UrbanTechIO/Laifen). This
library only sends commands that were confirmed on a real toothbrush. Airplane mode, head
calibration, factory reset, emergency shutdown and reading the brushing history are
intentionally not implemented.

## Development

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e '.[test,lint]'
pytest
ruff check . && ruff format --check . && mypy src tests
```

## License

MIT
