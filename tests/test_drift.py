import math
import duckdb
import numpy as np
import pytest
from scipy import stats

from snapdiff.checks.drift import (
    approximate_ks_pvalue,
    check_column_drift_categorical,
    check_column_drift_numeric,
    check_drift,
)
from snapdiff.config import Config
from snapdiff.findings import Severity
from snapdiff.loader import ColumnMeta


def test_ks_statistic_matches_scipy():
    np.random.seed(42)
    n1, n2 = 1000, 1000
    base_data = np.random.normal(loc=10.0, scale=2.0, size=n1)
    # Slight shift
    curr_data = np.random.normal(loc=10.5, scale=2.5, size=n2)

    expected_ks = stats.ks_2samp(base_data, curr_data).statistic

    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE base (val DOUBLE)")
    con.execute("CREATE TABLE curr (val DOUBLE)")
    con.execute("INSERT INTO base SELECT val FROM (SELECT unnest($1) AS val)", [base_data.tolist()])
    con.execute("INSERT INTO curr SELECT val FROM (SELECT unnest($1) AS val)", [curr_data.tolist()])

    meta = ColumnMeta(name="val", data_type="DOUBLE")
    res = check_column_drift_numeric(con, "base", "curr", meta, Config())

    assert res.ks_stat is not None
    # Compare with scipy ks_2samp statistic (within tolerance)
    assert abs(res.ks_stat - expected_ks) < 0.01


def test_psi_statistic_matches_numpy_reference():
    np.random.seed(123)
    n = 2000
    base_data = np.random.exponential(scale=5.0, size=n)
    curr_data = np.random.exponential(scale=6.5, size=n)

    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE base (x DOUBLE)")
    con.execute("CREATE TABLE curr (x DOUBLE)")
    con.execute("INSERT INTO base SELECT val FROM (SELECT unnest($1) AS val)", [base_data.tolist()])
    con.execute("INSERT INTO curr SELECT val FROM (SELECT unnest($1) AS val)", [curr_data.tolist()])

    meta = ColumnMeta(name="x", data_type="DOUBLE")
    res = check_column_drift_numeric(con, "base", "curr", meta, Config())

    # Reference PSI calculation
    deciles = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
    quant_edges = np.quantile(base_data, deciles, method="linear")
    clean_edges = np.unique(quant_edges)

    b_counts, _ = np.histogram(base_data, bins=[-np.inf, *clean_edges, np.inf])
    c_counts, _ = np.histogram(curr_data, bins=[-np.inf, *clean_edges, np.inf])

    e = b_counts / n
    a = c_counts / n
    e = np.maximum(e, 1e-6)
    a = np.maximum(a, 1e-6)
    expected_psi = np.sum((a - e) * np.log(a / e))

    assert abs(res.psi - expected_psi) < 0.05


def test_drift_identical_distribution():
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE base AS SELECT (i * 0.1)::DOUBLE AS num FROM range(500) t(i)")
    con.execute("CREATE TABLE curr AS SELECT (i * 0.1)::DOUBLE AS num FROM range(500) t(i)")

    meta = ColumnMeta(name="num", data_type="DOUBLE")
    res = check_column_drift_numeric(con, "base", "curr", meta, Config())

    assert res.psi < 0.01
    assert res.ks_stat == pytest.approx(0.0, abs=1e-5)
    assert res.severity == Severity.INFO


def test_drift_breaking_shift():
    con = duckdb.connect(":memory:")
    # Base: uniform 0..10. Curr: uniform 100..110 (completely separated distributions)
    con.execute("CREATE TABLE base AS SELECT (i * 0.02)::DOUBLE AS num FROM range(500) t(i)")
    con.execute("CREATE TABLE curr AS SELECT (100.0 + i * 0.02)::DOUBLE AS num FROM range(500) t(i)")

    meta = ColumnMeta(name="num", data_type="DOUBLE")
    res = check_column_drift_numeric(con, "base", "curr", meta, Config())

    assert res.psi > 0.25
    assert res.ks_stat == pytest.approx(1.0, abs=1e-3)
    assert res.severity == Severity.BREAKING


def test_drift_categorical_shift():
    con = duckdb.connect(":memory:")
    # Base: 50% A, 50% B. Curr: 95% A, 5% B
    con.execute("""
    CREATE TABLE base AS
    SELECT CASE WHEN i < 250 THEN 'A' ELSE 'B' END AS category
    FROM range(500) t(i)
    """)
    con.execute("""
    CREATE TABLE curr AS
    SELECT CASE WHEN i < 475 THEN 'A' ELSE 'B' END AS category
    FROM range(500) t(i)
    """)

    meta = ColumnMeta(name="category", data_type="VARCHAR")
    res = check_column_drift_categorical(con, "base", "curr", meta, Config())

    assert res.is_numeric is False
    assert res.psi > 0.25
    assert res.severity == Severity.BREAKING


def test_drift_skip_insufficient_rows():
    con = duckdb.connect(":memory:")
    # 50 rows (< 100 min_rows)
    con.execute("CREATE TABLE base AS SELECT i::DOUBLE AS num FROM range(50) t(i)")
    con.execute("CREATE TABLE curr AS SELECT i::DOUBLE AS num FROM range(50) t(i)")

    meta = ColumnMeta(name="num", data_type="DOUBLE")
    res = check_column_drift_numeric(con, "base", "curr", meta, Config(thresholds=Config().thresholds))

    assert res.skipped is True
    assert "Insufficient non-null rows" in (res.skip_reason or "")


def test_drift_skip_high_cardinality_id():
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE base AS SELECT 'id_' || i AS uid FROM range(200) t(i)")
    con.execute("CREATE TABLE curr AS SELECT 'id_' || i AS uid FROM range(200) t(i)")

    meta = ColumnMeta(name="uid", data_type="VARCHAR")
    res = check_column_drift_categorical(con, "base", "curr", meta, Config())

    assert res.skipped is True
    assert "High cardinality" in (res.skip_reason or "")
