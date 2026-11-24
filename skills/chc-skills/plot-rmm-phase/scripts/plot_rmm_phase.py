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
"""Render an RMM (Wheeler-Hendon) MJO phase-diagram PNG from mjo-rmm-index output."""

from __future__ import annotations

from pathlib import Path

from weather_skills_core import Dataset, UsageError, weather_skill

# Auto-populated by the version-bump CI workflow. Do not edit manually.
_SKILL_VERSION = "0.0.3"

MEMBER_COLORS = (
    "red",
    "hotpink",
    "darkorange",
    "tab:blue",
    "tab:green",
    "tab:cyan",
    "goldenrod",
    "tab:brown",
)
MEAN_COLOR = "purple"


def _phase_axes(ax, lim):
    """Square RMM diagram: unit circle, the eight phase sectors and the convection regions."""
    import matplotlib.pyplot as plt
    import numpy as np

    ax.set_xlim(-lim, lim)
    ax.set_ylim(-lim, lim)
    ax.set_aspect("equal")
    ax.add_patch(plt.Circle((0, 0), 1, fill=False, color="grey", lw=1))
    for a in (0, 45, 90, 135):
        r = np.radians(a)
        ax.plot(
            [-2 * lim * np.cos(r), 2 * lim * np.cos(r)],
            [-2 * lim * np.sin(r), 2 * lim * np.sin(r)],
            color="grey",
            lw=0.6,
            zorder=0,
        )
    for p in range(1, 9):
        ang = np.radians(-180 + 45 * (p - 0.5))
        edge = max(abs(np.cos(ang)), abs(np.sin(ang)))
        ax.text(
            0.85 * lim * np.cos(ang) / edge,
            0.85 * lim * np.sin(ang) / edge,
            str(p),
            ha="center",
            va="center",
            fontsize=13,
            color="grey",
        )
    kw = {"ha": "center", "va": "center", "fontsize": 10, "color": "dimgrey"}
    ax.text(0, -lim * 0.95, "Indian Ocean", **kw)
    ax.text(lim * 0.95, 0, "Maritime Continent", rotation=-90, **kw)
    ax.text(0, lim * 0.95, "Western Pacific", **kw)
    ax.text(-lim * 0.95, 0, "West. Hem. and Africa", rotation=90, **kw)


