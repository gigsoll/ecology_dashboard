from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.api.deps import get_db
from app.api.schemas import ParameterCreate, ParameterRead, ParameterUpdate
from app.crud.base import CRUDBase
from app.db.schemas import DimParameter

router = APIRouter(prefix="/parameters", tags=["parameters"])
crud = CRUDBase[DimParameter, ParameterCreate, ParameterUpdate](
    DimParameter,
    "parameter_key",
)


@router.get("", response_model=list[ParameterRead])
def list_parameters(
    db: Annotated[Session, Depends(get_db)],
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    parameter_code: str | None = None,
) -> list[DimParameter]:
    stmt = select(DimParameter).options(joinedload(DimParameter.unit))
    if parameter_code is not None:
        stmt = stmt.where(DimParameter.parameter_code == parameter_code)
    stmt = stmt.order_by(DimParameter.parameter_key)
    return crud.list(db, skip=skip, limit=limit, statement=stmt)


@router.post("", response_model=ParameterRead, status_code=status.HTTP_201_CREATED)
def create_parameter(
    payload: ParameterCreate,
    db: Annotated[Session, Depends(get_db)],
) -> DimParameter:
    try:
        parameter = crud.create(db, payload)
        return get_parameter(parameter.parameter_key, db)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Parameter violates a database constraint.",
        ) from exc


@router.get("/{parameter_key}", response_model=ParameterRead)
def get_parameter(
    parameter_key: int,
    db: Annotated[Session, Depends(get_db)],
) -> DimParameter:
    stmt = (
        select(DimParameter)
        .options(joinedload(DimParameter.unit))
        .where(DimParameter.parameter_key == parameter_key)
    )
    parameter = db.scalars(stmt).first()
    if parameter is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Parameter not found.",
        )
    return parameter


@router.patch("/{parameter_key}", response_model=ParameterRead)
def update_parameter(
    parameter_key: int,
    payload: ParameterUpdate,
    db: Annotated[Session, Depends(get_db)],
) -> DimParameter:
    parameter = crud.get(db, parameter_key)
    if parameter is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Parameter not found.",
        )
    try:
        parameter = crud.update(db, parameter, payload)
        return get_parameter(parameter.parameter_key, db)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Parameter violates a database constraint.",
        ) from exc


@router.delete("/{parameter_key}", status_code=status.HTTP_204_NO_CONTENT)
def delete_parameter(
    parameter_key: int,
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    parameter = crud.get(db, parameter_key)
    if parameter is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Parameter not found.",
        )
    try:
        crud.delete(db, parameter)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Parameter is referenced by measurements.",
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
