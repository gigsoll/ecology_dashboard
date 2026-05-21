from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import Float, and_, case, cast, func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.schemas import (
    DashboardCorrelation,
    DashboardCorrelationItem,
    DashboardMeasurementRow,
    DashboardMeasurementsTable,
    DashboardStationRating,
    DashboardStationRatingItem,
    DashboardSummary,
    DashboardTimePoint,
    DashboardTimeSeries,
    DashboardViolationPoint,
    DashboardViolationSeries,
)
from app.db.schemas import DimParameter, DimStation, DimUnit, FactMeasurement

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

DEFAULT_WINDOW_DAYS = 7


def normalize_datetime(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC).replace(tzinfo=None)
    return datetime(
        year=dt.year,
        month=dt.month,
        day=dt.day,
        hour=dt.hour,
        minute=dt.minute,
        second=dt.second,
        microsecond=dt.microsecond,
    )


def apply_dimension_filters(
    stmt: Any,
    *,
    station_id: int | None,
    parameter_code: str | None,
) -> Any:
    if station_id is not None:
        stmt = stmt.where(DimStation.station_id == station_id)
    if parameter_code is not None:
        stmt = stmt.where(DimParameter.parameter_code == parameter_code)
    return stmt


def apply_time_filters(
    stmt: Any,
    *,
    from_ts: datetime | None,
    to_ts: datetime | None,
) -> Any:
    from_ts = normalize_datetime(from_ts)
    to_ts = normalize_datetime(to_ts)
    if from_ts is not None:
        stmt = stmt.where(FactMeasurement.measurement_timestamp >= from_ts)
    if to_ts is not None:
        stmt = stmt.where(FactMeasurement.measurement_timestamp <= to_ts)
    return stmt


def require_station_exists(
    db: Session,
    *,
    station_id: int,
) -> None:
    stmt = select(func.count()).select_from(DimStation).where(
        DimStation.station_id == station_id
    )
    if not db.scalar(stmt):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Station not found.",
        )


def require_parameter_exists(
    db: Session,
    *,
    parameter_code: str,
) -> None:
    stmt = select(func.count()).select_from(DimParameter).where(
        DimParameter.parameter_code == parameter_code
    )
    if not db.scalar(stmt):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Parameter not found.",
        )


def get_effective_window(
    db: Session,
    *,
    station_id: int | None,
    parameter_code: str,
    from_ts: datetime | None,
    to_ts: datetime | None,
) -> tuple[datetime, datetime]:
    from_ts = normalize_datetime(from_ts)
    to_ts = normalize_datetime(to_ts)
    stmt = (
        select(func.max(FactMeasurement.measurement_timestamp))
        .select_from(FactMeasurement)
        .join(DimStation, FactMeasurement.station_key == DimStation.station_key)
        .join(DimParameter, FactMeasurement.parameter_key == DimParameter.parameter_key)
    )
    stmt = apply_dimension_filters(
        stmt,
        station_id=station_id,
        parameter_code=parameter_code,
    )
    latest_ts = normalize_datetime(db.scalar(stmt))
    effective_to = to_ts or latest_ts or datetime.utcnow()
    effective_from = from_ts or (effective_to - timedelta(days=DEFAULT_WINDOW_DAYS))
    return effective_from, effective_to


def momentary_threshold_expr() -> Any:
    threshold_limit = cast(DimParameter.threshold_limit, Float)
    return case(
        (threshold_limit > 0, threshold_limit),
        (DimParameter.gdk_short_term.is_not(None), DimParameter.gdk_short_term),
        else_=None,
    )


def daily_threshold_expr() -> Any:
    return cast(DimParameter.gdk_daily, Float)


def daily_window_hours_from_basis(basis: str | None) -> float | None:
    if basis is None:
        return 24.0
    normalized = basis.lower()
    if "instant" in normalized:
        return None
    if "8h" in normalized:
        return 8.0
    if "1h" in normalized:
        return 1.0
    if "24h" in normalized:
        return 24.0
    if "daily" in normalized or "avg" in normalized:
        return 24.0
    if "operational_pm" in normalized or "max_one_time" in normalized:
        return 24.0
    return 24.0


def row_value(row: Any, *keys: Any, default: Any = None) -> Any:
    mapping = getattr(row, "_mapping", None)
    for key in keys:
        if mapping is not None and key in mapping:
            return mapping[key]
        if isinstance(key, str):
            try:
                return getattr(row, key)
            except AttributeError:
                continue
    return default


