from __future__ import annotations

import sys
import math

import matplotlib

matplotlib.use("Agg")  # no display needed — saves to PNG
import matplotlib.pyplot as plt
import sqlalchemy as sa
from sqlalchemy import text

from app.core.config import app_config


# DB connection
def _build_engine() -> sa.Engine:
    cfg = app_config
    url = sa.engine.URL.create(
        drivername=cfg.db_driver or "mysql+mysqldb",
        username=cfg.db_user,
        password=cfg.db_password,
        host=cfg.db_host,
        port=cfg.db_port,
        database=cfg.db_name,
    )
    return sa.create_engine(url, future=True)


# Data queries
def _fetch_imputed_parameter_keys(conn) -> list[int]:
    """Return parameter_keys that have at least one imputed row."""
    rows = conn.execute(
        text("""
        SELECT DISTINCT parameter_key
        FROM   fact_measurements
        WHERE  quality_ratio = 0.5
        ORDER  BY parameter_key
    """)
    ).fetchall()
    return [r.parameter_key for r in rows]


def _fetch_parameter_meta(conn, parameter_keys: list[int]) -> dict[int, dict]:
    """
    Return {parameter_key: {name, code, unit_symbol, physical_min, physical_max}}
    for the given keys.
    """
    if not parameter_keys:
        return {}

    keys_csv = ", ".join(str(k) for k in parameter_keys)
    rows = conn.execute(
        text(f"""
        SELECT
            p.parameter_key,
            COALESCE(p.parameter_name, p.parameter_code) AS display_name,
            p.parameter_code,
            p.physical_min,
            p.physical_max,
            u.unit_symbol
        FROM  dim_parameters p
        LEFT  JOIN dim_units u ON u.unit_key = p.unit_key
        WHERE p.parameter_key IN ({keys_csv})
          AND p.is_current = 1
    """)
    ).fetchall()

    return {
        r.parameter_key: {
            "name": r.display_name,
            "code": r.parameter_code,
            "unit": r.unit_symbol or "",
            "physical_min": r.physical_min,
            "physical_max": r.physical_max,
        }
        for r in rows
    }


def _fetch_values(
    conn,
    parameter_key: int,
    physical_min: float | None,
    physical_max: float | None,
) -> tuple[list[float], list[float]]:
    """
    Return (valid_values, imputed_values) for a single parameter.

    Values are clamped to [physical_min - 10%, physical_max + 10%] so that
    any remaining extreme outliers don't collapse the histogram bins.
    """
    lo = (
        physical_min * 0.9
        if physical_min is not None and physical_min >= 0
        else (physical_min * 1.1 if physical_min is not None else None)
    )
    hi = physical_max * 1.1 if physical_max is not None else None

    clamp_parts = ["value IS NOT NULL", f"parameter_key = {parameter_key}"]
    if lo is not None:
        clamp_parts.append(f"value >= {lo}")
    if hi is not None:
        clamp_parts.append(f"value <= {hi}")
    clamp_where = " AND ".join(clamp_parts)

    # Valid readings (quality_ratio != 0.5, i.e. original non-imputed)
    valid_rows = conn.execute(
        text(f"""
        SELECT value
        FROM   fact_measurements
        WHERE  {clamp_where}
          AND  (quality_ratio IS NULL OR quality_ratio != 0.5)
        LIMIT  200000
    """)
    ).fetchall()

    # Imputed readings
    imputed_rows = conn.execute(
        text(f"""
        SELECT value
        FROM   fact_measurements
        WHERE  {clamp_where}
          AND  quality_ratio = 0.5
    """)
    ).fetchall()

    valid_values = [float(r.value) for r in valid_rows]
    imputed_values = [float(r.value) for r in imputed_rows]
    return valid_values, imputed_values


# Plotting
BINS = 60


