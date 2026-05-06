"""Fix out-of-bounds fact_measurements values via interpolation (MySQL, batched)

Strategy
--------
For every (station_key, parameter_key) series that contains at least one value
outside [physical_min, physical_max]:

  1. Linear interpolation between the nearest valid neighbours within ±6 h
     (station-local, same series).
  2. Flat fill from whichever single valid neighbour exists within ±6 h
     (station-local).
  3. Station-local median — median of all valid readings for that
     (station_key, parameter_key) pair over the last 30 days.
  4. Cross-station global median — median of all valid readings for that
     parameter_key across every station.  True last resort (e.g. sensor
     was offline for the entire 30-day window).

NULL is NEVER written.  Every corrected row gets quality_ratio = 0.5 to
signal to downstream ML that the value is synthetic.

Batching
--------
The fact table has ~7 M rows.  Affected series are processed (station, param)
pair by (station, param) pair, but within each series rows are fetched and
updated in pages of ``app_config.batch_size`` rows to keep memory bounded and
avoid long-running transactions.

MySQL notes
-----------
* Uses a portable subquery median compatible with MySQL 5.7+.
  (MySQL < 8 has no PERCENTILE_CONT; MySQL 8 has it only as a window
  function, not an aggregate.)
* Parameter binding uses :name style (SQLAlchemy text() dialect).
* No PostgreSQL-specific casts, ANY(), VALUES-CTE, or OFFSET expressions
  that require a subquery — all replaced with MySQL-safe equivalents.
* UPDATE uses a CASE expression over an IN list (MySQL has no
  UPDATE … FROM … VALUES syntax).

Revision ID: 53191fb5895f
Revises: facdd25e45cb
Create Date: 2026-05-05 08:51:11.318492

"""

from __future__ import annotations

from datetime import timedelta
from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

from app.core.config import app_config


# revision identifiers, used by Alembic.
revision: str = "53191fb5895f"
down_revision: Union[str, Sequence[str], None] = "facdd25e45cb"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

WINDOW_SECONDS: int = 6 * 3600
IMPUTED_QUALITY_RATIO: float = 0.5
STATION_MEDIAN_DAYS: int = 30


# ---------------------------------------------------------------------------
# MySQL-compatible median — two-step approach
#
# MySQL forbids a subquery inside LIMIT / OFFSET, so we cannot write:
#   LIMIT 1 OFFSET (SELECT FLOOR(...) FROM ...)
#
# Instead we split into two round-trips, both cheap (COUNT uses the index;
# the value fetch uses LIMIT 1 on a sorted scan):
#   1. SELECT COUNT(*) → compute offset in Python as (n - 1) // 2
#   2. SELECT value … ORDER BY value LIMIT 1 OFFSET <python_int>
#
# This gives the lower-median convention for even N, which is perfectly
# adequate for an imputation fallback.
# ---------------------------------------------------------------------------


def _mysql_median(conn, where_clause: str) -> float | None:
    """
    Compute the median of fact_measurements.value for rows matching
    ``where_clause``.  Returns None when no matching rows exist.

    ``where_clause`` must reference the table without an alias
    (e.g. "value IS NOT NULL AND parameter_key = 3").
    """
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

    offset = (n - 1) // 2  # lower-median index, safe Python int

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


# ---------------------------------------------------------------------------
# Step 1 — parameter bounds
# ---------------------------------------------------------------------------


def _fetch_parameter_bounds(conn) -> dict[int, tuple[float | None, float | None]]:
    rows = conn.execute(
        text("""
        SELECT parameter_key, physical_min, physical_max
        FROM   dim_parameters
        WHERE  physical_min IS NOT NULL
           OR  physical_max IS NOT NULL
    """)
    ).fetchall()
    return {r.parameter_key: (r.physical_min, r.physical_max) for r in rows}


# ---------------------------------------------------------------------------
# Step 2 — cross-station global medians (tier-4 fallback, computed once)
# ---------------------------------------------------------------------------


def _fetch_global_medians(
    conn,
    bounds: dict[int, tuple[float | None, float | None]],
) -> dict[int, float]:
    medians: dict[int, float] = {}

    for pk, (p_min, p_max) in bounds.items():
        conditions = ["value IS NOT NULL", f"parameter_key = {pk}"]
        if p_min is not None:
            conditions.append(f"value >= {p_min}")
        if p_max is not None:
            conditions.append(f"value <= {p_max}")
        where = " AND ".join(conditions)

        result = _mysql_median(conn, where)
        if result is not None:
            medians[pk] = result

    return medians


# ---------------------------------------------------------------------------
# Step 3 — station-local 30-day median (tier-3 fallback, computed per series)
# ---------------------------------------------------------------------------


