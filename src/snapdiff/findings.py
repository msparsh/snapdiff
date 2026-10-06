from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any


class Severity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    BREAKING = "BREAKING"

    @property
    def level(self) -> int:
        levels = {
            Severity.INFO: 1,
            Severity.WARNING: 2,
            Severity.BREAKING: 3,
        }
        return levels[self]

    def __ge__(self, other: object) -> bool:
        if isinstance(other, Severity):
            return self.level >= other.level
        return NotImplemented

    def __gt__(self, other: object) -> bool:
        if isinstance(other, Severity):
            return self.level > other.level
        return NotImplemented

    def __le__(self, other: object) -> bool:
        if isinstance(other, Severity):
            return self.level <= other.level
        return NotImplemented

    def __lt__(self, other: object) -> bool:
        if isinstance(other, Severity):
            return self.level < other.level
        return NotImplemented


@dataclass(frozen=True)
class Finding:
    check: str
    column: str | None
    severity: Severity
    detail: str
    value: float | str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["severity"] = self.severity.value
        return d
