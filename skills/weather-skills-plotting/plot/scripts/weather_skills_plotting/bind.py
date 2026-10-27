"""Fill Plotly traces from weather-skills datasets.

A spec trace names its data in ``meta`` (``source``, ``bind``, ``facet``, …).
Each binder turns one such trace into concrete Plotly traces, grouped into
panels (one per facet value, or a single panel), and reports what the figure
needs to know: whether it is a map, its lon/lat extent, the values that drive
its color scale, and default axis / panel titles. Binders never style beyond a
sensible default; the user's own trace keys are merged on afterwards.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from weather_skills_core.cf import auto_variable, cf_dim
from weather_skills_core.errors import UsageError
from weather_skills_core.standard_utils import ensure_normalized_longitude, polygon_from_geojson
from weather_skills_core.units import (
    STANDARD,
    classify_variable,
    units_equal,
    ureg,
    variable_units,
)

from weather_skills_plotting.geodata import (
    extent_from_da,
    plain,
    point_dim,
    subset_points,
    subset_spatial,
)
from weather_skills_plotting.kinds import aggregation_days, is_precip
from weather_skills_plotting.labels import (
    axis_label,
    format_plot_date,
    format_step,
    format_units_for_display,
    is_datetime_axis,
    panel_title,
    period_axis_title,
    timeseries_axis,
    variable_label_for_display,
)
from weather_skills_plotting.palettes import (
    along_dim,
    along_member_label,
    continuous_colorscale,
    parse_palette,
)
from weather_skills_plotting.spec import DEFAULT_BIND, fill_defaults
from weather_skills_plotting.wind import (
    WIND_ROSE_SECTORS,
    flat_numeric,
    is_sample_dim,
    native_spacing_deg,
    quiver_step,
    resolve_uv,
    speed_bin_labels,
    speed_edges,
    speed_units_display,
    subsample_quiver,
    uv_to_speed_fromdir,
    wind_rose_hist,
    wind_speed_cbar_label,
    wind_speed_da,
)

FACET_DIMS = ("step", "time", "valid_time")
# A typical (95th percentile) arrow is this many arrow spacings long: under one, so
# neighbours do not overlap.
ARROW_LEN_SPACING = 0.9
ARROWS_ACROSS = 25  # default thinning: about this many arrows across a panel
ARROW_KEY_SPEED = 10.0


@dataclass
class Panel:
    """Concrete traces for one panel; ``title`` is the default panel title."""

    traces: list
    title: str | None = None
    key: object = None


@dataclass
class Bound:
    """What one spec trace became."""

    panels: list
    is_map: bool = False
    static: bool = False
    extent: list | None = None
    # Color-scaled traces: the raw values, where they live in each concrete
    # trace ("z" or "marker.color"), a sample DataArray for palette detection,
    # and the colorbar title.
    color_values: list = field(default_factory=list)
    color_key: str | None = None
    color_da: object = None
    color_title: str | None = None
    axis_titles: dict = field(default_factory=dict)
    title: str | None = None
    layout: dict = field(default_factory=dict)
    x_tickformat: str | None = None


@dataclass
class Context:
    datasets: dict
    palettes: dict
    theme: dict
    bbox: tuple | None = None
    polygon: object = None
    point: dict | None = None


def bind_of(trace: dict) -> str | None:
    """The trace's bind mode, or ``None`` for a plain Plotly trace."""
    meta = trace.get("meta") or {}
    if meta.get("bind"):
        return meta["bind"]
    if meta.get("x") or meta.get("y"):
        return "xy"
    if not meta.get("source"):
        return None
    return DEFAULT_BIND.get(trace.get("type") or "scatter", "series")


# ------------------------------------------------------------------- selection


def _dataset(ctx: Context, source: dict, loc: str):
    input_id = source.get("input")
    if input_id is None:
        if len(ctx.datasets) != 1:
            raise UsageError(
                f"{loc}.input is required when there are several inputs "
                f"(have {', '.join(ctx.datasets) or 'none'})"
            )
        input_id = next(iter(ctx.datasets))
    ds = ctx.datasets.get(str(input_id))
    if ds is None:
        raise UsageError(
            f"{loc}.input {input_id!r} is not an input (have {', '.join(ctx.datasets) or 'none'}); "
            "pass the file with -i or list it in layout.meta.inputs"
        )
    return ds


def _display(ds, variable):
    da = ds[variable]
    units = variable_units(da)
    try:
        parsed = str(ureg.Unit(units))
    except Exception:  # noqa: BLE001 — no or unparseable units
        parsed = ""
    mm_rate = parsed.startswith("millimeter / ")
    kind = classify_variable(variable, units=units, standard_name=da.attrs.get("standard_name"))
    # Values are drawn in the file's own units (K stays K, Pa stays Pa): zmin, contour
    # levels and palette edges in a spec mean those units. unit-convert changes them.
    if mm_rate and kind == "precip":
        # Tag a mm rate as precipitation so it gets the rainfall palette.
        ds = ds.copy(deep=False)
        ds[variable] = da.copy(deep=False)
        ds[variable].attrs["standard_name"] = STANDARD["precip"]["standard_name"]
    elif kind in ("precip", "precip_amount") and parsed and not parsed.startswith("millimeter"):
        print(
            f"Note: {variable!r} is drawn in its own units ({units}); the rainfall colors are "
            "classed in mm, so run unit-convert first for mm or mm day-1",
            file=sys.stderr,
        )
    period = da.attrs.get("aggregation_period")
    days = aggregation_days(da)
    if kind == "precip" and period and (days is None or days >= 1):
        # Plotting draws what the file holds; the total is convert-to-totals' job.
        print(
            f"Note: {variable!r} is a rate with aggregation_period {period!r}; "
            "run convert-to-totals first to draw period totals (mm)",
            file=sys.stderr,
        )
    return ds


