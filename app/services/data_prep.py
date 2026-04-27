import re
from pathlib import Path
import numpy as np
import pandas as pd

from app.services.scraper import scrape_data
from app.core.config import app_config
from typing import Any


def check_if_columns_same(data_dir: str) -> bool:
    c_set = set()
    files = list(Path(data_dir).rglob("*.csv"))
    if len(files) == 0:
        print("Data dir is empty, scraping the data")
        scrape_data()

    for f in list(files):
        sdf = pd.read_csv(f)
        c_set.add(tuple(sdf.columns))

    return len(c_set) == 1


def combine_tables(data_dir: str) -> pd.DataFrame:
    files = Path(data_dir).rglob("*.csv")
    data_frame = pd.concat(map(pd.read_csv, files))
    return data_frame


def prune_invalid_measurements(df: pd.DataFrame) -> pd.DataFrame:
    """
    Deletes all records where the measurement value OR the original parameter ID is missing.
    Ensures data integrity before loading into Fact_Measurements.
    """
    df = df.copy()
    initial_count: int = len(df)
    check_list: list[str] = ["stations_params_value", "stations_params_id"]

    # Delete the record if one of subsest's values is zero
    df = df.dropna(subset=check_list)

    final_count: int = len(df)
    dropped_count: int = initial_count - final_count

    print(f"- Removed lines: {dropped_count}")
    print(f"- lines left: {final_count}")

    return df


def remap_conflicting_names(data: pd.DataFrame) -> pd.DataFrame:
    # station mapping based on notebook analysys heuristic
    stations_mapping: dict[int, str] = {
        256: "vinnytsia-256",
        281: "vinnytsia-281",
        315: "vinnytsia-315",
        90: "vinnytsia-90",
        271: "vinnytsia-271",
        767: "Соборна 36",
        1183: "Вишенька",
        246: "vinnytsia-246",
        274: "vinnytsia-274",
    }
    mask = data["stations_id"].isin(list(stations_mapping.keys()))
    data.loc[mask, "stations_name"] = data.loc[mask, "stations_id"].map(
        stations_mapping
    )
    return data


def standardize_coordinates(df: pd.DataFrame) -> pd.DataFrame:
    """
    Cleans up Lat/Long coordinates, converts them to a numeric value, rounds
    and replaces all values ​​for each station with their mode (most frequent value).
    """

    cols = ["Lat", "Long"]
    df = df.copy()

    for col in cols:
        # Cleaning and converting into numeric value
        # if column contain strings, leaves only number and dots
        if df[col].dtype == "object":
            df[col] = df[col].astype(str).str.extract(r"(\d+\.\d+)")[0]

        # Перетворюємо на float та округлюємо
        df[col] = pd.to_numeric(df[col], errors="coerce").round(5)  # type: ignore

    def get_first_mode(series):
        m = series.mode()
        return m.iloc[0] if not m.empty else None

    df[cols] = df.groupby("stations_id")[cols].transform(get_first_mode)
    return df


def get_canonical_parameters_map() -> dict[str, str]:
    return {
        # dust
        "SDS_P1": "PM10",
        "SDS_P2": "PM2.5",
        "PMS_P0": "PM1.0",
        "PMS_P1": "PM10",
        "PMS_P2": "PM2.5",
        "PM0": "PM1.0",
        "PM1": "PM1.0",
        "PM25": "PM2.5",
        "PM100": "PM10",
        "PM1.0": "PM1.0",
        "PM2.5": "PM2.5",
        "PM10": "PM10",
        # gases
        "CO2": "CO2",
        "CO": "CO",
        "NO2": "NO2",
        "O3": "O3",
        "O₃": "O3",
        "NH3": "NH3",
        "CH2O": "HCHO",
        "H2CO": "HCHO",
        "VOC": "VOC",
        "NO₂": "NO2",
        # metheodata
        "TEMPERATURE": "Temperature",
        "HUMIDITY": "Humidity",
        "PRESSURE": "Pressure",
        # Radiation
        "RAD": "Radiation",
        # Specific Sensor
        "A4": "CO",
        "E1": "NO2",
        "E3": "O3",
    }


def resolve_canonical_parameter(raw_key: Any) -> str:
    """
    Recognizes the canonical name of a parameter from a technical string of any complexity.
    """
    if pd.isna(raw_key) or str(raw_key).lower() == "nan":
        return "UNKNOWN"

    # remove unit names in parentacies
    key = str(raw_key).strip()

    # remove everything before =
    if "=" in key:
        key = key.split("=")[-1]

    # remove everything inside parentacies
    key = re.sub(r"\(.*?\)", "", key)

    # basic data formating
    key = key.replace(" ", "").replace("_", "").replace(".", "")
    key_upper = key.upper()
    param_map = get_canonical_parameters_map()

    # remplace incorect values
    return param_map.get(key_upper, key_upper)


def unify_measurement_units(df: pd.DataFrame) -> pd.DataFrame:
    """
    Standardizes units of measurement to a single Unicode format.
    """
    # unit names replacement dictionary
    unit_standardization = {
        "ug/m3": "µg/m³",
        "мкг/м³": "µg/m³",
        "ug/m³": "µg/m³",
        "ppm": "ppm",
        "ppb": "ppb",
        "%": "%",
        "Rh": "%",
        "°C": "°C",
        "C": "°C",
        "Pa": "Pa",
        "hPa": "hPa",
        "mg/m3": "mg/m³",
        "uSv/h": "µSv/h",
    }

    def clean_unit(unit_str):
        if pd.isna(unit_str):
            return "unknown"
        u = str(unit_str).strip()
        # remove parentacies missed in previeous rounds
        u = u.replace("(", "").replace(")", "")
        return unit_standardization.get(u, u)

    df["stations_params_unit"] = df["stations_params_unit"].apply(clean_unit)
    return df


def standardize_dimension_attributes(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["stations_params_key"] = df["stations_params_key"].apply(
        resolve_canonical_parameter
    )
    df = unify_measurement_units(df)
    df["stations_params_name"] = df["stations_params_name"].fillna(
        df["stations_params_key"]
    )
    return df


def prepare_dataframe_dates(df: pd.DataFrame) -> pd.DataFrame:
    """
    Pre-processed the dataframe to ensure that all date
    columns had a common format. Supports ISO8601 and mixed formats.
    """
    date_cols = ["stations_time", "stations_params_time"]
    for col in date_cols:
        # format='mixed' for exceptions
        # '2022-10-25 13:24:28' and '2023-07-03T12:10:00Z'
        df[col] = pd.to_datetime(df[col], format="mixed", utc=True)
    return df


def prepare_data():
    # check for data consistency
    if not check_if_columns_same(app_config.data_dir):
        raise ValueError("Tabular data is not consistend")

    df = (
        combine_tables(app_config.data_dir)
        .pipe(prune_invalid_measurements)
        .replace({np.nan: None})
        .pipe(remap_conflicting_names)
        .pipe(standardize_coordinates)
        .pipe(standardize_dimension_attributes)
        .pipe(prepare_dataframe_dates)
    )

    return df
