import duckdb
import pytest

from snapdiff.checks.duplicates import check_duplicates
from snapdiff.config import Config
from snapdiff.findings import Severity


def test_duplicates_declared_key_clean():
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE base AS SELECT i AS id, 'val' AS data FROM range(50) t(i)")
    con.execute("CREATE TABLE curr AS SELECT i AS id, 'val' AS data FROM range(50) t(i)")

    res = check_duplicates(con, "base", "curr", ["id", "data"], Config(keys=["id"]))
    assert res.is_key_based is True
    assert res.curr_duplicate_count == 0
    assert len(res.findings) == 0


def test_duplicates_declared_key_breaking():
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE base AS SELECT i AS id, 'val' AS data FROM range(50) t(i)")
    # curr has duplicate id=1
    con.execute("""
    CREATE TABLE curr AS
    SELECT i AS id, 'val' AS data FROM range(50) t(i)
    UNION ALL
    SELECT 1 AS id, 'val' AS data
    """)

    res = check_duplicates(con, "base", "curr", ["id", "data"], Config(keys=["id"]))
    assert res.is_key_based is True
    assert res.curr_duplicate_count == 1
    breaking = [f for f in res.findings if f.severity == Severity.BREAKING]
    assert len(breaking) == 1
    assert "became non-unique" in breaking[0].detail
    assert len(res.top_duplicates_curr) == 1
    assert res.top_duplicates_curr[0].key_values == {"id": 1}
    assert res.top_duplicates_curr[0].count == 2


def test_duplicates_full_row():
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE base AS SELECT i AS id, 'x' AS txt FROM range(100) t(i)")
    # curr has 6 duplicate rows (6% > 5% breaking threshold)
    con.execute("""
    CREATE TABLE curr AS
    SELECT i AS id, 'x' AS txt FROM range(100) t(i)
    UNION ALL
    SELECT i AS id, 'x' AS txt FROM range(6) t(i)
    """)

    res = check_duplicates(con, "base", "curr", ["id", "txt"], Config())
    assert res.is_key_based is False
    assert res.curr_duplicate_count == 6
    breaking = [f for f in res.findings if f.severity == Severity.BREAKING]
    assert len(breaking) == 1
    assert "Full-row duplicate rate grew" in breaking[0].detail