def pick_variable(ds, name, loc):
    """``name``, or the file's only data variable; several and none named is an error."""
    variable = name
    fields = [v for v in ds.data_vars if "grid_mapping_name" not in ds[v].attrs]
    if not variable and len(fields) > 1:
        # Guessing among several fields draws the wrong one without a word.
        raise UsageError(
            f"{loc}.variable is not set and the data holds {', '.join(map(str, fields))}; "
            f"set {loc}.variable to the one to draw"
        )
    variable = variable or auto_variable(ds)
    if not variable or variable not in ds:
        raise UsageError(
            f"{loc}.variable {variable!r} is not in the data; available: {', '.join(ds.data_vars)}"
        )
    return variable


def _sel(da, source: dict, loc: str):
    """Apply ``isel`` (positions) then ``sel`` (labels, nearest for numbers/dates)."""
    for dim, idx in (source.get("isel") or {}).items():
        if dim not in da.dims:
            raise UsageError(
                f"{loc}.isel dimension {dim!r} is not in the data (dims: {list(da.dims)})"
            )
        size = da.sizes[dim]
        if isinstance(idx, dict):
            # A range: {"start": 0, "stop": 12, "step": 3} (stop excluded, like Python).
            unknown = set(idx) - {"start", "stop", "step"}
            if unknown:
                raise UsageError(
                    f"{loc}.isel {dim} range takes start, stop, step; got {sorted(unknown)}"
                )
            idx = list(range(size))[slice(idx.get("start"), idx.get("stop"), idx.get("step"))]
            if not idx:
                raise UsageError(f"{loc}.isel {dim} range selects nothing (size {size})")
        for pos in idx if isinstance(idx, list) else [idx]:
            if isinstance(pos, bool) or not isinstance(pos, int) or not -size <= pos < size:
                raise UsageError(f"{loc}.isel {dim}={pos!r} is out of range (size {size})")
        da = da.isel({dim: idx})
    for dim, value in (source.get("sel") or {}).items():
        if dim not in da.dims:
            raise UsageError(
                f"{loc}.sel dimension {dim!r} is not in the data (dims: {list(da.dims)})"
            )
        coord = da[dim].values
        kind = getattr(coord.dtype, "kind", "O")
        try:
            if kind == "M":
                target = (
                    np.datetime64(value)
                    if not isinstance(value, list)
                    else [np.datetime64(v) for v in value]
                )
                da = da.sel({dim: target}, method="nearest")
            elif kind == "m":
                target = (
                    np.timedelta64(int(value), "D") if isinstance(value, (int, float)) else value
                )
                da = da.sel({dim: target})
            elif kind in "iuf":
                da = da.sel({dim: value}, method="nearest")
            elif coord.size and hasattr(coord.flat[0], "calendar"):
                # Model calendars (360_day, noleap): nearest date, like datetime64 axes.
                import cftime

                targets = []
                for v in value if isinstance(value, list) else [value]:
                    # "2099-02-30" or "2099-02-30T12": parsed by hand, since it may not
                    # exist in the standard calendar.
                    y, m, d = (int(x) for x in str(v)[:10].split("-"))
                    hour = int(str(v)[11:13]) if len(str(v)) >= 13 else 0
                    targets.append(cftime.datetime(y, m, d, hour, calendar=coord.flat[0].calendar))
                target = targets if isinstance(value, list) else targets[0]
                da = da.sel({dim: target}, method="nearest")
            else:
                da = da.sel({dim: value})
        except (KeyError, ValueError, TypeError) as exc:
            raise UsageError(
                f"{loc}.sel {dim}={value!r} does not match the coordinate ({exc})"
            ) from None
    empty = [d for d in da.dims if da.sizes[d] == 0]
    if empty:
        raise UsageError(f"{loc}.isel / sel left no values on {', '.join(empty)}")
    # A one-value dim (a single pressure level or member) has nothing to choose
    # or average; time and step stay so a single map keeps its date, and a
    # one-station file keeps its station axis.
    keep = {
        "time",
        "step",
        cf_dim(da, "time"),
        cf_dim(da, "latitude"),
        cf_dim(da, "longitude"),
        point_dim(da),
    }
    single = [d for d in da.dims if da.sizes[d] == 1 and d not in keep]
    return da.squeeze(single) if single else da


def _polygon(ctx: Context, source: dict):
    path = source.get("mask_geojson")
    return polygon_from_geojson(path) if path else ctx.polygon


def select(ctx: Context, source: dict, loc: str, *, variable=None):
    """The source's variable after units, ``isel``/``sel`` and the geo subset."""
    ds = _dataset(ctx, source, loc)
    name = pick_variable(ds, variable or source.get("variable"), loc)
    ds = _display(ds, name)
    da = _sel(plain(ds[name]), source, loc)
    return _subset(ctx, source, da), ds


def _subset(ctx: Context, source: dict, da):
    polygon = _polygon(ctx, source)
    if ctx.bbox is None and polygon is None:
        return da
    lat, lon = cf_dim(da, "latitude"), cf_dim(da, "longitude")
    if lat in da.dims and lon in da.dims:
        bbox = ctx.bbox
        spacing = native_spacing_deg(da[lat].values, da[lon].values) if bbox else None
        if spacing:
            # Keep every cell the window overlaps, not only the cells centred in it:
            # a zoom smaller than a 1.5° cell still shows that cell.
            north, west, south, east = bbox
            half = spacing / 2
            bbox = (north + half, west - half, south - half, east + half)
        da, _ = subset_spatial(da, lat, lon, bbox, polygon, None)
        if da.sizes[lat] == 0 or da.sizes[lon] == 0:
            raise UsageError(
                "no grid cells remain after layout.meta.geo.bbox / mask_geojson; nothing to plot"
            )
    elif lat and lon:
        da = subset_points(da, ctx.bbox, polygon)
    return da


def _facet_dim(meta: dict, da, keep: tuple, loc: str):
    """The dim that becomes one panel per value, or ``None``."""
    facet = meta.get("facet")
    if facet is False:
        return None
    if isinstance(facet, str):
        if facet not in da.dims:
            raise UsageError(f"{loc}.facet {facet!r} is not a dimension (dims: {list(da.dims)})")
        return facet
    for name in FACET_DIMS:
        if name in da.dims and name not in keep:
            return name
    cf = cf_dim(da, "time")
    return cf if cf in da.dims and cf not in keep else None


