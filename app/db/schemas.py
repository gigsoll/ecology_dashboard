from sqlalchemy import (
    Column,
    Integer,
    BigInteger,
    Float,
    String,
    Boolean,
    DateTime,
    ForeignKey,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class DimUnit(Base):
    """
    Dimentional table for measure units
    """

    __tablename__ = "dim_units"

    unit_key = Column(Integer, primary_key=True, autoincrement=True)
    unit_name = Column(String(64), nullable=False)
    unit_symbol = Column(String(8))
    local_unit_name = Column(String(64))

    # Links
    parameters = relationship("DimParameter", back_populates="unit")


class DimParameter(Base):
    """
    Dimentional table for measurement params
    """

    __tablename__ = "dim_parameters"

    parameter_key = Column(Integer, primary_key=True, autoincrement=True)
    parameter_code = Column(String(64), nullable=False)
    parameter_name = Column(String(64))
    local_name = Column(String(64))
    unit_key = Column(Integer, ForeignKey("dim_units.unit_key"))

    # SCD Type 2 columns
    valid_from = Column(DateTime, nullable=False)
    valid_to = Column(DateTime)
    is_current = Column(Boolean, default=True)

    # Foreign keys
    unit = relationship("DimUnit", back_populates="parameters")
    measurements = relationship("FactMeasurement", back_populates="parameter")


class DimStation(Base):
    """
    Dimentiona table for stations info
    """

    __tablename__ = "dim_stations"

    station_key = Column(Integer, primary_key=True, autoincrement=True)
    station_id = Column(Integer)
    station_name = Column(String(128))
    latitude = Column(Float)
    longitude = Column(Float)
    timezone_offset = Column(Integer)

    # SCD Type 2 columns
    valid_from = Column(DateTime, nullable=False)
    valid_to = Column(DateTime)
    is_current = Column(Boolean, default=True)

    # Foreign keys
    measurements = relationship("FactMeasurement", back_populates="station")


class FactMeasurement(Base):
    """
    Fact table
    """

    __tablename__ = "fact_measurements"

    measurement_id = Column(BigInteger, primary_key=True, autoincrement=True)

    # Foreign keys
    station_key = Column(
        Integer, ForeignKey("dim_stations.station_key"), nullable=False
    )
    parameter_key = Column(
        Integer, ForeignKey("dim_parameters.parameter_key"), nullable=False
    )

    # Dimentions
    value = Column(Float)
    quality_ratio = Column(Float, nullable=True)
    pollution_level = Column(Integer)

    # Metadata
    measurement_timestamp = Column(DateTime, nullable=False, index=True)
    offset_minutes = Column(Integer)

    # Links
    station = relationship("DimStation", back_populates="measurements")
    parameter = relationship("DimParameter", back_populates="measurements")
