from __future__ import annotations


class LaifenError(Exception):
    pass


class LaifenConnectionError(LaifenError):
    pass


class InvalidFrameError(LaifenError):
    pass


class UnsupportedStateError(InvalidFrameError):
    pass