def _leftover_error(da, allowed, what, loc):
    extra = [d for d in da.dims if d not in allowed]
    if extra:
        names = ", ".join(repr(d) for d in extra)
        raise UsageError(
            f"dimension{'s' if len(extra) > 1 else ''} {names} remain{'' if len(extra) > 1 else 's'} "
            f"for {what}; select a position with "
            f"{loc}.source.isel / sel, panel it with {loc}.facet, or average it first with "
            f"summarize-dim. Side-by-side maps are separate inputs on separate axes, "
            f"not one dataset concatenated along {extra[0]!r}"
        )


def _lonlat(da, what):
    lat, lon = cf_dim(da, "latitude"), cf_dim(da, "longitude")
    if lat is None or lon is None or lat not in da.dims or lon not in da.dims:
        raise UsageError(
            f"{what} needs latitude/longitude dimensions; got {list(da.dims)}. "
            'Station data draws as a scatter trace with meta.bind "points"'
        )
    return lat, lon


def _wrapped(ctx):
    return ctx.bbox is not None and ctx.bbox[1] > ctx.bbox[3]


def _map_field(ctx, da, meta, loc, what):
    """Grid field ready to panel: subset, transposed."""
    lat, lon = _lonlat(da, what)
    if not _wrapped(ctx):
        da = ensure_normalized_longitude(da, lon)
    fdim = _facet_dim(meta, da, (lat, lon), loc)
    _leftover_error(da, {lat, lon, fdim}, what, loc)
    order = ([fdim] if fdim else []) + [lat, lon]
    return da.transpose(*order), lat, lon, fdim


def _slices(da, fdim):
    """``[(key, title, slice)]`` along the facet dim (one ``None`` slice without)."""
    if fdim is None:
        # A time or step picked with isel/sel stays as a scalar coord: still date the map.
        for dim in ("step", "time"):
            if dim in da.coords and da[dim].ndim == 0 and dim not in da.dims:
                value = da[dim].values
                if (
                    dim == "time"
                    and da["time"].attrs.get("standard_name") == "forecast_reference_time"
                ):
                    # An init time whose steps were averaged away: not a valid date.
                    return [(None, f"init {format_plot_date(value)}", da)]
                one = da.expand_dims(dim)
                return [(None, panel_title(one, dim, value, [value]), da)]
        return [(None, None, da)]
    values = list(da[fdim].values)
    out = [(v, panel_title(da, fdim, v, values), da.isel({fdim: i})) for i, v in enumerate(values)]
    empty = [
        title for _, title, part in out if not np.isfinite(np.asarray(part.values, float)).any()
    ]
    if empty:
        print(f"Note: panels with no data (all NaN): {', '.join(map(str, empty))}", file=sys.stderr)
    return out


# --------------------------------------------------------------------- binders


def bind_field(trace, meta, ctx, loc, *, speed=False):
    source = meta.get("source") or {}
    if speed:
        ds = _dataset(ctx, source, f"{loc}.source")
        u_name, v_name = resolve_uv(ds, source.get("u"), source.get("v"))
        u_da = _subset(ctx, source, _sel(plain(ds[u_name]), source, f"{loc}.source"))
        v_da = _subset(ctx, source, _sel(plain(ds[v_name]), source, f"{loc}.source"))
        da = wind_speed_da(u_da, v_da)
        title = wind_speed_cbar_label(u_da)
    else:
        da, _ = select(ctx, source, f"{loc}.source")
        title = variable_label_for_display(da)
    da, lat, lon, fdim = _map_field(ctx, da, meta, loc, "a map")
    x, y = da[lon].values, da[lat].values
    if len(x) == 1 or len(y) == 1:
        # Plotly cannot size a cell from one centre: give that axis the cell edges.
        half = (native_spacing_deg(y, x) or 1.0) / 2
        x = np.array([x[0] - half, x[0] + half]) if len(x) == 1 else x
        y = np.array([y[0] - half, y[0] + half]) if len(y) == 1 else y
    panels, values = [], []
    units = format_units_for_display(variable_units(da))
    hover = (
        f"lat %{{y:.2f}}, lon %{{x:.2f}}: %{{z:.4g}}{' ' + units if units else ''}<extra></extra>"
    )
    for key, ptitle, part in _slices(da, fdim):
        z = np.asarray(part.values, dtype=float)
        values.append(z)
        panels.append(Panel([{"x": x, "y": y, "z": z, "hovertemplate": hover}], ptitle, key))
    return Bound(
        panels,
        is_map=True,
        extent=extent_from_da(da, lat, lon, ctx.bbox),
        color_values=values,
        color_key="z",
        color_da=da,
        color_title=title,
    )


def _arrow_segments(lon, lat, u, v, scale):
    """Shaft plus a two-stroke head per arrow, as one None-separated polyline."""
    xs, ys = [], []
    for x0, y0, du, dv in zip(lon.ravel(), lat.ravel(), u.ravel(), v.ravel(), strict=True):
        if not (np.isfinite(du) and np.isfinite(dv)) or (du == 0 and dv == 0):
            continue
        x1, y1 = x0 + du * scale, y0 + dv * scale
        ang = np.arctan2(dv, du)
        head = 0.3 * np.hypot(du, dv) * scale
        xs += [x0, x1, None]
        ys += [y0, y1, None]
        for side in (+1, -1):
            a = ang + np.pi - side * np.radians(25)
            xs += [x1, x1 + head * np.cos(a), None]
            ys += [y1, y1 + head * np.sin(a), None]
    return xs, ys


