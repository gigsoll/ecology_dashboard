from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.types import DECIMAL
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class DimUnit(Base):
    """
    Dimentional table for measure units
    """

    __tablename__ = "dim_units"
    __table_args__ = (UniqueConstraint("unit_symbol", name="uq_dim_units_symbol"),)

    unit_key = Column(Integer, primary_key=True, autoincrement=True)
    unit_name = Column(
        String(64), nullable=True
    )  # nullable: ETL was copying symbol here
    unit_symbol = Column(String(16), nullable=False)
    local_unit_name = Column(String(64), nullable=True)

    parameters = relationship("DimParameter", back_populates="unit")


class DimParameter(Base):
    """
    Dimentional table for measurement params
    """

    __tablename__ = "dim_parameters"
    __table_args__ = (
        Index("ix_dim_parameters_code_current", "parameter_code", "is_current"),
        UniqueConstraint(
            "parameter_code",
            "valid_from",
            name="uq_dim_parameters_code_valid_from",
        ),
        CheckConstraint(
            "threshold_limit >= 0",
            name="ck_dim_parameters_threshold_non_negative",
        ),
    )

    parameter_key = Column(Integer, primary_key=True, autoincrement=True)
    parameter_code = Column(String(64), nullable=False)
    parameter_name = Column(String(64), nullable=True)
    local_name = Column(String(64), nullable=True)
    unit_key = Column(Integer, ForeignKey("dim_units.unit_key"), nullable=True)
    threshold_limit = Column(
        DECIMAL(precision=8, scale=4),
        nullable=False,
        default=0,
        server_default=text("0"),
    )

    # SCD Type 2
    valid_from = Column(DateTime, nullable=False)
    valid_to = Column(
        DateTime, nullable=True
    )  # nullable is correct for open-ended records
    is_current = Column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )

    unit = relationship("DimUnit", back_populates="parameters")
    measurements = relationship("FactMeasurement", back_populates="parameter")


class DimStation(Base):
    """
    Dimentiona table for stations info
    """

    __tablename__ = "dim_stations"
    __table_args__ = (
        Index("ix_dim_stations_id_current", "station_id", "is_current"),
        UniqueConstraint(
            "station_id",
            "valid_from",
            name="uq_dim_stations_id_valid_from",
        ),
        CheckConstraint(
            "latitude  BETWEEN -90  AND  90", name="ck_dim_stations_latitude"
        ),
        CheckConstraint(
            "longitude BETWEEN -180 AND 180", name="ck_dim_stations_longitude"
        ),
    )

    station_key = Column(Integer, primary_key=True, autoincrement=True)
    station_id = Column(Integer, nullable=False)
    station_name = Column(String(128), nullable=True)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    timezone_offset = Column(Integer, nullable=True)

    # SCD Type 2
    valid_from = Column(DateTime, nullable=False)
    valid_to = Column(DateTime, nullable=True)
    is_current = Column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )

    measurements = relationship("FactMeasurement", back_populates="station")


class FactMeasurement(Base):
    """
    Fact table for air quality measurements.
    """

    __tablename__ = "fact_measurements"
    __table_args__ = (
        # Primary dashboard / time-series query index
        Index(
            "ix_fact_station_param_ts",
            "station_key",
            "parameter_key",
            "measurement_timestamp",
        ),
        # Breach / alert queries (partial — only indexes rows with a level set)
        Index(
            "ix_fact_pollution_level_ts",
            "pollution_level",
            "measurement_timestamp",
            postgresql_where=text("pollution_level IS NOT NULL"),
        ),
        # Quality-ratio queries (anomaly detector, KPI card)
        Index(
            "ix_fact_quality_ts",
            "station_key",
            "quality_ratio",
            "measurement_timestamp",
            postgresql_where=text("quality_ratio IS NOT NULL"),
        ),
        CheckConstraint(
            "pollution_level BETWEEN 1 AND 5",
            name="ck_fact_pollution_level_range",
        ),
        CheckConstraint(
            "quality_ratio BETWEEN 0 AND 1",
            name="ck_fact_quality_ratio_range",
        ),
    )

    measurement_id = Column(BigInteger, primary_key=True, autoincrement=True)

    station_key = Column(
        Integer, ForeignKey("dim_stations.station_key"), nullable=False
    )
    parameter_key = Column(
        Integer, ForeignKey("dim_parameters.parameter_key"), nullable=False
    )

    value = Column(Float, nullable=True)
    quality_ratio = Column(Float, nullable=True)
    pollution_level = Column(Integer, nullable=True)

    measurement_timestamp = Column(DateTime, nullable=False)
    offset_minutes = Column(Integer, nullable=True)

    # lazy="select" is explicit here. For the dashboard API endpoints that
    # always join station + parameter, override with options(joinedload(...))
    # at the query site instead of forcing a join for every access pattern.
    station = relationship("DimStation", back_populates="measurements", lazy="select")
    parameter = relationship(
        "DimParameter", back_populates="measurements", lazy="select"
    )
