from __future__ import annotations

from .models import MotorStatus


class LaifenError(Exception):
    pass


class LaifenConnectionError(LaifenError):
    pass


class LaifenCommandError(LaifenError):
    def __init__(self, command: int, status: int) -> None:
        super().__init__(f"Command 0x{command:02x} was refused with status {status}")
        self.command = command
        self.status = status


class LaifenMotorError(LaifenCommandError):
    @property
    def reason(self) -> MotorStatus | None:
        try:
            return MotorStatus(self.status)
        except ValueError:
            return None


class InvalidFrameError(LaifenError):
    pass


class UnsupportedStateError(InvalidFrameError):
    pass