def bind_arrows(trace, meta, ctx, loc):
    source = meta.get("source") or {}
    opts = meta.get("arrows") or {}
    ds = _dataset(ctx, source, f"{loc}.source")
    u_name, v_name = resolve_uv(ds, source.get("u"), source.get("v"))
    u_units, v_units = variable_units(ds[u_name]), variable_units(ds[v_name])
    if u_units and v_units and not units_equal(u_units, v_units):
        raise UsageError(f"u units {u_units!r} do not match v units {v_units!r}")
    u_da = _subset(ctx, source, _sel(plain(ds[u_name]), source, f"{loc}.source"))
    v_da = _subset(ctx, source, _sel(plain(ds[v_name]), source, f"{loc}.source"))
    u_da, lat, lon, fdim = _map_field(ctx, u_da, meta, loc, "wind arrows")
    v_da, *_ = _map_field(ctx, v_da, meta, loc, "wind arrows")
    step = opts.get("step")
    native = native_spacing_deg(u_da[lat].values, u_da[lon].values)
    if step is None and native:
        lons = np.asarray(u_da[lon].values, float)
        step = max(1, round((np.nanmax(lons) - np.nanmin(lons)) / ARROWS_ACROSS / native))
    step = quiver_step(u_da[lat].values, u_da[lon].values, step)
    scale = opts.get("scale")
    if scale is None:
        spacing = (native_spacing_deg(u_da[lat].values, u_da[lon].values) or 1.0) * step
        speed = np.hypot(np.asarray(u_da.values, float), np.asarray(v_da.values, float))
        typical = float(np.nanpercentile(speed, 95)) if np.isfinite(speed).any() else 0.0
        scale = ARROW_LEN_SPACING * spacing / typical if typical > 0 else 0.1
    elif scale <= 0:
        raise UsageError(f"{loc}.arrows.scale must be > 0 (degrees of arrow per unit of speed)")
    extent = extent_from_da(u_da, lat, lon, ctx.bbox)
    units = speed_units_display(ds[u_name])
    panels = []
    for (key, ptitle, u_part), (_, _, v_part) in zip(
        _slices(u_da, fdim), _slices(v_da, fdim), strict=True
    ):
        lon_q, lat_q, uq, vq = subsample_quiver(
            u_part[lon].values, u_part[lat].values, u_part.values, v_part.values, step
        )
        xs, ys = _arrow_segments(lon_q, lat_q, uq, vq, scale)
        span_x, span_y = extent[1] - extent[0], extent[3] - extent[2]
        kx, ky = extent[1] - 0.04 * span_x - ARROW_KEY_SPEED * scale, extent[2] + 0.04 * span_y
        kxs, kys = _arrow_segments(
            np.array([kx]), np.array([ky]), np.array([ARROW_KEY_SPEED]), np.array([0.0]), scale
        )
        styles = ctx.theme["styles"]
        arrows = {
            "mode": "lines",
            "x": xs,
            "y": ys,
            "hoverinfo": "skip",
            "showlegend": False,
        }
        fill_defaults(arrows, styles["arrows"])
        # A white box behind the reference arrow so data arrows do not run through it.
        box_x = [kx - 0.2 * span_x, extent[1] - 0.01 * span_x]
        box_y = [ky - 0.04 * span_y, ky + 0.04 * span_y]
        key_box = {
            "type": "scatter",
            "mode": "lines",
            "x": [box_x[0], box_x[1], box_x[1], box_x[0], box_x[0]],
            "y": [box_y[0], box_y[0], box_y[1], box_y[1], box_y[0]],
            "fill": "toself",
            "line": {"width": 0},
            "hoverinfo": "skip",
            "showlegend": False,
            "uid_suffix": "key-box",
        }
        fill_defaults(key_box, styles["arrow_key_box"])
        key_trace = {
            "type": "scatter",
            "mode": "lines+text",
            "x": kxs + [kx - 0.01 * span_x],
            "y": kys + [ky],
            "text": [""] * len(kxs) + [f"{ARROW_KEY_SPEED:g} {units}"],
            "textposition": "middle left",
            "hoverinfo": "skip",
            "showlegend": False,
            "uid_suffix": "key",
        }
        fill_defaults(key_trace, styles["arrows"])
        panels.append(Panel([arrows, key_box, key_trace], ptitle, key))
    return Bound(panels, is_map=True, extent=extent)


def _points_extent(da):
    lat_name, lon_name = cf_dim(da, "latitude"), cf_dim(da, "longitude")
    lat = np.asarray(da[lat_name].values, dtype=float)
    lon = np.asarray(da[lon_name].values, dtype=float)
    pad_lat = max(0.5, 0.1 * float(np.nanmax(lat) - np.nanmin(lat)))
    pad_lon = max(0.5, 0.1 * float(np.nanmax(lon) - np.nanmin(lon)))
    return [
        float(np.nanmin(lon)) - pad_lon,
        float(np.nanmax(lon)) + pad_lon,
        float(np.nanmin(lat)) - pad_lat,
        float(np.nanmax(lat)) + pad_lat,
    ]