def _fetch_station_local_median(
    conn,
    station_key: int,
    parameter_key: int,
    p_min: float | None,
    p_max: float | None,
) -> float | None:
    conditions = [
        "value IS NOT NULL",
        f"station_key   = {station_key}",
        f"parameter_key = {parameter_key}",
        f"measurement_timestamp >= NOW() - INTERVAL {STATION_MEDIAN_DAYS} DAY",
    ]
    if p_min is not None:
        conditions.append(f"value >= {p_min}")
    if p_max is not None:
        conditions.append(f"value <= {p_max}")
    where = " AND ".join(conditions)

    return _mysql_median(conn, where)


# ---------------------------------------------------------------------------
# Step 4 — affected (station, parameter) pairs
# ---------------------------------------------------------------------------


def _fetch_affected_series(
    conn,
    bounds: dict[int, tuple[float | None, float | None]],
) -> list[tuple[int, int]]:
    """
    One query per parameter (MySQL has no array binding / VALUES-CTE).
    Returns distinct (station_key, parameter_key) pairs.
    """
    affected: list[tuple[int, int]] = []

    for pk, (p_min, p_max) in bounds.items():
        oob_parts = []
        if p_min is not None:
            oob_parts.append(f"fm.value < {p_min}")
        if p_max is not None:
            oob_parts.append(f"fm.value > {p_max}")
        if not oob_parts:
            continue

        oob_clause = " OR ".join(oob_parts)

        rows = conn.execute(
            text(f"""
            SELECT DISTINCT fm.station_key
            FROM   fact_measurements fm
            WHERE  fm.parameter_key = {pk}
              AND  fm.value IS NOT NULL
              AND  ({oob_clause})
        """)
        ).fetchall()

        for row in rows:
            affected.append((row.station_key, pk))

    return affected


# ---------------------------------------------------------------------------
# Step 5 — interpolation (pure Python, operates on a fully-loaded series)
# ---------------------------------------------------------------------------


def _interpolate_series(
    readings: list[tuple],  # (measurement_id, timestamp, value)
    p_min: float | None,
    p_max: float | None,
    station_median: float | None,  # tier-3 fallback
    global_median: float,  # tier-4 fallback, guaranteed non-NULL
    window_seconds: int,
) -> list[tuple[int, float]]:

    def is_valid(v: float | None) -> bool:
        if v is None:
            return False
        if p_min is not None and v < p_min:
            return False
        if p_max is not None and v > p_max:
            return False
        return True

    window = timedelta(seconds=window_seconds)
    corrections: list[tuple[int, float]] = []

    for i, (meas_id, ts, value) in enumerate(readings):
        if is_valid(value):
            continue

        # ── Search for nearest valid predecessor within window ────────────
        prev_ts, prev_val = None, None
        for j in range(i - 1, -1, -1):
            _, jts, jval = readings[j]
            if ts - jts > window:
                break
            if is_valid(jval):
                prev_ts, prev_val = jts, jval
                break

        # ── Search for nearest valid successor within window ──────────────
        next_ts, next_val = None, None
        for j in range(i + 1, len(readings)):
            _, jts, jval = readings[j]
            if jts - ts > window:
                break
            if is_valid(jval):
                next_ts, next_val = jts, jval
                break

        # ── Choose replacement value ──────────────────────────────────────
        if prev_val is not None and next_val is not None:
            # Tier 1 — linear interpolation
            span = (next_ts - prev_ts).total_seconds()
            if span == 0:
                corrected = (prev_val + next_val) / 2.0
            else:
                t_frac = (ts - prev_ts).total_seconds() / span
                corrected = prev_val + t_frac * (next_val - prev_val)

        elif prev_val is not None:
            # Tier 2 — flat forward-fill
            corrected = prev_val

        elif next_val is not None:
            # Tier 2 — flat back-fill
            corrected = next_val

        elif station_median is not None:
            # Tier 3 — station-local 30-day median
            corrected = station_median

        else:
            # Tier 4 — cross-station global median
            corrected = global_median

        # Safety clamp: guards against tiny floating-point overshoot at
        # the boundary when both anchors are close to the limit.
        if p_min is not None:
            corrected = max(corrected, p_min)
        if p_max is not None:
            corrected = min(corrected, p_max)

        corrections.append((meas_id, corrected))

    return corrections


# ---------------------------------------------------------------------------
# Step 6 — batched UPDATE (MySQL CASE/IN approach)
# ---------------------------------------------------------------------------


def _apply_corrections_in_batches(
    conn,
    corrections: list[tuple[int, float]],
    batch_size: int,
) -> None:
    """
    MySQL does not support UPDATE … FROM … VALUES, so we use:

        UPDATE fact_measurements
        SET value = CASE measurement_id
                        WHEN <id1> THEN <val1>
                        WHEN <id2> THEN <val2>
                        …
                    END,
            quality_ratio = 0.5
        WHERE measurement_id IN (<id1>, <id2>, …)

    The WHERE … IN clause makes MySQL use the primary key index directly,
    so the CASE scan is O(batch) not O(table).
    """
    for start in range(0, len(corrections), batch_size):
        chunk = corrections[start : start + batch_size]

        case_body = " ".join(
            f"WHEN {mid} THEN {new_val:.10f}" for mid, new_val in chunk
        )
        id_list = ", ".join(str(mid) for mid, _ in chunk)

        conn.execute(
            text(f"""
            UPDATE fact_measurements
            SET    value         = CASE measurement_id {case_body} END,
                   quality_ratio = {IMPUTED_QUALITY_RATIO}
            WHERE  measurement_id IN ({id_list})
        """)
        )