def compute_daily_rolling_average_points(
    rows: list[Any],
    window_hours: float | None,
) -> tuple[list[DashboardTimePoint], int]:
    if not rows or window_hours is None or window_hours <= 0:
        return [], 0

    window = timedelta(hours=window_hours)
    rolling: deque[tuple[datetime, float]] = deque()
    rolling_sum = 0.0
    threshold = None
    rolling_points: list[DashboardTimePoint] = []
    violation_count = 0
    violated_days: set[datetime.date] = set()

    for row in rows:
        if row.daily_threshold is not None:
            threshold = float(row.daily_threshold)
        cutoff = row.measurement_timestamp - window
        while rolling and rolling[0][0] < cutoff:
            _, old_value = rolling.popleft()
            rolling_sum -= old_value

        if row.value is not None:
            value = float(row.value)
            rolling.append((row.measurement_timestamp, value))
            rolling_sum += value

        rolling_average = None if not rolling else rolling_sum / len(rolling)
        rolling_points.append(
            DashboardTimePoint(
                timestamp=row.measurement_timestamp,
                value=rolling_average,
            )
        )

        day_key = row.measurement_timestamp.date()
        if (
            row.value is not None
            and threshold is not None
            and threshold > 0
            and rolling_average is not None
            and rolling_average > threshold
            and day_key not in violated_days
        ):
            violated_days.add(day_key)
            violation_count += 1

    return rolling_points, violation_count


def momentary_violation_condition() -> Any:
    momentary_threshold = momentary_threshold_expr()
    return or_(
        and_(
            FactMeasurement.value.is_not(None),
            momentary_threshold.is_not(None),
            momentary_threshold > 0,
            FactMeasurement.value > momentary_threshold,
        ),
        and_(
            momentary_threshold.is_(None),
            FactMeasurement.pollution_level.is_not(None),
            FactMeasurement.pollution_level >= 4,
        ),
    )


def load_dashboard_measurements(
    db: Session,
    *,
    station_id: int | None,
    parameter_code: str,
    from_ts: datetime,
    to_ts: datetime,
) -> list[Any]:
    stmt = (
        select(
            DimStation.station_id.label("station_id"),
            DimStation.station_name.label("station_name"),
            FactMeasurement.measurement_timestamp.label("measurement_timestamp"),
            FactMeasurement.value.label("value"),
            FactMeasurement.quality_ratio.label("quality_ratio"),
            FactMeasurement.pollution_level.label("pollution_level"),
            momentary_threshold_expr().label("momentary_threshold"),
            daily_threshold_expr().label("daily_threshold"),
            DimParameter.gdk_unit_basis.label("daily_threshold_basis"),
        )
        .select_from(FactMeasurement)
        .join(DimStation, FactMeasurement.station_key == DimStation.station_key)
        .join(DimParameter, FactMeasurement.parameter_key == DimParameter.parameter_key)
    )
    stmt = apply_dimension_filters(stmt, station_id=station_id, parameter_code=parameter_code)
    stmt = apply_time_filters(stmt, from_ts=from_ts, to_ts=to_ts)
    return db.execute(stmt.order_by(FactMeasurement.measurement_timestamp.asc())).all()


def count_momentary_violations(rows: list[Any]) -> int:
    count = 0
    for row in rows:
        threshold = None if row.momentary_threshold is None else float(row.momentary_threshold)
        if (
            row.value is not None
            and threshold is not None
            and threshold > 0
            and float(row.value) > threshold
        ) or (
            threshold is None
            and row.pollution_level is not None
            and row.pollution_level >= 4
        ):
            count += 1
    return count


def count_daily_rolling_violations(rows: list[Any], window_hours: float | None) -> int:
    if not rows or window_hours is None or window_hours <= 0:
        return 0

    grouped: dict[int, list[Any]] = defaultdict(list)
    for row in rows:
        grouped[int(row.station_id)].append(row)

    violation_count = 0
    window = timedelta(hours=window_hours)
    for station_rows in grouped.values():
        station_rows.sort(key=lambda row: row.measurement_timestamp)
        rolling: deque[tuple[datetime, float]] = deque()
        rolling_sum = 0.0
        threshold = None
        violated_days: set[datetime.date] = set()

        for row in station_rows:
            # Daily GDK is evaluated on the rolling mean of the most recent window.
            if row.value is None:
                continue
            if row.daily_threshold is not None:
                threshold = float(row.daily_threshold)
            if threshold is None or threshold <= 0:
                continue
            current_ts = row.measurement_timestamp
            cutoff = current_ts - window
            while rolling and rolling[0][0] < cutoff:
                old_ts, old_value = rolling.popleft()
                rolling_sum -= old_value
            value = float(row.value)
            rolling.append((current_ts, value))
            rolling_sum += value
            rolling_average = rolling_sum / len(rolling)
            day_key = current_ts.date()
            if rolling_average > threshold and day_key not in violated_days:
                violated_days.add(day_key)
                violation_count += 1

    return violation_count


