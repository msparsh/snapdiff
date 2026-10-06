import duckdb
import pytest

from snapdiff.checks.drift import check_column_drift_numeric, check_drift
from snapdiff.checks.duplicates import check_duplicates
from snapdiff.checks.nulls import check_nulls
from snapdiff.checks.schema import check_schema
from snapdiff.config import Config
from snapdiff.diff import compare
from snapdiff.findings import Severity
from snapdiff.loader import ColumnMeta


def test_edge_case_quoted_identifiers_and_spaces():
    con = duckdb.connect(":memory:")
    # Column with spaces and internal double quote
    con.execute('CREATE TABLE base AS SELECT 1 AS "user name", 10.0 AS "col""quote"')
    con.execute('CREATE TABLE curr AS SELECT 1 AS "user name", 10.0 AS "col""quote"')

    schema_res = check_schema(con, "base", "curr", Config())
    assert schema_res.base_row_count == 1
    assert len(schema_res.columns) == 2

    nulls_res = check_nulls(con, "base", "curr", ['user name', 'col"quote'], Config())
    assert len(nulls_res.columns) == 2
    assert all(c.delta_pts == 0.0 for c in nulls_res.columns)

    dup_res = check_duplicates(con, "base", "curr", ['user name', 'col"quote'], Config())
    assert dup_res.curr_duplicate_count == 0


def test_edge_case_constant_column():
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE base AS SELECT 42.0::DOUBLE AS const_col FROM range(200) t(i)")
    con.execute("CREATE TABLE curr AS SELECT 42.0::DOUBLE AS const_col FROM range(200) t(i)")

    meta = ColumnMeta(name="const_col", data_type="DOUBLE")
    res = check_column_drift_numeric(con, "base", "curr", meta, Config())
    assert res.psi == 0.0
    assert res.ks_stat == pytest.approx(0.0, abs=1e-5)


def test_edge_case_all_null_column():
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE base AS SELECT CAST(NULL AS DOUBLE) AS null_col FROM range(150) t(i)")
    con.execute("CREATE TABLE curr AS SELECT CAST(NULL AS DOUBLE) AS null_col FROM range(150) t(i)")

    nulls_res = check_nulls(con, "base", "curr", ["null_col"], Config())
    assert len(nulls_res.columns) == 1
    assert nulls_res.columns[0].base_null_rate == 1.0
    assert nulls_res.columns[0].curr_null_rate == 1.0
    assert nulls_res.columns[0].delta_pts == 0.0

    meta = ColumnMeta(name="null_col", data_type="DOUBLE")
    drift_res = check_column_drift_numeric(con, "base", "curr", meta, Config())
    assert drift_res.skipped is True
    assert "Insufficient non-null rows" in (drift_res.skip_reason or "")


def test_edge_case_empty_table():
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE base (id INTEGER, name VARCHAR, score DOUBLE)")
    con.execute("CREATE TABLE curr (id INTEGER, name VARCHAR, score DOUBLE)")

    schema_res = check_schema(con, "base", "curr", Config())
    assert schema_res.base_row_count == 0
    assert schema_res.curr_row_count == 0

    nulls_res = check_nulls(con, "base", "curr", ["id", "name", "score"], Config())
    assert len(nulls_res.columns) == 3
    assert all(c.base_null_rate == 0.0 for c in nulls_res.columns)

    dup_res = check_duplicates(con, "base", "curr", ["id", "name", "score"], Config())
    assert dup_res.curr_duplicate_count == 0


def test_edge_case_single_distinct_value():
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE base AS SELECT 'fixed_label' AS cat FROM range(200) t(i)")
    con.execute("CREATE TABLE curr AS SELECT 'fixed_label' AS cat FROM range(200) t(i)")

    drift_res = check_drift(
        con, "base", "curr", [ColumnMeta(name="cat", data_type="VARCHAR")], Config()
    )
    assert len(drift_res.columns) == 1
    assert drift_res.columns[0].psi == pytest.approx(0.0, abs=1e-4)
