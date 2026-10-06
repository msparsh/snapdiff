from __future__ import annotations

from snapdiff.config import ColumnConfig, Config, Thresholds, load_config
from snapdiff.diff import SnapDiff, compare
from snapdiff.findings import Finding, Severity
from snapdiff.report import DiffReport

__version__ = "0.1.0"

__all__ = [
    "SnapDiff",
    "compare",
    "Config",
    "Thresholds",
    "ColumnConfig",
    "load_config",
    "Finding",
    "Severity",
    "DiffReport",
    "__version__",
]
