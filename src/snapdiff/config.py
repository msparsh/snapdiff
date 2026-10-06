from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomllib  # type: ignore
    except ModuleNotFoundError:
        import tomli as tomllib  # type: ignore

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore


DEFAULT_SAFE_WIDENINGS: dict[str, list[str]] = {
    # Integers to larger integers or floats
    "TINYINT": ["SMALLINT", "INTEGER", "BIGINT", "HUGEINT", "FLOAT", "DOUBLE", "INT2", "INT4", "INT8", "INT16"],
    "INT1": ["SMALLINT", "INTEGER", "BIGINT", "HUGEINT", "FLOAT", "DOUBLE", "INT2", "INT4", "INT8", "INT16"],
    "SMALLINT": ["INTEGER", "BIGINT", "HUGEINT", "FLOAT", "DOUBLE", "INT4", "INT8", "INT16"],
    "INT2": ["INTEGER", "BIGINT", "HUGEINT", "FLOAT", "DOUBLE", "INT4", "INT8", "INT16"],
    "INTEGER": ["BIGINT", "HUGEINT", "DOUBLE", "INT8", "INT16"],
    "INT": ["BIGINT", "HUGEINT", "DOUBLE", "INT8", "INT16"],
    "INT4": ["BIGINT", "HUGEINT", "DOUBLE", "INT8", "INT16"],
    "INT32": ["INT64", "BIGINT", "HUGEINT", "DOUBLE", "INT8", "INT16"],
    "BIGINT": ["HUGEINT", "INT16"],
    "INT8": ["HUGEINT", "INT16"],
    "INT64": ["HUGEINT", "INT16"],
    "FLOAT": ["DOUBLE", "FLOAT8"],
    "FLOAT4": ["DOUBLE", "FLOAT8"],
    "REAL": ["DOUBLE", "FLOAT8"],
}


@dataclass
class Thresholds:
    null_delta_warn: float = 0.02
    null_delta_break: float = 0.10
    psi_warn: float = 0.10
    psi_break: float = 0.25
    ks_warn: float = 0.10
    ks_break: float = 0.20
    row_count_drop_break: float = 0.30
    duplicate_growth_warn: float = 0.01
    duplicate_growth_break: float = 0.05
    min_rows_drift: int = 100
    categorical_top_n: int = 20


@dataclass
class ColumnConfig:
    psi_warn: float | None = None
    psi_break: float | None = None
    ks_warn: float | None = None
    ks_break: float | None = None
    null_delta_warn: float | None = None
    null_delta_break: float | None = None
    ignore: bool | None = None


@dataclass
class Config:
    keys: list[str] = field(default_factory=list)
    ignore_columns: list[str] = field(default_factory=list)
    not_null_columns: list[str] = field(default_factory=list)
    thresholds: Thresholds = field(default_factory=Thresholds)
    columns: dict[str, ColumnConfig] = field(default_factory=dict)
    safe_widenings: dict[str, list[str]] = field(
        default_factory=lambda: {k: list(v) for k, v in DEFAULT_SAFE_WIDENINGS.items()}
    )

    def is_column_ignored(self, col: str) -> bool:
        if col in self.ignore_columns:
            return True
        col_cfg = self.columns.get(col)
        return bool(col_cfg and col_cfg.ignore)

    def get_psi_thresholds(self, col: str) -> tuple[float, float]:
        col_cfg = self.columns.get(col)
        warn = col_cfg.psi_warn if (col_cfg and col_cfg.psi_warn is not None) else self.thresholds.psi_warn
        brek = col_cfg.psi_break if (col_cfg and col_cfg.psi_break is not None) else self.thresholds.psi_break
        return warn, brek

    def get_ks_thresholds(self, col: str) -> tuple[float, float]:
        col_cfg = self.columns.get(col)
        warn = col_cfg.ks_warn if (col_cfg and col_cfg.ks_warn is not None) else self.thresholds.ks_warn
        brek = col_cfg.ks_break if (col_cfg and col_cfg.ks_break is not None) else self.thresholds.ks_break
        return warn, brek

    def get_null_thresholds(self, col: str) -> tuple[float, float]:
        col_cfg = self.columns.get(col)
        warn = (
            col_cfg.null_delta_warn
            if (col_cfg and col_cfg.null_delta_warn is not None)
            else self.thresholds.null_delta_warn
        )
        brek = (
            col_cfg.null_delta_break
            if (col_cfg and col_cfg.null_delta_break is not None)
            else self.thresholds.null_delta_break
        )
        return warn, brek

    def is_safe_widening(self, old_type: str, new_type: str) -> bool:
        norm_old = old_type.strip().upper().split("(")[0]
        norm_new = new_type.strip().upper().split("(")[0]
        if norm_old == norm_new:
            return True
        allowed = self.safe_widenings.get(norm_old, [])
        return norm_new in allowed


