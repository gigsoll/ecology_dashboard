import math
import os
import pandas as pd
from sqlalchemy import text
from app.db.database import get_engine

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "mysql+pymysql://user:password@localhost/airquality",  # ← change or set env var
)

DETAIL_QUERY = text("""
SELECT
    fm.station_key,
    ds.station_name,
    fm.parameter_key,
    dp.parameter_code,
    dp.parameter_name,
    dp.physical_min,
    dp.physical_max,

    COUNT(*)                                                   AS total_rows,

    SUM(CASE
        WHEN fm.value IS NULL                                   THEN 0
        WHEN fm.quality_ratio = 0.5                            THEN 0
        WHEN dp.physical_min IS NOT NULL
         AND fm.value < dp.physical_min                        THEN 0
        WHEN dp.physical_max IS NOT NULL
         AND fm.value > dp.physical_max                        THEN 0
        ELSE 1
    END)                                                       AS valid_rows,

    SUM(CASE WHEN fm.value IS NULL THEN 1 ELSE 0 END)         AS null_rows,
    SUM(CASE WHEN fm.quality_ratio = 0.5
              AND fm.value IS NOT NULL THEN 1 ELSE 0 END)      AS imputed_rows,

    MIN(fm.measurement_timestamp)                              AS date_min,
    MAX(fm.measurement_timestamp)                              AS date_max

FROM  fact_measurements  fm
JOIN  dim_stations   ds ON ds.station_key   = fm.station_key   AND ds.is_current  = 1
JOIN  dim_parameters dp ON dp.parameter_key = fm.parameter_key AND dp.is_current = 1

GROUP BY
    fm.station_key, ds.station_name,
    fm.parameter_key, dp.parameter_code, dp.parameter_name,
    dp.physical_min, dp.physical_max
""")


