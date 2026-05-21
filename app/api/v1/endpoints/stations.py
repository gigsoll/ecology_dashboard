from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.schemas import MeasurementPretty, StationCreate, StationRead, StationUpdate
from app.api.v1.endpoints.measurements import build_measurement_pretty_stmt
from app.crud.base import CRUDBase
from app.db.schemas import DimStation, FactMeasurement

router = APIRouter(prefix="/stations", tags=["stations"])
crud = CRUDBase[DimStation, StationCreate, StationUpdate](DimStation, "station_key")


@router.get("", response_model=list[StationRead])
def list_stations(
    db: Annotated[Session, Depends(get_db)],
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    station_id: int | None = None,
) -> list[DimStation]:
    stmt = select(DimStation)
    if station_id is not None:
        stmt = stmt.where(DimStation.station_id == station_id)
    stmt = stmt.order_by(DimStation.station_key)
    return crud.list(db, skip=skip, limit=limit, statement=stmt)


@router.get("/{station_key}/measurements", response_model=list[MeasurementPretty])
def list_station_measurements(
    station_key: int,
    db: Annotated[Session, Depends(get_db)],
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
) -> list[MeasurementPretty]:
    station = crud.get(db, station_key)
    if station is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Station not found.",
        )

    stmt = (
        build_measurement_pretty_stmt()
        .where(FactMeasurement.station_key == station_key)
        .order_by(FactMeasurement.measurement_timestamp.desc())
    )
    rows = db.execute(stmt.offset(skip).limit(limit)).mappings().all()
    return [MeasurementPretty.model_validate(row) for row in rows]


@router.post("", response_model=StationRead, status_code=status.HTTP_201_CREATED)
def create_station(
    payload: StationCreate,
    db: Annotated[Session, Depends(get_db)],
) -> DimStation:
    try:
        return crud.create(db, payload)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Station violates a database constraint.",
        ) from exc


@router.get("/{station_key}", response_model=StationRead)
def get_station(
    station_key: int,
    db: Annotated[Session, Depends(get_db)],
) -> DimStation:
    station = crud.get(db, station_key)
    if station is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Station not found.",
        )
    return station


@router.patch("/{station_key}", response_model=StationRead)
def update_station(
    station_key: int,
    payload: StationUpdate,
    db: Annotated[Session, Depends(get_db)],
) -> DimStation:
    station = crud.get(db, station_key)
    if station is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Station not found.",
        )
    try:
        return crud.update(db, station, payload)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Station violates a database constraint.",
        ) from exc


@router.delete("/{station_key}", status_code=status.HTTP_204_NO_CONTENT)
def delete_station(
    station_key: int,
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    station = crud.get(db, station_key)
    if station is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Station not found.",
        )
    try:
        crud.delete(db, station)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Station is referenced by measurements.",
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
