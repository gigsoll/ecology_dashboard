from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.db.schemas import DimParameter


@dataclass(frozen=True)
class ParameterNorm:
    threshold_limit: Decimal | None
    gdk_daily: float | None
    gdk_short_term: float | None
    gdk_unit_basis: str | None


UKRAINIAN_AIR_NORMS: dict[str, ParameterNorm] = {
    # Operational PM thresholds used by the dashboard for the sensor families
    # that dominate this dataset.
    "SDSP2": ParameterNorm(
        threshold_limit=Decimal("35"),
        gdk_daily=25.0,
        gdk_short_term=35.0,
        gdk_unit_basis="operational_pm25",
    ),
    "PM2.5": ParameterNorm(
        threshold_limit=Decimal("35"),
        gdk_daily=25.0,
        gdk_short_term=35.0,
        gdk_unit_basis="operational_pm25",
    ),
    "PMSP2": ParameterNorm(
        threshold_limit=Decimal("35"),
        gdk_daily=25.0,
        gdk_short_term=35.0,
        gdk_unit_basis="operational_pm25",
    ),
    "SDSP1": ParameterNorm(
        threshold_limit=Decimal("150"),
        gdk_daily=45.0,
        gdk_short_term=150.0,
        gdk_unit_basis="operational_pm10",
    ),
    "PM10": ParameterNorm(
        threshold_limit=Decimal("150"),
        gdk_daily=45.0,
        gdk_short_term=150.0,
        gdk_unit_basis="operational_pm10",
    ),
    "PMSP1": ParameterNorm(
        threshold_limit=Decimal("150"),
        gdk_daily=45.0,
        gdk_short_term=150.0,
        gdk_unit_basis="operational_pm10",
    ),
    # Current Ukrainian ambient-air norms verified from official МОЗ documents.
    "NO2": ParameterNorm(
        threshold_limit=Decimal("0.2"),
        gdk_daily=0.04,
        gdk_short_term=0.2,
        gdk_unit_basis="max_one_time/daily_avg",
    ),
    "O3": ParameterNorm(
        threshold_limit=Decimal("0.16"),
        gdk_daily=0.03,
        gdk_short_term=0.16,
        gdk_unit_basis="max_one_time/daily_avg",
    ),
    "CO": ParameterNorm(
        threshold_limit=Decimal("5"),
        gdk_daily=3.0,
        gdk_short_term=5.0,
        gdk_unit_basis="max_one_time/daily_avg",
    ),
    "NH₃": ParameterNorm(
        threshold_limit=Decimal("0.2"),
        gdk_daily=0.04,
        gdk_short_term=0.2,
        gdk_unit_basis="max_one_time/daily_avg",
    ),
    "HCHO": ParameterNorm(
        threshold_limit=Decimal("0.035"),
        gdk_daily=0.003,
        gdk_short_term=0.035,
        gdk_unit_basis="max_one_time/daily_avg",
    ),
    # The dataset also carries this sensor under a generic VOC code but the
    # label explicitly points to formaldehyde.
    "VOC": ParameterNorm(
        threshold_limit=Decimal("0.035"),
        gdk_daily=0.003,
        gdk_short_term=0.035,
        gdk_unit_basis="max_one_time/daily_avg",
    ),
}


# Parameters in this dataset for which we do not have a verified current
# Ukrainian ambient-air norm in the official sources used for this update.
UNVERIFIED_OR_NON_AMBIENT_CODES = {
    "PM1.0",
    "PMSP0",
    "CO2",
    "BME280TEMPERATURE",
    "BME280HUMIDITY",
    "BME280PRESSURE",
    "Temperature",
    "Humidity",
    "Pressure",
    "Radiation",
}


def apply_parameter_norms(parameter: DimParameter) -> bool:
    norm = UKRAINIAN_AIR_NORMS.get(str(parameter.parameter_code))
    changed = False

    if norm is not None:
        threshold_limit = norm.threshold_limit or Decimal("0")
        if parameter.threshold_limit != threshold_limit:
            parameter.threshold_limit = threshold_limit
            changed = True
        if parameter.gdk_daily != norm.gdk_daily:
            parameter.gdk_daily = norm.gdk_daily
            changed = True
        if parameter.gdk_short_term != norm.gdk_short_term:
            parameter.gdk_short_term = norm.gdk_short_term
            changed = True
        if parameter.gdk_unit_basis != norm.gdk_unit_basis:
            parameter.gdk_unit_basis = norm.gdk_unit_basis
            changed = True
        return changed

    if str(parameter.parameter_code) in UNVERIFIED_OR_NON_AMBIENT_CODES:
        if parameter.threshold_limit != Decimal("0"):
            parameter.threshold_limit = Decimal("0")
            changed = True
        if parameter.gdk_daily is not None:
            parameter.gdk_daily = None
            changed = True
        if parameter.gdk_short_term is not None:
            parameter.gdk_short_term = None
            changed = True
        if parameter.gdk_unit_basis != "unverified_or_not_applicable":
            parameter.gdk_unit_basis = "unverified_or_not_applicable"
            changed = True

    return changed
