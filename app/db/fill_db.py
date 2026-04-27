import pandas as pd
from sqlalchemy.orm import Session
from typing import Any
from datetime import datetime

from app.db.schemas import DimUnit, DimParameter, DimStation, FactMeasurement
from app.core.config import app_config


def transform_and_load_units(df: pd.DataFrame, session: Session) -> dict[str, int]:
    """
    Extracts unique units from a data frame and loads them into DimUnits.
    Returns a mapping of {unit_symbol: unit_key}.
    """
    # identifies unique measure units
    unique_units = df[
        ["stations_params_unit", "stations_params_localUnit"]
    ].drop_duplicates()

    unit_map = {}
    for _, row in unique_units.iterrows():
        unit = (
            session.query(DimUnit)
            .filter_by(unit_symbol=row["stations_params_unit"])
            .first()
        )
        if not unit:
            unit = DimUnit(
                unit_name=row["stations_params_unit"],  # use symbol as name
                unit_symbol=row["stations_params_unit"],
                local_unit_name=row["stations_params_localUnit"],
            )
            session.add(unit)
            session.flush()
        unit_map[unit.unit_symbol] = unit.unit_key

    return unit_map


def transform_and_load_parameters(
    df: pd.DataFrame, session: Session, unit_map: dict[str, int]
) -> dict[str, int]:
    """
    Extracts unique parameters and loads them into DimParameters.
    Handles normalization references to DimUnits.
    """
    # get unique values
    unique_params = df[
        [
            "stations_params_key",
            "stations_params_name",
            "stations_params_localName",
            "stations_params_unit",
        ]
    ].drop_duplicates()

    param_map = {}
    for _, row in unique_params.iterrows():
        param = (
            session.query(DimParameter)
            .filter_by(parameter_code=row["stations_params_key"])
            .first()
        )
        if not param:
            param = DimParameter(
                parameter_code=row["stations_params_key"],
                parameter_name=row["stations_params_name"],
                local_name=row["stations_params_localName"],
                unit_key=unit_map.get(str(row["stations_params_unit"])),
                valid_from=datetime.now(),
                is_current=True,
            )
            session.add(param)
            session.flush()
        param_map[param.parameter_code] = param.parameter_key

    return param_map


def transform_and_load_stations(df: pd.DataFrame, session: Session) -> dict[int, int]:
    """
    Extracts unique stations and loads them into DimStations.
    Returns the mapping {business_station_id: surrogate_station_key}.
    """
    unique_stations = df[
        ["stations_id", "stations_name", "Lat", "Long", "stations_offset"]
    ].drop_duplicates()

    station_map = {}
    for _, row in unique_stations.iterrows():
        station = (
            session.query(DimStation)
            .filter_by(station_id=row["stations_id"], is_current=True)
            .first()
        )
        if not station:
            station = DimStation(
                station_id=row["stations_id"],
                station_name=row["stations_name"],
                latitude=row["Lat"],
                longitude=row["Long"],
                timezone_offset=row["stations_offset"],
                valid_from=datetime.now(),
                is_current=True,
            )
            session.add(station)
            session.flush()
        station_map[station.station_id] = station.station_key

    return station_map


def load_fact_measurements(
    df: pd.DataFrame,
    session: Session,
    station_map: dict[int, int],
    param_map: dict[str, int],
    batch_size: int = app_config.batch_size,
) -> None:
    """
    Iterates over the dataframe and populates the Fact_Measurements table.
    """
    batch_buffer: list[FactMeasurement] = []

    n_added = 0
    df_size = len(df["stations_params_value"])
    for _, row in df.iterrows():
        ts: Any = row["stations_params_time"]
        if pd.isna(ts):
            continue

        # get surogat keys from mappings
        s_key = station_map.get(row["stations_id"])
        p_key = param_map.get(row["stations_params_key"])

        # skip the record if there is no key name in the dict
        if s_key is None or p_key is None:
            continue

        # create instance of a model
        fact = FactMeasurement(
            station_key=s_key,
            parameter_key=p_key,
            value=row["stations_params_value"],
            quality_ratio=row["stations_params_cr"],
            pollution_level=row["stations_params_level"],
            measurement_timestamp=ts,
            offset_minutes=row["stations_params_offset"],
        )
        batch_buffer.append(fact)

        # batch loading when reached batch_size
        if len(batch_buffer) >= batch_size:
            session.bulk_save_objects(batch_buffer)
            session.commit()

            # free memory
            batch_buffer.clear()
            session.expunge_all()
            n_added += batch_size
            print(f"\rAdded: {n_added}/{df_size}", end="")

    # Load remaining data
    if batch_buffer:
        session.bulk_save_objects(batch_buffer)
        session.commit()
        session.expunge_all()
        batch_buffer.clear()


def run_pipeline(df: pd.DataFrame, session: Session) -> None:
    """
    Orchestrates the transformation and loading process.
    """
    try:
        print("Process unit of measurements...")
        u_map = transform_and_load_units(df, session)

        print("Process measuremnts params...")
        p_map = transform_and_load_parameters(df, session, u_map)

        print("Process stations...")
        s_map = transform_and_load_stations(df, session)

        print("Load facts tables...")
        load_fact_measurements(df, session, s_map, p_map)

        print("\nETL pipeline is finished.")
    except Exception as e:
        session.rollback()
        print(f"ETL pipeline error: {e}")
        raise e
