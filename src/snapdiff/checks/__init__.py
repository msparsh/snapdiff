from __future__ import annotations

from snapdiff.checks.drift import DriftCheckResult, check_drift
from snapdiff.checks.duplicates import DuplicateDiffResult, check_duplicates
from snapdiff.checks.nulls import NullCheckResult, check_nulls
from snapdiff.checks.schema import SchemaDiffResult, check_schema

__all__ = [
    "check_schema",
    "SchemaDiffResult",
    "check_nulls",
    "NullCheckResult",
    "check_duplicates",
    "DuplicateDiffResult",
    "check_drift",
    "DriftCheckResult",
]