def bind_points(trace, meta, ctx, loc):
    source = meta.get("source") or {}
    da, ds = select(ctx, source, f"{loc}.source")
    pdim = point_dim(ds)
    if pdim is None or pdim not in da.dims:
        raise UsageError(
            f'{loc}: meta.bind "points" needs a station_id or point_id dimension '
            f"(dims: {list(da.dims)})"
        )
    if da.sizes[pdim] == 0:
        raise UsageError(
            f"{loc}: no stations remain after layout.meta.geo.bbox / mask_geojson; nothing to plot"
        )
    fdim = _facet_dim(meta, da, (pdim,), loc)
    _leftover_error(da, {pdim, fdim}, "station points", loc)
    if da.attrs.get("extremum_kind") in ("high", "low"):
        # Highs / lows from local-extrema print as H / L with the central value.
        fill_defaults(trace, ctx.theme["styles"][f"{da.attrs['extremum_kind']}s"])
    if fdim:
        da = da.transpose(fdim, pdim)
    lat = np.asarray(da[cf_dim(da, "latitude")].values, dtype=float)
    lon = np.asarray(da[cf_dim(da, "longitude")].values, dtype=float)
    if _wrapped(ctx):
        # Same continuous axis as the wrapped grid: 150E..80W is 150..280.
        lon = ((lon - ctx.bbox[1]) % 360.0) + ctx.bbox[1]
    ids = [str(v) for v in da[pdim].values]
    names = ids
    if "name" in da.coords and da["name"].dims == (pdim,):
        # A text label reads the station name ("Kibera"); hover keeps the id too.
        names = [str(n).strip() or i for i, n in zip(ids, da["name"].values, strict=True)]
    hover_names = [i if n == i else f"{i} {n}" for i, n in zip(ids, names, strict=True)]
    units = format_units_for_display(variable_units(da))
    panels, values = [], []
    for key, ptitle, part in _slices(da, fdim):
        vals = np.asarray(part.values, dtype=float)
        values.append(vals)
        finite = np.isfinite(vals)
        keep = np.ones(vals.shape, bool)
        if "markers" not in (trace.get("mode") or "markers"):
            # A text-only trace prints values: "NaN" says nothing, so skip those points.
            keep = finite
        elif not finite.all():
            print(
                f"Note: {loc}: {int((~finite).sum())} of {finite.size} stations have no value"
                + (f" for {ptitle}" if ptitle else "")
                + "; drawn as ×",
                file=sys.stderr,
            )
        panels.append(
            Panel(
                [
                    {
                        "mode": "markers",
                        "x": lon[keep],
                        "y": lat[keep],
                        "text": [n for n, k in zip(names, keep, strict=True) if k],
                        "hovertext": [n for n, k in zip(hover_names, keep, strict=True) if k],
                        "customdata": vals[keep],
                        # An outline keeps a station visible when its class is white.
                        # A few stations read as dots, not specks; a dense network stays small.
                        "marker": {
                            "color": vals[keep],
                            "size": 13 if vals.size <= 50 else 8,
                            "line": {"width": 1},
                            # No value: a grey ×, so it never reads as a color class (a white
                            # ring is the < 1 mm class).
                            "symbol": np.where(finite[keep], "circle", "x").tolist(),
                        },
                        "hovertemplate": "%{hovertext}: %{customdata:.4g}"
                        + (f" {units}" if units else "")
                        + "<extra></extra>",
                        "showlegend": False,
                    }
                ],
                ptitle,
                key,
            )
        )
    extent = (
        [ctx.bbox[1], ctx.bbox[3], ctx.bbox[2], ctx.bbox[0]] if ctx.bbox else _points_extent(da)
    )
    return Bound(
        panels,
        is_map=True,
        extent=extent,
        color_values=values,
        color_key="marker.color",
        color_da=da,
        color_title=variable_label_for_display(da),
    )


def _geojson_lines(path):
    import shapely
    from shapely.geometry import shape

    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise UsageError(f"cannot read GeoJSON {path}: {exc}") from None
    feats = raw.get("features") or ([raw] if raw.get("type") == "Feature" else [{"geometry": raw}])
    xs, ys, geoms = [], [], []
    for feat in feats:
        geom = shape(feat.get("geometry") or {})
        geoms.append(geom)
        boundary = shapely.boundary(geom) if geom.geom_type.endswith("Polygon") else geom
        for part in getattr(boundary, "geoms", [boundary]):
            x, y = part.xy
            xs += list(x) + [None]
            ys += list(y) + [None]
    union = shapely.union_all(geoms)
    return xs, ys, union.bounds


def bind_geojson(trace, meta, ctx, loc):
    path = (meta.get("source") or {}).get("geojson")
    if not path:
        raise UsageError(f'{loc}: meta.bind "geojson" needs meta.source.geojson (a file path)')
    xs, ys, (w, s, e, n) = _geojson_lines(path)
    parts = sum(1 for v in xs if v is None)
    line = {
        "mode": "lines",
        "x": xs,
        "y": ys,
        "hoverinfo": "skip",
        "showlegend": False,
    }
    # One boundary stands out; dozens of county lines would smother the data, so they
    # take the theme's admin-1 style (KMSA: dark grey).
    admin1 = {"line": ctx.theme["overlays"]["admin1"]["line"]}
    fill_defaults(line, ctx.theme["styles"]["boundary"] if parts <= 5 else admin1)
    return Bound([Panel([line])], is_map=True, static=True, extent=[w, e, s, n])


def _color_name(trace, color):
    return (
        (trace.get("line") or {}).get("color") or (trace.get("marker") or {}).get("color") or color
    )


def _rgba(color, alpha):
    from PIL import ImageColor
    from plotly.colors import unlabel_rgb

    if not isinstance(color, str):
        return color
    try:
        if color.startswith("rgb"):
            r, g, b = unlabel_rgb(color)[:3]
        else:
            # Hex and CSS names (royalblue) alike.
            r, g, b = ImageColor.getrgb(color)[:3]
    except ValueError:
        return color
    return f"rgba({int(r)}, {int(g)}, {int(b)}, {alpha})"


