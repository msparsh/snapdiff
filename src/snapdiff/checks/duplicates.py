from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
import duckdb

from snapdiff.config import Config
from snapdiff.findings import Finding, Severity
from snapdiff.loader import quote_ident


@dataclass
class TopDuplicate:
    key_values: dict[str, Any]
    count: int

    def __str__(self) -> str:
        vals = ", ".join(f"{k}={v}" for k, v in self.key_values.items())
        return f"({vals}) × {self.count}"


@dataclass
class DuplicateDiffResult:
    keys: list[str]
    is_key_based: bool
    base_duplicate_count: int
    base_total_count: int
    base_duplicate_rate: float
    curr_duplicate_count: int
    curr_total_count: int
    curr_duplicate_rate: float
    growth_rate_pts: float  # (curr_rate - base_rate) * 100
    top_duplicates_curr: list[TopDuplicate] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)


def check_duplicates(
    con: duckdb.DuckDBPyConnection,
    base_view: str,
    curr_view: str,
    columns: list[str],
    config: Config,
) -> DuplicateDiffResult:
    """Check for duplicate rows or duplicate keys and analyze duplicate growth."""
    # Determine columns to check
    configured_keys = [k for k in config.keys if k in columns]
    is_key_based = len(configured_keys) > 0
    check_cols = configured_keys if is_key_based else columns

    if not check_cols:
        return DuplicateDiffResult(
            keys=[],
            is_key_based=False,
            base_duplicate_count=0,
            base_total_count=0,
            base_duplicate_rate=0.0,
            curr_duplicate_count=0,
            curr_total_count=0,
            curr_duplicate_rate=0.0,
            growth_rate_pts=0.0,
        )

    quoted_cols = ", ".join(quote_ident(c) for c in check_cols)

    dup_calc_sql = f"""
    WITH grouped AS (
        SELECT {quoted_cols}, COUNT(*) AS __c
        FROM {{view}}
        GROUP BY {quoted_cols}
    )
    SELECT
        COALESCE(SUM(__c), 0) AS total_rows,
        COALESCE(SUM(__c - 1), 0) AS duplicate_rows
    FROM grouped
    """

    base_stats = con.execute(dup_calc_sql.format(view=quote_ident(base_view))).fetchone()
    curr_stats = con.execute(dup_calc_sql.format(view=quote_ident(curr_view))).fetchone()

    base_total = int(base_stats[0]) if base_stats else 0
    base_dups = int(base_stats[1]) if base_stats else 0
    curr_total = int(curr_stats[0]) if curr_stats else 0
    curr_dups = int(curr_stats[1]) if curr_stats else 0

    base_rate = (base_dups / base_total) if base_total > 0 else 0.0
    curr_rate = (curr_dups / curr_total) if curr_total > 0 else 0.0
    growth_fraction = curr_rate - base_rate
    growth_pts = growth_fraction * 100.0

    # Query top 5 duplicates in curr
    top_duplicates: list[TopDuplicate] = []
    if curr_dups > 0:
        top_sql = f"""
        SELECT {quoted_cols}, COUNT(*) AS __dup_count
        FROM {quote_ident(curr_view)}
        GROUP BY {quoted_cols}
        HAVING COUNT(*) > 1
        ORDER BY __dup_count DESC
        LIMIT 5
        """
        rows = con.execute(top_sql).fetchall()
        for row in rows:
            kv = {col: row[i] for i, col in enumerate(check_cols)}
            cnt = int(row[len(check_cols)])
            top_duplicates.append(TopDuplicate(key_values=kv, count=cnt))

    findings: list[Finding] = []
    key_str = f"keys {configured_keys}" if is_key_based else "full-row duplicates"

    # Evaluation rules
    if is_key_based:
        if curr_dups > 0 and base_dups == 0:
            findings.append(
                Finding(
                    check="duplicates",
                    column=", ".join(configured_keys),
                    severity=Severity.BREAKING,
                    detail=(
                        f"Declared key(s) {configured_keys} became non-unique: "
                        f"{curr_dups:,} duplicate rows ({curr_rate * 100:.2f}%) found in current"
                    ),
                    value=curr_rate,
                )
            )
        elif curr_dups > 0 and growth_fraction >= config.thresholds.duplicate_growth_break:
            findings.append(
                Finding(
                    check="duplicates",
                    column=", ".join(configured_keys),
                    severity=Severity.BREAKING,
                    detail=(
                        f"Duplicate rate on declared key(s) grew by {growth_pts:.2f} pts "
                        f"({base_rate * 100:.2f}% -> {curr_rate * 100:.2f}%)"
                    ),
                    value=growth_fraction,
                )
            )
        elif curr_dups > 0 and growth_fraction >= config.thresholds.duplicate_growth_warn:
            findings.append(
                Finding(
                    check="duplicates",
                    column=", ".join(configured_keys),
                    severity=Severity.WARNING,
                    detail=(
                        f"Duplicate rate on declared key(s) grew by {growth_pts:.2f} pts "
                        f"({base_rate * 100:.2f}% -> {curr_rate * 100:.2f}%)"
                    ),
                    value=growth_fraction,
                )
            )
    else:
        # Full-row duplicates
        if growth_fraction >= config.thresholds.duplicate_growth_break:
            findings.append(
                Finding(
                    check="duplicates",
                    column=None,
                    severity=Severity.BREAKING,
                    detail=(
                        f"Full-row duplicate rate grew by {growth_pts:.2f} pts "
                        f"({base_rate * 100:.2f}% -> {curr_rate * 100:.2f}%), "
                        f"exceeding threshold {config.thresholds.duplicate_growth_break * 100:.1f} pts"
                    ),
                    value=growth_fraction,
                )
            )
        elif growth_fraction >= config.thresholds.duplicate_growth_warn:
            findings.append(
                Finding(
                    check="duplicates",
                    column=None,
                    severity=Severity.WARNING,
                    detail=(
                        f"Full-row duplicate rate grew by {growth_pts:.2f} pts "
                        f"({base_rate * 100:.2f}% -> {curr_rate * 100:.2f}%)"
                    ),
                    value=growth_fraction,
                )
            )

    return DuplicateDiffResult(
        keys=configured_keys,
        is_key_based=is_key_based,
        base_duplicate_count=base_dups,
        base_total_count=base_total,
        base_duplicate_rate=base_rate,
        curr_duplicate_count=curr_dups,
        curr_total_count=curr_total,
        curr_duplicate_rate=curr_rate,
        growth_rate_pts=growth_pts,
        top_duplicates_curr=top_duplicates,
        findings=findings,
    )
