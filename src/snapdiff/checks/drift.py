from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any
import duckdb

from snapdiff.config import Config
from snapdiff.findings import Finding, Severity
from snapdiff.loader import ColumnMeta, quote_ident, quote_literal


@dataclass
class BucketShare:
    label: str
    base_count: int
    base_pct: float
    curr_count: int
    curr_pct: float


@dataclass
class ColumnDriftResult:
    name: str
    column_type: str
    is_numeric: bool
    psi: float = 0.0
    ks_stat: float | None = None
    ks_pvalue: float | None = None
    severity: Severity = Severity.INFO
    buckets: list[BucketShare] = field(default_factory=list)
    skipped: bool = False
    skip_reason: str | None = None

    @property
    def max_drift_score(self) -> float:
        ks = self.ks_stat if self.ks_stat is not None else 0.0
        return max(self.psi, ks)


@dataclass
class DriftCheckResult:
    columns: list[ColumnDriftResult] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)


EPSILON = 1e-6


def approximate_ks_pvalue(d_stat: float, n1: int, n2: int) -> float:
    """Compute asymptotic Kolmogorov-Smirnov 2-sample p-value approximation."""
    if n1 <= 0 or n2 <= 0 or d_stat <= 0:
        return 1.0
    n_eff = (n1 * n2) / (n1 + n2)
    # Kolmogorov distribution approximation: 2 * exp(-2 * n_eff * D^2)
    exponent = -2.0 * n_eff * (d_stat**2)
    if exponent < -700:  # avoid underflow
        return 0.0
    p = 2.0 * math.exp(exponent)
    return max(0.0, min(1.0, p))


