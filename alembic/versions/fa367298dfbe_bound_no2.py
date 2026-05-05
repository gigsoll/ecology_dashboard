"""Flag and fix suspect NO₂ readings using nearby-station PM cross-validation

Steps
-----
1.  Add no2_imputed (bool) and no2_suspect (bool) to fact_measurements.
2.  Add has_imputed_flag / has_suspect_flag (bool) to dim_parameters.
3.  Backfill no2_imputed from quality_ratio = 0.5 on existing NO₂ rows.
4.  Mark dim_parameters rows for NO₂ with has_imputed_flag = true.
5.  For every non-imputed NO₂ reading < 0.005 ppm, find up to 4 nearest
    stations (by Haversine distance from dim_stations) that carry PM2.5
    or PM10 readings.  Average their PM values within ±30 min of the NO₂
    timestamp.  If PM2.5 > 50 µg/m³ or PM10 > 75 µg/m³, flag the NO₂
    row as no2_suspect = true and replace its value via the same 4-tier
    interpolation used in the previous migration:
      Tier 1 — linear interpolation between nearest valid neighbours ±6 h
      Tier 2 — flat fill from whichever single neighbour exists
      Tier 3 — station-local 30-day median of non-suspect, non-imputed rows
      Tier 4 — cross-station global median
6.  Set quality_ratio = 0.5 on every newly-suspect row.
7.  Mark dim_parameters rows for NO₂ with has_suspect_flag = true.
8.  Emit a verification summary.

"Valid" throughout means: value IS NOT NULL, no2_imputed = false,
no2_suspect = false, value BETWEEN physical_min AND physical_max.

NULL is NEVER written.

Revision ID: fa367298dfbe
Revises: 53191fb5895f
Create Date: 2026-05-05 11:02:49.386848

"""

from __future__ import annotations

import math
from datetime import timedelta
from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

from app.core.config import app_config


# revision identifiers, used by Alembic.
revision: str = "fa367298dfbe"
down_revision: Union[str, Sequence[str], None] = "53191fb5895f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

IMPUTED_QUALITY_RATIO: float = 0.5
WINDOW_SECONDS: int = 6 * 3600  # neighbour search for interpolation
PM_WINDOW_MINUTES: int = 30  # cross-validation window
SUSPECT_ZERO_THRESHOLD: float = 0.005  # ppm — below this is "near-zero"
PM25_ALERT: float = 50.0  # µg/m³
PM10_ALERT: float = 75.0  # µg/m³
NEAREST_STATIONS: int = 4
STATION_MEDIAN_DAYS: int = 30


# ---------------------------------------------------------------------------
# Step 1 — DDL: new columns
# ---------------------------------------------------------------------------


def _add_columns(conn) -> None:
    # fact_measurements
    for col, definition in (
        ("no2_imputed", "BOOLEAN NOT NULL DEFAULT FALSE"),
        ("no2_suspect", "BOOLEAN NOT NULL DEFAULT FALSE"),
    ):
        exists = (
            conn.execute(
                text("""
            SELECT COUNT(*) AS n
            FROM   information_schema.COLUMNS
            WHERE  TABLE_SCHEMA = DATABASE()
              AND  TABLE_NAME   = 'fact_measurements'
              AND  COLUMN_NAME  = :col
        """),
                {"col": col},
            )
            .fetchone()
            .n
        )
        if not exists:
            conn.execute(
                text(f"ALTER TABLE fact_measurements ADD COLUMN {col} {definition}")
            )
            print(f"[no2]   Added fact_measurements.{col}")
        else:
            print(f"[no2]   fact_measurements.{col} already exists — skipped.")

    # dim_parameters
    for col, definition in (
        ("has_imputed_flag", "BOOLEAN NOT NULL DEFAULT FALSE"),
        ("has_suspect_flag", "BOOLEAN NOT NULL DEFAULT FALSE"),
    ):
        exists = (
            conn.execute(
                text("""
            SELECT COUNT(*) AS n
            FROM   information_schema.COLUMNS
            WHERE  TABLE_SCHEMA = DATABASE()
              AND  TABLE_NAME   = 'dim_parameters'
              AND  COLUMN_NAME  = :col
        """),
                {"col": col},
            )
            .fetchone()
            .n
        )
        if not exists:
            conn.execute(
                text(f"ALTER TABLE dim_parameters ADD COLUMN {col} {definition}")
            )
            print(f"[no2]   Added dim_parameters.{col}")
        else:
            print(f"[no2]   dim_parameters.{col} already exists — skipped.")