@weather_skill(name="plot-rmm-phase", version=_SKILL_VERSION)
@weather_skill.argument(
    "-i",
    "--input",
    type=Dataset("time"),
    required=True,
    dest="ds",
    help="mjo-rmm-index output Zarr.",
)
@weather_skill.argument(
    "--member-days",
    default="1,5,10,15,20",
    help="Forecast days (1 = init day) whose members are drawn as dots (default 1,5,10,15,20).",
)
@weather_skill.argument(
    "--obs-days",
    type=int,
    default=None,
    help="Observed days drawn before the init (default: all in the input).",
)
@weather_skill.argument("--no-bom", action="store_true", help="Leave out BoM's observed RMM track.")
@weather_skill.argument("--title", default=None, help="Figure title (default: automatic).")
def plot_rmm_phase(ds, member_days, obs_days, no_bom, title, output, **kwargs):
    """Render an RMM (Wheeler-Hendon) MJO phase-diagram PNG from mjo-rmm-index output."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    import pandas as pd
    from matplotlib import patheffects
    from weather_skills_core.units import dequantify_dataset

    ds = dequantify_dataset(ds)
    if not {"rmm1", "rmm2", "rmm1_mean", "rmm2_mean"} <= set(ds.data_vars):
        raise UsageError("input is not mjo-rmm-index output (no rmm1/rmm2 variables)")
    try:
        days = sorted({int(d) for d in member_days.split(",") if d.strip()})
    except ValueError as exc:
        raise UsageError(f"--member-days {member_days!r}: use comma-separated integers") from exc
    times = ds.get_index("time")
    is_fc = ds["is_forecast"].values.astype(bool)
    if not is_fc.any() or is_fc.all():
        raise UsageError("the input needs observed days before and forecast days after the init")
    init = times[is_fc][0]
    fc_times = times[is_fc]
    if any(d < 1 or d > len(fc_times) for d in days):
        raise UsageError(f"--member-days must be within 1..{len(fc_times)}")
    obs_idx = np.flatnonzero(~is_fc)
    if obs_days is not None:
        if obs_days < 1:
            raise UsageError("--obs-days must be at least 1")
        obs_idx = obs_idx[-obs_days:]

    mean = np.stack([ds["rmm1_mean"].values, ds["rmm2_mean"].values], axis=-1)
    members = np.stack(
        [
            ds["rmm1"].transpose("time", "number").values,
            ds["rmm2"].transpose("time", "number").values,
        ],
        axis=-1,
    )[is_fc]
    obs_xy = mean[obs_idx]
    fc_mean = np.vstack([obs_xy[-1:], mean[is_fc]])  # joined onto the last observed day
    bom_xy = np.empty((0, 2))
    if not no_bom and "rmm1_bom" in ds.data_vars:
        bom_xy = np.stack([ds["rmm1_bom"].values, ds["rmm2_bom"].values], axis=-1)[obs_idx]
        bom_xy = bom_xy[~np.isnan(bom_xy).any(axis=1)]
    shown = np.r_[members[[d - 1 for d in days]].ravel(), obs_xy.ravel(), fc_mean.ravel()]
    lim = max(3.0, float(np.ceil(np.nanmax(np.abs(shown)) * 2) / 2))

    fig, ax = plt.subplots(figsize=(9, 9), layout="constrained")
    _phase_axes(ax, lim)
    ax.set_xlabel("RMM1")
    ax.set_ylabel("RMM2")
    for k, day in enumerate(days):
        ax.scatter(
            *members[day - 1].T,
            s=24,
            color=MEMBER_COLORS[k % len(MEMBER_COLORS)],
            linewidths=0,
            zorder=2,
            label=f"members, day {day} ({fc_times[day - 1]:%d %b})",
        )
    ax.plot(fc_mean[:, 0], fc_mean[:, 1], color=MEAN_COLOR, lw=2.5, zorder=3, label="ensemble mean")
    for d in range(8, len(fc_mean), 7):  # a week, two weeks, ... after the start
        ax.plot(*fc_mean[d], "o", color=MEAN_COLOR, ms=6, zorder=3)
        ax.annotate(
            f"{fc_times[d - 1]:%d %b}",
            fc_mean[d],
            textcoords="offset points",
            xytext=(5, 5),
            fontsize=8,
            color="w",
            zorder=6,
            path_effects=[patheffects.withStroke(linewidth=2, foreground="k")],
        )
    ax.plot(obs_xy[:, 0], obs_xy[:, 1], color="k", lw=2, label=f"observed, last {len(obs_xy)} days")
    ax.plot(*obs_xy[0], "s", color="k", ms=6)
    ax.annotate(
        times[obs_idx[0]].strftime("%d %b"),
        obs_xy[0],
        textcoords="offset points",
        xytext=(5, -10),
        fontsize=8,
    )
    if len(bom_xy):
        ax.plot(
            bom_xy[:, 0], bom_xy[:, 1], color="grey", lw=1.5, ls="--", label="BoM RMM, same days"
        )
    ax.plot(*obs_xy[-1], "o", color="k", ms=7, zorder=5)
    ax.legend(loc="lower left", fontsize=9, framealpha=0.9)
    eof_years = ds.attrs.get("eof_years", "")
    ax.set_title(
        title
        or (
            f"RMM index (Wheeler-Hendon), forecast from {pd.Timestamp(init):%Y-%m-%d}\n"
            f"ensemble-mean dots every 7 days; EOFs of ERA5 OLR, u850 and u200, {eof_years}"
        ),
        fontsize=11,
    )

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=120)
    plt.close(fig)
    return output


if __name__ == "__main__":
    plot_rmm_phase()