def count_threshold_violations(rows: list[Any]) -> int:
    if not rows:
        return 0

    grouped: dict[int, list[Any]] = defaultdict(list)
    for row in rows:
        grouped[int(row.station_id)].append(row)

    violation_count = 0
    window_hours = daily_window_hours_from_basis(rows[0].daily_threshold_basis)
    window = timedelta(hours=window_hours) if window_hours else None

    for station_rows in grouped.values():
        station_rows.sort(key=lambda row: row.measurement_timestamp)
        rolling: deque[tuple[datetime, float]] = deque()
        rolling_sum = 0.0
        threshold = None
        violated_days: set[datetime.date] = set()

        for row in station_rows:
            threshold_limit = (
                None if row.momentary_threshold is None else float(row.momentary_threshold)
            )
            is_momentary_violation = (
                row.value is not None
                and threshold_limit is not None
                and threshold_limit > 0
                and float(row.value) > threshold_limit
            )
            if threshold_limit is None:
                is_momentary_violation = (
                    row.pollution_level is not None and row.pollution_level >= 4
                )

            is_daily_violation = False
            if window is not None and row.value is not None and row.daily_threshold is not None:
                threshold = float(row.daily_threshold)
                if threshold > 0:
                    cutoff = row.measurement_timestamp - window
                    while rolling and rolling[0][0] < cutoff:
                        _, old_value = rolling.popleft()
                        rolling_sum -= old_value
                    value = float(row.value)
                    rolling.append((row.measurement_timestamp, value))
                    rolling_sum += value
                    day_key = row.measurement_timestamp.date()
                    is_daily_violation = (
                        (rolling_sum / len(rolling)) > threshold and day_key not in violated_days
                    )
                    if is_daily_violation:
                        violated_days.add(day_key)

            if is_momentary_violation or is_daily_violation:
                violation_count += 1

    return violation_count


def build_station_rating_items(
    rows: list[Any],
    *,
    selected_station_id: int | None,
    limit: int,
) -> list[DashboardStationRatingItem]:
    if not rows:
        return []

    grouped: dict[int, dict[str, Any]] = {}
    station_rows_by_id: dict[int, list[Any]] = defaultdict(list)

    for row in rows:
        station_id = int(row.station_id)
        station_rows_by_id[station_id].append(row)
        station_name = row_value(
            row,
            "station_name",
            DimStation.station_name,
            "stations_name",
        )
        station_data = grouped.setdefault(
            station_id,
            {
                "station_name": station_name or f"Station {station_id}",
                "measurement_count": 0,
                "value_sum": 0.0,
                "value_count": 0,
                "quality_sum": 0.0,
                "quality_count": 0,
                "momentary_violations": 0,
                "daily_violations": 0,
            },
        )
        if station_name and station_data["station_name"] == f"Station {station_id}":
            station_data["station_name"] = station_name
        station_data["measurement_count"] += 1
        if row.value is not None:
            station_data["value_sum"] += float(row.value)
            station_data["value_count"] += 1
        if row.quality_ratio is not None:
            station_data["quality_sum"] += float(row.quality_ratio)
            station_data["quality_count"] += 1

        threshold_limit = (
            None if row.momentary_threshold is None else float(row.momentary_threshold)
        )
        is_momentary_violation = (
            row.value is not None
            and threshold_limit is not None
            and threshold_limit > 0
            and float(row.value) > threshold_limit
        )
        if threshold_limit is None:
            is_momentary_violation = (
                row.pollution_level is not None and row.pollution_level >= 4
            )
        if is_momentary_violation:
            station_data["momentary_violations"] += 1

    for station_id, station_rows in station_rows_by_id.items():
        station_rows.sort(key=lambda row: row.measurement_timestamp)
        window_hours = daily_window_hours_from_basis(
            station_rows[0].daily_threshold_basis if station_rows else None
        )
        if window_hours is None or window_hours <= 0:
            continue
        window = timedelta(hours=window_hours)
        rolling: deque[tuple[datetime, float]] = deque()
        rolling_sum = 0.0
        violated_days: set[datetime.date] = set()

        for row in station_rows:
            if row.value is None or row.daily_threshold is None:
                continue
            threshold = float(row.daily_threshold)
            if threshold <= 0:
                continue
            cutoff = row.measurement_timestamp - window
            while rolling and rolling[0][0] < cutoff:
                _, old_value = rolling.popleft()
                rolling_sum -= old_value
            value = float(row.value)
            rolling.append((row.measurement_timestamp, value))
            rolling_sum += value
            day_key = row.measurement_timestamp.date()
            if (
                rolling
                and (rolling_sum / len(rolling)) > threshold
                and day_key not in violated_days
            ):
                violated_days.add(day_key)
                grouped[station_id]["daily_violations"] += 1

    ranked_items = [
        DashboardStationRatingItem(
            rank=0,
            station_id=station_id,
            station_name=values["station_name"],
            average_value=(
                values["value_sum"] / values["value_count"]
                if values["value_count"] > 0
                else None
            ),
            average_quality_ratio=(
                round(values["quality_sum"] / values["quality_count"], 4)
                if values["quality_count"] > 0
                else None
            ),
            measurement_count=values["measurement_count"],
            threshold_violations=(
                values["momentary_violations"] + values["daily_violations"]
            ),
            momentary_violations=values["momentary_violations"],
            daily_violations=values["daily_violations"],
            is_selected=(selected_station_id == station_id),
        )
        for station_id, values in grouped.items()
    ]
    ranked_items.sort(
        key=lambda item: (
            item.threshold_violations,
            item.momentary_violations,
            item.daily_violations,
            item.station_id,
        )
    )
    for index, item in enumerate(ranked_items, start=1):
        item.rank = index

    items = ranked_items[:limit]
    if selected_station_id is not None and all(
        item.station_id != selected_station_id for item in items
    ):
        selected_item = next(
            (item for item in ranked_items if item.station_id == selected_station_id),
            None,
        )
        if selected_item is not None:
            items = items[:-1] + [selected_item] if items else [selected_item]

    return items


