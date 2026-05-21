from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class UnitBase(ApiModel):
    unit_name: str | None = Field(default=None, max_length=64)
    unit_symbol: str = Field(max_length=16)
    local_unit_name: str | None = Field(default=None, max_length=64)


class UnitCreate(UnitBase):
    pass


class UnitUpdate(ApiModel):
    unit_name: str | None = Field(default=None, max_length=64)
    unit_symbol: str | None = Field(default=None, max_length=16)
    local_unit_name: str | None = Field(default=None, max_length=64)


class UnitRead(UnitBase):
    unit_key: int


class ParameterBase(ApiModel):
    parameter_code: str = Field(max_length=64)
    parameter_name: str | None = Field(default=None, max_length=64)
    local_name: str | None = Field(default=None, max_length=64)
    unit_key: int | None = None
    threshold_limit: Decimal = Decimal("0")
    physical_min: float | None = None
    physical_max: float | None = None
    gdk_daily: float | None = None
    gdk_short_term: float | None = None
    gdk_unit_basis: str | None = Field(default=None, max_length=32)
    valid_from: datetime
    valid_to: datetime | None = None
    is_current: bool = True


class ParameterCreate(ParameterBase):
    pass


class ParameterUpdate(ApiModel):
    parameter_code: str | None = Field(default=None, max_length=64)
    parameter_name: str | None = Field(default=None, max_length=64)
    local_name: str | None = Field(default=None, max_length=64)
    unit_key: int | None = None
    threshold_limit: Decimal | None = None
    physical_min: float | None = None
    physical_max: float | None = None
    gdk_daily: float | None = None
    gdk_short_term: float | None = None
    gdk_unit_basis: str | None = Field(default=None, max_length=32)
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    is_current: bool | None = None


class ParameterRead(ParameterBase):
    parameter_key: int
    unit: UnitRead | None = None


class StationBase(ApiModel):
    station_id: int
    station_name: str | None = Field(default=None, max_length=128)
    latitude: float | None = None
    longitude: float | None = None
    timezone_offset: int | None = None
    valid_from: datetime
    valid_to: datetime | None = None
    is_current: bool = True


class StationCreate(StationBase):
    pass


class StationUpdate(ApiModel):
    station_id: int | None = None
    station_name: str | None = Field(default=None, max_length=128)
    latitude: float | None = None
    longitude: float | None = None
    timezone_offset: int | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    is_current: bool | None = None


class StationRead(StationBase):
    station_key: int


class MeasurementBase(ApiModel):
    station_key: int
    parameter_key: int
    value: float | None = None
    quality_ratio: float | None = None
    pollution_level: int | None = None
    measurement_timestamp: datetime
    offset_minutes: int | None = None


class MeasurementCreate(MeasurementBase):
    pass


class MeasurementUpdate(ApiModel):
    station_key: int | None = None
    parameter_key: int | None = None
    value: float | None = None
    quality_ratio: float | None = None
    pollution_level: int | None = None
    measurement_timestamp: datetime | None = None
    offset_minutes: int | None = None


class MeasurementRead(MeasurementBase):
    measurement_id: int


class MeasurementPretty(ApiModel):
    measurement_id: int
    measurement_timestamp: datetime
    offset_minutes: int | None
    value: float | None
    quality_ratio: float | None
    pollution_level: int | None
    station_key: int
    station_id: int
    station_name: str | None
    latitude: float | None
    longitude: float | None
    timezone_offset: int | None
    parameter_key: int
    parameter_code: str
    parameter_name: str | None
    parameter_local_name: str | None
    threshold_limit: Decimal
    physical_min: float | None
    physical_max: float | None
    gdk_daily: float | None
    gdk_short_term: float | None
    gdk_unit_basis: str | None
    unit_key: int | None
    unit_name: str | None
    unit_symbol: str | None
    local_unit_name: str | None


class DashboardSummary(ApiModel):
    station_id: int | None = None
    parameter_code: str
    from_ts: datetime
    to_ts: datetime
    air_quality_index: float | None = None
    air_quality_index_delta: float | None = None
    threshold_violations: int
    threshold_violations_delta: int | None = None
    momentary_violations: int = 0
    momentary_violations_delta: int | None = None
    daily_violations: int = 0
    daily_violations_delta: int | None = None
    data_health_percent: float | None = None
    data_health_delta: float | None = None
    last_data_update: datetime | None = None
    last_data_update_age_hours: float | None = None


class DashboardTimePoint(ApiModel):
    timestamp: datetime
    value: float | None = None


class DashboardTimeSeries(ApiModel):
    station_id: int | None = None
    parameter_code: str
    from_ts: datetime
    to_ts: datetime
    unit_symbol: str | None = None
    momentary_threshold: Decimal | None = None
    daily_threshold: float | None = None
    daily_window_hours: float | None = None
    daily_rolling_average_points: list[DashboardTimePoint] = Field(default_factory=list)
    daily_rolling_violations: int = 0
    points: list[DashboardTimePoint]


class DashboardViolationPoint(ApiModel):
    timestamp: datetime
    total_measurements: int
    threshold_violations: int
    momentary_violations: int = 0
    daily_violations: int = 0


class DashboardViolationSeries(ApiModel):
    station_id: int | None = None
    parameter_code: str
    from_ts: datetime
    to_ts: datetime
    bucket: str
    points: list[DashboardViolationPoint]


class DashboardStationRatingItem(ApiModel):
    rank: int
    station_id: int
    station_name: str | None = None
    average_value: float | None = None
    average_quality_ratio: float | None = None
    measurement_count: int
    threshold_violations: int
    momentary_violations: int = 0
    daily_violations: int = 0
    is_selected: bool = False


class DashboardStationRating(ApiModel):
    parameter_code: str
    from_ts: datetime
    to_ts: datetime
    lower_is_better: bool = True
    items: list[DashboardStationRatingItem]


class DashboardCorrelationItem(ApiModel):
    parameter_code: str
    parameter_name: str | None = None
    correlation: float | None = None
    samples: int


class DashboardCorrelation(ApiModel):
    station_id: int
    target_parameter_code: str
    from_ts: datetime
    to_ts: datetime
    items: list[DashboardCorrelationItem]


class DashboardMeasurementRow(ApiModel):
    measurement_id: int
    measurement_timestamp: datetime
    value: float | None = None
    quality_ratio: float | None = None
    pollution_level: int | None = None
    parameter_code: str
    parameter_name: str | None = None
    unit_symbol: str | None = None


class DashboardMeasurementsTable(ApiModel):
    station_id: int | None = None
    parameter_code: str
    from_ts: datetime
    to_ts: datetime
    items: list[DashboardMeasurementRow]
