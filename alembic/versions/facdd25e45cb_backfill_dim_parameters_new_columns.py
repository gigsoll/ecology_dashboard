"""backfill dim_parameters new columns
Revision ID: facdd25e45cb
Revises: 639662397362
Create Date: 2026-04-30 17:42:14.871287
"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.sql import table, column
from sqlalchemy import String, Float

revision: str = "facdd25e45cb"
down_revision: Union[str, Sequence[str], None] = "639662397362"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

dim_parameters = table(
    "dim_parameters",
    column("parameter_code", String),
    column("physical_min", Float),
    column("physical_max", Float),
    column("gdk_daily", Float),
    column("gdk_short_term", Float),
    column("gdk_unit_basis", String),
)

# ── Canonical thresholds — all ranges verified against actual observed data ───
#
# Key findings from diagnostic query:
#   Pressure   → stored in Pascals (observed 90 599–108 508 Pa), NOT hPa
#   NO2/O3/CO  → stored in ppm, NOT µg/m³
#   Radiation  → stored in µR/h, NOT µSv/h  (1 µSv/h = 114 µR/h)
#   PM*        → µg/m³; p99.9 = 1999/999; spikes to 3333 are noise → cap at 500
#   NH₃        → all-zero in data (sensor likely offline); range kept for future data
#   VOC        → ppb; p99=11, p99.9=110 → cap at 200 ppb
#   Temperature→ observed -27 to +49 °C; BME280 max 85 °C is valid ceiling
#   Humidity   → observed 2–100 % RH; 0–100 is correct

THRESHOLDS = {
    "PM2.5": {
        # Unit: µg/m³ | observed max 3333 (noise), p99=999, p99.9=999
        # PMS5003 effective range 0–500; anything above is saturation/noise
        "physical_min": 0,
        "physical_max": 500,
        "gdk_daily": 25,  # EU 2024/2881 daily limit
        "gdk_short_term": 35,  # WHO Interim Target-1 / UA ГДК
        "basis": "24h_avg",
    },
    "PM10": {
        # Unit: µg/m³ | observed max 3333 (noise), p99=309, p99.9=1999
        # p99.9 driven by same spike events as PM2.5 — 500 rejects all noise
        "physical_min": 0,
        "physical_max": 500,
        "gdk_daily": 45,  # WHO 2021 AQG 24h mean
        "gdk_short_term": 150,  # EU current daily alert ceiling
        "basis": "24h_avg",
    },
    "PM1.0": {
        # Unit: µg/m³ | same sensor family as PM2.5/PM10
        "physical_min": 0,
        "physical_max": 500,
        "basis": "unknown",
    },
    "CO2": {
        # Unit: ppm | SCD4x/MH-Z19 sensor range
        # Atmospheric floor ~420 ppm; OSHA STEL ceiling 5000 ppm
        "physical_min": 400,
        "physical_max": 5000,
        "gdk_daily": 1000,  # ASHRAE 62.1 / EN 16798 acceptable indoor IAQ
        "gdk_short_term": 1500,  # EN 16798 action/alert level
        "basis": "instant",
    },
    "Temperature": {
        # Unit: °C | observed -27.3 to +49.6 °C; BME280 operates to 85 °C
        "physical_min": -40,
        "physical_max": 85,
        "basis": "unknown",
    },
    "Humidity": {
        # Unit: % RH | observed 2.2–100 % — full sensor range correct
        "physical_min": 0,
        "physical_max": 100,
        "gdk_daily": 30,  # EN 16798 lower comfort bound
        "gdk_short_term": 70,  # EN 16798 upper comfort / mould risk threshold
        "basis": "instant",
    },
    "Pressure": {
        # Unit: PASCALS (not hPa!) | observed 90 599–108 508 Pa = 906–1085 hPa
        # Previous bounds (850–1100) were in hPa — caused 100% false-rejection
        "physical_min": 87000,  # ~870 hPa — extreme low (severe storm)
        "physical_max": 108600,  # ~1086 hPa — absolute world record high + margin
        "basis": "unknown",
    },
    "HCHO": {
        # Unit: µg/m³ | ZE08-CH2O sensor range 0–5 mg/m³ = 0–5000 µg/m³
        "physical_min": 0,
        "physical_max": 5000,
        "gdk_daily": 50,  # UA ГДК daily avg; WHO indoor reference
        "gdk_short_term": 100,  # WHO Indoor AQG 30-min guideline
        "basis": "instant",
    },
    "VOC": {
        # Unit: ppb (isobutylene-equiv.) | observed 0–126, p99=11, p99.9=110
        # 200 ppb gives headroom above p99.9 while cleanly rejecting sensor faults
        "physical_min": 0,
        "physical_max": 200,
        "gdk_daily": 100,  # WHO/EN 16798 acceptable indoor TVOC (~ppb equiv.)
        "gdk_short_term": 150,  # Action/alert level
        "basis": "instant",
    },
    "O3": {
        # Unit: ppm | observed 0.01–20.22; p95=0.07, p99=20 (20 ppm = sensor ceiling)
        # 20 ppm is the MiCS-2614 measurement ceiling; above = saturation
        # WHO 8h guideline = 0.051 ppm; EU target = 0.06 ppm (120 µg/m³)
        "physical_min": 0,
        "physical_max": 20,
        "gdk_short_term": 0.06,  # EU 2008/50 target value converted to ppm
        "basis": "8h",
    },
    "NO2": {
        # Unit: ppm | observed 0.01–74.32; p95=36.26 which is implausibly high
        # (36 ppm NO2 outdoors = immediately dangerous). Sensor likely needs
        # calibration but physical range of MiCS-2714 is 0–10 ppm → cap there
        "physical_min": 0,
        "physical_max": 10,
        "gdk_daily": 0.02,  # EU annual limit 40 µg/m³ converted to ppm
        "gdk_short_term": 0.1,  # EU hourly alert 200 µg/m³ converted to ppm
        "basis": "1h",
    },
    "CO": {
        # Unit: ppm | observed 0–6.23; p99=4.15, p99.9=4.74 — clean data
        # MQ-7 range 10–1000 ppm but observed max is 6.23 → cap at 50 ppm
        # (50 ppm = OSHA PEL; above that in ambient air = clear sensor fault)
        "physical_min": 0,
        "physical_max": 50,
        "gdk_daily": 3.5,  # WHO 2021 AQG 24h = 4 mg/m³ ≈ 3.5 ppm
        "gdk_short_term": 9,  # EU 2008/50 8h = 10 mg/m³ ≈ 9 ppm
        "basis": "8h",
    },
    "NH3": {
        # Unit: ppm | observed all-zero (sensor offline in dataset)
        # Range based on ME2-NH3 datasheet: 0–100 ppm
        # UA ГДК: daily 40 µg/m³ ≈ 0.057 ppm; max single 200 µg/m³ ≈ 0.29 ppm
        "physical_min": 0,
        "physical_max": 100,
        "gdk_daily": 0.057,  # UA ГДК daily average converted to ppm
        "gdk_short_term": 0.29,  # UA ГДК max single measurement converted to ppm
        "basis": "instant",
    },
    "Radiation": {
        # Unit: µR/h | observed 0–133.89; p99=16.55, p99.9=17.44 µR/h
        # Normal background in Ukraine: 10–20 µR/h (~0.1–0.2 µSv/h)
        # Ukrainian alert threshold: 30 µR/h (≈ 0.30 µSv/h)
        # 133.89 µR/h observed max is elevated but plausible near industrial sites
        # Cap at 300 µR/h — above that is a clear sensor fault or emergency event
        "physical_min": 0,
        "physical_max": 300,
        "gdk_daily": 20,  # normal urban background upper bound (µR/h)
        "gdk_short_term": 30,  # Ukrainian regulatory public alert threshold (µR/h)
        "basis": "instant",
    },
}

# ── Maps every parameter_code in dim_parameters → canonical threshold key ─────
PARAMETER_CODE_TO_THRESHOLD = {
    "PM2.5": "PM2.5",
    "SDSP2": "PM2.5",
    "PMSP2": "PM2.5",
    "PM10": "PM10",
    "SDSP1": "PM10",
    "PMSP1": "PM10",
    "PM1.0": "PM1.0",
    "PMSP0": "PM1.0",
    "CO2": "CO2",
    "BME280TEMPERATURE": "Temperature",
    "Temperature": "Temperature",
    "BME280HUMIDITY": "Humidity",
    "Humidity": "Humidity",
    "BME280PRESSURE": "Pressure",
    "Pressure": "Pressure",
    "HCHO": "HCHO",
    "VOC": "VOC",
    "O3": "O3",
    "NO2": "NO2",
    "CO": "CO",
    "NH₃": "NH3",
    "Radiation": "Radiation",
}


def upgrade():
    conn = op.get_bind()

    for param_code, threshold_key in PARAMETER_CODE_TO_THRESHOLD.items():
        vals = THRESHOLDS[threshold_key]

        update_values = {
            "physical_min": vals.get("physical_min"),
            "physical_max": vals.get("physical_max"),
            "gdk_unit_basis": vals.get("basis", "unknown"),
        }
        if "gdk_daily" in vals:
            update_values["gdk_daily"] = vals["gdk_daily"]
        if "gdk_short_term" in vals:
            update_values["gdk_short_term"] = vals["gdk_short_term"]

        conn.execute(
            dim_parameters.update()
            .where(dim_parameters.c.parameter_code == param_code)
            .values(**update_values)
        )

    op.execute("""
        UPDATE dim_parameters
        SET gdk_unit_basis = 'unknown'
        WHERE gdk_unit_basis IS NULL
    """)


def downgrade() -> None:
    """Downgrade schema."""
    pass