def check_column_drift_numeric(
    con: duckdb.DuckDBPyConnection,
    base_view: str,
    curr_view: str,
    meta: ColumnMeta,
    config: Config,
) -> ColumnDriftResult:
    col = meta.name
    quoted_col = quote_ident(col)
    quoted_base = quote_ident(base_view)
    quoted_curr = quote_ident(curr_view)

    # 1. Non-null counts
    n_sql = f"""
    SELECT
        (SELECT COUNT({quoted_col}) FROM {quoted_base} WHERE {quoted_col} IS NOT NULL) AS b_n,
        (SELECT COUNT({quoted_col}) FROM {quoted_curr} WHERE {quoted_col} IS NOT NULL) AS c_n
    """
    n_row = con.execute(n_sql).fetchone()
    b_n = int(n_row[0]) if n_row else 0
    c_n = int(n_row[1]) if n_row else 0

    if b_n < config.thresholds.min_rows_drift or c_n < config.thresholds.min_rows_drift:
        return ColumnDriftResult(
            name=col,
            column_type=meta.data_type,
            is_numeric=True,
            skipped=True,
            skip_reason=f"Insufficient non-null rows (base={b_n}, curr={c_n}, min={config.thresholds.min_rows_drift})",
        )

    # 2. Decile edges from baseline using quantile_cont
    deciles = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
    quant_sql = f"SELECT quantile_cont({quoted_col}, {deciles}) FROM {quoted_base} WHERE {quoted_col} IS NOT NULL"
    raw_edges = con.execute(quant_sql).fetchone()[0]  # type: ignore

    # Deduplicate and sort edges
    clean_edges: list[float] = sorted(list({float(x) for x in raw_edges if x is not None}))

    # 3. Bucket both snapshots in SQL
    buckets: list[BucketShare] = []
    psi_total = 0.0

    if not clean_edges:
        # Constant or single value in baseline
        buckets.append(
            BucketShare(
                label="all_values",
                base_count=b_n,
                base_pct=100.0,
                curr_count=c_n,
                curr_pct=100.0,
            )
        )
        psi_total = 0.0
    else:
        # Build CASE expression
        # Bucket 0: <= edge_0
        # Bucket i: <= edge_i
        # Bucket last: > edge_last
        k = len(clean_edges)
        case_branches = [f"WHEN {quoted_col} <= {clean_edges[0]} THEN 0"]
        for i in range(1, k):
            case_branches.append(f"WHEN {quoted_col} <= {clean_edges[i]} THEN {i}")
        case_expr = f"CASE {' '.join(case_branches)} ELSE {k} END"

        # Bucket labels
        labels: list[str] = [f"(-inf, {clean_edges[0]:.4g}]"]
        for i in range(1, k):
            labels.append(f"({clean_edges[i-1]:.4g}, {clean_edges[i]:.4g}]")
        labels.append(f"({clean_edges[-1]:.4g}, +inf)")

        num_buckets = k + 1

        bucket_query = f"""
        WITH b_counts AS (
            SELECT {case_expr} AS b_id, COUNT(*) AS cnt
            FROM {quoted_base}
            WHERE {quoted_col} IS NOT NULL
            GROUP BY b_id
        ),
        c_counts AS (
            SELECT {case_expr} AS b_id, COUNT(*) AS cnt
            FROM {quoted_curr}
            WHERE {quoted_col} IS NOT NULL
            GROUP BY b_id
        ),
        all_b AS (
            SELECT unnest(range(0, {num_buckets})) AS b_id
        )
        SELECT
            all_b.b_id,
            COALESCE(b.cnt, 0) AS b_cnt,
            COALESCE(c.cnt, 0) AS c_cnt
        FROM all_b
        LEFT JOIN b_counts b ON all_b.b_id = b.b_id
        LEFT JOIN c_counts c ON all_b.b_id = c.b_id
        ORDER BY all_b.b_id
        """

        rows = con.execute(bucket_query).fetchall()
        for row in rows:
            b_id = int(row[0])
            b_cnt = int(row[1])
            c_cnt = int(row[2])

            e = b_cnt / b_n if b_n > 0 else 0.0
            a = c_cnt / c_n if c_n > 0 else 0.0

            e_clamped = max(e, EPSILON)
            a_clamped = max(a, EPSILON)

            psi_contrib = (a - e) * math.log(a_clamped / e_clamped)
            psi_total += psi_contrib

            lbl = labels[b_id] if b_id < len(labels) else f"bucket_{b_id}"
            buckets.append(
                BucketShare(
                    label=lbl,
                    base_count=b_cnt,
                    base_pct=e * 100.0,
                    curr_count=c_cnt,
                    curr_pct=a * 100.0,
                )
            )

    # 4. KS Statistic in pure SQL
    ks_sql = f"""
    WITH u AS (
        SELECT {quoted_col}::DOUBLE AS x, 1.0 AS a, 0.0 AS b
        FROM {quoted_base}
        WHERE {quoted_col} IS NOT NULL
        UNION ALL
        SELECT {quoted_col}::DOUBLE AS x, 0.0 AS a, 1.0 AS b
        FROM {quoted_curr}
        WHERE {quoted_col} IS NOT NULL
    ),
    agg AS (
        SELECT x, SUM(a) AS sa, SUM(b) AS sb
        FROM u
        GROUP BY x
    ),
    cdf AS (
        SELECT
            x,
            SUM(sa) OVER (ORDER BY x ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) / NULLIF(SUM(sa) OVER (), 0) AS f1,
            SUM(sb) OVER (ORDER BY x ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) / NULLIF(SUM(sb) OVER (), 0) AS f2
        FROM agg
    )
    SELECT COALESCE(MAX(ABS(f1 - f2)), 0.0) AS ks
    FROM cdf
    """
    ks_res = con.execute(ks_sql).fetchone()
    ks_d = float(ks_res[0]) if ks_res and ks_res[0] is not None else 0.0
    ks_p = approximate_ks_pvalue(ks_d, b_n, c_n)

    # Check thresholds
    psi_warn, psi_break = config.get_psi_thresholds(col)
    ks_warn, ks_break = config.get_ks_thresholds(col)

    severity = Severity.INFO
    if psi_total >= psi_break or ks_d >= ks_break:
        severity = Severity.BREAKING
    elif psi_total >= psi_warn or ks_d >= ks_warn:
        severity = Severity.WARNING

    return ColumnDriftResult(
        name=col,
        column_type=meta.data_type,
        is_numeric=True,
        psi=psi_total,
        ks_stat=ks_d,
        ks_pvalue=ks_p,
        severity=severity,
        buckets=buckets,
    )


