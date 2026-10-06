from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from snapdiff.checks.drift import DriftCheckResult
from snapdiff.checks.duplicates import DuplicateDiffResult
from snapdiff.checks.nulls import NullCheckResult
from snapdiff.checks.schema import SchemaDiffResult
from snapdiff.config import Config
from snapdiff.findings import Finding, Severity


@dataclass
class DiffReport:
    baseline_source: str
    current_source: str
    timestamp: str
    schema: SchemaDiffResult
    nulls: NullCheckResult
    duplicates: DuplicateDiffResult
    drift: DriftCheckResult
    config: Config
    fail_on: str = "breaking"

    @property
    def all_findings(self) -> list[Finding]:
        return (
            self.schema.findings
            + self.nulls.findings
            + self.duplicates.findings
            + self.drift.findings
        )

    @property
    def breaking_findings(self) -> list[Finding]:
        return [f for f in self.all_findings if f.severity == Severity.BREAKING]

    @property
    def warning_findings(self) -> list[Finding]:
        return [f for f in self.all_findings if f.severity == Severity.WARNING]

    @property
    def info_findings(self) -> list[Finding]:
        return [f for f in self.all_findings if f.severity == Severity.INFO]

    @property
    def is_passed(self) -> bool:
        if self.fail_on == "warning":
            return len(self.breaking_findings) == 0 and len(self.warning_findings) == 0
        return len(self.breaking_findings) == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "metadata": {
                "baseline": self.baseline_source,
                "current": self.current_source,
                "timestamp": self.timestamp,
                "passed": self.is_passed,
                "fail_on": self.fail_on,
            },
            "summary": {
                "breaking": len(self.breaking_findings),
                "warning": len(self.warning_findings),
                "info": len(self.info_findings),
                "total": len(self.all_findings),
            },
            "findings": [f.to_dict() for f in self.all_findings],
            "schema": {
                "baseline_rows": self.schema.base_row_count,
                "current_rows": self.schema.curr_row_count,
                "row_delta_abs": self.schema.row_count_delta_abs,
                "row_delta_pct": self.schema.row_count_delta_pct,
                "order_changed": self.schema.order_changed,
                "columns": [
                    {
                        "name": c.name,
                        "status": c.status,
                        "base_type": c.base_type,
                        "curr_type": c.curr_type,
                        "severity": c.severity.value,
                    }
                    for c in self.schema.columns
                ],
            },
            "nulls": [
                {
                    "column": c.name,
                    "base_null_count": c.base_null_count,
                    "base_null_rate": c.base_null_rate,
                    "curr_null_count": c.curr_null_count,
                    "curr_null_rate": c.curr_null_rate,
                    "delta_pts": c.delta_pts,
                    "severity": c.severity.value,
                }
                for c in self.nulls.columns
            ],
            "duplicates": {
                "is_key_based": self.duplicates.is_key_based,
                "keys": self.duplicates.keys,
                "base_duplicates": self.duplicates.base_duplicate_count,
                "base_rate": self.duplicates.base_duplicate_rate,
                "curr_duplicates": self.duplicates.curr_duplicate_count,
                "curr_rate": self.duplicates.curr_duplicate_rate,
                "growth_pts": self.duplicates.growth_rate_pts,
                "top_duplicates": [str(d) for d in self.duplicates.top_duplicates_curr],
            },
            "drift": [
                {
                    "column": c.name,
                    "type": c.column_type,
                    "is_numeric": c.is_numeric,
                    "psi": c.psi,
                    "ks_stat": c.ks_stat,
                    "ks_pvalue": c.ks_pvalue,
                    "severity": c.severity.value,
                    "skipped": c.skipped,
                    "skip_reason": c.skip_reason,
                    "buckets": [
                        {
                            "label": b.label,
                            "base_count": b.base_count,
                            "base_pct": b.base_pct,
                            "curr_count": b.curr_count,
                            "curr_pct": b.curr_pct,
                        }
                        for b in c.buckets
                    ],
                }
                for c in self.drift.columns
            ],
            "thresholds": {
                "null_delta_warn": self.config.thresholds.null_delta_warn,
                "null_delta_break": self.config.thresholds.null_delta_break,
                "psi_warn": self.config.thresholds.psi_warn,
                "psi_break": self.config.thresholds.psi_break,
                "ks_warn": self.config.thresholds.ks_warn,
                "ks_break": self.config.thresholds.ks_break,
                "row_count_drop_break": self.config.thresholds.row_count_drop_break,
                "duplicate_growth_warn": self.config.thresholds.duplicate_growth_warn,
                "duplicate_growth_break": self.config.thresholds.duplicate_growth_break,
                "min_rows_drift": self.config.thresholds.min_rows_drift,
                "categorical_top_n": self.config.thresholds.categorical_top_n,
            },
        }

    def render_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)

    def render_markdown(self) -> str:
        lines: list[str] = []

        # 1. Header
        status_badge = "🟢 **PASS**" if self.is_passed else "🔴 **FAIL**"
        fail_reason = (
            "No breaking changes detected"
            if self.is_passed
            else (
                f"Gate violation ({len(self.breaking_findings)} breaking finding(s))"
                if self.fail_on == "breaking"
                else f"Gate violation ({len(self.breaking_findings)} breaking, {len(self.warning_findings)} warnings)"
            )
        )

        lines.append("# Snapdiff Dataset Snapshot Diff Report")
        lines.append("")
        lines.append(f"**Status:** {status_badge} — *{fail_reason}*")
        lines.append("")
        lines.append(f"- **Baseline:** `{self.baseline_source}` ({self.schema.base_row_count:,} rows)")
        lines.append(f"- **Current:** `{self.current_source}` ({self.schema.curr_row_count:,} rows)")
        row_delta = self.schema.row_count_delta_abs
        row_pct = self.schema.row_count_delta_pct * 100
        lines.append(f"- **Row Delta:** {row_delta:+,} ({row_pct:+.2f}%)")
        lines.append(f"- **Run Timestamp:** `{self.timestamp}`")
        lines.append(f"- **Gating Mode:** `--fail-on {self.fail_on}`")
        lines.append("")

        # 2. Summary table
        lines.append("## Executive Summary")
        lines.append("")
        lines.append("| Severity | Findings Count | Gate Status |")
        lines.append("|:---|---:|:---|")
        b_count = len(self.breaking_findings)
        w_count = len(self.warning_findings)
        i_count = len(self.info_findings)
        b_gate = "❌ Gated" if b_count > 0 else "✅ Clear"
        w_gate = (
            "❌ Gated" if (w_count > 0 and self.fail_on == "warning") else ("⚠️ Warning" if w_count > 0 else "✅ Clear")
        )
        lines.append(f"| 🚨 **BREAKING** | {b_count} | {b_gate} |")
        lines.append(f"| ⚠️ **WARNING** | {w_count} | {w_gate} |")
        lines.append(f"| ℹ️ **INFO** | {i_count} | — |")
        lines.append(f"| **TOTAL** | **{len(self.all_findings)}** | — |")
        lines.append("")

        # 3. Actionable findings sections
        if self.breaking_findings:
            lines.append("## 🚨 Breaking Changes")
            lines.append("")
            lines.append("| Check | Column | Detail |")
            lines.append("|:---|:---|:---|")
            for f in self.breaking_findings:
                col_str = f"`{f.column}`" if f.column else "*(table)*"
                lines.append(f"| **{f.check.upper()}** | {col_str} | {f.detail} |")
            lines.append("")

        if self.warning_findings:
            lines.append("## ⚠️ Warnings")
            lines.append("")
            lines.append("| Check | Column | Detail |")
            lines.append("|:---|:---|:---|")
            for f in self.warning_findings:
                col_str = f"`{f.column}`" if f.column else "*(table)*"
                lines.append(f"| **{f.check.upper()}** | {col_str} | {f.detail} |")
            lines.append("")

        if self.info_findings:
            lines.append("## ℹ️ Informational Findings")
            lines.append("")
            lines.append("| Check | Column | Detail |")
            lines.append("|:---|:---|:---|")
            for f in self.info_findings:
                col_str = f"`{f.column}`" if f.column else "*(table)*"
                lines.append(f"| **{f.check.upper()}** | {col_str} | {f.detail} |")
            lines.append("")

        # 4. Schema diff section
        lines.append("## Schema Differences")
        lines.append("")
        if self.schema.columns:
            lines.append("| Column | Baseline Type | Current Type | Status | Severity |")
            lines.append("|:---|:---|:---|:---|:---|")
            for col in self.schema.columns:
                b_t = f"`{col.base_type}`" if col.base_type else "—"
                c_t = f"`{col.curr_type}`" if col.curr_type else "—"
                status_icon = {
                    "added": "➕ added",
                    "removed": "➖ removed",
                    "type_changed": "🔄 type_changed",
                    "unchanged": "✔ unchanged",
                }.get(col.status, col.status)
                sev_icon = {
                    Severity.BREAKING: "🚨 BREAKING",
                    Severity.WARNING: "⚠️ WARNING",
                    Severity.INFO: "ℹ️ INFO",
                }[col.severity]
                lines.append(f"| `{col.name}` | {b_t} | {c_t} | {status_icon} | {sev_icon} |")
            lines.append("")

        # 5. Null-rate shift section
        lines.append("## Null Rate Shifts")
        lines.append("")
        if self.nulls.columns:
            lines.append("| Column | Baseline Nulls | Current Nulls | Baseline % | Current % | Shift (pts) | Severity |")
            lines.append("|:---|---:|---:|---:|---:|---:|:---|")
            for nc in self.nulls.columns:
                sev_icon = {
                    Severity.BREAKING: "🚨 BREAKING",
                    Severity.WARNING: "⚠️ WARNING",
                    Severity.INFO: "ℹ️ INFO",
                }[nc.severity]
                shift_sign = "+" if nc.delta_pts >= 0 else ""
                lines.append(
                    f"| `{nc.name}` | {nc.base_null_count:,} / {nc.base_total_count:,} | "
                    f"{nc.curr_null_count:,} / {nc.curr_total_count:,} | "
                    f"{nc.base_null_rate * 100:.2f}% | {nc.curr_null_rate * 100:.2f}% | "
                    f"{shift_sign}{nc.delta_pts:.2f} | {sev_icon} |"
                )
            lines.append("")
        else:
            lines.append("*No shared columns to evaluate for null shifts.*")
            lines.append("")

        # 6. Duplicates section
        lines.append("## Duplicate Growth")
        lines.append("")
        dup = self.duplicates
        mode = f"Declared Key(s): `{', '.join(dup.keys)}`" if dup.is_key_based else "Full-Row Duplicates"
        lines.append(f"- **Mode:** {mode}")
        lines.append(
            f"- **Baseline Duplicates:** {dup.base_duplicate_count:,} rows ({dup.base_duplicate_rate * 100:.2f}%)"
        )
        lines.append(
            f"- **Current Duplicates:** {dup.curr_duplicate_count:,} rows ({dup.curr_duplicate_rate * 100:.2f}%)"
        )
        growth_sign = "+" if dup.growth_rate_pts >= 0 else ""
        lines.append(f"- **Duplicate Growth:** {growth_sign}{dup.growth_rate_pts:.2f} pts")
        lines.append("")

        if dup.top_duplicates_curr:
            lines.append("**Top Duplicated Values in Current Snapshot:**")
            lines.append("")
            for td in dup.top_duplicates_curr:
                lines.append(f"- {td}")
            lines.append("")

        # 7. Distribution drift section
        lines.append("## Distribution Drift (PSI & KS)")
        lines.append("")
        if self.drift.columns:
            lines.append("| Column | Type | PSI | KS D | KS p-value (approx) | Severity | Note |")
            lines.append("|:---|:---|---:|---:|---:|:---|:---|")
            for dc in self.drift.columns:
                if dc.skipped:
                    lines.append(
                        f"| `{dc.name}` | `{dc.column_type}` | — | — | — | ℹ️ INFO | *Skipped: {dc.skip_reason}* |"
                    )
                else:
                    psi_str = f"{dc.psi:.4f}"
                    ks_str = f"{dc.ks_stat:.4f}" if dc.ks_stat is not None else "—"
                    p_str = f"{dc.ks_pvalue:.2e}" if dc.ks_pvalue is not None else "—"
                    sev_icon = {
                        Severity.BREAKING: "🚨 BREAKING",
                        Severity.WARNING: "⚠️ WARNING",
                        Severity.INFO: "ℹ️ INFO",
                    }[dc.severity]
                    lines.append(
                        f"| `{dc.name}` | `{dc.column_type}` | {psi_str} | {ks_str} | {p_str} | {sev_icon} | — |"
                    )
            lines.append("")

            # 8. Top 3 drifted columns bucket details
            valid_drifted = [c for c in self.drift.columns if not c.skipped and len(c.buckets) > 0]
            if valid_drifted:
                top3 = valid_drifted[:3]
                lines.append("### Top Drifted Columns Bucket Breakdown")
                lines.append("")
                for col in top3:
                    ks_info = f", KS D={col.ks_stat:.4f}" if col.ks_stat is not None else ""
                    lines.append(f"#### Column `{col.name}` (PSI={col.psi:.4f}{ks_info})")
                    lines.append("")
                    lines.append("| Bucket / Category | Baseline Count | Baseline % | Current Count | Current % | Delta % |")
                    lines.append("|:---|---:|---:|---:|---:|---:|")
                    for b in col.buckets:
                        delta_pct = b.curr_pct - b.base_pct
                        d_sign = "+" if delta_pct >= 0 else ""
                        lines.append(
                            f"| `{b.label}` | {b.base_count:,} | {b.base_pct:.2f}% | "
                            f"{b.curr_count:,} | {b.curr_pct:.2f}% | {d_sign}{delta_pct:.2f}% |"
                        )
                    lines.append("")
        else:
            lines.append("*No shared columns evaluated for distribution drift.*")
            lines.append("")

        # 9. Appendix
        lines.append("## Appendix: Thresholds & Configuration")
        lines.append("")
        lines.append("| Parameter | Value | Description |")
        lines.append("|:---|---:|:---|")
        t = self.config.thresholds
        lines.append(f"| `null_delta_warn` | {t.null_delta_warn * 100:.1f} pts | Warning threshold for null-rate increase |")
        lines.append(f"| `null_delta_break` | {t.null_delta_break * 100:.1f} pts | Breaking threshold for null-rate increase |")
        lines.append(f"| `psi_warn` | {t.psi_warn:.2f} | Warning threshold for Population Stability Index |")
        lines.append(f"| `psi_break` | {t.psi_break:.2f} | Breaking threshold for Population Stability Index |")
        lines.append(f"| `ks_warn` | {t.ks_warn:.2f} | Warning threshold for Kolmogorov-Smirnov D statistic |")
        lines.append(f"| `ks_break` | {t.ks_break:.2f} | Breaking threshold for Kolmogorov-Smirnov D statistic |")
        lines.append(f"| `row_count_drop_break` | {t.row_count_drop_break * 100:.1f}% | Breaking threshold for table row count drops |")
        lines.append(f"| `duplicate_growth_warn` | {t.duplicate_growth_warn * 100:.1f} pts | Warning threshold for duplicate rate growth |")
        lines.append(f"| `duplicate_growth_break` | {t.duplicate_growth_break * 100:.1f} pts | Breaking threshold for duplicate rate growth |")
        lines.append(f"| `min_rows_drift` | {t.min_rows_drift:,} | Minimum non-null rows required to evaluate drift |")
        lines.append(f"| `categorical_top_n` | {t.categorical_top_n} | Top baseline categories evaluated for categorical PSI |")
        lines.append("")
        if self.config.keys:
            lines.append(f"- **Configured Keys:** `{self.config.keys}`")
        if self.config.not_null_columns:
            lines.append(f"- **Not-Null Guaranteed Columns:** `{self.config.not_null_columns}`")
        if self.config.ignore_columns:
            lines.append(f"- **Ignored Columns:** `{self.config.ignore_columns}`")
        lines.append("")

        return "\n".join(lines)