def station_richness_table(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    d["valid_ratio"] = d["valid_rows"] / d["total_rows"].clip(lower=1)

    agg = (
        d.groupby(["station_key", "station_name"])
        .agg(
            n_params=("parameter_code", "nunique"),
            total_rows=("total_rows", "sum"),
            valid_rows=("valid_rows", "sum"),
            null_rows=("null_rows", "sum"),
            imputed_rows=("imputed_rows", "sum"),
            mean_coverage=("valid_ratio", "mean"),
            date_min=("date_min", "min"),
            date_max=("date_max", "max"),
            parameters=("parameter_code", lambda s: ", ".join(sorted(s.unique()))),
        )
        .reset_index()
    )

    agg["valid_pct"] = (
        agg["valid_rows"] / agg["total_rows"].clip(lower=1) * 100
    ).round(1)
    agg["richness_score"] = (
        agg["mean_coverage"] * agg["n_params"].apply(math.sqrt)
    ).round(4)
    agg = agg.sort_values("richness_score", ascending=False).reset_index(drop=True)
    agg.index += 1  # 1-based rank
    agg.index.name = "rank"

    return agg[
        [
            "station_key",
            "station_name",
            "richness_score",
            "n_params",
            "valid_pct",
            "mean_coverage",
            "total_rows",
            "valid_rows",
            "null_rows",
            "imputed_rows",
            "date_min",
            "date_max",
            "parameters",
        ]
    ]


def parameter_coverage_table(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    d["valid_ratio"] = d["valid_rows"] / d["total_rows"].clip(lower=1)

    agg = (
        d.groupby(["parameter_code", "parameter_name"])
        .agg(
            n_stations=("station_key", "nunique"),
            total_rows=("total_rows", "sum"),
            valid_rows=("valid_rows", "sum"),
            imputed_rows=("imputed_rows", "sum"),
            mean_coverage=("valid_ratio", "mean"),
        )
        .reset_index()
    )

    agg["valid_pct"] = (
        agg["valid_rows"] / agg["total_rows"].clip(lower=1) * 100
    ).round(1)
    agg = agg.sort_values("n_stations", ascending=False).reset_index(drop=True)
    agg.index += 1
    agg.index.name = "rank"

    return agg[
        [
            "parameter_code",
            "parameter_name",
            "n_stations",
            "valid_pct",
            "mean_coverage",
            "total_rows",
            "valid_rows",
            "imputed_rows",
        ]
    ]


def _bar(ratio: float, width: int = 20) -> str:
    filled = round(ratio * width)
    return "█" * filled + "░" * (width - filled)


def print_station_table(st: pd.DataFrame) -> None:
    W = 132
    print(f"\n{'━' * W}")
    print(f"  STATIONS — ranked by feature richness  ({len(st)} total)")
    print(f"{'━' * W}")
    print(
        f"  {'#':>3}  {'Station':<28}  {'Score':>6}  {'Params':>6}  "
        f"  {'Valid%':>6}  {'Coverage bar':<22}  {'Rows':>9}  "
        f"  {'Null':>6}  {'Imputed':>7}  {'Date range'}"
    )
    print(
        f"  {'─' * 3}  {'─' * 28}  {'─' * 6}  {'─' * 6}  "
        f"  {'─' * 6}  {'─' * 22}  {'─' * 9}  "
        f"  {'─' * 6}  {'─' * 7}  {'─' * 21}"
    )

    for rank, row in st.iterrows():
        name = str(row["station_name"] or row["station_key"])[:28]
        date_lo = str(row["date_min"])[:10] if pd.notna(row["date_min"]) else "?"
        date_hi = str(row["date_max"])[:10] if pd.notna(row["date_max"]) else "?"
        print(
            f"  {rank:>3}  {name:<28}  {row['richness_score']:>6.3f}  {int(row['n_params']):>6}  "
            f"  {row['valid_pct']:>5.1f}%  {_bar(row['mean_coverage']):<22}  "
            f"{int(row['total_rows']):>9,}  "
            f"  {int(row['null_rows']):>6,}  {int(row['imputed_rows']):>7,}  "
            f"{date_lo} → {date_hi}"
        )

    print(f"{'━' * W}")

    best = st.iloc[0]
    print(
        f"\n  ★  Best station: {best['station_name'] or best['station_key']}"
        f"  (key {int(best['station_key'])}, score {best['richness_score']:.3f})"
    )
    print(f"     Parameters ({int(best['n_params'])}): {best['parameters']}")
    print()


def print_parameter_table(pt: pd.DataFrame) -> None:
    W = 100
    print(f"\n{'━' * W}")
    print(
        f"  PARAMETERS — ranked by number of stations measuring them  ({len(pt)} total)"
    )
    print(f"{'━' * W}")
    print(
        f"  {'#':>3}  {'Code':<14}  {'Name':<24}  {'Stations':>8}  "
        f"  {'Valid%':>6}  {'Coverage bar':<22}  {'Total rows':>11}  {'Imputed':>8}"
    )
    print(
        f"  {'─' * 3}  {'─' * 14}  {'─' * 24}  {'─' * 8}  "
        f"  {'─' * 6}  {'─' * 22}  {'─' * 11}  {'─' * 8}"
    )

    for rank, row in pt.iterrows():
        name = str(row["parameter_name"] or "")[:24]
        print(
            f"  {rank:>3}  {str(row['parameter_code']):<14}  {name:<24}  "
            f"{int(row['n_stations']):>8}  "
            f"  {row['valid_pct']:>5.1f}%  {_bar(row['mean_coverage']):<22}  "
            f"{int(row['total_rows']):>11,}  {int(row['imputed_rows']):>8,}"
        )

    print(f"{'━' * W}\n")


def main() -> None:
    print(f"[richness] Connecting …  {DATABASE_URL.split('@')[-1]}")  # hide credentials
    engine = get_engine()

    print("[richness] Querying fact_measurements (may take a moment) …")
    with engine.connect() as conn:
        df = pd.read_sql(DETAIL_QUERY, conn)

    if df.empty:
        print("No data returned — check that fact_measurements is populated.")
        return

    print(f"[richness] Loaded {len(df):,} (station × parameter) pairs.\n")

    st = station_richness_table(df)
    pt = parameter_coverage_table(df)

    print_station_table(st)
    print_parameter_table(pt)

    st.to_csv("station_richness.csv")
    pt.to_csv("parameter_coverage.csv")
    print("[richness] Saved → station_richness.csv")
    print("[richness] Saved → parameter_coverage.csv")


if __name__ == "__main__":
    main()
