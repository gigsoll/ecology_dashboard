from functools import lru_cache

from sqlalchemy import Engine, create_engine, URL
from sqlalchemy.orm import Session, sessionmaker
from app.core.config import app_config
from app.db.schemas import Base


@lru_cache
def get_engine() -> Engine:
    engine = create_engine(
        URL.create(
            drivername=app_config.db_driver,
            username=app_config.db_user,
            password=app_config.db_password,
            host=app_config.db_host,
            port=app_config.db_port,
            database=app_config.db_name,
        )
    )
    return engine


@lru_cache
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), autoflush=False, autocommit=False)


def get_session() -> Session:
    return get_session_factory()()


def init_db():
    """Creates all tables defined in schemas.py"""
    engine = get_engine()
    Base.metadata.create_all(engine)
