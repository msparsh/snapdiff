from __future__ import annotations

from dataclasses import dataclass, field
import duckdb

from snapdiff.config import Config
from snapdiff.findings import Finding, Severity
from snapdiff.loader import quote_ident


@dataclass
class NullColumnDiff:
    name: str
    base_null_count: int
    base_total_count: int
    base_null_rate: float
    curr_null_count: int
    curr_total_count: int
    curr_null_rate: float
    delta_pts: float  # (curr_null_rate - base_null_rate) * 100
    severity: Severity = Severity.INFO


@dataclass
class NullCheckResult:
    columns: list[NullColumnDiff] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)


def check_nulls(
    con: duckdb.DuckDBPyConnection,
    base_view: str,
    curr_view: str,
    shared_columns: list[str],
    config: Config,
) -> NullCheckResult:
    """Compute null rates for shared columns in one SQL pass per table and detect shifts."""
    if not shared_columns:
        return NullCheckResult()

    # Build SQL to get total count and count of non-nulls for each column in one query
    select_items = ["COUNT(*) AS __snapdiff_total"]
    for col in shared_columns:
        select_items.append(f"COUNT({quote_ident(col)})")

    query_cols = ", ".join(select_items)

    base_sql = f"SELECT {query_cols} FROM {quote_ident(base_view)}"
    curr_sql = f"SELECT {query_cols} FROM {quote_ident(curr_view)}"

    base_row = con.execute(base_sql).fetchone()
    curr_row = con.execute(curr_sql).fetchone()

    if not base_row or not curr_row:
        return NullCheckResult()

    base_total = int(base_row[0])
    curr_total = int(curr_row[0])

    diffs: list[NullColumnDiff] = []
    findings: list[Finding] = []

    for idx, col in enumerate(shared_columns, start=1):
        if config.is_column_ignored(col):
            continue

        base_non_nulls = int(base_row[idx])
        curr_non_nulls = int(curr_row[idx])

        base_null_cnt = base_total - base_non_nulls
        curr_null_cnt = curr_total - curr_non_nulls

        base_rate = (base_null_cnt / base_total) if base_total > 0 else 0.0
        curr_rate = (curr_null_cnt / curr_total) if curr_total > 0 else 0.0

        delta_fraction = curr_rate - base_rate
        delta_pts = delta_fraction * 100.0

        warn_th, break_th = config.get_null_thresholds(col)

        severity = Severity.INFO
        is_breaking_not_null = False

        # Check not_null_columns rule: previously 0% null gains any nulls
        if col in config.not_null_columns:
            if base_null_cnt == 0 and curr_null_cnt > 0:
                is_breaking_not_null = True
                severity = Severity.BREAKING
                findings.append(
                    Finding(
                        check="nulls",
                        column=col,
                        severity=Severity.BREAKING,
                        detail=(
                            f"Column '{col}' is configured not-null but gained {curr_null_cnt:,} "
                            f"nulls (0.0% -> {curr_rate * 100:.2f}%)"
                        ),
                        value=curr_rate,
                    )
                )

        if not is_breaking_not_null:
            if delta_fraction >= break_th:
                severity = Severity.BREAKING
                findings.append(
                    Finding(
                        check="nulls",
                        column=col,
                        severity=Severity.BREAKING,
                        detail=(
                            f"Null rate for '{col}' increased by {delta_pts:.2f} pts "
                            f"({base_rate * 100:.2f}% -> {curr_rate * 100:.2f}%), "
                            f"exceeding breaking threshold {break_th * 100:.1f} pts"
                        ),
                        value=delta_fraction,
                    )
                )
            elif delta_fraction >= warn_th:
                severity = Severity.WARNING
                findings.append(
                    Finding(
                        check="nulls",
                        column=col,
                        severity=Severity.WARNING,
                        detail=(
                            f"Null rate for '{col}' increased by {delta_pts:.2f} pts "
                            f"({base_rate * 100:.2f}% -> {curr_rate * 100:.2f}%)"
                        ),
                        value=delta_fraction,
                    )
                )

        diffs.append(
            NullColumnDiff(
                name=col,
                base_null_count=base_null_cnt,
                base_total_count=base_total,
                base_null_rate=base_rate,
                curr_null_count=curr_null_cnt,
                curr_total_count=curr_total,
                curr_null_rate=curr_rate,
                delta_pts=delta_pts,
                severity=severity,
            )
        )

    # Sort diffs by delta_pts descending
    diffs.sort(key=lambda d: d.delta_pts, reverse=True)

    return NullCheckResult(columns=diffs, findings=findings)