def air_quality_score_expr() -> Any:
    effective_threshold = momentary_threshold_expr()
    return case(
        (
            and_(
                FactMeasurement.value.is_not(None),
                effective_threshold.is_not(None),
                effective_threshold > 0,
            ),
            (
                1.0
                - func.least(
                    func.greatest(
                        FactMeasurement.value / effective_threshold,
                        0.0,
                    ),
                    1.0,
                )
            )
            * 100.0,
        ),
        (
            FactMeasurement.quality_ratio.is_not(None),
            func.least(
                func.greatest(FactMeasurement.quality_ratio, 0.0),
                1.0,
            )
            * 100.0,
        ),
        else_=None,
    )


def aggregate_summary_window(
    db: Session,
    *,
    station_id: int | None,
    parameter_code: str,
    from_ts: datetime,
    to_ts: datetime,
) -> dict[str, Any]:
    total_count = func.count(FactMeasurement.measurement_id)
    effective_threshold = momentary_threshold_expr()
    parameter_bucket = func.coalesce(
        DimParameter.parameter_name,
        DimParameter.parameter_code,
    )
    score_stmt = (
        select(
            parameter_bucket.label("parameter_bucket"),
            func.avg(air_quality_score_expr()).label("average_score"),
            func.max(effective_threshold).label("effective_threshold"),
        )
        .select_from(FactMeasurement)
        .join(DimStation, FactMeasurement.station_key == DimStation.station_key)
        .join(DimParameter, FactMeasurement.parameter_key == DimParameter.parameter_key)
        .where(effective_threshold.is_not(None), effective_threshold > 0)
    )
    score_stmt = apply_dimension_filters(
        score_stmt,
        station_id=station_id,
        parameter_code=parameter_code,
    )
    score_stmt = apply_time_filters(score_stmt, from_ts=from_ts, to_ts=to_ts)
    score_stmt = score_stmt.group_by(parameter_bucket)
    score_rows = db.execute(score_stmt).all()

    weighted_score_sum = 0.0
    weight_sum = 0.0
    for row in score_rows:
        if row.average_score is None or row.effective_threshold is None:
            continue
        threshold = float(row.effective_threshold)
        if threshold <= 0:
            continue
        # Stricter pollutants get a larger coefficient; coefficients are
        # normalized implicitly by dividing by the total inverse-threshold sum.
        weight = 1.0 / threshold
        weighted_score_sum += float(row.average_score) * weight
        weight_sum += weight

    air_quality_index = None
    if weight_sum > 0:
        air_quality_index = weighted_score_sum / weight_sum

    rows = load_dashboard_measurements(
        db,
        station_id=station_id,
        parameter_code=parameter_code,
        from_ts=from_ts,
        to_ts=to_ts,
    )
    momentary_violations = count_momentary_violations(rows)
    daily_window_hours = daily_window_hours_from_basis(
        rows[0].daily_threshold_basis if rows else None
    )
    daily_violations = count_daily_rolling_violations(rows, daily_window_hours)
    total_count = len(rows)
    valid_count = sum(1 for row in rows if row.value is not None)
    last_data_update = max((row.measurement_timestamp for row in rows), default=None)

    return {
        "air_quality_index": air_quality_index,
        "momentary_violations": momentary_violations,
        "daily_violations": daily_violations,
        "threshold_violations": count_threshold_violations(rows),
        "data_health_percent": (valid_count * 100.0 / total_count) if total_count else None,
        "last_data_update": last_data_update,
    }