def check_column_drift_categorical(
    con: duckdb.DuckDBPyConnection,
    base_view: str,
    curr_view: str,
    meta: ColumnMeta,
    config: Config,
) -> ColumnDriftResult:
    col = meta.name
    quoted_col = quote_ident(col)
    quoted_base = quote_ident(base_view)
    quoted_curr = quote_ident(curr_view)

    # Check non-null counts and distinct count in baseline
    card_sql = f"""
    SELECT
        COUNT({quoted_col}) AS b_n,
        COUNT(DISTINCT {quoted_col}) AS b_distinct
    FROM {quoted_base}
    WHERE {quoted_col} IS NOT NULL
    """
    card_row = con.execute(card_sql).fetchone()
    b_n = int(card_row[0]) if card_row else 0
    b_distinct = int(card_row[1]) if card_row else 0

    curr_n_sql = f"SELECT COUNT({quoted_col}) FROM {quoted_curr} WHERE {quoted_col} IS NOT NULL"
    c_n = int(con.execute(curr_n_sql).fetchone()[0])  # type: ignore

    if b_n < config.thresholds.min_rows_drift or c_n < config.thresholds.min_rows_drift:
        return ColumnDriftResult(
            name=col,
            column_type=meta.data_type,
            is_numeric=False,
            skipped=True,
            skip_reason=f"Insufficient non-null rows (base={b_n}, curr={c_n}, min={config.thresholds.min_rows_drift})",
        )

    # Skip high-cardinality string IDs: distinct ratio > 0.9 and distinct > 50
    if b_n > 50 and (b_distinct / b_n) > 0.90:
        return ColumnDriftResult(
            name=col,
            column_type=meta.data_type,
            is_numeric=False,
            skipped=True,
            skip_reason=f"High cardinality identifier ({b_distinct} distinct values in {b_n} rows)",
        )

    top_n = config.thresholds.categorical_top_n
    top_cats_sql = f"""
    SELECT {quoted_col}::VARCHAR AS cat, COUNT(*) AS cnt
    FROM {quoted_base}
    WHERE {quoted_col} IS NOT NULL
    GROUP BY cat
    ORDER BY cnt DESC
    LIMIT {top_n}
    """
    top_cat_rows = con.execute(top_cats_sql).fetchall()
    top_categories = [str(r[0]) for r in top_cat_rows]

    # Build CASE statement
    if top_categories:
        branches = []
        for cat in top_categories:
            branches.append(f"WHEN {quoted_col}::VARCHAR = {quote_literal(cat)} THEN {quote_literal(cat)}")
        case_expr = f"CASE {' '.join(branches)} ELSE '__OTHER__' END"
    else:
        case_expr = "'__OTHER__'"

    all_cats = list(top_categories) + ["__OTHER__"]

    cat_query = f"""
    WITH b_counts AS (
        SELECT {case_expr} AS cat, COUNT(*) AS cnt
        FROM {quoted_base}
        WHERE {quoted_col} IS NOT NULL
        GROUP BY cat
    ),
    c_counts AS (
        SELECT {case_expr} AS cat, COUNT(*) AS cnt
        FROM {quoted_curr}
        WHERE {quoted_col} IS NOT NULL
        GROUP BY cat
    )
    SELECT
        b.cat,
        COALESCE(b.cnt, 0) AS b_cnt,
        COALESCE(c.cnt, 0) AS c_cnt
    FROM b_counts b
    FULL OUTER JOIN c_counts c ON b.cat = c.cat
    """

    rows = con.execute(cat_query).fetchall()
    counts_map: dict[str, tuple[int, int]] = {}
    for r in rows:
        cat_name = str(r[0]) if r[0] is not None else "__OTHER__"
        counts_map[cat_name] = (int(r[1]), int(r[2]))

    buckets: list[BucketShare] = []
    psi_total = 0.0

    for cat in all_cats:
        b_cnt, c_cnt = counts_map.get(cat, (0, 0))
        e = b_cnt / b_n if b_n > 0 else 0.0
        a = c_cnt / c_n if c_n > 0 else 0.0

        e_clamped = max(e, EPSILON)
        a_clamped = max(a, EPSILON)

        psi_contrib = (a - e) * math.log(a_clamped / e_clamped)
        psi_total += psi_contrib

        buckets.append(
            BucketShare(
                label=cat,
                base_count=b_cnt,
                base_pct=e * 100.0,
                curr_count=c_cnt,
                curr_pct=a * 100.0,
            )
        )

    psi_warn, psi_break = config.get_psi_thresholds(col)
    severity = Severity.INFO
    if psi_total >= psi_break:
        severity = Severity.BREAKING
    elif psi_total >= psi_warn:
        severity = Severity.WARNING

    return ColumnDriftResult(
        name=col,
        column_type=meta.data_type,
        is_numeric=False,
        psi=psi_total,
        ks_stat=None,
        ks_pvalue=None,
        severity=severity,
        buckets=buckets,
    )


