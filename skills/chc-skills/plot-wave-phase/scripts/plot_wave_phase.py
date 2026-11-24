# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "weather-skills-core>=0.4.0",
#   "cftime>=1.6",
#   "matplotlib>=3.8",
#   "numpy",
#   "pandas",
#   "pillow",
#   "xarray",
#   "zarr",
# ]
# ///
"""Render local wave phase roses (weekly or daily) from olr-wave-filter output."""

from __future__ import annotations

from itertools import pairwise
from pathlib import Path

from weather_skills_core import Dataset, UsageError, weather_skill

# Auto-populated by the version-bump CI workflow. Do not edit manually.
_SKILL_VERSION = "0.0.4"

KINDS = ("rose", "rose-daily")
WAVE_NAMES = ("kelvin", "er", "mjo", "mrg_td", "low")
LABELS = {"low": "Low", "mjo": "MJO", "kelvin": "Kelvin", "er": "ER", "mrg_td": "MRG/TD"}
AMP_LAT = 5  # A and B are the 5S-5N filtered anomaly and its time derivative
ROSE_AMP_BINS = (1, 1.5, 2, 3, float("inf"))
ROSE_COLORS = ("#FFD54F", "#FB8C00", "#D84315", "#6D1B7B")


def _phase_of(x, y):
    """Phase 1..8 of (x, y) = (-A, -B): P5 (wet) centred on +x, P1 (dry) on -x, clockwise."""
    import numpy as np

    theta = np.degrees(np.arctan2(y, x))
    return (np.round(((180 - theta) % 360) / 45).astype(int) % 8) + 1


def _phase_label(p, sep="\n"):
    return f"P{p}" + {1: f"{sep}dry", 5: f"{sep}wet"}.get(p, "")


def _local(ds, wave, lon):
    """Members' (x, y) = (-A, -B) over the seasonal ERA5 std at the nearest longitude:
    (x, y, longitude used)."""
    import numpy as np

    lons = ds.longitude.values
    i = int(np.argmin(np.abs((lons - lon + 180) % 360 - 180)))
    field = ds[f"olr_{wave}"].isel(longitude=i)
    lats = field.latitude
    band = field.sel(latitude=lats[np.abs(lats) <= AMP_LAT + 1e-6])
    a = band.weighted(np.cos(np.deg2rad(band.latitude))).mean("latitude")
    a = a.transpose("number", "time").values.astype(np.float64)
    b = np.gradient(a, axis=1)
    sa = float(ds[f"olr_{wave}_local_std_a"].isel(longitude=i))
    sb = float(ds[f"olr_{wave}_local_std_b"].isel(longitude=i))
    return -a / sa, -b / sb, float(lons[i])