def _plot_parameter(
    ax: plt.Axes,
    meta: dict,
    valid_values: list[float],
    imputed_values: list[float],
) -> None:
    p_min = meta["physical_min"]
    p_max = meta["physical_max"]
    unit = meta["unit"]
    name = meta["name"]

    all_values = valid_values + imputed_values
    if not all_values:
        ax.set_title(f"{name}\n(no data)", fontsize=9)
        ax.axis("off")
        return

    lo = min(all_values)
    hi = max(all_values)
    if lo == hi:
        lo -= 1
        hi += 1
    bins = [lo + (hi - lo) * i / BINS for i in range(BINS + 1)]

    # Valid distribution
    if valid_values:
        ax.hist(
            valid_values,
            bins=bins,
            color="#4C72B0",
            alpha=0.6,
            label=f"Valid  (n={len(valid_values):,})",
            zorder=2,
        )

    # Imputed distribution
    if imputed_values:
        ax.hist(
            imputed_values,
            bins=bins,
            color="#DD8452",
            alpha=0.85,
            label=f"Imputed (n={len(imputed_values):,})",
            zorder=3,
        )

    # Physical bound lines
    bound_kw = dict(linewidth=1.6, linestyle="--", zorder=4)
    if p_min is not None:
        ax.axvline(p_min, color="#d62728", label=f"physical_min={p_min}", **bound_kw)
    if p_max is not None:
        ax.axvline(p_max, color="#9467bd", label=f"physical_max={p_max}", **bound_kw)

    unit_str = f" ({unit})" if unit else ""
    ax.set_title(f"{name}{unit_str}", fontsize=9, fontweight="bold")
    ax.set_xlabel("Value", fontsize=7)
    ax.set_ylabel("Count", fontsize=7)
    ax.tick_params(labelsize=7)
    ax.legend(fontsize=6, loc="upper right")
    ax.yaxis.set_major_formatter(
        matplotlib.ticker.FuncFormatter(lambda x, _: f"{int(x):,}")
    )


def _build_figure(
    meta_map: dict[int, dict],
    data_map: dict[int, tuple[list[float], list[float]]],
) -> plt.Figure:
    n = len(meta_map)
    cols = 3
    rows = math.ceil(n / cols)

    fig, axes = plt.subplots(
        rows,
        cols,
        figsize=(cols * 5.5, rows * 4),
        constrained_layout=True,
    )
    # Flatten axes even when there's only one row
    axes_flat = axes.flat if hasattr(axes, "flat") else [axes]

    for ax, (pk, meta) in zip(axes_flat, meta_map.items()):
        valid_vals, imputed_vals = data_map[pk]
        _plot_parameter(ax, meta, valid_vals, imputed_vals)

    # Hide unused subplot slots
    for ax in list(axes_flat)[n:]:
        ax.axis("off")

    fig.suptitle(
        "Post-migration data quality check\n"
        "Blue = original valid readings  |  Orange = imputed (quality_ratio=0.5)  |"
        "  Dashed = physical bounds",
        fontsize=10,
        y=1.01,
    )
    return fig


# Entry point
def main() -> None:
    print("[check] Connecting to database …")
    engine = _build_engine()

    with engine.connect() as conn:
        print("[check] Finding parameters with imputed rows …")
        imputed_keys = _fetch_imputed_parameter_keys(conn)

        if not imputed_keys:
            print("[check] No imputed rows found (quality_ratio=0.5 is absent).")
            print(
                "        Either the migration has not run yet, or nothing was corrected."
            )
            sys.exit(0)

        print(
            f"[check] {len(imputed_keys)} parameter(s) have imputed rows: {imputed_keys}"
        )

        print("[check] Fetching parameter metadata …")
        meta_map = _fetch_parameter_meta(conn, imputed_keys)

        # Warn about any key with no current dim_parameters row
        missing = set(imputed_keys) - set(meta_map)
        if missing:
            print(
                f"[check] WARNING: no current dim_parameters row for keys {missing} — skipped."
            )

        data_map: dict[int, tuple[list[float], list[float]]] = {}
        for pk, meta in meta_map.items():
            print(f"[check]   Loading values for '{meta['name']}' (key={pk}) …")
            valid_vals, imputed_vals = _fetch_values(
                conn, pk, meta["physical_min"], meta["physical_max"]
            )
            print(
                f"[check]     valid={len(valid_vals):,}  imputed={len(imputed_vals):,}"
            )
            data_map[pk] = (valid_vals, imputed_vals)

    print("[check] Rendering chart …")
    fig = _build_figure(meta_map, data_map)

    out_path = "imputed_data_check.png"
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    print(f"[check] Saved → {out_path}")
    plt.close(fig)


if __name__ == "__main__":
    main()
