from __future__ import annotations

from dataclasses import dataclass, field
import duckdb

from snapdiff.config import Config
from snapdiff.findings import Finding, Severity
from snapdiff.loader import ColumnMeta, quote_ident


@dataclass
class SchemaColumnDiff:
    name: str
    status: str  # "added", "removed", "type_changed", "unchanged"
    base_type: str | None = None
    curr_type: str | None = None
    severity: Severity = Severity.INFO


@dataclass
class SchemaDiffResult:
    base_row_count: int
    curr_row_count: int
    columns: list[SchemaColumnDiff] = field(default_factory=list)
    order_changed: bool = False
    findings: list[Finding] = field(default_factory=list)

    @property
    def row_count_delta_abs(self) -> int:
        return self.curr_row_count - self.base_row_count

    @property
    def row_count_delta_pct(self) -> float:
        if self.base_row_count == 0:
            return 0.0 if self.curr_row_count == 0 else 1.0
        return (self.curr_row_count - self.base_row_count) / self.base_row_count


def check_schema(
    con: duckdb.DuckDBPyConnection,
    base_view: str,
    curr_view: str,
    config: Config,
) -> SchemaDiffResult:
    """Compare schemas and row counts between base and curr views using SQL."""
    # 1. Fetch column metadata via DESCRIBE in DuckDB
    base_cols_raw = con.execute(f"DESCRIBE {quote_ident(base_view)}").fetchall()
    curr_cols_raw = con.execute(f"DESCRIBE {quote_ident(curr_view)}").fetchall()

    base_cols: dict[str, str] = {str(row[0]): str(row[1]) for row in base_cols_raw}
    curr_cols: dict[str, str] = {str(row[0]): str(row[1]) for row in curr_cols_raw}

    base_order = [str(row[0]) for row in base_cols_raw]
    curr_order = [str(row[0]) for row in curr_cols_raw]

    # Row counts
    base_count = int(con.execute(f"SELECT COUNT(*) FROM {quote_ident(base_view)}").fetchone()[0])  # type: ignore
    curr_count = int(con.execute(f"SELECT COUNT(*) FROM {quote_ident(curr_view)}").fetchone()[0])  # type: ignore

    findings: list[Finding] = []
    column_diffs: list[SchemaColumnDiff] = []

    # Row count checks
    if base_count > 0 and curr_count < base_count:
        drop_ratio = (base_count - curr_count) / base_count
        if drop_ratio >= config.thresholds.row_count_drop_break:
            findings.append(
                Finding(
                    check="schema",
                    column=None,
                    severity=Severity.BREAKING,
                    detail=(
                        f"Row count dropped by {drop_ratio * 100:.1f}% "
                        f"({base_count:,} -> {curr_count:,}), exceeding threshold "
                        f"{config.thresholds.row_count_drop_break * 100:.1f}%"
                    ),
                    value=drop_ratio,
                )
            )
        elif drop_ratio >= 0.10:
            findings.append(
                Finding(
                    check="schema",
                    column=None,
                    severity=Severity.WARNING,
                    detail=(
                        f"Row count dropped by {drop_ratio * 100:.1f}% "
                        f"({base_count:,} -> {curr_count:,})"
                    ),
                    value=drop_ratio,
                )
            )

    all_names = list(dict.fromkeys(base_order + curr_order))
    shared_base_order = [c for c in base_order if c in curr_cols]
    shared_curr_order = [c for c in curr_order if c in base_cols]
    order_changed = shared_base_order != shared_curr_order

    if order_changed:
        findings.append(
            Finding(
                check="schema",
                column=None,
                severity=Severity.INFO,
                detail=f"Shared columns order changed: {shared_base_order} -> {shared_curr_order}",
            )
        )

    for name in all_names:
        in_base = name in base_cols
        in_curr = name in curr_cols

        if in_base and not in_curr:
            column_diffs.append(
                SchemaColumnDiff(
                    name=name,
                    status="removed",
                    base_type=base_cols[name],
                    curr_type=None,
                    severity=Severity.BREAKING,
                )
            )
            findings.append(
                Finding(
                    check="schema",
                    column=name,
                    severity=Severity.BREAKING,
                    detail=f"Column removed: {name} (was {base_cols[name]})",
                )
            )
        elif not in_base and in_curr:
            column_diffs.append(
                SchemaColumnDiff(
                    name=name,
                    status="added",
                    base_type=None,
                    curr_type=curr_cols[name],
                    severity=Severity.INFO,
                )
            )
            findings.append(
                Finding(
                    check="schema",
                    column=name,
                    severity=Severity.INFO,
                    detail=f"Column added: {name} ({curr_cols[name]})",
                )
            )
        else:
            b_type = base_cols[name]
            c_type = curr_cols[name]
            # Normalize comparison
            if b_type.strip().upper() != c_type.strip().upper():
                is_safe = config.is_safe_widening(b_type, c_type)
                severity = Severity.WARNING if is_safe else Severity.BREAKING
                column_diffs.append(
                    SchemaColumnDiff(
                        name=name,
                        status="type_changed",
                        base_type=b_type,
                        curr_type=c_type,
                        severity=severity,
                    )
                )
                label = "Safe type widening" if is_safe else "Breaking type change"
                findings.append(
                    Finding(
                        check="schema",
                        column=name,
                        severity=severity,
                        detail=f"{label} for column {name}: {b_type} -> {c_type}",
                    )
                )
            else:
                column_diffs.append(
                    SchemaColumnDiff(
                        name=name,
                        status="unchanged",
                        base_type=b_type,
                        curr_type=c_type,
                        severity=Severity.INFO,
                    )
                )

    return SchemaDiffResult(
        base_row_count=base_count,
        curr_row_count=curr_count,
        columns=column_diffs,
        order_changed=order_changed,
        findings=findings,
    )