def load_config(path: str | Path | None) -> Config:
    if path is None:
        return Config()

    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"Config file not found: {p}")

    content = p.read_text(encoding="utf-8")
    ext = p.suffix.lower()

    data: dict[str, Any]
    if ext in (".yaml", ".yml"):
        if yaml is None:
            raise RuntimeError("PyYAML is required to parse YAML config files.")
        data = yaml.safe_load(content) or {}
    elif ext == ".toml":
        data = tomllib.loads(content)
    else:
        # Try TOML first, then YAML
        try:
            data = tomllib.loads(content)
        except Exception:
            if yaml:
                data = yaml.safe_load(content) or {}
            else:
                raise ValueError(f"Unsupported config file extension: {ext}")

    return from_dict(data)


def from_dict(data: dict[str, Any]) -> Config:
    keys = list(data.get("keys", []))
    ignore_columns = list(data.get("ignore_columns", []))
    not_null_columns = list(data.get("not_null_columns", []))

    thresh_data = data.get("thresholds", {})
    thresholds = Thresholds(
        null_delta_warn=float(thresh_data.get("null_delta_warn", 0.02)),
        null_delta_break=float(thresh_data.get("null_delta_break", 0.10)),
        psi_warn=float(thresh_data.get("psi_warn", 0.10)),
        psi_break=float(thresh_data.get("psi_break", 0.25)),
        ks_warn=float(thresh_data.get("ks_warn", 0.10)),
        ks_break=float(thresh_data.get("ks_break", 0.20)),
        row_count_drop_break=float(thresh_data.get("row_count_drop_break", 0.30)),
        duplicate_growth_warn=float(thresh_data.get("duplicate_growth_warn", 0.01)),
        duplicate_growth_break=float(thresh_data.get("duplicate_growth_break", 0.05)),
        min_rows_drift=int(thresh_data.get("min_rows_drift", 100)),
        categorical_top_n=int(thresh_data.get("categorical_top_n", 20)),
    )

    columns: dict[str, ColumnConfig] = {}
    for col_name, col_data in data.get("columns", {}).items():
        if isinstance(col_data, dict):
            columns[col_name] = ColumnConfig(
                psi_warn=float(col_data["psi_warn"]) if "psi_warn" in col_data else None,
                psi_break=float(col_data["psi_break"]) if "psi_break" in col_data else None,
                ks_warn=float(col_data["ks_warn"]) if "ks_warn" in col_data else None,
                ks_break=float(col_data["ks_break"]) if "ks_break" in col_data else None,
                null_delta_warn=float(col_data["null_delta_warn"]) if "null_delta_warn" in col_data else None,
                null_delta_break=float(col_data["null_delta_break"]) if "null_delta_break" in col_data else None,
                ignore=bool(col_data["ignore"]) if "ignore" in col_data else None,
            )

    widenings = {k: list(v) for k, v in DEFAULT_SAFE_WIDENINGS.items()}
    if "safe_widenings" in data:
        for k, v in data["safe_widenings"].items():
            widenings[k.upper()] = [x.upper() for x in v]

    return Config(
        keys=keys,
        ignore_columns=ignore_columns,
        not_null_columns=not_null_columns,
        thresholds=thresholds,
        columns=columns,
        safe_widenings=widenings,
    )
