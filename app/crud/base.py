from typing import Any, Generic, TypeVar

from pydantic import BaseModel
from sqlalchemy import Select, select
from sqlalchemy.orm import Session

ModelT = TypeVar("ModelT")
CreateT = TypeVar("CreateT", bound=BaseModel)
UpdateT = TypeVar("UpdateT", bound=BaseModel)


class CRUDBase(Generic[ModelT, CreateT, UpdateT]):
    def __init__(self, model: type[ModelT], pk_name: str) -> None:
        self.model = model
        self.pk_name = pk_name

    @property
    def pk_column(self) -> Any:
        return getattr(self.model, self.pk_name)

    def list(
        self,
        db: Session,
        *,
        skip: int = 0,
        limit: int = 100,
        statement: Select[tuple[ModelT]] | None = None,
    ) -> list[ModelT]:
        stmt = statement if statement is not None else select(self.model)
        return list(db.scalars(stmt.offset(skip).limit(limit)).all())

    def get(self, db: Session, item_id: int) -> ModelT | None:
        return db.get(self.model, item_id)

    def create(self, db: Session, obj_in: CreateT) -> ModelT:
        db_obj = self.model(**obj_in.model_dump())
        db.add(db_obj)
        db.commit()
        db.refresh(db_obj)
        return db_obj

    def update(self, db: Session, db_obj: ModelT, obj_in: UpdateT) -> ModelT:
        update_data = obj_in.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(db_obj, field, value)
        db.add(db_obj)
        db.commit()
        db.refresh(db_obj)
        return db_obj

    def delete(self, db: Session, db_obj: ModelT) -> None:
        db.delete(db_obj)
        db.commit()
