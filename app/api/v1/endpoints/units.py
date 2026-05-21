from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.schemas import UnitCreate, UnitRead, UnitUpdate
from app.crud.base import CRUDBase
from app.db.schemas import DimUnit

router = APIRouter(prefix="/units", tags=["units"])
crud = CRUDBase[DimUnit, UnitCreate, UnitUpdate](DimUnit, "unit_key")


@router.get("", response_model=list[UnitRead])
def list_units(
    db: Annotated[Session, Depends(get_db)],
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
) -> list[DimUnit]:
    return crud.list(db, skip=skip, limit=limit)


@router.post("", response_model=UnitRead, status_code=status.HTTP_201_CREATED)
def create_unit(
    payload: UnitCreate,
    db: Annotated[Session, Depends(get_db)],
) -> DimUnit:
    try:
        return crud.create(db, payload)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Unit violates a database constraint.",
        ) from exc


@router.get("/{unit_key}", response_model=UnitRead)
def get_unit(unit_key: int, db: Annotated[Session, Depends(get_db)]) -> DimUnit:
    unit = crud.get(db, unit_key)
    if unit is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unit not found.")
    return unit


@router.patch("/{unit_key}", response_model=UnitRead)
def update_unit(
    unit_key: int,
    payload: UnitUpdate,
    db: Annotated[Session, Depends(get_db)],
) -> DimUnit:
    unit = crud.get(db, unit_key)
    if unit is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unit not found.")
    try:
        return crud.update(db, unit, payload)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Unit violates a database constraint.",
        ) from exc


@router.delete("/{unit_key}", status_code=status.HTTP_204_NO_CONTENT)
def delete_unit(unit_key: int, db: Annotated[Session, Depends(get_db)]) -> Response:
    unit = crud.get(db, unit_key)
    if unit is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unit not found.")
    try:
        crud.delete(db, unit)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Unit is referenced by other records.",
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
