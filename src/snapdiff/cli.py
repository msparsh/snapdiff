from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from snapdiff.config import Config, load_config
from snapdiff.diff import compare
from snapdiff.findings import Severity
from snapdiff.report import DiffReport


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="snapdiff",
        description="Dataset snapshot differ powered by DuckDB SQL (schema, nulls, duplicates, drift)",
    )
    parser.add_argument("baseline", help="Baseline dataset path (.parquet, .csv) or DuckDB URI (duckdb://file.db::table)")
    parser.add_argument("current", help="Current dataset path (.parquet, .csv) or DuckDB URI (duckdb://file.db::table)")
    parser.add_argument("--config", "-c", type=str, default=None, help="Path to config file (.toml or .yaml)")
    parser.add_argument("--out", "-o", type=str, default=None, help="Output path for the report file")
    parser.add_argument("--format", "-f", choices=["md", "json"], default="md", help="Report output format (default: md)")
    parser.add_argument("--sample", "-s", type=int, default=None, help="Sample N rows from each snapshot using fixed seed")
    parser.add_argument("--sample-seed", type=int, default=42, help="Seed for reservoir sampling (default: 42)")
    parser.add_argument("--fail-on", choices=["breaking", "warning"], default="breaking", help="Gating severity threshold (default: breaking)")
    parser.add_argument("--keys", type=str, default=None, help="Comma-separated primary key columns (overrides config)")
    parser.add_argument("--ignore-columns", type=str, default=None, help="Comma-separated columns to ignore from drift and checks")
    parser.add_argument("--not-null-columns", type=str, default=None, help="Comma-separated columns that must remain non-null")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        # Return argparse exit code (usually 2 for syntax error)
        return e.code if isinstance(e.code, int) else 2

    try:
        # Load base configuration
        if args.config:
            config = load_config(args.config)
        else:
            # Check default snapdiff.toml or snapdiff.yaml in current dir
            if Path("snapdiff.toml").is_file():
                config = load_config("snapdiff.toml")
            elif Path("snapdiff.yaml").is_file():
                config = load_config("snapdiff.yaml")
            elif Path("snapdiff.yml").is_file():
                config = load_config("snapdiff.yml")
            else:
                config = Config()

        # CLI flag overrides
        if args.keys:
            config.keys = [k.strip() for k in args.keys.split(",") if k.strip()]
        if args.ignore_columns:
            config.ignore_columns.extend([c.strip() for c in args.ignore_columns.split(",") if c.strip()])
        if args.not_null_columns:
            config.not_null_columns.extend([c.strip() for c in args.not_null_columns.split(",") if c.strip()])

        # Run diff
        report = compare(
            baseline=args.baseline,
            current=args.current,
            config=config,
            sample=args.sample,
            sample_seed=args.sample_seed,
            fail_on=args.fail_on,
        )

        # Render output
        rendered = report.render_json() if args.format == "json" else report.render_markdown()

        if args.out:
            out_path = Path(args.out)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(rendered, encoding="utf-8")
        else:
            print(rendered)

        # Print one-line summary to stderr
        b_count = len(report.breaking_findings)
        w_count = len(report.warning_findings)
        i_count = len(report.info_findings)

        if report.is_passed:
            sys.stderr.write(
                f"snapdiff: PASS - 0 breaking changes, {w_count} warning(s), {i_count} info finding(s).\n"
            )
            return 0
        else:
            gate_reason = (
                f"{b_count} breaking change(s)"
                if args.fail_on == "breaking"
                else f"{b_count} breaking change(s), {w_count} warning(s)"
            )
            sys.stderr.write(
                f"snapdiff: FAIL - {gate_reason} detected (gated by --fail-on {args.fail_on}).\n"
            )
            return 1

    except Exception as exc:
        sys.stderr.write(f"snapdiff error: {exc}\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())
