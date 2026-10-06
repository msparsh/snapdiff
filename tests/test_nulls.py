import duckdb
import pytest

from snapdiff.checks.nulls import check_nulls
from snapdiff.config import Config
from snapdiff.findings import Severity


def test_nulls_identical_clean():
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE base AS SELECT i, i * 2 AS val FROM range(100) t(i)")
    con.execute("CREATE TABLE curr AS SELECT i, i * 2 AS val FROM range(100) t(i)")

    res = check_nulls(con, "base", "curr", ["i", "val"], Config())
    assert len(res.findings) == 0
    assert len(res.columns) == 2
    assert all(c.delta_pts == 0.0 for c in res.columns)


def test_nulls_warning_shift():
    con = duckdb.connect(":memory:")
    # Base: 100 rows, 0 nulls. Curr: 100 rows, 4 nulls (4% delta >= 2% warn, < 10% break)
    con.execute("CREATE TABLE base AS SELECT i AS val FROM range(100) t(i)")
    con.execute("CREATE TABLE curr AS SELECT CASE WHEN i < 4 THEN NULL ELSE i END AS val FROM range(100) t(i)")

    res = check_nulls(con, "base", "curr", ["val"], Config())
    warnings = [f for f in res.findings if f.severity == Severity.WARNING]
    assert len(warnings) == 1
    assert "increased by 4.00 pts" in warnings[0].detail
    assert not any(f.severity == Severity.BREAKING for f in res.findings)


def test_nulls_breaking_shift():
    con = duckdb.connect(":memory:")
    # Base: 100 rows, 0 nulls. Curr: 100 rows, 15 nulls (15% delta >= 10% break)
    con.execute("CREATE TABLE base AS SELECT i AS val FROM range(100) t(i)")
    con.execute("CREATE TABLE curr AS SELECT CASE WHEN i < 15 THEN NULL ELSE i END AS val FROM range(100) t(i)")

    res = check_nulls(con, "base", "curr", ["val"], Config())
    breaking = [f for f in res.findings if f.severity == Severity.BREAKING]
    assert len(breaking) == 1
    assert "increased by 15.00 pts" in breaking[0].detail


def test_nulls_not_null_column_violation():
    con = duckdb.connect(":memory:")
    # Base: 100 rows, 0 nulls. Curr: 100 rows, 1 null (1% delta < 2% warn, but listed in not_null_columns!)
    con.execute("CREATE TABLE base AS SELECT i AS user_id FROM range(100) t(i)")
    con.execute("CREATE TABLE curr AS SELECT CASE WHEN i = 0 THEN NULL ELSE i END AS user_id FROM range(100) t(i)")

    config = Config(not_null_columns=["user_id"])
    res = check_nulls(con, "base", "curr", ["user_id"], config)

    breaking = [f for f in res.findings if f.severity == Severity.BREAKING]
    assert len(breaking) == 1
    assert "configured not-null but gained 1 nulls" in breaking[0].detail
