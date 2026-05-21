from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import Select, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.schemas import (
    MeasurementCreate,
    MeasurementPretty,
    MeasurementRead,
    MeasurementUpdate,
)
from app.crud.base import CRUDBase
from app.db.schemas import DimParameter, DimStation, DimUnit, FactMeasurement

router = APIRouter(prefix="/measurements", tags=["measurements"])
crud = CRUDBase[FactMeasurement, MeasurementCreate, MeasurementUpdate](
    FactMeasurement,
    "measurement_id",
)


def apply_measurement_filters(
    stmt: Select,
    *,
    station_key: int | None,
    parameter_key: int | None,
    from_ts: datetime | None,
    to_ts: datetime | None,
    pollution_level: int | None,
) -> Select:
    if station_key is not None:
        stmt = stmt.where(FactMeasurement.station_key == station_key)
    if parameter_key is not None:
        stmt = stmt.where(FactMeasurement.parameter_key == parameter_key)
    if from_ts is not None:
        stmt = stmt.where(FactMeasurement.measurement_timestamp >= from_ts)
    if to_ts is not None:
        stmt = stmt.where(FactMeasurement.measurement_timestamp <= to_ts)
    if pollution_level is not None:
        stmt = stmt.where(FactMeasurement.pollution_level == pollution_level)
    return stmt


def build_measurement_pretty_stmt() -> Select:
    return (
        select(
            FactMeasurement.measurement_id,
            FactMeasurement.measurement_timestamp,
            FactMeasurement.offset_minutes,
            FactMeasurement.value,
            FactMeasurement.quality_ratio,
            FactMeasurement.pollution_level,
            DimStation.station_key,
            DimStation.station_id,
            DimStation.station_name,
            DimStation.latitude,
            DimStation.longitude,
            DimStation.timezone_offset,
            DimParameter.parameter_key,
            DimParameter.parameter_code,
            DimParameter.parameter_name,
            DimParameter.local_name.label("parameter_local_name"),
            DimParameter.threshold_limit,
            DimParameter.physical_min,
            DimParameter.physical_max,
            DimParameter.gdk_daily,
            DimParameter.gdk_short_term,
            DimParameter.gdk_unit_basis,
            DimUnit.unit_key,
            DimUnit.unit_name,
            DimUnit.unit_symbol,
            DimUnit.local_unit_name,
        )
        .join(DimStation, FactMeasurement.station_key == DimStation.station_key)
        .join(DimParameter, FactMeasurement.parameter_key == DimParameter.parameter_key)
        .outerjoin(DimUnit, DimParameter.unit_key == DimUnit.unit_key)
    )


@router.get("", response_model=list[MeasurementRead])
def list_measurements(
    db: Annotated[Session, Depends(get_db)],
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    station_key: int | None = None,
    parameter_key: int | None = None,
    from_ts: datetime | None = None,
    to_ts: datetime | None = None,
    pollution_level: Annotated[int | None, Query(ge=1, le=5)] = None,
) -> list[FactMeasurement]:
    stmt = select(FactMeasurement)
    stmt = apply_measurement_filters(
        stmt,
        station_key=station_key,
        parameter_key=parameter_key,
        from_ts=from_ts,
        to_ts=to_ts,
        pollution_level=pollution_level,
    )
    stmt = stmt.order_by(FactMeasurement.measurement_timestamp.desc())
    return crud.list(db, skip=skip, limit=limit, statement=stmt)


@router.get("/pretty", response_model=list[MeasurementPretty])
def list_measurements_pretty(
    db: Annotated[Session, Depends(get_db)],
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    station_key: int | None = None,
    station_id: int | None = None,
    parameter_key: int | None = None,
    parameter_code: str | None = None,
    from_ts: datetime | None = None,
    to_ts: datetime | None = None,
    pollution_level: Annotated[int | None, Query(ge=1, le=5)] = None,
) -> list[MeasurementPretty]:
    stmt = build_measurement_pretty_stmt()
    stmt = apply_measurement_filters(
        stmt,
        station_key=station_key,
        parameter_key=parameter_key,
        from_ts=from_ts,
        to_ts=to_ts,
        pollution_level=pollution_level,
    )
    if station_id is not None:
        stmt = stmt.where(DimStation.station_id == station_id)
    if parameter_code is not None:
        stmt = stmt.where(DimParameter.parameter_code == parameter_code)
    stmt = stmt.order_by(FactMeasurement.measurement_timestamp.desc())
    rows = db.execute(stmt.offset(skip).limit(limit)).mappings().all()
    return [MeasurementPretty.model_validate(row) for row in rows]


@router.post("", response_model=MeasurementRead, status_code=status.HTTP_201_CREATED)
def create_measurement(
    payload: MeasurementCreate,
    db: Annotated[Session, Depends(get_db)],
) -> FactMeasurement:
    try:
        return crud.create(db, payload)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Measurement violates a database constraint.",
        ) from exc


@router.get("/{measurement_id}", response_model=MeasurementRead)
def get_measurement(
    measurement_id: int,
    db: Annotated[Session, Depends(get_db)],
) -> FactMeasurement:
    measurement = crud.get(db, measurement_id)
    if measurement is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Measurement not found.",
        )
    return measurement


@router.patch("/{measurement_id}", response_model=MeasurementRead)
def update_measurement(
    measurement_id: int,
    payload: MeasurementUpdate,
    db: Annotated[Session, Depends(get_db)],
) -> FactMeasurement:
    measurement = crud.get(db, measurement_id)
    if measurement is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Measurement not found.",
        )
    try:
        return crud.update(db, measurement, payload)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Measurement violates a database constraint.",
        ) from exc


@router.delete("/{measurement_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_measurement(
    measurement_id: int,
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    measurement = crud.get(db, measurement_id)
    if measurement is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Measurement not found.",
        )
    crud.delete(db, measurement)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
