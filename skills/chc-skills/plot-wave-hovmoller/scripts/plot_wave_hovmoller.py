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
"""Render a time-longitude (Hovmoller) PNG of olr-wave-filter output."""

from __future__ import annotations

from pathlib import Path

from weather_skills_core import Dataset, UsageError, weather_skill

# Auto-populated by the version-bump CI workflow. Do not edit manually.
_SKILL_VERSION = "0.0.3"

STYLES = ("ncics", "amplitude", "contours", "probability")
WAVE_NAMES = ("low", "mjo", "kelvin", "er", "mrg_td")
LABELS = {"low": "Low", "mjo": "MJO", "kelvin": "Kelvin", "er": "ER", "mrg_td": "MRG/TD"}
DEFAULT_WAVES = {
    "ncics": ("kelvin", "er", "mjo", "low"),
    "amplitude": ("kelvin", "er", "mjo", "low"),
    "contours": ("mjo", "kelvin", "er", "mrg_td"),
    "probability": ("kelvin", "er", "mjo"),
}
# Carl Schreck's NCICS monitor colours; contour interval per wave for the contours style.
NCICS_COLORS = {
    "kelvin": "blue",
    "er": "red",
    "mjo": "black",
    "low": "darkviolet",
    "mrg_td": "darkorange",
}
CONTOURS = {
    "mjo": ("k", 5),
    "kelvin": ("tab:green", 10),
    "er": ("tab:purple", 5),
    "mrg_td": ("tab:orange", 10),
    "low": ("darkviolet", 5),
}
NCICS_LEVEL = 16  # W m-2
PROB_LEVELS = (0.50, 0.66, 0.80)
PROB_WIDTHS = (0.8, 1.7, 2.8)
OBS_AMP_THRESHOLD = 2.0  # observed activity line (about the NCICS +-16 W m-2 contour)


def _band_mean(da, lat):
    """cos(lat)-weighted mean over |lat| <= lat."""
    import numpy as np

    band = da.sel(latitude=da.latitude[np.abs(da.latitude) <= lat + 1e-6])
    if band.sizes["latitude"] == 0:
        raise UsageError(f"no latitudes within {lat:g}S-{lat:g}N in the input")
    return band.weighted(np.cos(np.deg2rad(band.latitude))).mean("latitude")


def _lon_0_360(x):
    """Longitudes -180..180 -> 0..360 so the Hovmoller runs 0E on the left through 180."""
    return x.assign_coords(longitude=x.longitude % 360).sortby("longitude")


def _member(da, member):
    if member == "mean":
        return da.mean("number")
    number = 0 if member == "control" else int(member)
    if number not in da.number.values:
        raise UsageError(f"--member {member}: no ensemble member {number} in the input")
    return da.sel(number=number)


def _member_label(member):
    return {"mean": "ensemble mean", "control": "control (member 0)"}.get(
        member, f"member {member}"
    )


def _hovmoller(ax, hov, highlight, cmap="RdBu_r", norm=None, vmax=50, label_size=8):
    """Shaded time-longitude plot (time running downward) with an optional boxed lon range."""
    from matplotlib.transforms import blended_transform_factory

    limits = {} if norm is not None else {"vmin": -vmax, "vmax": vmax}
    pc = ax.pcolormesh(hov.longitude, hov.time, hov, cmap=cmap, norm=norm, shading="auto", **limits)
    ax.set_ylim(hov.time.values[-1], hov.time.values[0])
    if highlight is not None:
        (west, east), label = highlight
        ax.axvspan(west % 360, east % 360, facecolor="none", edgecolor="k", lw=1.5, zorder=5)
        if label:
            ax.text(
                (west % 360 + east % 360) / 2,
                1.005,
                label,
                ha="center",
                va="bottom",
                fontsize=label_size,
                transform=blended_transform_factory(ax.transData, ax.transAxes),
            )
    ax.set_xlim(0, 360)
    ax.set_xticks(range(0, 361, 60))
    ax.set_xticklabels(["0", "60E", "120E", "180", "120W", "60W", "0"])
    ax.set_xlabel("longitude")
    return pc