def check_drift(
    con: duckdb.DuckDBPyConnection,
    base_view: str,
    curr_view: str,
    columns_meta: list[ColumnMeta],
    config: Config,
) -> DriftCheckResult:
    """Analyze distribution drift for all shared columns using pure DuckDB SQL."""
    results: list[ColumnDriftResult] = []
    findings: list[Finding] = []

    for meta in columns_meta:
        col = meta.name
        if config.is_column_ignored(col):
            continue

        if meta.is_numeric:
            res = check_column_drift_numeric(con, base_view, curr_view, meta, config)
        elif meta.is_categorical or meta.normalized_type == "VARCHAR":
            res = check_column_drift_categorical(con, base_view, curr_view, meta, config)
        else:
            # Other unsupported types (e.g. blobs, complex nested types)
            res = ColumnDriftResult(
                name=col,
                column_type=meta.data_type,
                is_numeric=False,
                skipped=True,
                skip_reason=f"Type {meta.data_type} not supported for drift analysis",
            )

        results.append(res)

        if not res.skipped:
            psi_warn, psi_break = config.get_psi_thresholds(col)
            if res.psi >= psi_break:
                findings.append(
                    Finding(
                        check="drift",
                        column=col,
                        severity=Severity.BREAKING,
                        detail=(
                            f"PSI drift on '{col}' is {res.psi:.4f}, exceeding breaking threshold {psi_break:.2f}"
                        ),
                        value=res.psi,
                    )
                )
            elif res.psi >= psi_warn:
                findings.append(
                    Finding(
                        check="drift",
                        column=col,
                        severity=Severity.WARNING,
                        detail=f"PSI drift on '{col}' is {res.psi:.4f} (warning threshold {psi_warn:.2f})",
                        value=res.psi,
                    )
                )

            if res.ks_stat is not None:
                ks_warn, ks_break = config.get_ks_thresholds(col)
                p_str = f"p={res.ks_pvalue:.2e}" if res.ks_pvalue is not None else ""
                if res.ks_stat >= ks_break:
                    findings.append(
                        Finding(
                            check="drift",
                            column=col,
                            severity=Severity.BREAKING,
                            detail=(
                                f"KS statistic D on '{col}' is {res.ks_stat:.4f} ({p_str}), "
                                f"exceeding breaking threshold {ks_break:.2f}"
                            ),
                            value=res.ks_stat,
                        )
                    )
                elif res.ks_stat >= ks_warn:
                    findings.append(
                        Finding(
                            check="drift",
                            column=col,
                            severity=Severity.WARNING,
                            detail=(
                                f"KS statistic D on '{col}' is {res.ks_stat:.4f} ({p_str}) "
                                f"(warning threshold {ks_warn:.2f})"
                            ),
                            value=res.ks_stat,
                        )
                    )

    # Sort results: breaking first, then warning, then info, then by max_drift_score desc
    results.sort(key=lambda r: (r.severity.level, r.max_drift_score), reverse=True)

    return DriftCheckResult(columns=results, findings=findings)