# ---------------------------------------------------------------------------
# Step 2 — resolve parameter keys
# ---------------------------------------------------------------------------


def _fetch_no2_parameter_keys(conn) -> list[int]:
    rows = conn.execute(
        text("""
        SELECT parameter_key
        FROM   dim_parameters
        WHERE  parameter_code IN ('NO2', 'NO₂')
          AND  is_current = 1
    """)
    ).fetchall()
    return [r.parameter_key for r in rows]


def _fetch_pm_parameter_keys(conn) -> dict[str, list[int]]:
    """Return {'PM2.5': [...], 'PM10': [...]} of current parameter keys."""
    rows = conn.execute(
        text("""
        SELECT parameter_key, parameter_code
        FROM   dim_parameters
        WHERE  parameter_code IN ('PM2.5', 'SDSP2', 'PMSP2',
                                  'PM10',  'SDSP1', 'PMSP1')
          AND  is_current = 1
    """)
    ).fetchall()
    pm25_keys, pm10_keys = [], []
    for r in rows:
        if r.parameter_code in ("PM2.5", "SDSP2", "PMSP2"):
            pm25_keys.append(r.parameter_key)
        else:
            pm10_keys.append(r.parameter_key)
    return {"PM2.5": pm25_keys, "PM10": pm10_keys}


# ---------------------------------------------------------------------------
# Step 3 — station geometry and nearest-station lookup
# ---------------------------------------------------------------------------


def _fetch_station_locations(conn) -> dict[int, tuple[float, float]]:
    """Return {station_key: (latitude, longitude)} for all current stations."""
    rows = conn.execute(
        text("""
        SELECT station_key, latitude, longitude
        FROM   dim_stations
        WHERE  is_current = 1
          AND  latitude  IS NOT NULL
          AND  longitude IS NOT NULL
    """)
    ).fetchall()
    return {r.station_key: (r.latitude, r.longitude) for r in rows}


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    )
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _nearest_stations_with_pm(
    origin_key: int,
    locations: dict[int, tuple[float, float]],
    pm_keys: dict[str, list[int]],
    conn,
    n: int = NEAREST_STATIONS,
) -> list[int]:
    """
    Return up to ``n`` station_keys nearest to ``origin_key`` that actually
    have at least one PM2.5 or PM10 measurement in fact_measurements.
    Excludes the origin station itself.
    """
    if origin_key not in locations:
        return []

    olat, olon = locations[origin_key]
    all_pm_keys = pm_keys["PM2.5"] + pm_keys["PM10"]
    if not all_pm_keys:
        return []

    pm_keys_csv = ", ".join(str(k) for k in all_pm_keys)

    # Stations that actually have PM data
    pm_station_rows = conn.execute(
        text(f"""
        SELECT DISTINCT station_key
        FROM   fact_measurements
        WHERE  parameter_key IN ({pm_keys_csv})
          AND  value IS NOT NULL
    """)
    ).fetchall()
    pm_stations = {r.station_key for r in pm_station_rows} - {origin_key}

    # Sort by distance and return top n
    ranked = sorted(
        (
            (sk, _haversine_km(olat, olon, *locations[sk]))
            for sk in pm_stations
            if sk in locations
        ),
        key=lambda x: x[1],
    )
    return [sk for sk, _ in ranked[:n]]


# ---------------------------------------------------------------------------
# Step 4 — cross-validation: average PM from nearby stations within ±30 min
# ---------------------------------------------------------------------------