def round_or_none(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 2)


def is_complete_day_window(day_start: datetime, from_ts: datetime, to_ts: datetime) -> bool:
    day_end = day_start + timedelta(days=1) - timedelta(seconds=1)
    return day_start >= from_ts and day_end <= to_ts


@router.get("/summary", response_model=DashboardSummary)
def get_dashboard_summary(
    db: Annotated[Session, Depends(get_db)],
    parameter_code: str,
    station_id: int | None = None,
    from_ts: datetime | None = None,
    to_ts: datetime | None = None,
) -> DashboardSummary:
    require_parameter_exists(db, parameter_code=parameter_code)
    if station_id is not None:
        require_station_exists(db, station_id=station_id)

    effective_from, effective_to = get_effective_window(
        db,
        station_id=station_id,
        parameter_code=parameter_code,
        from_ts=from_ts,
        to_ts=to_ts,
    )
    effective_from = normalize_datetime(effective_from)
    effective_to = normalize_datetime(effective_to)
    current_row = aggregate_summary_window(
        db,
        station_id=station_id,
        parameter_code=parameter_code,
        from_ts=effective_from,
        to_ts=effective_to,
    )
    span = effective_to - effective_from
    previous_row = aggregate_summary_window(
        db,
        station_id=station_id,
        parameter_code=parameter_code,
        from_ts=effective_from - span,
        to_ts=effective_from,
    )

    air_quality_index = round_or_none(current_row["air_quality_index"])
    previous_air_quality_index = round_or_none(previous_row["air_quality_index"])
    data_health_percent = round_or_none(current_row["data_health_percent"])
    previous_data_health_percent = round_or_none(previous_row["data_health_percent"])
    last_data_update = normalize_datetime(current_row["last_data_update"])
    age_hours = None
    if last_data_update is not None:
        age_hours = round_or_none((effective_to - last_data_update).total_seconds() / 3600)

    return DashboardSummary(
        station_id=station_id,
        parameter_code=parameter_code,
        from_ts=effective_from,
        to_ts=effective_to,
        air_quality_index=air_quality_index,
        air_quality_index_delta=(
            None
            if air_quality_index is None or previous_air_quality_index is None
            else round_or_none(air_quality_index - previous_air_quality_index)
        ),
        threshold_violations=int(current_row["threshold_violations"] or 0),
        threshold_violations_delta=(
            int(
                (current_row["threshold_violations"] or 0)
                - (previous_row["threshold_violations"] or 0)
            )
        ),
        momentary_violations=int(current_row["momentary_violations"] or 0),
        momentary_violations_delta=(
            int(
                (current_row["momentary_violations"] or 0)
                - (previous_row["momentary_violations"] or 0)
            )
        ),
        daily_violations=int(current_row["daily_violations"] or 0),
        daily_violations_delta=(
            int(
                (current_row["daily_violations"] or 0)
                - (previous_row["daily_violations"] or 0)
            )
        ),
        data_health_percent=data_health_percent,
        data_health_delta=(
            None
            if data_health_percent is None or previous_data_health_percent is None
            else round_or_none(data_health_percent - previous_data_health_percent)
        ),
        last_data_update=last_data_update,
        last_data_update_age_hours=age_hours,
    )