# ---------------------------------------------------------------------------
# Step 7 — paginated series fetch
# ---------------------------------------------------------------------------


def _fetch_series_in_batches(
    conn,
    station_key: int,
    parameter_key: int,
    batch_size: int,
) -> list[tuple]:
    """
    Fetches the complete ordered time-series for one (station, parameter) pair
    in pages of ``batch_size`` rows.  Stitching into a single list is
    intentional: the neighbour walk in _interpolate_series needs global
    visibility of the series to find anchors across page boundaries.
    """
    all_rows: list[tuple] = []
    offset = 0

    while True:
        batch = conn.execute(
            text("""
            SELECT measurement_id, measurement_timestamp, value
            FROM   fact_measurements
            WHERE  station_key   = :sk
              AND  parameter_key = :pk
            ORDER  BY measurement_timestamp ASC
            LIMIT  :lim OFFSET :off
        """),
            {
                "sk": station_key,
                "pk": parameter_key,
                "lim": batch_size,
                "off": offset,
            },
        ).fetchall()

        if not batch:
            break

        all_rows.extend(
            (row.measurement_id, row.measurement_timestamp, row.value) for row in batch
        )
        offset += batch_size

        if len(batch) < batch_size:
            break  # last page

    return all_rows


# ---------------------------------------------------------------------------
# Upgrade entry point
# ---------------------------------------------------------------------------


def upgrade() -> None:
    conn = op.get_bind()
    batch_size: int = app_config.batch_size

    print(f"[fix_oob] Starting migration  batch_size={batch_size}")

    # 1. Bounds
    print("[fix_oob] Loading parameter bounds …")
    bounds = _fetch_parameter_bounds(conn)
    if not bounds:
        print("[fix_oob] No parameters have physical bounds — nothing to do.")
        return
    print(f"[fix_oob] {len(bounds)} parameter(s) have physical bounds.")

    # 2. Global medians (tier-4 fallback, computed once up front)
    print("[fix_oob] Computing cross-station global medians …")
    global_medians = _fetch_global_medians(conn, bounds)

    # Guarantee a non-NULL global fallback even when ALL readings are
    # out-of-bounds for a parameter (e.g. NH₃ all-zero scenario).
    for pk, (p_min, p_max) in bounds.items():
        if pk not in global_medians:
            if p_min is not None and p_max is not None:
                global_medians[pk] = (p_min + p_max) / 2.0
            elif p_min is not None:
                global_medians[pk] = p_min
            elif p_max is not None:
                global_medians[pk] = p_max
            else:
                global_medians[pk] = (
                    0.0  # unreachable given _fetch_parameter_bounds filter
                )

    # 3. Affected series
    print("[fix_oob] Identifying affected (station, parameter) series …")
    affected = _fetch_affected_series(conn, bounds)
    print(f"[fix_oob] {len(affected)} series contain out-of-bounds readings.")

    total_corrected = 0

    for idx, (station_key, parameter_key) in enumerate(affected, 1):
        p_min, p_max = bounds[parameter_key]
        g_median = global_medians[parameter_key]

        print(
            f"[fix_oob] [{idx}/{len(affected)}] "
            f"station={station_key} parameter={parameter_key} …"
        )

        # Tier-3 fallback: station-local 30-day median
        station_median = _fetch_station_local_median(
            conn, station_key, parameter_key, p_min, p_max
        )

        # Pull full series in pages (neighbour walk needs global visibility)
        readings = _fetch_series_in_batches(
            conn, station_key, parameter_key, batch_size
        )
        if not readings:
            continue

        # Compute corrections in Python
        corrections = _interpolate_series(
            readings,
            p_min=p_min,
            p_max=p_max,
            station_median=station_median,
            global_median=g_median,
            window_seconds=WINDOW_SECONDS,
        )
        if not corrections:
            continue

        # Push corrections back to DB in batches
        _apply_corrections_in_batches(conn, corrections, batch_size)
        total_corrected += len(corrections)

        print(f"[fix_oob]   corrected {len(corrections)} row(s).")

    print(
        f"[fix_oob] Migration complete. "
        f"{total_corrected} measurement(s) corrected across "
        f"{len(affected)} series."
    )


# ---------------------------------------------------------------------------
# Downgrade — intentionally a no-op
# ---------------------------------------------------------------------------


def downgrade() -> None:
    """
    Data-repair migrations cannot be automatically reversed.
    The original values were invalid sensor readings — restoring them would
    re-introduce corrupted data into the ML pipeline.
    To roll back, restore from a pre-migration database snapshot.
    """
    pass
