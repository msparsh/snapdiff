from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import duckdb


def quote_ident(ident: str) -> str:
    """Safely quote a SQL identifier using DuckDB / standard SQL double quotes."""
    escaped = ident.replace('"', '""')
    return f'"{escaped}"'


def quote_literal(val: str) -> str:
    """Safely quote a string literal with single quotes."""
    escaped = val.replace("'", "''")
    return f"'{escaped}'"


NUMERIC_TYPE_PREFIXES = (
    "TINYINT",
    "SMALLINT",
    "INTEGER",
    "INT",
    "BIGINT",
    "HUGEINT",
    "UTINYINT",
    "USMALLINT",
    "UINTEGER",
    "UBIGINT",
    "UHUGEINT",
    "FLOAT",
    "DOUBLE",
    "REAL",
    "DECIMAL",
    "NUMERIC",
)

CATEGORICAL_TYPE_PREFIXES = (
    "VARCHAR",
    "CHAR",
    "TEXT",
    "STRING",
    "BOOLEAN",
    "BOOL",
    "ENUM",
)

TEMPORAL_TYPE_PREFIXES = (
    "DATE",
    "TIME",
    "TIMESTAMP",
    "INTERVAL",
)


@dataclass(frozen=True)
class ColumnMeta:
    name: str
    data_type: str
    nullable: bool = True

    @property
    def normalized_type(self) -> str:
        return self.data_type.strip().upper()

    @property
    def is_numeric(self) -> bool:
        base = self.normalized_type.split("(")[0].strip()
        return any(base == p or base.startswith(p) for p in NUMERIC_TYPE_PREFIXES)

    @property
    def is_categorical(self) -> bool:
        base = self.normalized_type.split("(")[0].strip()
        return any(base == p or base.startswith(p) for p in CATEGORICAL_TYPE_PREFIXES)

    @property
    def is_temporal(self) -> bool:
        base = self.normalized_type.split("(")[0].strip()
        return any(base == p or base.startswith(p) for p in TEMPORAL_TYPE_PREFIXES)


class SnapshotLoader:
    def __init__(self, con: duckdb.DuckDBPyConnection | None = None) -> None:
        self.con = con or duckdb.connect(":memory:")
        self._attached_dbs: set[str] = set()

    def _resolve_source_query(self, source: str) -> str:
        """Resolve a file path, URI, or table name into a valid SQL FROM expression."""
        # Check for duckdb:// URI: duckdb://file.db::table or duckdb://file.db:table or duckdb://file.db/table
        if source.startswith("duckdb://"):
            raw = source[len("duckdb://") :]
            if "::" in raw:
                db_path, table_name = raw.split("::", 1)
            elif "/" in raw:
                db_path, table_name = raw.rsplit("/", 1)
            elif ":" in raw:
                db_path, table_name = raw.split(":", 1)
            else:
                raise ValueError(
                    f"Invalid DuckDB URI: {source}. Expected format: duckdb://path/to/db.duckdb::table_name"
                )

            db_alias = f"attached_{abs(hash(db_path)) % 1000000}"
            if db_alias not in self._attached_dbs:
                attach_sql = f"ATTACH {quote_literal(db_path)} AS {quote_ident(db_alias)} (READ_ONLY)"
                self.con.execute(attach_sql)
                self._attached_dbs.add(db_alias)

            return f"{quote_ident(db_alias)}.{quote_ident(table_name)}"

        # Check file path extensions
        p = Path(source)
        # Normalize path string for SQL
        path_str = str(p.resolve() if p.exists() else source)
        clean_path = path_str.replace("\\", "/")

        suffix = p.suffix.lower()
        if suffix == ".parquet":
            return f"read_parquet({quote_literal(clean_path)})"
        if suffix in (".csv", ".tsv", ".txt"):
            return f"read_csv_auto({quote_literal(clean_path)})"

        # Check if table or view already exists in DuckDB connection
        try:
            self.con.execute(f"DESCRIBE {quote_ident(source)}")
            return quote_ident(source)
        except Exception:
            pass

        # If file exists without extension or unrecognized extension, attempt parquet then csv auto
        if p.is_file():
            try:
                self.con.execute(f"DESCRIBE SELECT * FROM read_parquet({quote_literal(clean_path)})")
                return f"read_parquet({quote_literal(clean_path)})"
            except Exception:
                return f"read_csv_auto({quote_literal(clean_path)})"

        # Fallback to quoting as table name
        return quote_ident(source)

    def register_view(
        self,
        view_name: str,
        source: str,
        sample: int | None = None,
        sample_seed: int = 42,
    ) -> None:
        """Register a baseline or current snapshot as a view in DuckDB."""
        source_expr = self._resolve_source_query(source)
        quoted_view = quote_ident(view_name)

        if sample is not None and sample > 0:
            # DuckDB reservoir sampling with fixed seed
            create_sql = (
                f"CREATE OR REPLACE VIEW {quoted_view} AS "
                f"SELECT * FROM {source_expr} USING SAMPLE {sample} ROWS (reservoir, {sample_seed})"
            )
        else:
            create_sql = f"CREATE OR REPLACE VIEW {quoted_view} AS SELECT * FROM {source_expr}"

        self.con.execute(create_sql)

    def get_columns_meta(self, view_name: str) -> list[ColumnMeta]:
        """Fetch and cache column metadata for a given view."""
        quoted_view = quote_ident(view_name)
        rows = self.con.execute(f"DESCRIBE {quoted_view}").fetchall()
        meta: list[ColumnMeta] = []
        for row in rows:
            col_name = str(row[0])
            col_type = str(row[1])
            nullable = str(row[2]).upper() == "YES" if len(row) > 2 else True
            meta.append(ColumnMeta(name=col_name, data_type=col_type, nullable=nullable))
        return meta

    def get_row_count(self, view_name: str) -> int:
        quoted_view = quote_ident(view_name)
        res = self.con.execute(f"SELECT COUNT(*) FROM {quoted_view}").fetchone()
        return int(res[0]) if res else 0
