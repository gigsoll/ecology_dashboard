import { useEffect, useMemo, useState } from "react";
import ReactEChartsCore from "echarts-for-react/lib/core";
import * as echarts from "echarts/core";
import { BarChart, LineChart } from "echarts/charts";
import {
    GridComponent,
    LegendComponent,
    MarkLineComponent,
    TooltipComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import { CircleMarker, MapContainer, TileLayer, Tooltip, useMap } from "react-leaflet";
import {
    Activity,
    AlertTriangle,
    CalendarRange,
    Clock3,
    Database,
    Gauge,
    LoaderCircle,
    MapPinned,
    Waves,
} from "lucide-react";

const API_BASE = "/api/v1";
const DEFAULT_CENTER = [49.2331, 28.4682];

echarts.use([
    BarChart,
    LineChart,
    CanvasRenderer,
    GridComponent,
    LegendComponent,
    MarkLineComponent,
    TooltipComponent,
]);

function buildApiUrl(path, params = {}) {
    const url = new URL(`${API_BASE}${path}`, window.location.origin);
    Object.entries(params).forEach(([key, value]) => {
        if (value !== null && value !== undefined && value !== "") {
            url.searchParams.set(key, value);
        }
    });
    return `${url.pathname}${url.search}`;
}

async function fetchJson(path, params = {}) {
    const response = await fetch(buildApiUrl(path, params));
    if (!response.ok) {
        const text = await response.text();
        throw new Error(text || `Request failed with ${response.status}`);
    }
    return response.json();
}

function dedupeStations(rows) {
    const stations = new Map();
    rows.forEach((row) => {
        const current = stations.get(row.station_id);
        if (!current || row.is_current || row.valid_from > current.valid_from) {
            stations.set(row.station_id, row);
        }
    });
    return Array.from(stations.values())
        .filter((station) => station.latitude !== null && station.longitude !== null)
        .sort((left, right) => left.station_name.localeCompare(right.station_name));
}

function dedupeParameters(rows) {
    const parameters = new Map();
    rows.forEach((row) => {
        const current = parameters.get(row.parameter_code);
        if (!current || row.is_current || row.valid_from > current.valid_from) {
            parameters.set(row.parameter_code, row);
        }
    });
    return Array.from(parameters.values()).sort((left, right) =>
        left.parameter_code.localeCompare(right.parameter_code),
    );
}

function formatMetric(value, digits = 2, fallback = "Немає даних") {
    if (value === null || value === undefined || Number.isNaN(Number(value))) {
        return fallback;
    }
    return Number(value).toFixed(digits);
}

function clamp(value, min = 0, max = 100) {
    return Math.min(max, Math.max(min, value));
}

function average(values) {
    if (!values.length) {
        return null;
    }
    return values.reduce((sum, value) => sum + value, 0) / values.length;
}

function median(values) {
    if (!values.length) {
        return null;
    }
    const sorted = [...values].sort((left, right) => left - right);
    const middle = Math.floor(sorted.length / 2);
    return sorted.length % 2 === 0
        ? (sorted[middle - 1] + sorted[middle]) / 2
        : sorted[middle];
}

function formatDelta(value, suffix = "") {
    if (value === null || value === undefined || Number.isNaN(Number(value))) {
        return "Немає порівняння";
    }
    const numeric = Number(value);
    const sign = numeric > 0 ? "+" : "";
    return `${sign}${numeric.toFixed(2)}${suffix}`;
}

function formatDateTime(value) {
    if (!value) {
        return "Немає даних";
    }
    return new Intl.DateTimeFormat("uk-UA", {
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
    }).format(new Date(value));
}

function relativeAge(hours) {
    if (hours === null || hours === undefined) {
        return "Немає даних";
    }
    if (hours < 1) {
        return `${Math.max(1, Math.round(hours * 60))} хв тому`;
    }
    if (hours < 24) {
        return `${hours.toFixed(1)} год тому`;
    }
    return `${(hours / 24).toFixed(1)} дн тому`;
}

function toDateInputValue(value) {
    if (!value) {
        return "";
    }
    return new Date(value).toISOString().slice(0, 10);
}

function toStartOfDayIso(dateValue) {
    return `${dateValue}T00:00:00Z`;
}

function toEndOfDayIso(dateValue) {
    return `${dateValue}T23:59:59Z`;
}

function getMomentaryThreshold(source) {
    if (!source) {
        return null;
    }
    const explicitThreshold = Number(source.threshold_limit ?? source.momentary_threshold);
    if (Number.isFinite(explicitThreshold) && explicitThreshold > 0) {
        return explicitThreshold;
    }
    const shortTermThreshold = Number(source.gdk_short_term);
    if (Number.isFinite(shortTermThreshold) && shortTermThreshold > 0) {
        return shortTermThreshold;
    }
    return null;
}

function getDailyWindowHours(source) {
    if (!source) {
        return null;
    }
    const basis = String(source.daily_threshold_basis || source.gdk_unit_basis || "").toLowerCase();
    if (!basis) {
        return null;
    }
    if (basis.includes("8h")) {
        return 8;
    }
    if (basis.includes("1h")) {
        return 1;
    }
    if (basis.includes("instant")) {
        return null;
    }
    return 24;
}

function computeRollingAverage(points, windowHours) {
    if (!points?.length || !windowHours || windowHours <= 0) {
        return [];
    }

    const windowMs = windowHours * 60 * 60 * 1000;
    const queue = [];
    let sum = 0;

    return points.map((point) => {
        const timestamp = new Date(point.timestamp).getTime();
        const value = point.value === null || point.value === undefined ? null : Number(point.value);

        while (queue.length > 0 && queue[0].timestamp < timestamp - windowMs) {
            sum -= queue.shift().value;
        }

        if (Number.isFinite(value)) {
            queue.push({ timestamp, value });
            sum += value;
        }

        if (!queue.length) {
            return null;
        }

        return sum / queue.length;
    });
}

function calculateDataHealthFormula(rows, fromTs, toTs) {
    if (!rows?.length || !fromTs || !toTs) {
        return null;
    }

    const dedupedRows = Array.from(
        rows.reduce((memo, row) => {
            const timestamp = new Date(row.measurement_timestamp).getTime();
            if (!Number.isNaN(timestamp) && !memo.has(timestamp)) {
                memo.set(timestamp, row);
            }
            return memo;
        }, new Map()).entries(),
    )
        .sort((left, right) => left[0] - right[0])
        .map((entry) => entry[1]);

    if (!dedupedRows.length) {
        return null;
    }

    const timestamps = dedupedRows
        .map((row) => new Date(row.measurement_timestamp).getTime())
        .filter((value) => !Number.isNaN(value));
    const intervals = timestamps
        .slice(1)
        .map((value, index) => value - timestamps[index])
        .filter((value) => value > 0);
    const medianInterval = median(intervals);
    const windowMs = Math.max(1000, new Date(toTs).getTime() - new Date(fromTs).getTime());
    const expectedInterval = medianInterval && medianInterval > 0 ? medianInterval : 24 * 60 * 60 * 1000;
    const bucketStarts = new Set(
        timestamps.map((timestamp) =>
            Math.floor((timestamp - new Date(fromTs).getTime()) / expectedInterval),
        ),
    );
    const expectedCount = Math.max(1, Math.floor(windowMs / expectedInterval) + 1);

    const validRows = dedupedRows.filter((row) => row.value !== null && row.value !== undefined);
    const missingBucketCount = Math.max(0, expectedCount - bucketStarts.size);
    const nullValueCount = Math.max(0, dedupedRows.length - validRows.length);
    const missingValuesScore = clamp(
        ((expectedCount - missingBucketCount - nullValueCount) / expectedCount) * 100,
    );

    const regularityScore = (() => {
        if (!intervals.length || !medianInterval || medianInterval <= 0) {
            return validRows.length > 0 ? 100 : 0;
        }
        const normalizedDeviation = average(
            intervals.map((interval) => Math.abs(interval - medianInterval) / medianInterval),
        );
        return clamp((1 - Math.min(normalizedDeviation ?? 1, 1)) * 100);
    })();

    const qualityValues = validRows
        .map((row) => Number(row.quality_ratio))
        .filter((value) => Number.isFinite(value));
    const averageQuality = average(qualityValues);
    const validRangeShare =
        validRows.length > 0
            ? validRows.filter((row) => {
                const value = Number(row.value);
                const min = row.physical_min === null ? -Infinity : Number(row.physical_min);
                const max = row.physical_max === null ? Infinity : Number(row.physical_max);
                return Number.isFinite(value) && value >= min && value <= max;
            }).length / validRows.length
            : 0;
    const consistencyScore = clamp(
        averageQuality === null
            ? validRangeShare * 100
            : averageQuality * 70 + validRangeShare * 30,
    );

    return {
        score: clamp(consistencyScore * 0.4 + regularityScore * 0.3 + missingValuesScore * 0.3),
        consistencyScore,
        regularityScore,
        missingValuesScore,
        expectedCount,
        actualCount: dedupedRows.length,
        validCount: validRows.length,
        missingCount: missingBucketCount + nullValueCount,
        missingBucketCount,
        nullValueCount,
        averageQuality,
    };
}

function severityColor(value, threshold) {
    if (value === null || value === undefined) {
        return "#94a3b8";
    }
    if (threshold && value > threshold) {
        return "#ef4444";
    }
    return "#14b8a6";
}

function markerRadius(value, threshold) {
    if (value === null || value === undefined) {
        return 8;
    }
    if (threshold && value > threshold) {
        return 13;
    }
    return 10;
}

function MapViewport({ stations, selectedStationId }) {
    const map = useMap();

    useEffect(() => {
        const selected = stations.find((station) => station.station_id === selectedStationId);
        if (selected) {
            map.flyTo([selected.latitude, selected.longitude], 12, { duration: 0.8 });
            return;
        }

        if (stations.length > 0) {
            const bounds = stations.map((station) => [station.latitude, station.longitude]);
            map.fitBounds(bounds, { padding: [28, 28] });
        } else {
            map.setView(DEFAULT_CENTER, 11);
        }
    }, [map, selectedStationId, stations]);

    return null;
}

function useDashboardData(selectedStationId, selectedParameter, dateRange) {
    const [state, setState] = useState({
        loading: true,
        error: "",
        stations: [],
        parameters: [],
        availableParameterCodes: [],
        summary: null,
        timeSeries: null,
        violations: null,
        rating: null,
        correlation: null,
        recentMeasurements: null,
        stationSnapshots: [],
        measurementRows: [],
    });

    useEffect(() => {
        let ignore = false;

        async function loadDashboard() {
            setState((current) => ({ ...current, loading: true, error: "" }));

            try {
                const [stationRows, parameterRows] = await Promise.all([
                    fetchJson("/stations", { limit: 1000 }),
                    fetchJson("/parameters", { limit: 1000 }),
                ]);

                const stations = dedupeStations(stationRows);
                const parameters = dedupeParameters(parameterRows);
                let availableParameterCodes = parameters.map((parameter) => parameter.parameter_code);

                if (selectedStationId !== null) {
                    const stationMeasurements = await fetchJson("/measurements/pretty", {
                        station_id: String(selectedStationId),
                        limit: 1000,
                    });
                    availableParameterCodes = Array.from(
                        new Set(stationMeasurements.map((row) => row.parameter_code)),
                    ).sort((left, right) => left.localeCompare(right));
                }

                const effectiveParameter =
                    selectedParameter && availableParameterCodes.includes(selectedParameter)
                        ? selectedParameter
                        : availableParameterCodes[0] || "";

                if (!effectiveParameter) {
                    if (!ignore) {
                        setState({
                            loading: false,
                            error: "",
                            stations,
                            parameters,
                            availableParameterCodes,
                            summary: null,
                            timeSeries: null,
                            violations: null,
                            rating: null,
                            correlation: null,
                            recentMeasurements: null,
                            stationSnapshots: [],
                            measurementRows: [],
                        });
                    }
                    return;
                }

                const anchorSummaryParams = {
                    parameter_code: effectiveParameter,
                };

                if (selectedStationId !== null) {
                    anchorSummaryParams.station_id = String(selectedStationId);
                }

                const anchorSummary = await fetchJson("/dashboard/summary", anchorSummaryParams);
                const hasCustomRange = Boolean(dateRange.fromDate && dateRange.toDate);
                const fromTs = hasCustomRange
                    ? toStartOfDayIso(dateRange.fromDate)
                    : anchorSummary.from_ts;
                const toTs = hasCustomRange ? toEndOfDayIso(dateRange.toDate) : anchorSummary.to_ts;
                const sharedParams = {
                    parameter_code: effectiveParameter,
                    from_ts: fromTs,
                    to_ts: toTs,
                };

                if (selectedStationId !== null) {
                    sharedParams.station_id = String(selectedStationId);
                }

                const requests = [
                    fetchJson("/dashboard/summary", sharedParams),
                    fetchJson("/dashboard/time-dynamics", { ...sharedParams, limit: 360 }),
                    fetchJson("/dashboard/violation-dynamics", {
                        ...sharedParams,
                        bucket: "day",
                    }),
                    fetchJson("/dashboard/station-rating", {
                        parameter_code: effectiveParameter,
                        selected_station_id:
                            selectedStationId === null ? undefined : String(selectedStationId),
                        from_ts: fromTs,
                        to_ts: toTs,
                        limit: 8,
                    }),
                    fetchJson("/dashboard/recent-measurements", { ...sharedParams, limit: 12 }),
                    fetchJson("/measurements/pretty", {
                        parameter_code: effectiveParameter,
                        from_ts: fromTs,
                        to_ts: toTs,
                        limit: 1000,
                    }),
                ];

                if (selectedStationId !== null) {
                    requests.push(
                        fetchJson("/dashboard/correlation", {
                            station_id: String(selectedStationId),
                            parameter_code: effectiveParameter,
                            from_ts: fromTs,
                            to_ts: toTs,
                        }),
                    );
                }

                const results = await Promise.all(requests);
                const [summary, timeSeries, violations, rating, recentMeasurements, prettyRows, correlation] =
                    results;

                const snapshots = Array.from(
                    prettyRows.reduce((memo, row) => {
                        if (!memo.has(row.station_id)) {
                            memo.set(row.station_id, row);
                        }
                        return memo;
                    }, new Map()).values(),
                );

                if (!ignore) {
                    setState({
                        loading: false,
                        error: "",
                        stations,
                        parameters,
                        availableParameterCodes,
                        summary,
                        timeSeries,
                        violations,
                        rating,
                        correlation: correlation || null,
                        recentMeasurements,
                        stationSnapshots: snapshots,
                        measurementRows: prettyRows,
                    });
                }
            } catch (error) {
                if (!ignore) {
                    setState((current) => ({
                        ...current,
                        loading: false,
                        error: error.message || "Не вдалося завантажити дані панелі.",
                    }));
                }
            }
        }

        loadDashboard();

        return () => {
            ignore = true;
        };
    }, [dateRange, selectedParameter, selectedStationId]);

    return state;
}

function StatCard({ icon: Icon, label, value, hint, accent = "teal" }) {
    return (
        <section className={`stat-card stat-card--${accent}`}>
            <div className="stat-card__header">
                <span>{label}</span>
                <Icon size={18} />
            </div>
            <strong>{value}</strong>
            <small>{hint}</small>
        </section>
    );
}

function ChartPanel({ title, subtitle, meta, children }) {
    return (
        <section className="panel">
            <div className="panel__head">
                <div>
                    <h3>{title}</h3>
                    {subtitle ? <p>{subtitle}</p> : null}
                </div>
            </div>
            {meta ? <div className="panel__meta">{meta}</div> : null}
            <div className="panel__body">{children}</div>
        </section>
    );
}

export default function App() {
    const [selectedStationId, setSelectedStationId] = useState(null);
    const [selectedParameter, setSelectedParameter] = useState("");
    const [dateRange, setDateRange] = useState({ fromDate: "", toDate: "" });
    const [draftDateRange, setDraftDateRange] = useState({ fromDate: "", toDate: "" });
    const {
        loading,
        error,
        stations,
        parameters,
        availableParameterCodes,
        summary,
        timeSeries,
        violations,
        rating,
        correlation,
        recentMeasurements,
        stationSnapshots,
        measurementRows,
    } = useDashboardData(selectedStationId, selectedParameter, dateRange);

    useEffect(() => {
        if (!availableParameterCodes.length) {
            if (selectedParameter !== "") {
                setSelectedParameter("");
            }
            return;
        }
        const hasSelected = availableParameterCodes.includes(selectedParameter);
        if (!hasSelected) {
            setSelectedParameter(availableParameterCodes[0]);
        }
    }, [availableParameterCodes, selectedParameter]);

    const visibleParameters = useMemo(() => {
        if (selectedStationId === null) {
            return parameters;
        }
        return parameters.filter((parameter) =>
            availableParameterCodes.includes(parameter.parameter_code),
        );
    }, [availableParameterCodes, parameters, selectedStationId]);

    const selectedStation = stations.find((station) => station.station_id === selectedStationId) || null;
    const activeParameterCode =
        timeSeries?.parameter_code || summary?.parameter_code || selectedParameter;
    const selectedParameterInfo =
        parameters.find((parameter) => parameter.parameter_code === activeParameterCode) || null;

    const activeFromDate = dateRange.fromDate || toDateInputValue(summary?.from_ts);
    const activeToDate = dateRange.toDate || toDateInputValue(summary?.to_ts);

    useEffect(() => {
        setDraftDateRange((current) => {
            const nextFromDate = dateRange.fromDate || toDateInputValue(summary?.from_ts);
            const nextToDate = dateRange.toDate || toDateInputValue(summary?.to_ts);
            if (current.fromDate === nextFromDate && current.toDate === nextToDate) {
                return current;
            }
            return {
                fromDate: nextFromDate,
                toDate: nextToDate,
            };
        });
    }, [dateRange.fromDate, dateRange.toDate, summary?.from_ts, summary?.to_ts]);

    const timeWindowHours =
        timeSeries?.from_ts && timeSeries?.to_ts
            ? (new Date(timeSeries.to_ts).getTime() - new Date(timeSeries.from_ts).getTime()) /
            (60 * 60 * 1000)
            : null;
    const frontendDataHealth = useMemo(() => {
        if (selectedStationId === null || !summary?.from_ts || !summary?.to_ts) {
            return null;
        }
        return calculateDataHealthFormula(measurementRows, summary.from_ts, summary.to_ts);
    }, [measurementRows, selectedStationId, summary?.from_ts, summary?.to_ts]);

    const stationSnapshotMap = useMemo(() => {
        return stationSnapshots.reduce((memo, row) => {
            memo.set(row.station_id, row);
            return memo;
        }, new Map());
    }, [stationSnapshots]);

    const intervalStations = useMemo(() => {
        return stations.filter((station) => stationSnapshotMap.has(station.station_id));
    }, [stationSnapshotMap, stations]);

    useEffect(() => {
        if (!stations.length) {
            if (selectedStationId !== null) {
                setSelectedStationId(null);
            }
            return;
        }

        if (selectedStationId === null) {
            setSelectedStationId(stations[0].station_id);
            return;
        }

        const existsInStations = stations.some((station) => station.station_id === selectedStationId);
        if (!existsInStations) {
            setSelectedStationId(stations[0].station_id);
        }
    }, [selectedStationId, stations]);

    const mapStations = useMemo(() => {
        return intervalStations.map((station) => ({
            ...station,
            snapshot: stationSnapshotMap.get(station.station_id) || null,
        }));
    }, [intervalStations, stationSnapshotMap]);

    const stationOptions = useMemo(
        () =>
            stations.map((station) => ({
                label: station.station_name,
                value: station.station_id,
            })),
        [stations],
    );

    const activeMomentaryThreshold =
        timeSeries?.momentary_threshold !== null && timeSeries?.momentary_threshold !== undefined
            ? Number(timeSeries.momentary_threshold)
            : getMomentaryThreshold(selectedParameterInfo);
    const dailyWindowHours =
        selectedStationId !== null
            ? timeSeries?.daily_window_hours ?? getDailyWindowHours(selectedParameterInfo)
            : null;
    const dailyThresholdValue =
        timeSeries?.daily_threshold !== null && timeSeries?.daily_threshold !== undefined
            ? Number(timeSeries.daily_threshold)
            : selectedParameterInfo?.gdk_daily !== null && selectedParameterInfo?.gdk_daily !== undefined
                ? Number(selectedParameterInfo.gdk_daily)
                : null;
    const dailyRollingAveragePoints = useMemo(() => {
        if (!selectedStationId || !timeSeries?.points?.length || !dailyWindowHours) {
            return [];
        }
        if (timeSeries.daily_rolling_average_points?.length) {
            return timeSeries.daily_rolling_average_points.map((point) =>
                point.value === null || point.value === undefined ? null : Number(point.value),
            );
        }
        return computeRollingAverage(timeSeries.points, dailyWindowHours);
    }, [
        dailyWindowHours,
        selectedStationId,
        timeSeries?.daily_rolling_average_points,
        timeSeries?.points,
    ]);
    const dailyRollingViolationCount =
        selectedStationId !== null
            ? timeSeries?.daily_rolling_violations ??
            (dailyRollingAveragePoints.length && dailyThresholdValue !== null && dailyThresholdValue > 0
                ? dailyRollingAveragePoints.filter(
                    (value) => value !== null && Number(value) > dailyThresholdValue,
                ).length
                : 0)
            : 0;
    const showDailyRollingSeries =
        selectedStationId !== null &&
        dailyWindowHours !== null &&
        dailyThresholdValue !== null &&
        dailyThresholdValue > 0 &&
        dailyRollingAveragePoints.length > 0;
    const timeSeriesChartMeta = showDailyRollingSeries
        ? `Добовий ГДК: ${formatMetric(dailyThresholdValue)} ${timeSeries?.unit_symbol || ""} · Перевищень за ковзним середнім: ${dailyRollingViolationCount}`
        : null;
    const timeSeriesChartSubtitle = showDailyRollingSeries
        ? `${selectedParameterInfo?.parameter_name || activeParameterCode} за обраний інтервал; добовий ГДК і ковзне середнє показані окремо`
        : `${selectedParameterInfo?.parameter_name || activeParameterCode} за обраний інтервал`;

    const timeOption = useMemo(() => {
        if (!timeSeries?.points?.length) {
            return null;
        }
        const hasDailyRollingSeries = showDailyRollingSeries;
        return {
            backgroundColor: "transparent",
            legend: {
                top: 0,
                textStyle: { color: "#475569" },
            },
            tooltip: {
                trigger: "axis",
                valueFormatter: (value) =>
                    value === null || value === undefined
                        ? "Немає даних"
                        : `${Number(value).toFixed(2)} ${timeSeries.unit_symbol || ""}`,
            },
            grid: { left: 18, right: 22, top: 48, bottom: 30, containLabel: true },
            xAxis: {
                type: "category",
                boundaryGap: false,
                axisLabel: {
                    color: "#64748b",
                    formatter: (value) =>
                        new Intl.DateTimeFormat("uk-UA", {
                            month: "short",
                            day: "numeric",
                            hour: timeWindowHours !== null && timeWindowHours <= 36 ? "2-digit" : undefined,
                        }).format(new Date(value)),
                },
                data: timeSeries.points.map((point) => point.timestamp),
            },
            yAxis: {
                type: "value",
                axisLabel: { color: "#64748b" },
                splitLine: { lineStyle: { color: "rgba(148, 163, 184, 0.16)" } },
            },
            series: [
                {
                    name: activeParameterCode,
                    type: "line",
                    smooth: true,
                    showSymbol: false,
                    lineStyle: { width: 3, color: "#0f766e" },
                    areaStyle: {
                        color: {
                            type: "linear",
                            x: 0,
                            y: 0,
                            x2: 0,
                            y2: 1,
                            colorStops: [
                                { offset: 0, color: "rgba(20, 184, 166, 0.35)" },
                                { offset: 1, color: "rgba(15, 118, 110, 0.04)" },
                            ],
                        },
                    },
                    data: timeSeries.points.map((point) => point.value),
                    markLine:
                        timeSeries.momentary_threshold && Number(timeSeries.momentary_threshold) > 0
                            ? {
                                symbol: "none",
                                lineStyle: { color: "#ef4444", type: "dashed", width: 2 },
                                label: { formatter: "Разовий ліміт", color: "#b91c1c" },
                                data: [{ yAxis: Number(timeSeries.momentary_threshold) }],
                            }
                            : undefined,
                },
                ...(hasDailyRollingSeries
                    ? [
                        {
                            name: "Ковзне середнє",
                            type: "line",
                            smooth: true,
                            showSymbol: false,
                            lineStyle: { width: 2, color: "#f59e0b", type: "dashed" },
                            data: dailyRollingAveragePoints,
                            markLine:
                                dailyThresholdValue && dailyThresholdValue > 0
                                    ? {
                                        symbol: "none",
                                        lineStyle: { color: "#b45309", type: "dotted", width: 2 },
                                        label: { formatter: "Добовий ГДК", color: "#92400e" },
                                        data: [{ yAxis: dailyThresholdValue }],
                                    }
                                    : undefined,
                        },
                    ]
                    : []),
            ],
        };
    }, [
        activeParameterCode,
        dailyRollingAveragePoints,
        dailyThresholdValue,
        showDailyRollingSeries,
        timeSeries,
        timeWindowHours,
    ]);

    const violationsOption = useMemo(() => {
        if (!violations?.points?.length) {
            return null;
        }
        return {
            backgroundColor: "transparent",
            tooltip: {
                trigger: "axis",
                axisPointer: { type: "shadow" },
                formatter: (params) => {
                    const point = params[0];
                    const row = violations.points[point.dataIndex];
                    return [
                        `${formatDateTime(row.timestamp)}`,
                        `Загальна кількість: ${row.threshold_violations}`,
                        `Разові перевищення: ${row.momentary_violations}`,
                        `Добові перевищення: ${row.daily_violations}`,
                        `Вимірювань у бакеті: ${row.total_measurements}`,
                    ].join("<br/>");
                },
            },
            legend: {
                top: 0,
                textStyle: { color: "#475569" },
            },
            grid: { left: 18, right: 22, top: 42, bottom: 28, containLabel: true },
            xAxis: {
                type: "category",
                axisLabel: { color: "#64748b" },
                data: violations.points.map((point) => point.timestamp),
            },
            yAxis: {
                type: "value",
                axisLabel: { color: "#64748b" },
                splitLine: { lineStyle: { color: "rgba(148, 163, 184, 0.16)" } },
            },
            series: [
                {
                    name: "Загальна кількість",
                    type: "line",
                    smooth: true,
                    showSymbol: false,
                    lineStyle: { width: 3, color: "#0f766e" },
                    itemStyle: { color: "#0f766e" },
                    data: violations.points.map((point) => point.threshold_violations),
                },
                {
                    name: "Разові перевищення",
                    type: "bar",
                    itemStyle: { color: "#f97316", borderRadius: [4, 4, 0, 0] },
                    data: violations.points.map((point) => point.momentary_violations),
                },
                {
                    name: "Добові перевищення",
                    type: "bar",
                    itemStyle: { color: "#dc2626", borderRadius: [4, 4, 0, 0] },
                    data: violations.points.map((point) => point.daily_violations),
                },
            ],
        };
    }, [violations]);

    const ratingOption = useMemo(() => {
        if (!rating?.items?.length) {
            return null;
        }
        return {
            backgroundColor: "transparent",
            tooltip: {
                trigger: "axis",
                axisPointer: { type: "shadow" },
                formatter: (params) => {
                    const point = params[0];
                    const item = rating.items[point.dataIndex];
                    return `${item.station_name}<br/>Сума перевищень: ${item.threshold_violations}<br/>Разові: ${item.momentary_violations}<br/>Добові: ${item.daily_violations}<br/>Середнє значення: ${formatMetric(
                        item.average_value,
                        2,
                    )}<br/>Кількість вимірювань: ${item.measurement_count}`;
                },
            },
            grid: { left: 12, right: 18, top: 18, bottom: 18, containLabel: true },
            xAxis: {
                type: "value",
                min: 0,
                axisLabel: { color: "#64748b" },
                splitLine: { lineStyle: { color: "rgba(148, 163, 184, 0.16)" } },
            },
            yAxis: {
                type: "category",
                axisLabel: { color: "#334155" },
                data: rating.items.map((item) => item.station_name || `Станція ${item.station_id}`),
            },
            series: [
                {
                    name: "Разові перевищення",
                    type: "bar",
                    stack: "violations",
                    data: rating.items.map((item) => ({
                        value: item.momentary_violations,
                        itemStyle: {
                            color: item.is_selected
                                ? "#f59e0b"
                                : item.threshold_violations === 0
                                    ? "#10b981"
                                    : "#f97316",
                            borderRadius: [0, 0, 0, 0],
                        },
                        label: {
                            show: item.threshold_violations > 0 && item.daily_violations === 0,
                            position: "right",
                            color: "#334155",
                            formatter: () => `${item.threshold_violations}`,
                        },
                    })),
                },
                {
                    name: "Добові перевищення",
                    type: "bar",
                    stack: "violations",
                    data: rating.items.map((item) => ({
                        value: item.daily_violations,
                        itemStyle: {
                            color: item.is_selected ? "#facc15" : "#dc2626",
                            borderRadius: item.daily_violations > 0 ? [0, 8, 8, 0] : [0, 0, 0, 0],
                        },
                        label: {
                            show: item.threshold_violations > 0,
                            position: "right",
                            color: "#334155",
                            formatter: () => `${item.threshold_violations}`,
                        },
                    })),
                },
            ],
        };
    }, [rating]);

    const correlationOption = useMemo(() => {
        if (!correlation?.items?.length) {
            return null;
        }
        return {
            tooltip: {
                trigger: "axis",
                axisPointer: { type: "shadow" },
                valueFormatter: (value) =>
                    value === null || value === undefined ? "Немає даних" : Number(value).toFixed(4),
            },
            grid: { left: 18, right: 18, top: 12, bottom: 28, containLabel: true },
            xAxis: {
                type: "value",
                min: -1,
                max: 1,
                axisLabel: { color: "#64748b" },
                splitLine: { lineStyle: { color: "rgba(148, 163, 184, 0.16)" } },
            },
            yAxis: {
                type: "category",
                axisLabel: { color: "#334155" },
                data: correlation.items.slice(0, 8).map((item) => item.parameter_code),
            },
            series: [
                {
                    type: "bar",
                    data: correlation.items.slice(0, 8).map((item) => ({
                        value: item.correlation,
                        itemStyle: {
                            color: item.correlation >= 0 ? "#0ea5e9" : "#f97316",
                            borderRadius: [6, 6, 6, 6],
                        },
                    })),
                },
            ],
        };
    }, [correlation]);

    const dataHealthValue =
        selectedStationId !== null
            ? frontendDataHealth
                ? `${formatMetric(frontendDataHealth.score, 0)}%`
                : "Немає даних"
            : summary
                ? `${formatMetric(summary.data_health_percent)}%`
                : "Завантаження";

    const dataHealthHint =
        selectedStationId !== null
            ? frontendDataHealth
                ? `${frontendDataHealth.missingBucketCount} пропусків часу, ${frontendDataHealth.nullValueCount} null-значень`
                : "Оберіть інтервал з вимірюваннями"
            : summary
                ? `${formatDelta(summary.data_health_delta, "%")} повнота`
                : " ";
    const violationValue =
        selectedStationId === null
            ? "Оберіть станцію"
            : summary
                ? String(summary.threshold_violations)
                : "Завантаження";
    const violationHint =
        selectedStationId === null
            ? "KPI показується для обраної станції"
            : summary
                ? `${summary.momentary_violations} разових, ${summary.daily_violations} добових`
                : " ";
    const canApplyWindow =
        Boolean(draftDateRange.fromDate && draftDateRange.toDate) &&
        (draftDateRange.fromDate !== dateRange.fromDate || draftDateRange.toDate !== dateRange.toDate);

    return (
        <div className="dashboard-shell">
            <aside className="sidebar">
                <div className="brand-card">
                    <div className="brand-card__mark">
                        <Waves size={18} />
                    </div>
                    <div>
                        <strong>Якість повітря</strong>
                    </div>
                </div>

                <div className="sidebar__group">
                    <label htmlFor="station-select">Станція</label>
                    <select
                        id="station-select"
                        value={selectedStationId ?? ""}
                        onChange={(event) =>
                            setSelectedStationId(
                                event.target.value === "" ? null : Number(event.target.value),
                            )
                        }
                    >
                        {stationOptions.map((station) => (
                            <option key={station.value} value={station.value}>
                                {station.label}
                            </option>
                        ))}
                    </select>
                </div>

                <div className="sidebar__group">
                    <span>Часовий інтервал</span>
                    <div className="date-range-card">
                        <div className="date-range-card__head">
                            <CalendarRange size={16} />
                            <strong>Власний діапазон</strong>
                        </div>
                        <label htmlFor="from-date">Від</label>
                        <input
                            id="from-date"
                            type="date"
                            value={draftDateRange.fromDate}
                            max={draftDateRange.toDate || undefined}
                            onChange={(event) =>
                                setDraftDateRange((current) => ({
                                    ...current,
                                    fromDate: event.target.value,
                                }))
                            }
                        />
                        <label htmlFor="to-date">До</label>
                        <input
                            id="to-date"
                            type="date"
                            value={draftDateRange.toDate}
                            min={draftDateRange.fromDate || undefined}
                            onChange={(event) =>
                                setDraftDateRange((current) => ({
                                    ...current,
                                    toDate: event.target.value,
                                }))
                            }
                        />
                        <button
                            type="button"
                            className={canApplyWindow ? "chip chip--active" : "chip"}
                            disabled={!canApplyWindow}
                            onClick={() =>
                                setDateRange({
                                    fromDate: draftDateRange.fromDate,
                                    toDate: draftDateRange.toDate,
                                })
                            }
                        >
                            Застосувати інтервал
                        </button>
                        <button
                            type="button"
                            className="chip"
                            onClick={() => {
                                setDraftDateRange({ fromDate: "", toDate: "" });
                                setDateRange({ fromDate: "", toDate: "" });
                            }}
                        >
                            Останній доступний інтервал
                        </button>
                    </div>
                </div>

                <div className="sidebar__group sidebar__station">
                    <span>Фокус</span>
                    <strong>{selectedStation?.station_name || "Огляд мережі"}</strong>
                    <small>
                        {selectedStation
                            ? `${selectedStation.latitude.toFixed(4)}, ${selectedStation.longitude.toFixed(4)}`
                            : "Зведені метрики за всіма доступними станціями"}
                    </small>
                </div>
            </aside>

            <main className="dashboard-main">
                <div className="parameter-pills parameter-pills--top">
                    {visibleParameters.map((parameter) => (
                        <button
                            key={parameter.parameter_code}
                            type="button"
                            className={
                                parameter.parameter_code === selectedParameter ? "pill pill--active" : "pill"
                            }
                            onClick={() => setSelectedParameter(parameter.parameter_code)}
                        >
                            {parameter.parameter_code}
                        </button>
                    ))}
                </div>

                {error ? <div className="error-banner">{error}</div> : null}

                <section className="stats-grid">
                    <StatCard
                        icon={Gauge}
                        label="Індекс якості повітря"
                        value={summary ? formatMetric(summary.air_quality_index) : "Завантаження"}
                        hint={summary ? `${formatDelta(summary.air_quality_index_delta)} до попер. вікна` : " "}
                    />
                    <StatCard
                        icon={AlertTriangle}
                        label="Перевищення норм"
                        value={violationValue}
                        hint={violationHint}
                        accent="amber"
                    />
                    <StatCard
                        icon={Database}
                        label="Якість даних"
                        value={dataHealthValue}
                        hint={dataHealthHint}
                        accent="blue"
                    />
                    <StatCard
                        icon={Clock3}
                        label="Останнє оновлення"
                        value={summary ? formatDateTime(summary.last_data_update) : "Завантаження"}
                        hint={summary ? relativeAge(summary.last_data_update_age_hours) : " "}
                        accent="slate"
                    />
                </section>

                <section className="dashboard-layout">
                    <div className="featured-grid">
                        <ChartPanel
                            title="Карта станцій"
                            subtitle="Показані лише станції з даними в поточному інтервалі. Натисніть маркер, щоб сфокусувати панель."
                        >
                            <div className="map-panel">
                                <MapContainer center={DEFAULT_CENTER} zoom={11} scrollWheelZoom className="map-view">
                                    <TileLayer
                                        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
                                        url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
                                    />
                                    <MapViewport stations={mapStations} selectedStationId={selectedStationId} />
                                    {mapStations.map((station) => {
                                        const snapshot = station.snapshot;
                                        const value = snapshot?.value ?? null;
                                        const threshold = activeMomentaryThreshold;

                                        return (
                                            <CircleMarker
                                                key={station.station_id}
                                                center={[station.latitude, station.longitude]}
                                                radius={
                                                    station.station_id === selectedStationId
                                                        ? 15
                                                        : markerRadius(value, threshold)
                                                }
                                                pathOptions={{
                                                    color:
                                                        station.station_id === selectedStationId
                                                            ? "#f59e0b"
                                                            : severityColor(value, threshold),
                                                    weight: station.station_id === selectedStationId ? 4 : 2,
                                                    fillOpacity: 0.78,
                                                }}
                                                eventHandlers={{
                                                    click: () => setSelectedStationId(station.station_id),
                                                }}
                                            >
                                                <Tooltip direction="top" offset={[0, -6]} opacity={1}>
                                                    <div className="map-tooltip">
                                                        <strong>{station.station_name}</strong>
                                                        <span>
                                                            {value === null || value === undefined
                                                                ? `Немає вимірювання ${selectedParameter}`
                                                                : `${selectedParameter}: ${Number(value).toFixed(2)} ${snapshot?.unit_symbol || ""
                                                                }`}
                                                        </span>
                                                    </div>
                                                </Tooltip>
                                            </CircleMarker>
                                        );
                                    })}
                                </MapContainer>

                                <div className="map-legend">
                                    <div>
                                        <span className="legend-dot legend-dot--ok" />
                                        У межах норми
                                    </div>
                                    <div>
                                        <span className="legend-dot legend-dot--warn" />
                                        Вище норми
                                    </div>
                                    <div>
                                        <span className="legend-dot legend-dot--focus" />
                                        Обрана станція
                                    </div>
                                </div>
                            </div>
                        </ChartPanel>

                        <ChartPanel
                            title="Рейтинг станцій"
                            subtitle="Менше перевищень означає вищу позицію; враховуються лише станції, що вимірюють цей показник"
                        >
                            {ratingOption ? (
                                <ReactEChartsCore
                                    echarts={echarts}
                                    option={ratingOption}
                                    style={{ height: "100%", width: "100%" }}
                                />
                            ) : (
                                <div className="empty-state">
                                    <Gauge size={20} />
                                    <span>Порівняння станцій недоступне для цього параметра.</span>
                                </div>
                            )}
                        </ChartPanel>
                    </div>

                    <div className="content-grid">
                        <ChartPanel
                            title="Кореляція між параметрами"
                            subtitle={
                                selectedStation
                                    ? `Зв'язки для ${selectedStation.station_name}`
                                    : "Оберіть станцію, щоб обчислити кореляції"
                            }
                        >
                            {correlationOption ? (
                                <ReactEChartsCore
                                    echarts={echarts}
                                    option={correlationOption}
                                    style={{ height: "100%", width: "100%" }}
                                />
                            ) : (
                                <div className="empty-state">
                                    <MapPinned size={20} />
                                    <span>Оберіть станцію з достатньою кількістю даних для аналізу кореляцій.</span>
                                </div>
                            )}
                        </ChartPanel>

                        <ChartPanel
                            title={`${activeParameterCode} часові зміни`}
                            subtitle={timeSeriesChartSubtitle}
                            meta={timeSeriesChartMeta}
                        >
                            {timeOption ? (
                                <ReactEChartsCore
                                    echarts={echarts}
                                    option={timeOption}
                                    style={{ height: "100%", width: "100%" }}
                                />
                            ) : (
                                <div className="empty-state">
                                    <Activity size={20} />
                                    <span>Для поточного вибору немає часових даних.</span>
                                </div>
                            )}
                        </ChartPanel>
                    </div>

                    <div className="content-grid">
                        <ChartPanel
                            title="Динаміка перевищень"
                            subtitle="Добовий вигляд з окремими разовими та добовими перевищеннями"
                        >
                            {violationsOption ? (
                                <ReactEChartsCore
                                    echarts={echarts}
                                    option={violationsOption}
                                    style={{ height: "100%", width: "100%" }}
                                />
                            ) : (
                                <div className="empty-state">
                                    <AlertTriangle size={20} />
                                    <span>Для цього вигляду немає історії перевищень.</span>
                                </div>
                            )}
                        </ChartPanel>

                        <ChartPanel
                            title="Останні вимірювання"
                            subtitle="Найновіші вимірювання для активної станції та забруднювача"
                        >
                            <div className="table-wrap">
                                <table>
                                    <thead>
                                        <tr>
                                            <th>Час</th>
                                            <th>Значення</th>
                                            <th>Одиниця</th>
                                            <th>Якість</th>
                                            <th>Рівень</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {recentMeasurements?.items?.length ? (
                                            recentMeasurements.items.map((row) => (
                                                <tr key={row.measurement_id}>
                                                    <td>{formatDateTime(row.measurement_timestamp)}</td>
                                                    <td>{row.value === null ? "Немає даних" : Number(row.value).toFixed(2)}</td>
                                                    <td>{row.unit_symbol || "н/д"}</td>
                                                    <td>{row.quality_ratio === null ? "н/д" : `${(row.quality_ratio * 100).toFixed(0)}%`}</td>
                                                    <td>
                                                        <span className={`badge badge--level-${row.pollution_level || 0}`}>
                                                            {row.pollution_level || "н/д"}
                                                        </span>
                                                    </td>
                                                </tr>
                                            ))
                                        ) : (
                                            <tr>
                                                <td colSpan="5">
                                                    <div className="empty-state empty-state--table">
                                                        <LoaderCircle size={18} className={loading ? "spin" : ""} />
                                                        <span>Для поточних фільтрів немає вимірювань.</span>
                                                    </div>
                                                </td>
                                            </tr>
                                        )}
                                    </tbody>
                                </table>
                            </div>
                        </ChartPanel>
                    </div>
                </section>
            </main>
        </div>
    );
}