@router.get("/time-dynamics", response_model=DashboardTimeSeries)
def get_time_dynamics(
    db: Annotated[Session, Depends(get_db)],
    parameter_code: str,
    station_id: int | None = None,
    from_ts: datetime | None = None,
    to_ts: datetime | None = None,
    limit: Annotated[int, Query(ge=10, le=2000)] = 300,
) -> DashboardTimeSeries:
    require_parameter_exists(db, parameter_code=parameter_code)
    if station_id is not None:
        require_station_exists(db, station_id=station_id)

    effective_from, effective_to = get_effective_window(
        db,
        station_id=station_id,
        parameter_code=parameter_code,
        from_ts=from_ts,
        to_ts=to_ts,
    )
    stmt = (
        select(
            FactMeasurement.measurement_timestamp,
            FactMeasurement.value,
            momentary_threshold_expr().label("momentary_threshold"),
            daily_threshold_expr().label("daily_threshold"),
            DimParameter.gdk_unit_basis.label("daily_threshold_basis"),
            DimUnit.unit_symbol,
        )
        .select_from(FactMeasurement)
        .join(DimStation, FactMeasurement.station_key == DimStation.station_key)
        .join(DimParameter, FactMeasurement.parameter_key == DimParameter.parameter_key)
        .outerjoin(DimUnit, DimParameter.unit_key == DimUnit.unit_key)
    )
    stmt = apply_dimension_filters(
        stmt,
        station_id=station_id,
        parameter_code=parameter_code,
    )
    stmt = apply_time_filters(stmt, from_ts=effective_from, to_ts=effective_to)
    stmt = stmt.order_by(FactMeasurement.measurement_timestamp.asc()).limit(limit)

    rows = db.execute(stmt).all()
    momentary_threshold = rows[0].momentary_threshold if rows else None
    daily_threshold = (
        None if not rows or rows[0].daily_threshold is None else float(rows[0].daily_threshold)
    )
    daily_window_hours = daily_window_hours_from_basis(
        rows[0].daily_threshold_basis if rows else None
    )
    unit_symbol = rows[0].unit_symbol if rows else None
    daily_rolling_average_points: list[DashboardTimePoint] = []
    daily_rolling_violations = 0
    if station_id is not None:
        daily_rolling_average_points, daily_rolling_violations = compute_daily_rolling_average_points(
            rows,
            daily_window_hours,
        )
    points = [
        DashboardTimePoint(
            timestamp=row.measurement_timestamp,
            value=None if row.value is None else float(row.value),
        )
        for row in rows
    ]
    return DashboardTimeSeries(
        station_id=station_id,
        parameter_code=parameter_code,
        from_ts=effective_from,
        to_ts=effective_to,
        unit_symbol=unit_symbol,
        momentary_threshold=momentary_threshold,
        daily_threshold=daily_threshold,
        daily_window_hours=daily_window_hours,
        daily_rolling_average_points=daily_rolling_average_points,
        daily_rolling_violations=daily_rolling_violations,
        points=points,
    )