def _ncics_colormap():
    """Brown (dry) / green (wet) steps of 16 W m-2 with -8..8 white: (cmap, norm, ticks)."""
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.colors import BoundaryNorm, ListedColormap

    bounds = np.array([-88, -72, -56, -40, -24, -8, 8, 24, 40, 56, 72, 88])
    colors = plt.get_cmap("BrBG_r")(np.linspace(0, 1, len(bounds) - 1))
    colors[len(colors) // 2] = (1, 1, 1, 1)
    cmap = ListedColormap(colors[1:-1])
    cmap.set_under(colors[0])
    cmap.set_over(colors[-1])
    return cmap, BoundaryNorm(bounds[1:-1], cmap.N), bounds[1:-1]


def _begin_forecast(ax, init, fontsize=10):
    ax.axhline(init, color="k", lw=1.5)
    ax.text(
        150,
        init,
        "Begin forecast",
        ha="center",
        va="bottom",
        fontsize=fontsize,
        bbox={"facecolor": "white", "edgecolor": "none", "pad": 1},
    )


def _std(ds, wave):
    attrs = ds[f"olr_{wave}"].attrs
    try:
        return float(attrs["amplitude_std_a"]), float(attrs["amplitude_std_b"])
    except KeyError as exc:
        raise UsageError(
            f"olr_{wave} has no amplitude_std_a/b attrs; rerun olr-wave-filter"
        ) from exc


def _amplitude(field, std):
    """Janiga et al. (2018) amplitude sqrt(A^2 + B^2), signed by A (negative = enhanced)."""
    import numpy as np

    b = field.differentiate("time", datetime_unit="D")
    return np.sign(field) * np.hypot(field / std[0], b / std[1])


def _plot_ncics(ds, init, window, members, waves, lat, highlight, threshold, amplitude):
    import matplotlib.pyplot as plt
    import numpy as np

    cmap, norm, ticks = _ncics_colormap()
    shade_all = _band_mean(ds["olr_anomaly"].sel(time=window), lat)
    fields = {w: _band_mean(ds[f"olr_{w}"].sel(time=window), lat) for w in waves}
    fig, axes = plt.subplots(
        1,
        len(members),
        figsize=(7.5 * len(members), 9.5),
        layout="constrained",
        sharey=True,
        squeeze=False,
    )
    for ax, member in zip(axes.flat, members, strict=True):
        pc = _hovmoller(ax, _lon_0_360(_member(shade_all, member)), highlight, cmap=cmap, norm=norm)
        for w in waves:
            field = _member(fields[w], member)
            cut = NCICS_LEVEL
            if amplitude:
                field, cut = _amplitude(field, _std(ds, w)), threshold
            field = _lon_0_360(field).load()
            for level, ls in ((-cut, "solid"), (cut, "dashed")):
                if float((np.sign(level) * field).max()) > cut:
                    ax.contour(
                        field.longitude,
                        field.time,
                        field,
                        levels=[level],
                        colors=NCICS_COLORS[w],
                        linewidths=1.6,
                        linestyles=ls,
                    )
            ax.plot([], [], color=NCICS_COLORS[w], lw=1.6, label=LABELS[w])
        _begin_forecast(ax, init)
        legend_title = (
            f"contours at wave amplitude {threshold:g}"
            if amplitude
            else f"contours at {NCICS_LEVEL} W m$^{{-2}}$"
        )
        ax.legend(
            loc="lower right", fontsize=9, framealpha=0.9, title_fontsize=9, title=legend_title
        )
        ax.set_title(f"OLR with forecast ({_member_label(member)}), {lat:g}S-{lat:g}N", fontsize=12)
    fig.colorbar(
        pc,
        ax=axes,
        orientation="horizontal",
        shrink=0.6,
        extend="both",
        ticks=ticks,
        label="OLR anomaly (W m$^{-2}$)",
    )
    return fig, (
        f"Filtered OLR anomalies, {lat:g}S-{lat:g}N: ERA5, then the forecast from {init:%Y-%m-%d}; "
        "solid = enhanced convection, dashed = suppressed"
    )


def _plot_contours(ds, init, window, members, waves, lat, highlight):
    import matplotlib.pyplot as plt
    import numpy as np

    pairs = [waves[i : i + 2] for i in range(0, len(waves), 2)]
    shade_all = _band_mean(ds["olr_anomaly"].sel(time=window), lat)
    fields = {w: _band_mean(ds[f"olr_{w}"].sel(time=window), lat) for w in waves}
    fig, axes = plt.subplots(
        len(members),
        len(pairs),
        figsize=(8 * len(pairs), 7 * len(members)),
        layout="constrained",
        sharey=True,
        squeeze=False,
    )
    for r, member in enumerate(members):
        shade = _lon_0_360(_member(shade_all, member)).load()
        for c, pair in enumerate(pairs):
            ax = axes[r, c]
            pc = _hovmoller(ax, shade, highlight, vmax=40)
            pc.set_alpha(0.6)
            for w in pair:
                color, step = CONTOURS[w]
                field = _lon_0_360(_member(fields[w], member)).load()
                vmax = max(step, float(np.abs(field).max()))
                levels = np.arange(step, vmax + step, step)
                ax.contour(
                    field.longitude,
                    field.time,
                    field,
                    levels=-levels[::-1],
                    colors=color,
                    linewidths=1.3,
                    linestyles="solid",
                )
                ax.contour(
                    field.longitude,
                    field.time,
                    field,
                    levels=levels,
                    colors=color,
                    linewidths=0.8,
                    linestyles="dashed",
                )
                ax.plot([], [], color=color, label=f"{LABELS[w]} (every {step} W m$^{{-2}}$)")
            ax.axhline(init, color="k", lw=1.5)
            ax.legend(loc="lower right", fontsize=9, framealpha=0.9)
            ax.set_title(
                f"{' + '.join(LABELS[w] for w in pair)}: {_member_label(member)}", fontsize=11
            )
    fig.colorbar(
        pc,
        ax=axes,
        shrink=0.6,
        label="unfiltered OLR anomaly (W m$^{-2}$), negative = enhanced convection",
    )
    return fig, (
        f"{lat:g}S-{lat:g}N filtered OLR anomalies, ERA5 then the forecast from {init:%Y-%m-%d} "
        "(black line); solid = enhanced convection, dashed = suppressed"
    )


def _plot_probability(ds, init, window, waves, lat, highlight, threshold):
    import matplotlib.pyplot as plt
    import numpy as np

    cmap, norm, ticks = _ncics_colormap()
    shade = _lon_0_360(_band_mean(ds["olr_anomaly"].sel(time=window), lat).mean("number")).load()
    fig, axes = plt.subplots(
        1,
        len(waves),
        figsize=(7 * len(waves), 12),
        layout="constrained",
        sharey=True,
        squeeze=False,
    )
    for ax, w in zip(axes.flat, waves, strict=True):
        color = NCICS_COLORS[w]
        std_a, std_b = _std(ds, w)
        x = _band_mean(ds[f"olr_{w}"].sel(time=window), lat).load()  # (number, time, lon)
        a = x / std_a
        b = x.differentiate("time", datetime_unit="D") / std_b
        amp = np.hypot(a, b)
        is_fc = x.time >= np.datetime64(init)
        pc = _hovmoller(ax, shade, highlight, cmap=cmap, norm=norm, label_size=13)
        for ls, sign in (("solid", -1), ("dashed", 1)):
            half = np.sign(a) == sign
            p = _lon_0_360(((amp > threshold) & half).mean("number").where(is_fc))
            # observed days are the same in every member: one line where the wave was active
            p_obs = _lon_0_360(
                ((amp > OBS_AMP_THRESHOLD) & half).isel(number=0).astype(float).where(~is_fc)
            )
            if float(p_obs.max()) >= 0.5:
                ax.contour(
                    p.longitude,
                    p.time,
                    p_obs,
                    levels=[0.5],
                    colors=color,
                    linewidths=1.4,
                    linestyles=ls,
                )
            if float(p.max()) >= PROB_LEVELS[0]:
                ax.contour(
                    p.longitude,
                    p.time,
                    p,
                    levels=list(PROB_LEVELS),
                    colors=color,
                    linewidths=list(PROB_WIDTHS),
                    linestyles=ls,
                )
        _begin_forecast(ax, init, fontsize=14)
        handles = [
            plt.Line2D([], [], color=color, lw=1.7, ls="solid", label="active, enhanced half"),
            plt.Line2D([], [], color=color, lw=1.7, ls="dashed", label="active, suppressed half"),
            plt.Line2D(
                [], [], color="k", lw=1.4, label=f"observed: amplitude > {OBS_AMP_THRESHOLD:g}"
            ),
        ]
        handles += [
            plt.Line2D(
                [], [], color="k", lw=lw, label=f"forecast: {lvl:.0%} of members > {threshold:g}"
            )
            for lvl, lw in zip(PROB_LEVELS, PROB_WIDTHS, strict=True)
        ]
        ax.legend(handles=handles, loc="lower right", fontsize=12, framealpha=0.9)
        ax.set_title(f"{LABELS[w]}: wave activity", fontsize=18)
        ax.tick_params(labelsize=14)
        ax.xaxis.label.set_size(15)
    cb = fig.colorbar(pc, ax=axes, orientation="horizontal", shrink=0.6, extend="both", ticks=ticks)
    cb.set_label("ensemble-mean OLR anomaly (W m$^{-2}$)", fontsize=15)
    cb.ax.tick_params(labelsize=14)
    return fig, (
        f"Ensemble agreement on wave activity from {init:%Y-%m-%d}, {lat:g}S-{lat:g}N "
        f"(ERA5 before the line), {ds.sizes['number']} members"
    )


@weather_skill(name="plot-wave-hovmoller", version=_SKILL_VERSION)
@weather_skill.argument(
    "-i",
    "--input",
    type=Dataset("any"),
    required=True,
    dest="ds",
    help="olr-wave-filter output Zarr.",
)
@weather_skill.argument(
    "--style",
    choices=STYLES,
    default="ncics",
    help="ncics (NCICS-monitor look, contours at 16 W m-2), amplitude (contours of the wave "
    "amplitude), contours (filtered OLR every few W m-2), probability (share of members with "
    "an active wave). Default: ncics.",
)
@weather_skill.argument(
    "--member",
    action="append",
    default=None,
    help="Panel per member: mean, control, or a member number; repeat for several "
    "(default: mean and control). Ignored by --style probability.",
)
@weather_skill.argument(
    "--waves",
    action="append",
    choices=WAVE_NAMES,
    default=None,
    help="Wave to draw; repeat for several (default depends on --style).",
)
@weather_skill.argument(
    "--lat-band",
    type=float,
    default=None,
    help="Average over |lat| <= this (default 5; 10 for --style contours).",
)
@weather_skill.argument(
    "--weeks-before",
    type=int,
    default=12,
    help="Observed weeks shown before the init (default 12).",
)
@weather_skill.argument(
    "--weeks-after",
    type=int,
    default=None,
    help="Forecast weeks shown (default: the whole forecast).",
)
@weather_skill.argument(
    "--threshold",
    type=float,
    default=None,
    help="Wave amplitude threshold (default 2 for --style amplitude, 1 for probability).",
)
@weather_skill.argument(
    "--highlight-lon",
    default="33/42",
    help="W/E longitudes boxed on every panel (default 33/42, Kenya), or none.",
)
@weather_skill.argument(
    "--highlight-label", default="Kenya", help="Label over the boxed longitudes (default Kenya)."
)
@weather_skill.argument("--title", default=None, help="Figure title (default: automatic).")
def plot_wave_hovmoller(
    ds,
    style,
    member,
    waves,
    lat_band,
    weeks_before,
    weeks_after,
    threshold,
    highlight_lon,
    highlight_label,
    title,
    output,
    **kwargs,
):
    """Render a time-longitude (Hovmoller) PNG of olr-wave-filter output."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd
    from weather_skills_core.units import dequantify_dataset

    ds = dequantify_dataset(ds)
    if "olr_anomaly" not in ds.data_vars or "forecast_reference_time" not in ds.attrs:
        raise UsageError("input is not olr-wave-filter output (no olr_anomaly variable)")
    init = pd.Timestamp(ds.attrs["forecast_reference_time"])
    requested = waves is not None
    waves = [w for w in dict.fromkeys(waves or DEFAULT_WAVES[style])]
    missing = [w for w in waves if f"olr_{w}" not in ds.data_vars]
    if missing and (requested or len(missing) == len(waves)):
        raise UsageError(
            f"the input has no olr_{missing[0]}; rerun olr-wave-filter with --waves {missing[0]}"
        )
    waves = [w for w in waves if w not in missing]
    members = member or ["mean", "control"]
    for m in members:
        if m not in ("mean", "control") and not m.lstrip("-").isdigit():
            raise UsageError(f"--member {m!r}: use mean, control or a member number")
    lat = lat_band if lat_band is not None else (10.0 if style == "contours" else 5.0)
    highlight = None
    if highlight_lon.lower() != "none":
        try:
            west, east = (float(v) for v in highlight_lon.split("/"))
        except ValueError as exc:
            raise UsageError(f"--highlight-lon {highlight_lon!r}: use W/E, e.g. 33/42") from exc
        highlight = ((west, east), highlight_label)
    start = init - pd.Timedelta(weeks=weeks_before)
    end = None if weeks_after is None else init + pd.Timedelta(weeks=weeks_after)
    window = slice(start, end)

    if style in ("ncics", "amplitude"):
        cut = threshold if threshold is not None else OBS_AMP_THRESHOLD
        fig, auto = _plot_ncics(
            ds, init, window, members, waves, lat, highlight, cut, style == "amplitude"
        )
    elif style == "contours":
        fig, auto = _plot_contours(ds, init, window, members, waves, lat, highlight)
    else:
        cut = threshold if threshold is not None else 1.0
        fig, auto = _plot_probability(ds, init, window, waves, lat, highlight, cut)
    fig.suptitle(title or auto, fontsize=19 if style == "probability" else 12)

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=110)
    plt.close(fig)
    return output


if __name__ == "__main__":
    plot_wave_hovmoller()