def bind_series(trace, meta, ctx, loc, *, color=None):
    source = meta.get("source") or {}
    da, _ = select(ctx, source, f"{loc}.source")
    sdim = "step" if "step" in da.dims else cf_dim(da, "time")
    if sdim is None or sdim not in da.dims:
        if da.ndim == 1:
            sdim = da.dims[0]
        else:
            raise UsageError(
                f"{loc}: a series needs a step or time axis; got {list(da.dims)}. To plot one "
                "variable against another, set meta.x and meta.y to their names"
            )
    along = along_dim(da, meta.get("along"))
    if meta.get("along") and along is None:
        raise UsageError(
            f"{loc}.along {meta['along']!r} is not a dimension (dims: {list(da.dims)})"
        )
    if along == sdim:
        raise UsageError(
            f"{loc}.along is the time axis {sdim!r}; pass a non-time dim such as number"
        )
    extra = [d for d in da.dims if d not in (sdim, along)]
    if extra:
        raise UsageError(
            f"{loc}: dims {extra} remain besides {sdim!r}. Pick one value with "
            f"{loc}.source.isel / sel, set {loc}.along {extra[0]!r} for one line per value, "
            "or average them first with summarize-dim (--lat-weighted for an area mean)"
        )
    x, xname = timeseries_axis(da, sdim)
    x = np.asarray(x)
    if x.dtype.kind == "M":
        xname = period_axis_title(da, xname)
    aligned = meta.get("align") == "dayofyear"
    if aligned:
        x = seasonal_dates(da[sdim], loc)
    if x.dtype.kind == "m":
        x = x / np.timedelta64(1, "D")
        xname = "Lead (days)"
    is_date = is_datetime_axis(x)
    xs = list(x.astype("datetime64[ms]").astype(str)) if x.dtype.kind == "M" else list(x)
    label = trace.get("name") or variable_label_for_display(da, include_units=False)
    ttype = trace.get("type") or "scatter"
    base = {"name": label}
    if ttype == "scatter":
        # Markers help a short series; on a long one they only blur the line.
        base["mode"] = "lines+markers" if len(xs) <= 24 else "lines"
    traces = []
    if along is None:
        traces.append({**base, "x": xs, "y": np.asarray(da.values, dtype=float)})
    else:
        da = da.transpose(sdim, along)
        y = np.asarray(da.values, dtype=float)
        members = [along_member_label(v) for v in da[along].values]
        if "name" in da.coords and da["name"].dims == (along,):
            # Stations by id and name: "TA00057 Kibera".
            members = [
                f"{m} {n}" if str(n).strip() and str(n) != m else m
                for m, n in zip(members, da["name"].values, strict=True)
            ]
        band = meta.get("band")
        if band is not None:
            col = _color_name(trace, color)
            # Members stay visible under the band unless meta.members is false.
            if ttype == "scatter" and meta.get("members", True):
                gx, gy = [], []
                for j in range(y.shape[1]):
                    gx += xs + [None]
                    gy += list(y[:, j]) + [None]
                traces.append(
                    fill_defaults(
                        {
                            "type": "scatter",
                            "x": gx,
                            "y": gy,
                            "mode": "lines",
                            "line": {"color": col},
                            "showlegend": False,
                            "hoverinfo": "skip",
                            "legendgroup": label,
                            "uid_suffix": "members",
                        },
                        ctx.theme["styles"]["band_members"],
                    )
                )
            lo, hi = np.nanpercentile(y, band[0], axis=1), np.nanpercentile(y, band[1], axis=1)
            common = {
                "type": "scatter",
                "x": xs,
                "mode": "lines",
                "line": {"width": 0},
                "showlegend": False,
                "hoverinfo": "skip",
                "legendgroup": label,
            }
            traces += [
                {**common, "y": lo, "uid_suffix": "band-low"},
                {
                    **common,
                    "y": hi,
                    "fill": "tonexty",
                    # The series color, translucent (trace opacity does not reach a fill).
                    "fillcolor": _rgba(col, 0.25),
                    "uid_suffix": "band",
                    # The legend says what the shading is.
                    "showlegend": True,
                    "name": "min–max" if list(band) == [0, 100] else f"{band[0]:g}–{band[1]:g}%",
                },
                {
                    **base,
                    # The mean is the trace's own uid: its spec style is the mean's.
                    "uid_suffix": "",
                    # The legend says the bold line is the mean, not one member.
                    "name": trace.get("name")
                    or f"{label} {'ensemble mean' if along == 'number' else 'mean'}",
                    "x": xs,
                    "y": np.nanmean(y, axis=1),
                    "mode": "lines",
                    "line": {"color": col},
                },
            ]
        elif meta.get("along_color") == "cycle":
            if (trace.get("line") or {}).get("color"):
                raise UsageError(
                    f"{loc}: along_color cycle gives each member its own color; drop line.color"
                )
            for j, member in enumerate(members):
                traces.append(
                    {
                        **base,
                        "x": xs,
                        "y": y[:, j],
                        "name": member,
                        **({"mode": "lines"} if ttype == "scatter" else {}),
                        "uid_suffix": f"{along}={member}",
                    }
                )
        elif ttype != "scatter":
            # Bars cannot share one gapped trace: one bar series per member.
            for j, member in enumerate(members):
                traces.append(
                    {
                        **base,
                        "x": xs,
                        "y": y[:, j],
                        "name": member,
                        "uid_suffix": f"{along}={member}",
                    }
                )
        else:
            gx, gy = [], []
            for j in range(y.shape[1]):
                gx += xs + [None]
                gy += list(y[:, j]) + [None]
            # Translucent (the theme's members style) so overlapping members read as density.
            traces.append(
                fill_defaults(
                    {
                        **base,
                        "x": gx,
                        "y": gy,
                        "mode": "lines",
                        "line": {"color": _color_name(trace, color)},
                    },
                    ctx.theme["styles"]["members"],
                )
            )
    # Dates need no axis title, except to say a date starts a multi-day total.
    period = is_date and xname.endswith(("starting", "Month"))
    titles = {
        "x": axis_label(xname) if period or not is_date else "",
        "y": variable_label_for_display(da),
    }
    finite = np.asarray(da.values, dtype=float)
    finite = finite[np.isfinite(finite)]
    if is_precip(da) and finite.size and finite.min() >= 0:
        # Rainfall cannot go below zero; neither should its axis.
        titles["y_nonnegative"] = True
    return Bound(
        [Panel(traces)],
        axis_titles=titles,
        x_tickformat=SEASONAL_TICKFORMAT if aligned else None,
    )


# align "dayofyear" puts every year on this non-leap year, so 1 Oct lines up
# whether or not the data year was a leap year.
SEASONAL_YEAR = 2001
SEASONAL_TICKFORMAT = "%-d %b"


def seasonal_dates(coord, loc):
    """Each date moved to ``SEASONAL_YEAR`` (29 Feb folds onto 28 Feb), as datetime64."""
    try:
        month, day = coord.dt.month.values, coord.dt.day.values
    except (TypeError, AttributeError):
        raise UsageError(f"{loc}.align dayofyear needs a calendar-date time axis") from None
    day = np.where((month == 2) & (day == 29), 28, day)
    text = [f"{SEASONAL_YEAR}-{m:02d}-{d:02d}" for m, d in zip(month, day, strict=True)]
    return np.array(text, dtype="datetime64[D]").astype("datetime64[ns]")


