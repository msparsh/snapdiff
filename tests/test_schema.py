import duckdb
import pytest

from snapdiff.checks.schema import check_schema
from snapdiff.config import Config
from snapdiff.findings import Severity


def test_schema_identical():
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE base AS SELECT 1 AS id, 'alice' AS name, 10.5 AS score")
    con.execute("CREATE TABLE curr AS SELECT 1 AS id, 'alice' AS name, 10.5 AS score")

    res = check_schema(con, "base", "curr", Config())
    assert res.base_row_count == 1
    assert res.curr_row_count == 1
    assert res.order_changed is False
    # No breaking or warning findings
    assert not any(f.severity in (Severity.BREAKING, Severity.WARNING) for f in res.findings)


def test_schema_column_removed():
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE base AS SELECT 1 AS id, 'alice' AS name, 10.5 AS score")
    con.execute("CREATE TABLE curr AS SELECT 1 AS id, 'alice' AS name")

    res = check_schema(con, "base", "curr", Config())
    breaking = [f for f in res.findings if f.severity == Severity.BREAKING]
    assert len(breaking) == 1
    assert breaking[0].column == "score"
    assert "Column removed" in breaking[0].detail


def test_schema_column_added():
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE base AS SELECT 1 AS id")
    con.execute("CREATE TABLE curr AS SELECT 1 AS id, 'extra' AS notes")

    res = check_schema(con, "base", "curr", Config())
    assert not any(f.severity in (Severity.BREAKING, Severity.WARNING) for f in res.findings)
    info = [f for f in res.findings if f.severity == Severity.INFO and f.column == "notes"]
    assert len(info) == 1
    assert "Column added" in info[0].detail


def test_schema_safe_widening():
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE base AS SELECT 10::INTEGER AS val")
    con.execute("CREATE TABLE curr AS SELECT 10::BIGINT AS val")

    res = check_schema(con, "base", "curr", Config())
    warnings = [f for f in res.findings if f.severity == Severity.WARNING]
    assert len(warnings) == 1
    assert "Safe type widening" in warnings[0].detail
    assert not any(f.severity == Severity.BREAKING for f in res.findings)


def test_schema_breaking_type_change():
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE base AS SELECT 10::INTEGER AS val")
    con.execute("CREATE TABLE curr AS SELECT '10'::VARCHAR AS val")

    res = check_schema(con, "base", "curr", Config())
    breaking = [f for f in res.findings if f.severity == Severity.BREAKING]
    assert len(breaking) == 1
    assert "Breaking type change" in breaking[0].detail


def test_schema_order_change():
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE base AS SELECT 1 AS a, 2 AS b")
    con.execute("CREATE TABLE curr AS SELECT 2 AS b, 1 AS a")

    res = check_schema(con, "base", "curr", Config())
    assert res.order_changed is True
    info = [f for f in res.findings if f.severity == Severity.INFO and "order changed" in f.detail]
    assert len(info) == 1


def test_schema_row_count_drop():
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE base AS SELECT i FROM range(100) t(i)")
    con.execute("CREATE TABLE curr AS SELECT i FROM range(50) t(i)")

    # 50% drop, exceeding default threshold 0.30
    res = check_schema(con, "base", "curr", Config())
    breaking = [f for f in res.findings if f.severity == Severity.BREAKING and f.column is None]
    assert len(breaking) == 1
    assert "Row count dropped" in breaking[0].detail
