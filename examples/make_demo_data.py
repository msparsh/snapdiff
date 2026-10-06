import os
import sys
import time
from pathlib import Path
import duckdb


def generate_demo_datasets(
    output_dir: str | Path = "examples",
    row_count: int = 1_000_000,
    seed: int = 42,
) -> tuple[Path, Path]:
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    v1_path = out_dir / "v1.parquet"
    v2_path = out_dir / "v2.parquet"

    print(f"Generating synthetic datasets ({row_count:,} rows each) into {out_dir}...")
    start_time = time.perf_counter()

    con = duckdb.connect(":memory:")
    con.execute(f"CALL setseed({seed / 100000.0})")

    # 1. Baseline dataset (v1)
    # Columns:
    # - order_id: 1..row_count (unique)
    # - customer_id: 1..50_000
    # - category: 6 retail categories
    # - price: normal distribution mean 50.0, std 15.0
    # - quantity: uniform 1..10
    # - discount_pct: 0% nulls, uniform 0.0..0.30
    # - legacy_notes: notes string (to be dropped in v2)
    # - tax_rate: double 0.05..0.15 (to have type change to VARCHAR in v2)
    # - ingested_at: timestamp (to be ignored via config)
    v1_sql = f"""
    CREATE OR REPLACE TABLE v1 AS
    SELECT
        i AS order_id,
        CAST(1 + FLOOR(random() * 50000) AS INTEGER) AS customer_id,
        CASE CAST(FLOOR(random() * 6) AS INTEGER)
            WHEN 0 THEN 'Electronics'
            WHEN 1 THEN 'Apparel'
            WHEN 2 THEN 'Home & Kitchen'
            WHEN 3 THEN 'Books'
            WHEN 4 THEN 'Beauty'
            ELSE 'Sports'
        END AS category,
        CAST(ROUND(GREATEST(5.0, 50.0 + (random() + random() + random() - 1.5) * 20.0), 2) AS DOUBLE) AS price,
        CAST(1 + FLOOR(random() * 10) AS INTEGER) AS quantity,
        CAST(ROUND(random() * 0.30, 4) AS DOUBLE) AS discount_pct,
        'Legacy audit note #' || i AS legacy_notes,
        CAST(0.05 + ROUND(random() * 0.10, 2) AS DOUBLE) AS tax_rate,
        TIMESTAMP '2026-01-01 00:00:00' + INTERVAL (i % 86400) SECOND AS ingested_at
    FROM range(1, {row_count + 1}) t(i);
    """
    con.execute(v1_sql)
    con.execute(f"COPY v1 TO '{v1_path.as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD);")
    v1_dur = time.perf_counter() - start_time
    print(f"Generated baseline v1.parquet in {v1_dur:.2f}s")

    # 2. Current dataset (v2) with injected anomalies:
    # - Dropped column: 'legacy_notes' removed
    # - Added column: 'currency' added ('USD')
    # - Type change: 'tax_rate' changed from DOUBLE to VARCHAR
    # - Null spike: 'discount_pct' has 15% nulls (was 0%)
    # - Duplicate keys: 20,000 duplicate order_id (2% duplicates)
    # - Mean shift: 'price' shifted from mean ~50 to mean ~78 (distribution drift)
    # - Categorical mix change: Electronics drops from ~16.7% to ~3%, Home surges
    v2_sql = f"""
    CREATE OR REPLACE TABLE v2 AS
    SELECT
        -- Inject 2% duplicate keys (for rows where i <= 20000, duplicate order_id from earlier rows)
        CASE
            WHEN i > {row_count - 20000} THEN (i - 20000)
            ELSE i
        END AS order_id,
        CAST(1 + FLOOR(random() * 50000) AS INTEGER) AS customer_id,
        -- Shifted categorical distribution
        CASE CAST(FLOOR(random() * 20) AS INTEGER)
            WHEN 0 THEN 'Electronics' -- down to ~5%
            WHEN 1 THEN 'Apparel'
            WHEN 2 THEN 'Home & Kitchen'
            WHEN 3 THEN 'Home & Kitchen' -- surged
            WHEN 4 THEN 'Home & Kitchen'
            WHEN 5 THEN 'Books'
            WHEN 6 THEN 'Beauty'
            ELSE 'Sports'
        END AS category,
        -- Shifted price distribution: mean ~78, std ~25
        CAST(ROUND(GREATEST(10.0, 78.0 + (random() + random() + random() - 1.5) * 35.0), 2) AS DOUBLE) AS price,
        CAST(1 + FLOOR(random() * 10) AS INTEGER) AS quantity,
        -- Null spike: 15% nulls in discount_pct
        CASE
            WHEN random() < 0.15 THEN NULL
            ELSE CAST(ROUND(random() * 0.30, 4) AS DOUBLE)
        END AS discount_pct,
        -- tax_rate converted to VARCHAR string (Breaking type change)
        CAST(ROUND(0.05 + random() * 0.10, 2) AS VARCHAR) || '%' AS tax_rate,
        -- currency is a newly added column
        'USD' AS currency,
        TIMESTAMP '2026-02-01 00:00:00' + INTERVAL (i % 86400) SECOND AS ingested_at
    FROM range(1, {row_count + 1}) t(i);
    """
    con.execute(v2_sql)
    con.execute(f"COPY v2 TO '{v2_path.as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD);")
    total_dur = time.perf_counter() - start_time
    print(f"Generated current v2.parquet in {total_dur - v1_dur:.2f}s (Total: {total_dur:.2f}s)")
    print(f"Files ready:\n  - {v1_path}\n  - {v2_path}")

    return v1_path, v2_path


if __name__ == "__main__":
    generate_demo_datasets()