def bind_xy(trace, meta, ctx, loc):
    """One variable against another from the same input: a point per shared position."""
    source = meta.get("source") or {}
    ds = _dataset(ctx, source, f"{loc}.source")
    picked = []
    for name in (meta.get("x"), meta.get("y")):
        # isel / sel reach only the side that has the dim: a county coordinate has no
        # time to pick. A dim the file lacks stays, so its "not in the data" error shows.
        dims = ds[name].dims if name in ds.variables else ds.dims
        own = dict(source)
        for key in ("isel", "sel"):
            if source.get(key):
                own[key] = {d: v for d, v in source[key].items() if d in dims or d not in ds.dims}
        picked.append(select(ctx, own, f"{loc}.source", variable=name)[0])
    x_da, y_da = picked
    if set(x_da.dims) != set(y_da.dims):
        raise UsageError(
            f"{loc}: x {meta.get('x')!r} has dims {list(x_da.dims)} but y {meta.get('y')!r} has "
            f"{list(y_da.dims)}; select one value of the extra dims with {loc}.source.isel / sel"
        )
    y_da = y_da.transpose(*x_da.dims)
    # Numbers and dates draw on a value axis; names (counties, stations, models)
    # draw as Plotly categories. Positions missing either value are dropped.
    values = []
    keep = np.ones(x_da.size, dtype=bool)
    for da in (x_da, y_da):
        v = np.asarray(da.values).ravel()
        if v.dtype.kind in "biuf":
            v = v.astype(float)
            keep &= np.isfinite(v)
        elif v.dtype.kind == "M":
            keep &= ~np.isnat(v)
        elif v.dtype.kind == "m":
            keep &= ~np.isnat(v)
            v = v / np.timedelta64(1, "D")
        else:
            # x != x is NaN; None and NaN mark a missing name.
            keep &= np.array([not (x is None or x != x) for x in v], dtype=bool)
            # cftime dates go as ISO strings, which Plotly draws on a date axis.
            v = np.array(
                [x.isoformat() if hasattr(x, "calendar") else str(x) for x in v], dtype=object
            )
        values.append(v)
    xv, yv = values
    if not keep.any():
        raise UsageError(f"{loc}: no position has a finite x and y to plot")
    bar = trace.get("type") == "bar"
    out = {"x": xv[keep], "y": yv[keep], "showlegend": False}
    if not bar:
        out["mode"] = "markers"
    if trace.get("text") or trace.get("texttemplate"):
        # The user's labels on the outermost points stay readable past the axis edge.
        out["cliponaxis"] = False
    # A short 1-D series of two numbers labels each point with its year, date or
    # name, unless the trace sets its own text.
    if (
        not bar
        and x_da.ndim == 1
        and xv.dtype.kind == "f"
        and yv.dtype.kind == "f"
        and keep.sum() <= 25
        and not (trace.get("text") or trace.get("texttemplate"))
    ):
        labels = [
            str(v) if isinstance(v, (int, np.integer)) else format_plot_date(v)
            for v in x_da[x_da.dims[0]].values
        ]
        text = [label for label, ok in zip(labels, keep, strict=True) if ok]
        # Greedy placement in axis fractions (a label ~ 0.018 wide per character, 0.05 tall):
        # each label takes the first side that does not cover one already placed.
        x0, y0 = out["x"], out["y"]
        fx = (x0 - x0.min()) / max(float(np.ptp(x0)), 1e-12)
        fy = (y0 - y0.min()) / max(float(np.ptp(y0)), 1e-12)
        sides = {
            "top right": (0, 1),
            "bottom right": (0, -1),
            "top left": (-1, 1),
            "bottom left": (-1, -1),
            "middle right": (0, 0),
            "middle left": (-1, 0),
        }
        boxes, positions = [], []
        for px, py, label in zip(fx, fy, text, strict=True):
            w, h = 0.018 * len(label), 0.05
            candidates = []
            for name, (dx, dy) in sides.items():
                bx = px + 0.01 + dx * (w + 0.02)
                by = py + (dy * h if dy else -h / 2)
                candidates.append((name, (bx, by, bx + w, by + h)))
            free = [
                (name, box)
                for name, box in candidates
                if not any(
                    box[0] < b[2] and b[0] < box[2] and box[1] < b[3] and b[1] < box[3]
                    for b in boxes
                )
            ]
            name, box = (free or candidates)[0]
            boxes.append(box)
            positions.append(name)
        out.update(mode="markers+text", text=text, textposition=positions, cliponaxis=False)
    x_qty = variable_label_for_display(x_da, include_units=False)
    y_qty = variable_label_for_display(y_da, include_units=False)
    return Bound(
        [Panel([out])],
        axis_titles={"x": variable_label_for_display(x_da), "y": variable_label_for_display(y_da)},
        title=f"{y_qty} vs {x_qty}",
    )


def bind_samples(trace, meta, ctx, loc):
    """Box of every sample per category (box), or the category mean (scatter)."""
    source = meta.get("source") or {}
    da, _ = select(ctx, source, f"{loc}.source")
    point = source.get("point") or ctx.point
    lat, lon = cf_dim(da, "latitude"), cf_dim(da, "longitude")
    if point is not None:
        if lat not in da.dims or lon not in da.dims:
            raise UsageError(
                f"{loc}.source.point needs latitude/longitude dims; got {list(da.dims)}"
            )
        da = da.sel({lat: point["lat"], lon: point["lon"]}, method="nearest")
    elif lat in da.dims or lon in da.dims:
        raise UsageError(
            f'{loc}.source.point or layout.meta.geo.point ({{"lat": …, "lon": …}}) is '
            "required to take samples at a point"
        )
    cat = "step" if "step" in da.dims else cf_dim(da, "time")
    if cat is None or cat not in da.dims:
        raise UsageError(f"{loc}: samples need a step or time axis for the categories")
    rest = [d for d in da.dims if d != cat]
    da = da.transpose(cat, *rest)
    labels = [format_step(v) for v in da[cat].values]
    vals = np.asarray(da.values, dtype=float).reshape(len(labels), -1)
    if (trace.get("type") or "box") == "box":
        out = {"x": np.repeat(labels, vals.shape[1]), "y": vals.ravel()}
    else:
        out = {"mode": "lines+markers", "x": labels, "y": np.nanmean(vals, axis=1)}
    return Bound([Panel([out])], axis_titles={"y": variable_label_for_display(da)})