def _plot_roses(x, y, times, init, where, wave, n_members, days_per_rose, n_roses, ncols):
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.ticker import PercentFormatter

    fc_days = np.flatnonzero(times >= init)
    groups = [
        fc_days[days_per_rose * k : days_per_rose * (k + 1)]
        for k in range(min(n_roses, len(fc_days) // days_per_rose))
    ]
    if not groups:
        raise UsageError("the forecast is too short for one rose")
    centres = np.radians([(180 - 45 * (p - 1)) % 360 for p in range(1, 9)])
    daily = days_per_rose == 1
    ncols = min(ncols, len(groups))
    nrows = -(-len(groups) // ncols)
    scale = 0.75 if daily else 1
    amp, ph = np.hypot(x, y), _phase_of(x, y)

    shares = []
    for days in groups:
        a, p = amp[:, days].ravel(), ph[:, days].ravel()
        shares.append(
            np.array(
                [
                    [np.mean((a > lo) & (a <= hi) & (p == k)) * 100 for k in range(1, 9)]
                    for lo, hi in pairwise(ROSE_AMP_BINS)
                ]
            )
        )
    rmax = max(max(sh.sum(0).max() for sh in shares) * 1.1, 1.0)

    fig, axes = plt.subplots(
        nrows,
        ncols,
        figsize=(17 / 3 * ncols * scale, 6.25 * nrows * scale),
        subplot_kw={"projection": "polar"},
        layout="constrained",
        squeeze=False,
    )
    for ax in axes.flat[len(groups) :]:
        ax.remove()
    for k, (ax, days, sh) in enumerate(zip(axes.flat, groups, shares, strict=False)):
        bottom = np.zeros(8)
        for c, part in zip(ROSE_COLORS, sh, strict=True):
            ax.bar(
                centres,
                part,
                width=np.radians(40),
                bottom=bottom,
                color=c,
                edgecolor="k",
                lw=0.6,
                zorder=3,
            )
            bottom += part
        ax.text(
            0.5,
            0.5,
            f"weak\n{100 - sh.sum():.0f}%",
            ha="center",
            va="center",
            fontsize=13 * scale,
            zorder=5,
            transform=ax.transAxes,
            bbox={"boxstyle": "circle", "facecolor": "white", "edgecolor": "k"},
        )
        ax.set_ylim(0, rmax)
        ax.set_rorigin(-0.2 * rmax)
        ax.set_thetalim(0, 2 * np.pi)
        ax.set_xticks(centres)
        ax.set_xticklabels([_phase_label(p) for p in range(1, 9)], fontsize=14 * scale)
        ax.tick_params(axis="y", labelsize=11 * scale)
        ax.set_rlabel_position(112.5)
        ax.yaxis.set_major_formatter(PercentFormatter(decimals=0))
        title = (
            f"Day {k + 1}: {times[days[0]]:%d %b}"
            if daily
            else f"Week {k + 1}: {times[days[0]]:%d %b} - {times[days[-1]]:%d %b}"
        )
        ax.set_title(title, fontsize=16 * scale, pad=14 * scale)
    handles = [
        plt.Rectangle(
            (0, 0),
            1,
            1,
            facecolor=c,
            edgecolor="k",
            label=f"amplitude {lo:g}-{hi:g}" if np.isfinite(hi) else f"amplitude > {lo:g}",
        )
        for c, lo, hi in zip(ROSE_COLORS, ROSE_AMP_BINS[:-1], ROSE_AMP_BINS[1:], strict=True)
    ]
    fig.legend(handles=handles, loc="outside lower center", ncol=4, fontsize=14, frameon=False)
    per, sample = ("day", "") if daily else ("week", f" x {days_per_rose} days")
    return (
        fig,
        (
            f"{LABELS[wave]} phase at {where} per forecast {per}: share of all "
            f"{n_members} members{sample}, forecast from {init:%Y-%m-%d}"
        ),
        17,
    )


@weather_skill(name="plot-wave-phase", version=_SKILL_VERSION)
@weather_skill.argument(
    "-i",
    "--input",
    type=Dataset("any"),
    required=True,
    dest="ds",
    help="olr-wave-filter output Zarr.",
)
@weather_skill.argument(
    "--wave", choices=WAVE_NAMES, default="kelvin", help="Wave band (default kelvin)."
)
@weather_skill.argument(
    "--lon",
    type=float,
    default=37.5,
    help="Longitude of the point (degrees east, default 37.5 = Kenya).",
)
@weather_skill.argument(
    "--kind",
    choices=KINDS,
    default="rose",
    help="rose (weekly phase roses) or rose-daily (one rose per day). Default: rose.",
)
@weather_skill.argument(
    "--days", type=int, default=20, help="Number of daily roses for --kind rose-daily (default 20)."
)
@weather_skill.argument(
    "--label", default=None, help="Place name in the title (default: the longitude)."
)
@weather_skill.argument("--title", default=None, help="Figure title (default: automatic).")
def plot_wave_phase(ds, wave, lon, kind, days, label, title, output, **kwargs):
    """Render local wave phase roses (weekly or daily) from olr-wave-filter output."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd
    from weather_skills_core.units import dequantify_dataset

    ds = dequantify_dataset(ds)
    if "forecast_reference_time" not in ds.attrs:
        raise UsageError("input is not olr-wave-filter output (no forecast_reference_time attr)")
    needed = [f"olr_{wave}{s}" for s in ("", "_local_std_a", "_local_std_b")]
    if any(v not in ds.data_vars for v in needed):
        raise UsageError(f"the input has no olr_{wave}; rerun olr-wave-filter with --waves {wave}")
    if days < 1:
        raise UsageError("--days must be at least 1")
    init = pd.Timestamp(ds.attrs["forecast_reference_time"])
    times = ds.get_index("time")
    if not (times >= init).any() or not (times < init).any():
        raise UsageError("the input needs observed days before and forecast days after the init")

    x, y, used_lon = _local(ds, wave, lon)
    where = (
        f"{label} ({used_lon:g}E, {AMP_LAT}S-{AMP_LAT}N OLR)"
        if label
        else (f"{used_lon:g}E ({AMP_LAT}S-{AMP_LAT}N OLR)")
    )
    n_members = ds.sizes["number"]
    if kind == "rose":
        fig, auto, size = _plot_roses(x, y, times, init, where, wave, n_members, 7, 6, 3)
    else:
        fig, auto, size = _plot_roses(x, y, times, init, where, wave, n_members, 1, days, 5)
    fig.suptitle(title or auto, fontsize=size)

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=110)
    plt.close(fig)
    return output


if __name__ == "__main__":
    plot_wave_phase()
