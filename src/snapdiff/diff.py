from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import duckdb

from snapdiff.checks.drift import check_drift
from snapdiff.checks.duplicates import check_duplicates
from snapdiff.checks.nulls import check_nulls
from snapdiff.checks.schema import check_schema
from snapdiff.config import Config, load_config
from snapdiff.loader import SnapshotLoader
from snapdiff.report import DiffReport


class SnapDiff:
    """Core orchestrator for comparing two dataset snapshots via DuckDB SQL."""

    def __init__(
        self,
        baseline_source: str,
        current_source: str,
        config: Config | None = None,
        sample: int | None = None,
        sample_seed: int = 42,
        fail_on: str = "breaking",
        con: duckdb.DuckDBPyConnection | None = None,
    ) -> None:
        self.baseline_source = baseline_source
        self.current_source = current_source
        self.config = config or Config()
        self.sample = sample
        self.sample_seed = sample_seed
        self.fail_on = fail_on
        self.loader = SnapshotLoader(con=con)

    def run(self) -> DiffReport:
        # 1. Register baseline and current snapshots as DuckDB views
        self.loader.register_view(
            view_name="base",
            source=self.baseline_source,
            sample=self.sample,
            sample_seed=self.sample_seed,
        )
        self.loader.register_view(
            view_name="curr",
            source=self.current_source,
            sample=self.sample,
            sample_seed=self.sample_seed,
        )

        con = self.loader.con

        # 2. Schema Diff
        schema_res = check_schema(con, "base", "curr", self.config)

        # 3. Retrieve column metadata
        base_meta = self.loader.get_columns_meta("base")
        curr_meta = self.loader.get_columns_meta("curr")

        base_col_names = {c.name for c in base_meta}
        curr_col_map = {c.name: c for c in curr_meta}

        # Shared columns in baseline order
        shared_col_names = [c.name for c in base_meta if c.name in curr_col_map]
        shared_col_meta = [curr_col_map[name] for name in shared_col_names]

        # 4. Null rate shifts
        nulls_res = check_nulls(con, "base", "curr", shared_col_names, self.config)

        # 5. Duplicate growth
        duplicates_res = check_duplicates(con, "base", "curr", shared_col_names, self.config)

        # 6. Distribution drift (PSI & KS)
        drift_res = check_drift(con, "base", "curr", shared_col_meta, self.config)

        # 7. Package report
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        return DiffReport(
            baseline_source=self.baseline_source,
            current_source=self.current_source,
            timestamp=timestamp,
            schema=schema_res,
            nulls=nulls_res,
            duplicates=duplicates_res,
            drift=drift_res,
            config=self.config,
            fail_on=self.fail_on,
        )


def compare(
    baseline: str,
    current: str,
    config: Config | str | Path | None = None,
    sample: int | None = None,
    sample_seed: int = 42,
    fail_on: str = "breaking",
    con: duckdb.DuckDBPyConnection | None = None,
) -> DiffReport:
    """Convenience functional API to compare two dataset snapshots."""
    cfg: Config
    if isinstance(config, (str, Path)):
        cfg = load_config(config)
    elif isinstance(config, Config):
        cfg = config
    else:
        cfg = Config()

    sd = SnapDiff(
        baseline_source=baseline,
        current_source=current,
        config=cfg,
        sample=sample,
        sample_seed=sample_seed,
        fail_on=fail_on,
        con=con,
    )
    return sd.run()
