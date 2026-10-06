from pathlib import Path
import pytest

from snapdiff.config import Config, load_config


def test_load_toml_config(tmp_path: Path):
    toml_content = """
    keys = ["order_id"]
    ignore_columns = ["secret_token"]
    not_null_columns = ["order_id"]

    [thresholds]
    null_delta_warn = 0.05
    psi_break = 0.30

    [columns.special_rate]
    psi_break = 0.50
    ignore = true
    """
    cfg_file = tmp_path / "test_config.toml"
    cfg_file.write_text(toml_content, encoding="utf-8")

    cfg = load_config(cfg_file)
    assert cfg.keys == ["order_id"]
    assert cfg.ignore_columns == ["secret_token"]
    assert cfg.not_null_columns == ["order_id"]
    assert cfg.thresholds.null_delta_warn == 0.05
    assert cfg.thresholds.psi_break == 0.30

    # Column override
    assert cfg.is_column_ignored("special_rate") is True
    warn, brek = cfg.get_psi_thresholds("special_rate")
    assert brek == 0.50
    # Column without override
    _, normal_brek = cfg.get_psi_thresholds("normal_col")
    assert normal_brek == 0.30


def test_load_yaml_config(tmp_path: Path):
    yaml_content = """
    keys:
      - id
    ignore_columns:
      - temp_col
    thresholds:
      ks_break: 0.25
    """
    cfg_file = tmp_path / "test_config.yaml"
    cfg_file.write_text(yaml_content, encoding="utf-8")

    cfg = load_config(cfg_file)
    assert cfg.keys == ["id"]
    assert cfg.ignore_columns == ["temp_col"]
    assert cfg.thresholds.ks_break == 0.25


def test_safe_widening_logic():
    cfg = Config()
    assert cfg.is_safe_widening("INTEGER", "BIGINT") is True
    assert cfg.is_safe_widening("INT32", "INT64") is True
    assert cfg.is_safe_widening("FLOAT", "DOUBLE") is True
    assert cfg.is_safe_widening("BIGINT", "INTEGER") is False
    assert cfg.is_safe_widening("VARCHAR", "INTEGER") is False