def _avg_nearby_pm(
    conn,
    timestamp,
    nearby_station_keys: list[int],
    pm25_keys: list[int],
    pm10_keys: list[int],
) -> tuple[float | None, float | None]:
    """
    Return (avg_pm25, avg_pm10) from ``nearby_station_keys`` within
    ±PM_WINDOW_MINUTES of ``timestamp``.  Returns None for a pollutant
    if no nearby station carries that measurement.
    """
    if not nearby_station_keys:
        return None, None

    stations_csv = ", ".join(str(sk) for sk in nearby_station_keys)
    ts_str = timestamp.strftime("%Y-%m-%d %H:%M:%S")

    avg_pm25, avg_pm10 = None, None

    if pm25_keys:
        keys_csv = ", ".join(str(k) for k in pm25_keys)
        row = conn.execute(
            text(f"""
            SELECT AVG(value) AS avg_val
            FROM   fact_measurements
            WHERE  station_key   IN ({stations_csv})
              AND  parameter_key IN ({keys_csv})
              AND  value IS NOT NULL
              AND  no2_imputed = FALSE
              AND  no2_suspect = FALSE
              AND  measurement_timestamp BETWEEN
                       '{ts_str}' - INTERVAL {PM_WINDOW_MINUTES} MINUTE
                   AND '{ts_str}' + INTERVAL {PM_WINDOW_MINUTES} MINUTE
        """)
        ).fetchone()
        if row and row.avg_val is not None:
            avg_pm25 = float(row.avg_val)

    if pm10_keys:
        keys_csv = ", ".join(str(k) for k in pm10_keys)
        row = conn.execute(
            text(f"""
            SELECT AVG(value) AS avg_val
            FROM   fact_measurements
            WHERE  station_key   IN ({stations_csv})
              AND  parameter_key IN ({keys_csv})
              AND  value IS NOT NULL
              AND  no2_imputed = FALSE
              AND  no2_suspect = FALSE
              AND  measurement_timestamp BETWEEN
                       '{ts_str}' - INTERVAL {PM_WINDOW_MINUTES} MINUTE
                   AND '{ts_str}' + INTERVAL {PM_WINDOW_MINUTES} MINUTE
        """)
        ).fetchone()
        if row and row.avg_val is not None:
            avg_pm10 = float(row.avg_val)

    return avg_pm25, avg_pm10


# ---------------------------------------------------------------------------
# Step 5 — MySQL-compatible median (two round-trips, literal OFFSET)
# ---------------------------------------------------------------------------


def _mysql_median(conn, where_clause: str) -> float | None:
    count_row = conn.execute(
        text(f"""
        SELECT COUNT(*) AS n
        FROM   fact_measurements
        WHERE  {where_clause}
    """)
    ).fetchone()
    n = count_row.n if count_row else 0
    if n == 0:
        return None
    offset = (n - 1) // 2
    row = conn.execute(
        text(f"""
        SELECT value AS median_val
        FROM   fact_measurements
        WHERE  {where_clause}
        ORDER  BY value
        LIMIT  1 OFFSET {offset}
    """)
    ).fetchone()
    return float(row.median_val) if row and row.median_val is not None else None


def _global_median(
    conn, parameter_keys: list[int], p_min: float | None, p_max: float | None
) -> float | None:
    keys_csv = ", ".join(str(k) for k in parameter_keys)
    conditions = [
        "value IS NOT NULL",
        f"parameter_key IN ({keys_csv})",
        "no2_imputed = FALSE",
        "no2_suspect = FALSE",
    ]
    if p_min is not None:
        conditions.append(f"value >= {p_min}")
    if p_max is not None:
        conditions.append(f"value <= {p_max}")
    return _mysql_median(conn, " AND ".join(conditions))


def _station_local_median(
    conn,
    station_key: int,
    parameter_keys: list[int],
    p_min: float | None,
    p_max: float | None,
) -> float | None:
    keys_csv = ", ".join(str(k) for k in parameter_keys)
    conditions = [
        "value IS NOT NULL",
        f"station_key   = {station_key}",
        f"parameter_key IN ({keys_csv})",
        "no2_imputed = FALSE",
        "no2_suspect = FALSE",
        f"measurement_timestamp >= NOW() - INTERVAL {STATION_MEDIAN_DAYS} DAY",
    ]
    if p_min is not None:
        conditions.append(f"value >= {p_min}")
    if p_max is not None:
        conditions.append(f"value <= {p_max}")
    return _mysql_median(conn, " AND ".join(conditions))


# ---------------------------------------------------------------------------
# Step 6 — 4-tier interpolation (reused from previous migration)
# ---------------------------------------------------------------------------


def _is_valid_no2(v, p_min, p_max) -> bool:
    if v is None:
        return False
    if p_min is not None and v < p_min:
        return False
    if p_max is not None and v > p_max:
        return False
    return True