def bind_windrose(trace, meta, ctx, loc):
    source = meta.get("source") or {}
    ds = _dataset(ctx, source, f"{loc}.source")
    u_name, v_name = resolve_uv(ds, source.get("u"), source.get("v"))
    u_da = _subset(ctx, source, _sel(plain(ds[u_name]), source, f"{loc}.source"))
    v_da = _subset(ctx, source, _sel(plain(ds[v_name]), source, f"{loc}.source"))
    point = source.get("point") or ctx.point
    if point is not None:
        # One place's winds, not the whole domain pooled.
        lat, lon = cf_dim(u_da, "latitude"), cf_dim(u_da, "longitude")
        if lat not in u_da.dims or lon not in u_da.dims:
            raise UsageError(f"{loc}.source.point needs latitude/longitude dims")
        where = {lat: point["lat"], lon: point["lon"]}
        u_da = u_da.sel(where, method="nearest")
        v_da = v_da.sel(where, method="nearest")
    else:
        lat, lon = cf_dim(u_da, "latitude"), cf_dim(u_da, "longitude")
        cells = (u_da.sizes.get(lat, 1) if lat else 1) * (u_da.sizes.get(lon, 1) if lon else 1)
        if cells > 1:
            print(
                f"Note: {loc}: the rose pools {cells} grid cells; for one place set "
                'layout.meta.geo.point {"lat": …, "lon": …}',
                file=sys.stderr,
            )
    extra = [d for d in u_da.dims if not is_sample_dim(u_da, d)]
    if extra:
        raise UsageError(
            f"dimension {extra[0]!r} remains; a wind rose pools space, time and ensemble into "
            f"samples — pick one value with {loc}.source.sel (e.g. a pressure level: "
            f'{{"vertical": 850}}) or {loc}.source.isel'
        )
    u, v = flat_numeric(u_da), flat_numeric(v_da)
    if u.size != v.size:
        raise UsageError(f"u {u_name!r} and v {v_name!r} have different sizes after selection")
    ok = np.isfinite(u) & np.isfinite(v)
    u, v = u[ok], v[ok]
    if u.size == 0:
        raise UsageError("wind rose has no finite u/v samples to plot")
    speed, direction = uv_to_speed_fromdir(u, v)
    edges = speed_edges(speed, variable_units(u_da))
    hist = wind_rose_hist(speed, direction, edges, nsector=WIND_ROSE_SECTORS)
    while hist.shape[1] > 1 and float(hist[:, -1].sum()) == 0:
        hist, edges = hist[:, :-1], edges[:-1]
    freq = 100.0 * hist / float(hist.sum())
    n = freq.shape[1]
    palette = meta.get("palette")
    if palette:
        from plotly.colors import sample_colorscale

        colors = parse_palette(palette, loc=f"{loc}.palette", registry=ctx.palettes)["colors"]
        colors = sample_colorscale(
            continuous_colorscale(colors), [i / max(n - 1, 1) for i in range(n)]
        )
    else:
        # One fixed colour per speed class, so 2–4 m/s looks the same on every rose.
        fixed = parse_palette("wind_rose", loc="palettes.wind_rose", registry=ctx.palettes)[
            "colors"
        ]
        colors = [fixed[min(i, len(fixed) - 1)] for i in range(n)]
    units = speed_units_display(u_da)
    theta = [i * 360.0 / WIND_ROSE_SECTORS for i in range(WIND_ROSE_SECTORS)]
    traces = [
        {
            "r": freq[:, i],
            "theta": theta,
            "name": f"{label} {units}",
            "uid_suffix": f"bin{i}",
            "marker": {"color": colors[i]},
        }
        for i, label in enumerate(speed_bin_labels(edges))
    ]
    compass = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    layout = {
        "polar": {
            "angularaxis": {
                "rotation": 90,
                "direction": "clockwise",
                "tickvals": [i * 45 for i in range(8)],
                "ticktext": compass,
            },
            # Radial labels in the gap between the NNE and NE sectors, small, clear of bars.
            "radialaxis": {
                "ticksuffix": "%",
                "angle": 56.25,  # math degrees: bearing 33.75, between NNE and NE
                "tickangle": 56.25,
                "tickfont": {"size": 10},
                "showline": False,
            },
            "bargap": 0.18,
        },
        "legend": {"title": {"text": "Wind speed"}},
        "barmode": "stack",
    }
    return Bound([Panel(traces)], layout=layout)


BINDERS = {
    "field": bind_field,
    "speed": lambda t, m, c, loc: bind_field(t, m, c, loc, speed=True),
    "arrows": bind_arrows,
    "points": bind_points,
    "geojson": bind_geojson,
    "series": bind_series,
    "xy": bind_xy,
    "samples": bind_samples,
    "windrose": bind_windrose,
}

_TYPES_FOR_BIND = {
    "field": {"heatmap", "contour"},
    "speed": {"heatmap", "contour"},
    "arrows": {"scatter", "scattergl"},
    "points": {"scatter", "scattergl"},
    "geojson": {"scatter", "scattergl"},
    "series": {"scatter", "scattergl", "bar"},
    "xy": {"scatter", "scattergl", "bar"},
    "samples": {"box", "scatter", "violin"},
    "windrose": {"barpolar"},
}


def bind_trace(trace: dict, ctx: Context, loc: str, *, color=None) -> Bound:
    bind = bind_of(trace)
    ttype = trace.get("type") or "scatter"
    allowed = _TYPES_FOR_BIND[bind]
    if ttype not in allowed:
        raise UsageError(
            f"{loc}: meta.bind {bind!r} draws as type {' or '.join(sorted(allowed))}, "
            f"not {ttype!r}"
            + (
                "; for a 1-D series of a gridded field, average lat/lon first with "
                'summarize-dim --lat-weighted and set meta.bind "series"'
                if bind == "field"
                else ""
            )
        )
    # Binders name keys under meta (source, along, arrows, …): errors say data[i].meta.<key>.
    meta_loc = f"{loc}.meta"
    if bind == "series":
        return bind_series(trace, trace.get("meta") or {}, ctx, meta_loc, color=color)
    return BINDERS[bind](trace, trace.get("meta") or {}, ctx, meta_loc)
