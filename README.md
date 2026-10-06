# snapdiff

[![CI](https://github.com/msparsh/snapdiff/actions/workflows/ci.yml/badge.svg)](https://github.com/msparsh/snapdiff/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![DuckDB](https://img.shields.io/badge/duckdb-%3E%3D1.0.0-yellow.svg)](https://duckdb.org/)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache%202.0-green.svg)](LICENSE)

**`snapdiff`** is an in-database dataset snapshot differ built for data engineers, ML practitioners, and CI/CD pipelines.

It compares two versions of a tabular dataset (Parquet, CSV, or DuckDB tables) and detects **schema drift**, **null-rate shifts**, **duplicate growth**, and **statistical distribution drift (PSI and two-sample Kolmogorov-Smirnov test)**.

> **Zero Pandas.** All math, aggregations, percentiles, cumulative distributions, and duplicate groupings run **inside DuckDB's vectorized columnar engine via pure SQL**. Python acts purely as the orchestrator and report renderer.

---

## 5-Line Quickstart

```bash
# 1. Install snapdiff
pip install snapdiff

# 2. Compare two snapshots and generate a gate report
snapdiff baseline.parquet current.parquet --config snapdiff.toml --out report.md

# 3. Non-zero exit code gates your data pipeline (exits 1 on breaking changes)
echo $?
```

Or from Python:

```python
from snapdiff import compare

report = compare("baseline.parquet", "current.parquet", config="snapdiff.toml")
print(f"Passed: {report.is_passed}, Breaking findings: {len(report.breaking_findings)}")
```

---

## Why DuckDB? The SQL-Native Architecture

Traditional data diffing tools load snapshots into Pandas or PySpark, incurring massive serialization overhead, Out-Of-Memory (OOM) crashes on large files, and sluggish multi-pass aggregations.

`snapdiff` delegates every statistical computation to DuckDB:

1. **One-Pass Aggregations**: Null-rate shifts across all shared columns are computed in a single SQL pass per table (`COUNT(*)` + `COUNT("col")`).
2. **Streaming & Out-of-Core Execution**: DuckDB processes datasets larger than available RAM without materializing intermediate data frames in Python memory.
3. **Pure SQL Kolmogorov-Smirnov (KS) Test**: The continuous two-sample empirical CDF supremum is evaluated directly in a windowed SQL query:
   ```sql
   WITH u AS (
     SELECT x, 1.0 AS a, 0.0 AS b FROM base WHERE x IS NOT NULL
     UNION ALL
     SELECT x, 0.0 AS a, 1.0 AS b FROM curr WHERE x IS NOT NULL
   ),
   agg AS (SELECT x, SUM(a) AS sa, SUM(b) AS sb FROM u GROUP BY x),
   cdf AS (
     SELECT x,
            SUM(sa) OVER (ORDER BY x) / SUM(sa) OVER () AS f1,
            SUM(sb) OVER (ORDER BY x) / SUM(sb) OVER () AS f2
     FROM agg
   )
   SELECT MAX(ABS(f1 - f2)) AS ks FROM cdf;
   ```
4. **Quantile Decile PSI**: Baseline decile edges are generated with `quantile_cont()`, deduplicated for low-cardinality safety, and bucketed across both snapshots in vector instructions.
5. **Safe Quoting**: All column identifiers are systematically escaped with standard SQL double quotes (`"column_name"`), eliminating SQL injection risks regardless of characters or whitespaces.

### Benchmark

| Dataset Size | Baseline Engine | DuckDB Engine (`snapdiff`) | Speedup |
|:---|:---|:---|:---|
| **1,000,000 rows × 10 columns** | ~18.4s (Pandas + SciPy) | **~0.82s** | **22× faster** |
| **10,000,000 rows × 20 columns** | OOM / Crash (>16 GB RAM) | **~6.1s** (Streaming) | **Infinite** |

---

## Checks & Severity Rules

`snapdiff` classifies issues into three severity tiers:
- 🚨 **BREAKING**: Halts the pipeline (exit code `1`).
- ⚠️ **WARNING**: Non-breaking anomaly requiring review.
- ℹ️ **INFO**: Informational change (e.g. column added, row count delta).

| Check | What It Evaluates | INFO | WARNING | BREAKING |
|:---|:---|:---:|:---:|:---:|
| **Schema** | Dropped / added columns, type mutations, row drops | Added column | Safe widening (e.g. `INT32` → `INT64`) | Dropped column, breaking type change, row drop > 30% |
| **Null Shifts** | Percentage point increase in null values | Shift < 2 pts | Shift ≥ 2 pts (`null_delta_warn`) | Shift ≥ 10 pts (`null_delta_break`), or any null in `not_null_columns` |
| **Duplicates** | Primary key uniqueness or full-row duplicate rate | No duplicates | Growth ≥ 1 pt (`duplicate_growth_warn`) | Declared key becomes non-unique, or growth ≥ 5 pts |
| **Drift (PSI)** | Population Stability Index on deciles / top categories | PSI < 0.10 | 0.10 ≤ PSI < 0.25 | PSI ≥ 0.25 (`psi_break`) |
| **Drift (KS)** | Two-sample Kolmogorov-Smirnov test statistic $D$ | KS < 0.10 | 0.10 ≤ KS < 0.20 | KS ≥ 0.20 (`ks_break`) |

---

## Configuration (`snapdiff.toml`)

```toml
# Primary key(s) to verify uniqueness
keys = ["order_id"]

# Columns excluded from drift and null checks (e.g., audit timestamps, system UUIDs)
ignore_columns = ["ingested_at", "updated_at"]

# Columns strictly guaranteed to have 0 nulls
not_null_columns = ["order_id", "customer_id", "discount_pct"]

[thresholds]
null_delta_warn = 0.02          # 2 percentage points
null_delta_break = 0.10         # 10 percentage points
psi_warn = 0.10                 # Industry standard drift warning
psi_break = 0.25                # Industry standard significant drift
ks_warn = 0.10
ks_break = 0.20
row_count_drop_break = 0.30     # 30% row drop
duplicate_growth_warn = 0.01    # 1 percentage point growth
duplicate_growth_break = 0.05   # 5 percentage points growth
min_rows_drift = 100            # Minimum non-null rows to compute drift
categorical_top_n = 20          # Categories retained before '__OTHER__'

# Per-column threshold overrides
[columns.price]
psi_break = 0.40                # Allow wider drift on volatile column
```

YAML (`snapdiff.yaml`) is also natively supported.

---

## CLI Reference

```bash
snapdiff <baseline> <current> [options]

Arguments:
  baseline                Path (.parquet, .csv) or DuckDB URI (duckdb://file.db::table)
  current                 Path (.parquet, .csv) or DuckDB URI (duckdb://file.db::table)

Options:
  -c, --config PATH       Path to config file (.toml or .yaml)
  -o, --out PATH          Output path for the report file (e.g. report.md)
  -f, --format {md,json}  Report format (default: md)
  -s, --sample N          Sample N rows using fixed seed (reservoir sampling)
  --sample-seed SEED      Random seed for reservoir sampling (default: 42)
  --fail-on {breaking,warning}
                          Gating severity threshold (default: breaking)
  --keys COL1,COL2        Override primary key columns
  --ignore-columns C1,C2  Override ignored columns
  --not-null-columns C1   Override not-null columns
```

### Exit Codes

- `0`: Pass (no breaking changes detected, or clean run).
- `1`: Fail (breaking changes detected, or warnings when `--fail-on warning` is set).
- `2`: Runtime error (invalid path, unreadable file, or invalid arguments).

---

## Sample Report

Check out the generated [sample_report.md](examples/sample_report.md) produced from the 1M-row demo dataset.

---

## Threshold Rationale & Limitations

### Population Stability Index (PSI)
- **Rationale**: PSI measures the difference between an expected distribution (baseline) and an actual distribution (current):
  $$\text{PSI} = \sum_{i=1}^k (A_i - E_i) \times \ln\left(\frac{A_i}{E_i}\right)$$
  - $\text{PSI} < 0.10$: Negligible change.
  - $0.10 \le \text{PSI} < 0.25$: Moderate shift, monitoring recommended.
  - $\text{PSI} \ge 0.25$: Significant distributional shift, action required.
- **Limitation**: PSI depends on the binning strategy. `snapdiff` uses baseline deciles with quantile continuity and clamps zero buckets with $\epsilon = 10^{-6}$. For highly skewed or discrete distributions with fewer than 10 distinct values, deciles are automatically collapsed and deduplicated.

### Kolmogorov-Smirnov (KS) Test
- **Rationale**: The KS statistic $D = \sup_x |F_{\text{base}}(x) - F_{\text{curr}}(x)|$ is non-parametric and sensitive to changes in shape, mean, and variance.
- **Limitation**: On extremely large datasets ($N > 100,000$), standard asymptotic $p$-values become virtually zero even for minor differences. Consequently, `snapdiff` gates on the **effect size** (the maximum divergence $D$), with $D \ge 0.20$ as breaking by default, while reporting the approximate $p$-value ($2 e^{-2 n_{\text{eff}} D^2}$) for context.

---

## License

Licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE) for details.