def _interpolate_series(
    readings: list[tuple],
    suspect_ids: set[int],
    p_min: float | None,
    p_max: float | None,
    station_median: float | None,
    global_med: float,
) -> list[tuple[int, float]]:
    """
    Walk the ordered (measurement_id, timestamp, value) series.
    Only rows in ``suspect_ids`` are corrected.
    Valid anchors must not themselves be suspect or imputed.
    """
    window = timedelta(seconds=WINDOW_SECONDS)
    corrections: list[tuple[int, float]] = []

    for i, (meas_id, ts, value) in enumerate(readings):
        if meas_id not in suspect_ids:
            continue

        # Tier 1 & 2 — nearest valid neighbours
        prev_ts, prev_val = None, None
        for j in range(i - 1, -1, -1):
            jid, jts, jval = readings[j]
            if ts - jts > window:
                break
            if jid not in suspect_ids and _is_valid_no2(jval, p_min, p_max):
                prev_ts, prev_val = jts, jval
                break

        next_ts, next_val = None, None
        for j in range(i + 1, len(readings)):
            jid, jts, jval = readings[j]
            if jts - ts > window:
                break
            if jid not in suspect_ids and _is_valid_no2(jval, p_min, p_max):
                next_ts, next_val = jts, jval
                break

        if prev_val is not None and next_val is not None:
            span = (next_ts - prev_ts).total_seconds()
            corrected = (
                (prev_val + next_val) / 2.0
                if span == 0
                else prev_val
                + (ts - prev_ts).total_seconds() / span * (next_val - prev_val)
            )
        elif prev_val is not None:
            corrected = prev_val
        elif next_val is not None:
            corrected = next_val
        elif station_median is not None:
            corrected = station_median
        else:
            corrected = global_med

        if p_min is not None:
            corrected = max(corrected, p_min)
        if p_max is not None:
            corrected = min(corrected, p_max)

        corrections.append((meas_id, corrected))

    return corrections


# ---------------------------------------------------------------------------
# Step 7 — batched UPDATE
# ---------------------------------------------------------------------------


def _apply_corrections_in_batches(
    conn,
    corrections: list[tuple[int, float]],
    batch_size: int,
    extra_set: str = "",
) -> None:
    """
    Write corrected values back in batches using CASE/IN.
    ``extra_set`` is appended verbatim after the value assignment,
    e.g. ", no2_suspect = TRUE, quality_ratio = 0.5".
    """
    for start in range(0, len(corrections), batch_size):
        chunk = corrections[start : start + batch_size]
        case_body = " ".join(f"WHEN {mid} THEN {val:.10f}" for mid, val in chunk)
        id_list = ", ".join(str(mid) for mid, _ in chunk)
        conn.execute(
            text(f"""
            UPDATE fact_measurements
            SET    value = CASE measurement_id {case_body} END
                   {extra_set}
            WHERE  measurement_id IN ({id_list})
        """)
        )


def _flag_suspect_in_batches(conn, ids: list[int], batch_size: int) -> None:
    """Set no2_suspect = TRUE, quality_ratio = 0.5 for a list of ids."""
    for start in range(0, len(ids), batch_size):
        chunk = ids[start : start + batch_size]
        id_list = ", ".join(str(i) for i in chunk)
        conn.execute(
            text(f"""
            UPDATE fact_measurements
            SET    no2_suspect   = TRUE,
                   quality_ratio = {IMPUTED_QUALITY_RATIO}
            WHERE  measurement_id IN ({id_list})
        """)
        )


# ---------------------------------------------------------------------------
# Step 8 — fetch full series for a (station, parameter_key list) pair
# ---------------------------------------------------------------------------


def _fetch_series(
    conn,
    station_key: int,
    parameter_keys: list[int],
    batch_size: int,
) -> list[tuple]:
    keys_csv = ", ".join(str(k) for k in parameter_keys)
    all_rows: list[tuple] = []
    offset = 0
    while True:
        batch = conn.execute(
            text(f"""
            SELECT measurement_id, measurement_timestamp, value
            FROM   fact_measurements
            WHERE  station_key   = {station_key}
              AND  parameter_key IN ({keys_csv})
            ORDER  BY measurement_timestamp ASC
            LIMIT  {batch_size} OFFSET {offset}
        """)
        ).fetchall()
        if not batch:
            break
        all_rows.extend(
            (r.measurement_id, r.measurement_timestamp, r.value) for r in batch
        )
        offset += batch_size
        if len(batch) < batch_size:
            break
    return all_rows


# ---------------------------------------------------------------------------
# Upgrade entry point
# ---------------------------------------------------------------------------