@router.get("/violation-dynamics", response_model=DashboardViolationSeries)
def get_violation_dynamics(
    db: Annotated[Session, Depends(get_db)],
    parameter_code: str,
    station_id: int | None = None,
    from_ts: datetime | None = None,
    to_ts: datetime | None = None,
    bucket: str = "day",
) -> DashboardViolationSeries:
    if bucket not in {"hour", "day"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Bucket must be 'hour' or 'day'.",
        )
    require_parameter_exists(db, parameter_code=parameter_code)
    if station_id is not None:
        require_station_exists(db, station_id=station_id)

    effective_from, effective_to = get_effective_window(
        db,
        station_id=station_id,
        parameter_code=parameter_code,
        from_ts=from_ts,
        to_ts=to_ts,
    )
    rows = load_dashboard_measurements(
        db,
        station_id=station_id,
        parameter_code=parameter_code,
        from_ts=effective_from,
        to_ts=effective_to,
    )

    grouped: dict[datetime, dict[str, int]] = defaultdict(
        lambda: {
            "total_measurements": 0,
            "threshold_violations": 0,
            "momentary_violations": 0,
            "daily_violations": 0,
        }
    )
    station_states: dict[int, dict[str, Any]] = defaultdict(
        lambda: {
            "rolling": deque(),
            "rolling_sum": 0.0,
            "threshold": None,
            "violated_days": set(),
        }
    )
    daily_window_hours = daily_window_hours_from_basis(
        rows[0].daily_threshold_basis if rows else None
    )
    rolling_window = timedelta(hours=daily_window_hours) if daily_window_hours else None
    for row in rows:
        ts = row.measurement_timestamp.replace(
            minute=0,
            second=0,
            microsecond=0,
        )
        if bucket == "day":
            ts = ts.replace(hour=0)
        grouped[ts]["total_measurements"] += 1

        threshold_limit = (
            None if row.momentary_threshold is None else float(row.momentary_threshold)
        )
        is_momentary_violation = (
            row.value is not None
            and threshold_limit is not None
            and threshold_limit > 0
            and float(row.value) > threshold_limit
        )
        if threshold_limit is None:
            is_momentary_violation = (
                row.pollution_level is not None and row.pollution_level >= 4
            )

        is_daily_violation = False
        if (
            rolling_window is not None
            and row.value is not None
            and row.daily_threshold is not None
        ):
            state = station_states[int(row.station_id)]
            threshold = float(row.daily_threshold)
            state["threshold"] = threshold
            violated_days: set[datetime.date] = state["violated_days"]
            cutoff = row.measurement_timestamp - rolling_window
            rolling: deque[tuple[datetime, float]] = state["rolling"]
            rolling_sum = float(state["rolling_sum"])
            while rolling and rolling[0][0] < cutoff:
                _, old_value = rolling.popleft()
                rolling_sum -= old_value
            value = float(row.value)
            rolling.append((row.measurement_timestamp, value))
            rolling_sum += value
            state["rolling_sum"] = rolling_sum
            day_key = row.measurement_timestamp.date()
            is_daily_violation = (
                threshold > 0
                and rolling
                and (rolling_sum / len(rolling)) > threshold
                and day_key not in violated_days
            )
            if is_daily_violation:
                violated_days.add(day_key)

        if is_momentary_violation:
            grouped[ts]["momentary_violations"] += 1
        if is_daily_violation:
            grouped[ts]["daily_violations"] += 1
        if is_momentary_violation or is_daily_violation:
            grouped[ts]["threshold_violations"] += 1

    points = [
        DashboardViolationPoint(
            timestamp=timestamp,
            total_measurements=values["total_measurements"],
            threshold_violations=values["threshold_violations"],
            momentary_violations=values["momentary_violations"],
            daily_violations=values["daily_violations"],
        )
        for timestamp, values in sorted(grouped.items())
    ]
    return DashboardViolationSeries(
        station_id=station_id,
        parameter_code=parameter_code,
        from_ts=effective_from,
        to_ts=effective_to,
        bucket=bucket,
        points=points,
    )


@router.get("/station-rating", response_model=DashboardStationRating)
def get_station_rating(
    db: Annotated[Session, Depends(get_db)],
    parameter_code: str,
    selected_station_id: int | None = None,
    from_ts: datetime | None = None,
    to_ts: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 10,
) -> DashboardStationRating:
    require_parameter_exists(db, parameter_code=parameter_code)
    if selected_station_id is not None:
        require_station_exists(db, station_id=selected_station_id)

    effective_from, effective_to = get_effective_window(
        db,
        station_id=selected_station_id,
        parameter_code=parameter_code,
        from_ts=from_ts,
        to_ts=to_ts,
    )
    rows = load_dashboard_measurements(
        db,
        station_id=None,
        parameter_code=parameter_code,
        from_ts=effective_from,
        to_ts=effective_to,
    )
    items = build_station_rating_items(
        rows,
        selected_station_id=selected_station_id,
        limit=limit,
    )
    return DashboardStationRating(
        parameter_code=parameter_code,
        from_ts=effective_from,
        to_ts=effective_to,
        lower_is_better=True,
        items=items,
    )


