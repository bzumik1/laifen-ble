# laifen-ble

Async Bluetooth LE library for the **Laifen Wave** electric toothbrush (`LFTB01-P`).

It reads the toothbrush state (active mode, per-mode settings, battery level, brushing time,
running state and feature flags) and its firmware version. It is built on
[bleak](https://github.com/hbldh/bleak) and
[bleak-retry-connector](https://github.com/Bluetooth-Devices/bleak-retry-connector) and is
intended to be used by the Home Assistant `laifen` integration.

## Supported devices

| Model | Advertised name | Status |
|---|---|---|
| Laifen Wave (plastic, stainless steel and aluminium shells) | `LFTB01-P-xxxx` | Supported |
| Laifen Wave SE, Wave Pro and other models | `LFTB01-SE-…`, `LFTB02-…` | Not supported (different protocol) |

## Usage

```python
import asyncio

from bleak import BleakScanner

from laifen_ble import LaifenToothbrush, is_supported


async def main() -> None:
    device = await BleakScanner.find_device_by_filter(
        lambda device, advertisement: is_supported(advertisement.local_name)
    )
    toothbrush = LaifenToothbrush(device)
    toothbrush.register_callback(lambda: print(toothbrush.state))
    await toothbrush.connect()
    print(toothbrush.firmware_version, toothbrush.state)
    await asyncio.sleep(30)
    await toothbrush.disconnect()


asyncio.run(main())
```

The toothbrush only advertises while it is awake (after a button press) and disconnects on its
own about a minute after the last activity. While connected it pushes its state on every change,
so registered callbacks are called whenever the state, the firmware version or the connection
changes.

## Protocol

The protocol was documented by
[marc-alexis-com/laifen-toolbox](https://github.com/marc-alexis-com/laifen-toolbox/blob/main/docs/PROTOCOL.md)
and earlier explored by [UrbanTechIO/Laifen](https://github.com/UrbanTechIO/Laifen). This
library only sends the documented read requests.

## Development

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e '.[test,lint]'
pytest
ruff check . && ruff format --check . && mypy src
```

## License

MIT