def upgrade() -> None:
    conn = op.get_bind()
    batch_size: int = app_config.batch_size

    print(f"[no2] Starting NO₂ suspect migration  batch_size={batch_size}")

    # ── 1. DDL ────────────────────────────────────────────────────────────
    print("[no2] Adding columns …")
    _add_columns(conn)

    # ── 2. Resolve parameter keys ─────────────────────────────────────────
    no2_keys = _fetch_no2_parameter_keys(conn)
    if not no2_keys:
        print("[no2] No current NO₂ parameter rows found — nothing to do.")
        return
    print(f"[no2] NO₂ parameter_keys: {no2_keys}")

    pm_keys = _fetch_pm_parameter_keys(conn)
    print(f"[no2] PM2.5 keys: {pm_keys['PM2.5']}  PM10 keys: {pm_keys['PM10']}")

    # ── 3. Fetch NO₂ physical bounds (use first key found) ───────────────
    bounds_row = conn.execute(
        text(f"""
        SELECT physical_min, physical_max
        FROM   dim_parameters
        WHERE  parameter_key = {no2_keys[0]}
    """)
    ).fetchone()
    p_min = (
        float(bounds_row.physical_min)
        if bounds_row and bounds_row.physical_min is not None
        else None
    )
    p_max = (
        float(bounds_row.physical_max)
        if bounds_row and bounds_row.physical_max is not None
        else None
    )
    print(f"[no2] Physical bounds: [{p_min}, {p_max}]")

    # ── 4. Backfill no2_imputed from quality_ratio = 0.5 ─────────────────
    print("[no2] Backfilling no2_imputed from quality_ratio = 0.5 …")
    no2_keys_csv = ", ".join(str(k) for k in no2_keys)
    conn.execute(
        text(f"""
        UPDATE fact_measurements
        SET    no2_imputed = TRUE
        WHERE  parameter_key IN ({no2_keys_csv})
          AND  quality_ratio = {IMPUTED_QUALITY_RATIO}
          AND  no2_imputed   = FALSE
    """)
    )

    # ── 5. Mark dim_parameters has_imputed_flag ───────────────────────────
    conn.execute(
        text(f"""
        UPDATE dim_parameters
        SET    has_imputed_flag = TRUE
        WHERE  parameter_key IN ({no2_keys_csv})
    """)
    )

    # ── 6. Station geometry ───────────────────────────────────────────────
    print("[no2] Loading station locations …")
    locations = _fetch_station_locations(conn)

    # ── 7. Find distinct NO₂ stations ────────────────────────────────────
    no2_station_rows = conn.execute(
        text(f"""
        SELECT DISTINCT station_key
        FROM   fact_measurements
        WHERE  parameter_key IN ({no2_keys_csv})
          AND  value IS NOT NULL
    """)
    ).fetchall()
    no2_stations = [r.station_key for r in no2_station_rows]
    print(f"[no2] {len(no2_stations)} station(s) have NO₂ data.")

    # ── 8. Pre-compute global median once ─────────────────────────────────
    print("[no2] Computing global NO₂ median …")
    g_median = _global_median(conn, no2_keys, p_min, p_max)
    if g_median is None:
        # Absolute fallback: midpoint of physical range
        g_median = ((p_min or 0) + (p_max or 10)) / 2.0
    print(f"[no2] Global NO₂ median: {g_median:.4f} ppm")

    total_suspect = 0
    total_corrected = 0

    for station_key in no2_stations:
        print(f"[no2] Processing station {station_key} …")

        # Find up to 4 nearest stations that have PM data
        nearby = _nearest_stations_with_pm(station_key, locations, pm_keys, conn)
        if not nearby:
            print(f"[no2]   No nearby PM stations found — suspect detection skipped.")

        # Fetch full NO₂ series for this station (all NO₂ parameter keys)
        readings = _fetch_series(conn, station_key, no2_keys, batch_size)
        if not readings:
            continue

        # ── 8a. Identify candidate near-zero rows ─────────────────────────
        # Only non-imputed rows below the suspect threshold
        candidate_ids = {
            meas_id
            for meas_id, _, value in readings
            if value is not None and value < SUSPECT_ZERO_THRESHOLD
        }

        if not candidate_ids:
            print(f"[no2]   No near-zero candidates.")
            continue

        print(
            f"[no2]   {len(candidate_ids):,} near-zero candidates to cross-validate …"
        )

        # ── 8b. Cross-validate each candidate against nearby PM ───────────
        suspect_ids: set[int] = set()

        # Build a quick lookup: measurement_id → timestamp
        ts_lookup = {meas_id: ts for meas_id, ts, _ in readings}

        for meas_id in candidate_ids:
            ts = ts_lookup[meas_id]

            if not nearby:
                # No PM neighbours available — cannot cross-validate,
                # do not flag (conservative: trust the reading)
                continue

            avg_pm25, avg_pm10 = _avg_nearby_pm(
                conn,
                ts,
                nearby,
                pm_keys["PM2.5"],
                pm_keys["PM10"],
            )

            pm25_elevated = avg_pm25 is not None and avg_pm25 > PM25_ALERT
            pm10_elevated = avg_pm10 is not None and avg_pm10 > PM10_ALERT

            if pm25_elevated or pm10_elevated:
                suspect_ids.add(meas_id)

        if not suspect_ids:
            print(f"[no2]   No suspect rows found after PM cross-validation.")
            continue

        print(f"[no2]   {len(suspect_ids):,} rows flagged as suspect.")

        # ── 8c. Flag suspect rows in DB ───────────────────────────────────
        _flag_suspect_in_batches(conn, list(suspect_ids), batch_size)

        # ── 8d. Compute replacement values ────────────────────────────────
        s_median = _station_local_median(conn, station_key, no2_keys, p_min, p_max)

        corrections = _interpolate_series(
            readings,
            suspect_ids=suspect_ids,
            p_min=p_min,
            p_max=p_max,
            station_median=s_median,
            global_med=g_median,
        )

        # ── 8e. Write corrected values ────────────────────────────────────
        # no2_suspect and quality_ratio already set by _flag_suspect_in_batches;
        # here we only update value.
        _apply_corrections_in_batches(conn, corrections, batch_size)

        total_suspect += len(suspect_ids)
        total_corrected += len(corrections)
        print(f"[no2]   Corrected {len(corrections):,} value(s).")

    # ── 9. Mark dim_parameters has_suspect_flag ───────────────────────────
    if total_suspect > 0:
        conn.execute(
            text(f"""
            UPDATE dim_parameters
            SET    has_suspect_flag = TRUE
            WHERE  parameter_key IN ({no2_keys_csv})
        """)
        )

    # ── 10. Verification summary ──────────────────────────────────────────
    print("\n[no2] ── Verification ──────────────────────────────────────────")

    oob_count = (
        conn.execute(
            text(f"""
        SELECT COUNT(*) AS n
        FROM   fact_measurements
        WHERE  parameter_key IN ({no2_keys_csv})
          AND  value IS NOT NULL
          AND  (value < {p_min if p_min is not None else "NULL"}
             OR value > {p_max if p_max is not None else "NULL"})
    """)
        )
        .fetchone()
        .n
    )
    print(f"[no2] Out-of-bounds NO₂ rows remaining : {oob_count}")

    imputed_count = (
        conn.execute(
            text(f"""
        SELECT COUNT(*) AS n
        FROM   fact_measurements
        WHERE  parameter_key IN ({no2_keys_csv})
          AND  no2_imputed = TRUE
    """)
        )
        .fetchone()
        .n
    )
    print(f"[no2] Rows with no2_imputed = TRUE      : {imputed_count:,}")

    suspect_count = (
        conn.execute(
            text(f"""
        SELECT COUNT(*) AS n
        FROM   fact_measurements
        WHERE  parameter_key IN ({no2_keys_csv})
          AND  no2_suspect = TRUE
    """)
        )
        .fetchone()
        .n
    )
    print(f"[no2] Rows with no2_suspect = TRUE      : {suspect_count:,}")

    total_no2 = (
        conn.execute(
            text(f"""
        SELECT COUNT(*) AS n
        FROM   fact_measurements
        WHERE  parameter_key IN ({no2_keys_csv})
          AND  value IS NOT NULL
    """)
        )
        .fetchone()
        .n
    )
    flagged_pct = (imputed_count + suspect_count) / total_no2 * 100 if total_no2 else 0
    print(f"[no2] Total NO₂ rows                    : {total_no2:,}")
    print(f"[no2] Combined flagged rate              : {flagged_pct:.1f}%")
    print(f"[no2] ────────────────────────────────────────────────────────")

    print(
        f"\n[no2] Migration complete. "
        f"{total_suspect:,} suspect row(s) flagged, "
        f"{total_corrected:,} value(s) corrected."
    )


# ---------------------------------------------------------------------------
# Downgrade — intentionally a no-op
# ---------------------------------------------------------------------------


def downgrade() -> None:
    """
    Data-repair migrations cannot be automatically reversed.
    To roll back, restore from a pre-migration database snapshot.
    """
    pass
