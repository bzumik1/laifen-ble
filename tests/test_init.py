from __future__ import annotations

import pytest

from laifen_ble import is_supported


@pytest.mark.parametrize(
    ("local_name", "expected"),
    [
        ("LFTB01-P-A220", True),
        ("LFTB01-SE-1234", False),
        ("LFTB02-S-412B", False),
        (None, False),
    ],
)
def test_is_supported(local_name: str | None, expected: bool) -> None:
    assert is_supported(local_name) is expected
