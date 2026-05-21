import pandas as pd
from sqlalchemy import insert
from sqlalchemy.orm import Session
from typing import Any
from datetime import datetime

from app.db.database import init_db
from app.db.parameter_norms import apply_parameter_norms
from app.db.schemas import DimUnit, DimParameter, DimStation, FactMeasurement
from app.core.config import app_config


def transform_and_load_units(df: pd.DataFrame, session: Session) -> dict[str, int]:
    """
    Extracts unique units from a data frame and loads them into DimUnits.
    Returns a mapping of {unit_symbol: unit_key}.
    """
    unique_units = df[
        ["stations_params_unit", "stations_params_localUnit"]
    ].drop_duplicates()

    unit_map: dict[str, int] = {}
    for _, row in unique_units.iterrows():
        symbol: str = str(row["stations_params_unit"])

        unit = session.query(DimUnit).filter_by(unit_symbol=symbol).first()
        if not unit:
            unit = DimUnit(
                unit_symbol=symbol,
                unit_name=None,
                local_unit_name=row["stations_params_localUnit"] or None,
            )
            session.add(unit)
            session.flush()

        unit_map[str(unit.unit_symbol)] = int(unit.unit_key)  # type: ignore[arg-type]

    return unit_map


def transform_and_load_parameters(
    df: pd.DataFrame,
    session: Session,
    unit_map: dict[str, int],
) -> dict[str, int]:
    """
    Extracts unique parameters and loads them into DimParameters.
    """
    unique_params = df[
        [
            "stations_params_key",
            "stations_params_name",
            "stations_params_localName",
            "stations_params_unit",
        ]
    ].drop_duplicates()

    param_map: dict[str, int] = {}
    for _, row in unique_params.iterrows():
        code: str = str(row["stations_params_key"])

        param = (
            session.query(DimParameter)
            .filter_by(parameter_code=code, is_current=True)
            .first()
        )
        if not param:
            param = DimParameter(
                parameter_code=code,
                parameter_name=row["stations_params_name"] or None,
                local_name=row["stations_params_localName"] or None,
                unit_key=unit_map.get(str(row["stations_params_unit"])),
                valid_from=datetime.utcnow(),
                is_current=True,
            )
            session.add(param)
            session.flush()
        else:
            if param.parameter_name is None and row["stations_params_name"]:
                param.parameter_name = row["stations_params_name"]
            if param.local_name is None and row["stations_params_localName"]:
                param.local_name = row["stations_params_localName"]
            if param.unit_key is None:
                param.unit_key = unit_map.get(str(row["stations_params_unit"]))

        apply_parameter_norms(param)

        param_map[str(param.parameter_code)] = int(param.parameter_key)  # type: ignore[arg-type]

    return param_map


def transform_and_load_stations(
    df: pd.DataFrame,
    session: Session,
) -> dict[int, int]:
    """
    Extracts unique stations and loads them into DimStations.
    Returns mapping {business_station_id: surrogate_station_key}.
    """
    unique_stations = df[
        ["stations_id", "stations_name", "Lat", "Long", "stations_offset"]
    ].drop_duplicates()

    station_map: dict[int, int] = {}
    for _, row in unique_stations.iterrows():
        station_id: int = int(row["stations_id"])  # type: ignore[arg-type]

        station = (
            session.query(DimStation)
            .filter_by(station_id=station_id, is_current=True)
            .first()
        )
        if not station:
            station = DimStation(
                station_id=station_id,
                station_name=row["stations_name"] or None,
                latitude=row["Lat"],
                longitude=row["Long"],
                timezone_offset=row["stations_offset"],
                valid_from=datetime.utcnow(),
                is_current=True,
            )
            session.add(station)
            session.flush()

        station_map[int(station.station_id)] = int(station.station_key)  # type: ignore[arg-type]

    return station_map


def load_fact_measurements(
    df: pd.DataFrame,
    session: Session,
    station_map: dict[int, int],
    param_map: dict[str, int],
    batch_size: int = app_config.batch_size,
) -> None:
    """
    Iterates over the DataFrame row-by-row (memory-safe), accumulates dicts
    into a buffer, then flushes each full batch as a single multi-row Core
    INSERT — one DB round-trip per batch.
    """
    batch_buffer: list[dict[str, Any]] = []

    n_inserted = 0
    n_skipped = 0
    df_size = len(df)

    for _, row in df.iterrows():
        ts: Any = row["stations_params_time"]
        if pd.isna(ts):
            n_skipped += 1
            continue

        s_key = station_map.get(int(row["stations_id"]))
        p_key = param_map.get(str(row["stations_params_key"]))

        if s_key is None or p_key is None:
            n_skipped += 1
            continue

        # Nullify out-of-range values to satisfy DB check constraints.
        # quality_ratio: BETWEEN 0 AND 1  (ck_fact_quality_ratio_range)
        # pollution_level: BETWEEN 1 AND 5 (ck_fact_pollution_level_range)
        # Clamping would fabricate data, so we store NULL instead.
        raw_cr = row["stations_params_cr"]
        if pd.isna(raw_cr):
            quality_ratio: float | None = None
        else:
            cr = float(raw_cr)
            quality_ratio = cr if 0.0 <= cr <= 1.0 else None

        raw_level = row["stations_params_level"]
        if pd.isna(raw_level):
            pollution_level: int | None = None
        else:
            level = int(raw_level)
            pollution_level = level if 1 <= level <= 5 else None

        batch_buffer.append(
            {
                "station_key": s_key,
                "parameter_key": p_key,
                "value": None
                if pd.isna(row["stations_params_value"])
                else row["stations_params_value"],
                "quality_ratio": quality_ratio,
                "pollution_level": pollution_level,
                "measurement_timestamp": ts,
                "offset_minutes": None
                if pd.isna(row["stations_params_offset"])
                else row["stations_params_offset"],
            }
        )

        if len(batch_buffer) >= batch_size:
            # insert().values(list) compiles to a single multi-row INSERT.
            # session.execute() re-uses (or opens) the current transaction's
            # connection, so it stays valid across commits.
            session.execute(insert(FactMeasurement).values(batch_buffer))
            session.commit()
            n_inserted += len(batch_buffer)
            batch_buffer.clear()
            print(
                f"\rInserted: {n_inserted}/{df_size} (skipped: {n_skipped})",
                end="",
                flush=True,
            )

    # Flush any remaining rows that didn't fill a full batch
    if batch_buffer:
        session.execute(insert(FactMeasurement).values(batch_buffer))
        session.commit()
        n_inserted += len(batch_buffer)
        batch_buffer.clear()

    print(f"\rInserted: {n_inserted}/{df_size} (skipped: {n_skipped})", flush=True)


def run_pipeline(df: pd.DataFrame, session: Session) -> None:
    """
    Orchestrates the transformation and loading process.
    """
    init_db()
    try:
        print("Processing units of measurement...")
        u_map = transform_and_load_units(df, session)
        session.commit()

        print("Processing measurement parameters...")
        p_map = transform_and_load_parameters(df, session, u_map)
        session.commit()

        print("Processing stations...")
        s_map = transform_and_load_stations(df, session)
        session.commit()

        print("Loading fact table...")
        load_fact_measurements(df, session, s_map, p_map)

        print("ETL pipeline complete.")
    except Exception as e:
        session.rollback()
        print(f"\nETL pipeline error: {e}")
        raise