@router.get("/correlation", response_model=DashboardCorrelation)
def get_parameter_correlation(
    db: Annotated[Session, Depends(get_db)],
    station_id: int,
    parameter_code: str,
    from_ts: datetime | None = None,
    to_ts: datetime | None = None,
) -> DashboardCorrelation:
    require_station_exists(db, station_id=station_id)
    require_parameter_exists(db, parameter_code=parameter_code)

    effective_from, effective_to = get_effective_window(
        db,
        station_id=station_id,
        parameter_code=parameter_code,
        from_ts=from_ts,
        to_ts=to_ts,
    )
    stmt = (
        select(
            FactMeasurement.measurement_timestamp,
            DimParameter.parameter_code,
            DimParameter.parameter_name,
            FactMeasurement.value,
        )
        .select_from(FactMeasurement)
        .join(DimStation, FactMeasurement.station_key == DimStation.station_key)
        .join(DimParameter, FactMeasurement.parameter_key == DimParameter.parameter_key)
        .where(DimStation.station_id == station_id)
    )
    stmt = apply_time_filters(stmt, from_ts=effective_from, to_ts=effective_to)
    rows = db.execute(stmt).all()
    if not rows:
        return DashboardCorrelation(
            station_id=station_id,
            target_parameter_code=parameter_code,
            from_ts=effective_from,
            to_ts=effective_to,
            items=[],
        )

    frame = pd.DataFrame(
        [
            {
                "measurement_timestamp": row.measurement_timestamp,
                "parameter_code": row.parameter_code,
                "parameter_name": row.parameter_name,
                "value": None if row.value is None else float(row.value),
            }
            for row in rows
        ]
    )
    parameter_names = (
        frame[["parameter_code", "parameter_name"]]
        .drop_duplicates(subset=["parameter_code"])
        .set_index("parameter_code")["parameter_name"]
        .to_dict()
    )
    pivot = frame.pivot_table(
        index="measurement_timestamp",
        columns="parameter_code",
        values="value",
        aggfunc="mean",
    )
    if parameter_code not in pivot.columns:
        return DashboardCorrelation(
            station_id=station_id,
            target_parameter_code=parameter_code,
            from_ts=effective_from,
            to_ts=effective_to,
            items=[],
        )

    correlations: list[DashboardCorrelationItem] = []
    target_series = pivot[parameter_code]
    for candidate_code in pivot.columns:
        if candidate_code == parameter_code:
            continue
        pair = pd.concat([target_series, pivot[candidate_code]], axis=1).dropna()
        samples = int(len(pair))
        correlation = None
        if samples >= 2:
            value = pair.iloc[:, 0].corr(pair.iloc[:, 1])
            correlation = None if pd.isna(value) else round(float(value), 4)
        correlations.append(
            DashboardCorrelationItem(
                parameter_code=str(candidate_code),
                parameter_name=parameter_names.get(str(candidate_code)),
                correlation=correlation,
                samples=samples,
            )
        )

    correlations.sort(
        key=lambda item: (
            item.correlation is None,
            0.0 if item.correlation is None else -abs(item.correlation),
            item.parameter_code,
        )
    )
    return DashboardCorrelation(
        station_id=station_id,
        target_parameter_code=parameter_code,
        from_ts=effective_from,
        to_ts=effective_to,
        items=correlations,
    )


@router.get("/recent-measurements", response_model=DashboardMeasurementsTable)
def get_recent_measurements(
    db: Annotated[Session, Depends(get_db)],
    parameter_code: str,
    station_id: int | None = None,
    from_ts: datetime | None = None,
    to_ts: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
) -> DashboardMeasurementsTable:
    require_parameter_exists(db, parameter_code=parameter_code)
    if station_id is not None:
        require_station_exists(db, station_id=station_id)

    effective_from, effective_to = get_effective_window(
        db,
        station_id=station_id,
        parameter_code=parameter_code,
        from_ts=from_ts,
        to_ts=to_ts,
    )
    stmt = (
        select(
            FactMeasurement.measurement_id,
            FactMeasurement.measurement_timestamp,
            FactMeasurement.value,
            FactMeasurement.quality_ratio,
            FactMeasurement.pollution_level,
            DimParameter.parameter_code,
            DimParameter.parameter_name,
            DimUnit.unit_symbol,
        )
        .select_from(FactMeasurement)
        .join(DimStation, FactMeasurement.station_key == DimStation.station_key)
        .join(DimParameter, FactMeasurement.parameter_key == DimParameter.parameter_key)
        .outerjoin(DimUnit, DimParameter.unit_key == DimUnit.unit_key)
    )
    stmt = apply_dimension_filters(
        stmt,
        station_id=station_id,
        parameter_code=parameter_code,
    )
    stmt = apply_time_filters(stmt, from_ts=effective_from, to_ts=effective_to)
    stmt = stmt.order_by(FactMeasurement.measurement_timestamp.desc()).limit(limit)
    rows = db.execute(stmt).all()

    items = [
        DashboardMeasurementRow(
            measurement_id=row.measurement_id,
            measurement_timestamp=row.measurement_timestamp,
            value=None if row.value is None else float(row.value),
            quality_ratio=(
                None if row.quality_ratio is None else round(float(row.quality_ratio), 4)
            ),
            pollution_level=row.pollution_level,
            parameter_code=row.parameter_code,
            parameter_name=row.parameter_name,
            unit_symbol=row.unit_symbol,
        )
        for row in rows
    ]
    return DashboardMeasurementsTable(
        station_id=station_id,
        parameter_code=parameter_code,
        from_ts=effective_from,
        to_ts=effective_to,
        items=items,
    )
