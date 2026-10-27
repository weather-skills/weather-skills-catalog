"""Assemble a Plotly figure from a spec and its datasets.

1. Bind: every trace with ``meta`` data becomes concrete traces (``bind.py``).
2. Place: map traces group by their ``xaxis`` anchor; a group panels its
   facet values; static layers (outlines, plain traces) repeat on each panel.
3. Color: each color-scaled source trace gets a ``coloraxis`` (shared when
   the user points traces at the same one, or when same-variable layers
   share a panel). Class palettes become stepped colorscales.
4. Defaults (axis titles, panel titles, colorbar titles, sizes) fill only
   what the user's layout leaves unset; the user's layout is merged last.
"""

from __future__ import annotations

import copy
import json
import math
import re
import sys

import numpy as np
from weather_skills_core.errors import UsageError
from weather_skills_core.standard_utils import parse_bbox, polygon_from_geojson
from weather_skills_core.units import convert_values, variable_units

from weather_skills_plotting.bind import Context, bind_of, bind_trace
from weather_skills_plotting.geodata import (
    dateline_bbox,
    geoms_to_xy,
    overlay_geoms,
    overlay_settings,
)
from weather_skills_plotting.kinds import (
    ISOBAR_INTERVAL_HPA,
    STANDARD_SEA_LEVEL_PRESSURE_HPA,
    is_precip_anomaly,
    is_sea_level_pressure,
    is_temperature,
)
from weather_skills_plotting.layout import (
    DEFAULT_TEMPLATE,
    WIDE_MAP,
    axis_suffix,
    choose_grid,
    colorbar_mode,
    layout_value,
    map_axes,
    map_layout,
    panel_title_annotation,
    place_colorbars,
    title_lines,
)
from weather_skills_plotting.palettes import (
    _class_slots,
    continuous_colorscale,
    default_palette,
    discretize,
    parse_palette,
    resolve_default_precip,
)
from weather_skills_plotting.spec import (
    deep_merge,
    fill_defaults,
    merge_spec,
    plotly_error,
    validate,
)
from weather_skills_plotting.themes import load_theme

# Draw order inside a map panel: fields under overlays, then arrows, stations, outlines.
MANY_PANELS = 24  # more map panels than this get a warning
_MAP_RANK = {"field": 0, "speed": 0, "overlay": 1, "arrows": 2, "points": 3, "geojson": 4, None: 5}
_TRACE_COLOR_KEYS = ("colorscale", "reversescale", "showscale", "colorbar")
_TRACE_LIMIT_KEYS = {"zmin": "cmin", "zmax": "cmax", "zmid": "cmid"}
_MARKER_LIMIT_KEYS = {"cmin": "cmin", "cmax": "cmax", "cmid": "cmid"}


def compile_figure(spec: dict, datasets: dict, *, theme: dict | None = None):
    """Return a validated ``plotly.graph_objects.Figure``."""
    import plotly.graph_objects as go

    theme = theme or load_theme()
    spec = validate(copy.deepcopy(spec), theme)
    user_layout = copy.deepcopy(spec.get("layout") or {})
    lmeta = user_layout.pop("meta", None) or {}
    geo = lmeta.get("geo") or {}
    bbox = geo.get("bbox")
    bbox = tuple(parse_bbox(bbox)) if isinstance(bbox, str) else (tuple(bbox) if bbox else None)
    if bbox is None:
        # A grid that crosses the dateline is drawn on one continuous 0..360 window.
        bbox = dateline_bbox(datasets.values())
    elif bbox[0] < bbox[2]:
        raise UsageError(f"layout.meta.geo.bbox {list(bbox)} is [N, W, S, E]; north is below south")
    ctx = Context(
        datasets=datasets,
        palettes=theme["palettes"],
        theme=theme,
        bbox=bbox,
        polygon=polygon_from_geojson(geo["mask_geojson"]) if geo.get("mask_geojson") else None,
        point=geo.get("point"),
    )
    colorway = list(
        layout_value(user_layout, "colorway") or theme["template"]["layout"]["colorway"]
    )

    items = []
    n_colored = 0
    traces = spec.get("data") or []
    # UID-members / UID-band style the parts of band trace UID; they draw nothing alone.
    banded = {t.get("uid"): t for t in traces if (t.get("meta") or {}).get("band") is not None}
    parts: dict = {}
    for trace in traces:
        base, _, part = str(trace.get("uid") or "").rpartition("-")
        if part in ("members", "band") and base in banded:
            if part == "members" and (banded[base].get("meta") or {}).get("members") is False:
                raise UsageError(
                    f"uid {trace['uid']!r} styles the member lines of {base!r}, but its "
                    "meta.members is false"
                )
            parts.setdefault(base, {})[part] = trace
    skip = {id(p) for d in parts.values() for p in d.values()}
    for i, trace in enumerate(traces):
        if id(trace) in skip:
            continue
        if trace.get("uid") in banded:
            trace = {**trace, "meta": {**trace["meta"], "_parts": parts.get(trace["uid"], {})}}
        bound = None
        if bind_of(trace):
            bound = bind_trace(trace, ctx, f"data[{i}]", color=colorway[n_colored % len(colorway)])
            n_colored += 1
        items.append((i, trace, bound))
    if not items:
        raise UsageError("plot spec has no traces to draw")

    if any(b is not None and b.is_map for _, _, b in items):
        data, layout = _assemble_map(items, user_layout, lmeta, ctx)
    else:
        data, layout = _assemble_chart(items, user_layout)
    layout.setdefault("template", DEFAULT_TEMPLATE)
    built = {a.get("name") for a in layout.get("annotations") or []}
    for ann in user_layout.get("annotations") or []:
        name = str(ann.get("name") or "")
        if name.startswith("panel-title-") and name not in built:
            have = sorted(
                (n for n in built if n and n.startswith("panel-title-")),
                key=lambda n: int(n.rsplit("-", 1)[1]),
            )
            raise UsageError(
                f"layout.annotations name {name!r} matches no panel; this figure's panel titles "
                f"are {', '.join(have) or 'none'} (numbered from 1)"
            )
    out_layout = merge_spec({"layout": layout}, {"layout": user_layout})["layout"]
    try:
        return go.Figure({"data": data, "layout": out_layout})
    except ValueError as exc:
        raise plotly_error(exc) from None


# ---------------------------------------------------------------- trace merge


def _style(trace: dict) -> dict:
    return {k: v for k, v in trace.items() if k not in ("meta", "uid", "xaxis", "yaxis")}


def _concrete(spec_trace: dict, generated: dict, uid: str) -> dict:
    """Generated arrays with the user's own trace keys on top (user wins).

    A band trace draws three parts: its own uid is the mean line, and the spec
    traces ``UID-members`` / ``UID-band`` style the member lines and the shading.
    """
    gen = dict(generated)
    suffix = gen.pop("uid_suffix", None)
    meta = spec_trace.get("meta") or {}
    out = {"type": spec_trace.get("type") or "scatter"}
    if meta.get("band") is not None and suffix in ("members", "band-low", "band"):
        style = _style(
            (meta.get("_parts") or {}).get("members" if suffix == "members" else "band", {})
        )
        if suffix == "band-low":
            # The lower edge only carries the outline; the fill and legend are the band's.
            style = {k: v for k, v in style.items() if k == "line"}
        elif suffix == "members" and "name" in style:
            # A named trace has a legend entry, as in Plotly.
            style = {"showlegend": True, **style}
        out.update(deep_merge(gen, style))
    else:
        user = _style(spec_trace)
        if "=" in str(suffix or ""):
            # One trace per along member: the member's name, not the input's.
            user.pop("name", None)
        out.update(deep_merge(gen, user))
    out["uid"] = safe_uid(f"{uid}-{suffix}" if suffix else uid)
    return out


def safe_uid(text) -> str:
    """plotly.js puts uids in CSS selectors: keep letters, digits, ``-`` and ``_``."""
    return re.sub(r"[^A-Za-z0-9_-]+", "-", str(text))


def _set_path(obj: dict, path: str, value):
    keys = path.split(".")
    for key in keys[:-1]:
        obj = obj.setdefault(key, {})
    obj[keys[-1]] = value


def _get_path(obj: dict, path: str):
    for key in path.split("."):
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


# ---------------------------------------------------------------- color axes


def _lift_color(spec_trace: dict, color_key: str) -> dict:
    """Trace-level colorscale / limits / colorbar, moved onto its coloraxis."""
    holder = spec_trace.get("marker") or {} if color_key == "marker.color" else spec_trace
    limits = _MARKER_LIMIT_KEYS if color_key == "marker.color" else _TRACE_LIMIT_KEYS
    out = {k: copy.deepcopy(holder[k]) for k in _TRACE_COLOR_KEYS if k in holder}
    out.update({dst: holder[src] for src, dst in limits.items() if src in holder})
    return out


def _uncolored(spec_trace: dict) -> bool:
    """No color scale to show: a contour drawn as plain lines (``contours.coloring: none``),
    or a scatter drawn without markers (``mode: "text"``, ``"lines"``) or with the
    spec's own ``marker.color`` (``"black"``: every dot that color, as in Plotly)."""
    if spec_trace.get("type") == "contour":
        return (spec_trace.get("contours") or {}).get("coloring") == "none"
    if spec_trace.get("type") not in ("scatter", "scattergl"):
        return False
    mode = spec_trace.get("mode")
    return (bool(mode) and "markers" not in mode) or ("color" in (spec_trace.get("marker") or {}))


def _resolve_coloraxis(name, members, user_axis, ctx, *, contour_traces):
    """Fill ``layout.<name>`` for the bound traces in ``members``.

    ``members`` are ``(spec_trace, bound, concrete_traces)``. Returns the
    generated coloraxis dict (the user's own keys are merged later).
    """
    lifted = {}
    for spec_trace, bound, _ in members:
        lifted = deep_merge(lifted, _lift_color(spec_trace, bound.color_key))
    user = deep_merge(lifted, user_axis or {})
    first = members[0][1]
    palette_raw = next(
        (
            (st.get("meta") or {}).get("palette")
            for st, _, _ in members
            if (st.get("meta") or {}).get("palette")
        ),
        None,
    )
    palette_raw = resolve_default_precip(
        palette_raw, [b.color_da for _, b, _ in members], ctx.palettes
    )
    palette = parse_palette(palette_raw, registry=ctx.palettes) if palette_raw else None
    values = np.concatenate([np.ravel(v) for _, b, _ in members for v in b.color_values])
    finite = values[np.isfinite(values)]
    stretch = "cmin" in user or "cmax" in user
    lo = float(finite.min()) if finite.size else 0.0
    hi = float(finite.max()) if finite.size else 1.0
    # Limits the user set decide whether the scale straddles zero; they are kept as given.
    # A temperature difference (delta_degC) is centred on zero whatever its sign; an
    # absolute temperature crossing 0 °C (sea ice, the Arctic) keeps the temperature scale.
    color_das = [b.color_da for _, b, _ in members if b.color_da is not None]
    difference = any("delta_" in str(variable_units(d) or "") for d in color_das)
    absolute_temp = bool(color_das) and not difference and all(map(is_temperature, color_das))
    diverging = difference or (
        not absolute_temp and user.get("cmin", lo) < 0 < user.get("cmax", hi)
    )
    axis = {"colorbar": {"title": {"text": first.color_title or ""}}}
    if palette is None and "colorscale" not in user:
        # The theme's color for this kind of field: precip, wind speed, diverging, temperature …
        das = [b.color_da for _, b, _ in members if b.color_da is not None]
        palette = default_palette(*(das or [None]), theme=ctx.theme, diverging=diverging)
        if palette.get("colorscale"):
            axis["colorscale"] = palette["colorscale"]
            palette = None
    if palette and palette.get("bounds") and stretch:
        print(
            f"Warning: zmin/zmax turn the class palette {palette.get('name') or ''} into a "
            "continuous scale; drop them to keep its classes, or set bounds in the palette",
            file=sys.stderr,
        )
    if palette and palette.get("bounds") and not stretch and "colorscale" not in user:
        under, classes, over = _class_slots(palette)
        if under and finite.size and finite.min() >= palette["bounds"][0]:
            # Nothing falls below the first edge (rain is never negative): no empty
            # box under "0" on the colorbar.
            palette = {k: v for k, v in palette.items() if k not in ("colors", "under", "over")}
            palette.update(colors=list(classes), **({"over": over} if over else {}))
        for _, bound, traces in members:
            for tr in traces:
                raw = _get_path(tr, bound.color_key)
                slots, scale = discretize(raw, palette)
                _set_path(tr, bound.color_key, slots)
                if bound.color_key == "z":
                    # z now holds class slots: hover reads the raw values instead.
                    tr["customdata"] = raw
                    if "hovertemplate" in tr:
                        tr["hovertemplate"] = tr["hovertemplate"].replace("%{z", "%{customdata")
        _, scale = discretize(finite, palette)
        axis.update(colorscale=scale["colorscale"], cmin=scale["cmin"], cmax=scale["cmax"])
        axis["colorbar"].update(tickvals=scale["tickvals"], ticktext=scale["ticktext"])
        # An outline keeps white classes (dry, near-zero anomaly) visible on the bar.
        axis["colorbar"].update(copy.deepcopy(ctx.theme["styles"]["classed_colorbar"]))
        n_slots = len(scale["colorscale"]) // 2
        for tr in contour_traces:
            tr.setdefault("autocontour", False)
            fill_defaults(tr, {"contours": {"start": 0.5, "end": n_slots - 1.5, "size": 1}})
        return axis
    if palette:
        axis["colorscale"] = continuous_colorscale(palette["colors"])
    if "cmid" in user and not stretch:
        # Plotly's zmid / cmid: the scale centres on it, wide enough for the data.
        mid = float(user["cmid"])
        m = max(abs(lo - mid), abs(hi - mid))
        lo, hi = mid - m, mid + m
    elif diverging and not stretch:
        m = max(abs(lo), abs(hi))
        lo, hi = -m, m
    elif not stretch and first.color_da is not None and is_sea_level_pressure(first.color_da):
        # White at the standard atmosphere: lows read blue, highs red.
        mid = float(
            convert_values(
                STANDARD_SEA_LEVEL_PRESSURE_HPA, "hPa", variable_units(first.color_da) or "hPa"
            )[0]
        )
        m = max(abs(lo - mid), abs(hi - mid))
        lo, hi = mid - m, mid + m
    probability = bool(color_das) and all(
        "probab" in f"{d.name} {d.attrs.get('long_name') or ''}".lower() for d in color_das
    )
    if not stretch and probability and 0 <= lo and hi <= 1:
        # A probability reads against its whole range: 0.85 is not "the maximum".
        lo, hi = 0.0, 1.0
    if lo == hi:
        lo, hi = lo - 1, hi + 1
    axis.setdefault("cmin", lo)
    axis.setdefault("cmax", hi)
    lo, hi = float(user.get("cmin", lo)), float(user.get("cmax", hi))
    for tr in contour_traces:
        contours = tr.setdefault("contours", {})
        if tr.get("autocontour") or {"start", "end", "size"} & contours.keys() or hi <= lo:
            continue
        # Plotly picks levels from each panel's own values, so panels and animation
        # frames would disagree; take ~ncontours round levels across the colour range.
        raw = (hi - lo) / tr.get("ncontours", 15)
        mag = 10 ** math.floor(math.log10(raw))
        size = next(s * mag for s in (1, 2, 2.5, 5, 10) if s * mag >= raw)
        tr["autocontour"] = False
        contours.update(
            start=math.floor(lo / size) * size, end=math.ceil(hi / size) * size, size=size
        )
    return axis


# ------------------------------------------------------------------- map figure


def _anchor(trace: dict) -> int:
    raw = trace.get("xaxis") or "x"
    try:
        return int(raw[1:] or 1)
    except ValueError:
        raise UsageError(f"trace xaxis {raw!r} must look like x, x2, x3 …") from None


def _input_label(ctx, trace):
    from pathlib import Path

    from weather_skills_core.decorator import INPUT_PATH_ATTR

    source = (trace.get("meta") or {}).get("source") or {}
    if source.get("input") is not None and not str(source["input"]).isdigit():
        return str(source["input"])  # the NAME from -i PATH=NAME
    ds = ctx.datasets.get(str(source.get("input"))) if source.get("input") else None
    path = getattr(ds, "attrs", {}).get(INPUT_PATH_ATTR) if ds is not None else None
    return Path(path).stem if path else None


def _assemble_map(items, user_layout, lmeta, ctx):
    groups: dict[int, list] = {}
    # A plain Plotly trace (city markers, a box) with no axis of its own goes on every map.
    floating = [item for item in items if item[2] is None and not item[1].get("xaxis")]
    for item in items:
        if not any(item is f for f in floating):
            groups.setdefault(_anchor(item[1]), []).append(item)
    if not groups:
        groups[1] = []
    order = sorted(groups)
    for g in order:
        if any(b is not None and st.get("type") == "heatmap" for _, st, b in groups[g]):
            for _, st, b in groups[g]:
                if b is not None and st.get("type") == "contour":
                    # Contours stacked on shading are labeled lines (isobars over a
                    # field), not a fill that hides it; coloring "fill" keeps the fill.
                    fill_defaults(st, {"contours": {"coloring": "none", "showlabels": True}})
    plan = []  # per group: list of panel keys (None for an unfaceted group)
    for g in order:
        faceted = [
            b for _, _, b in groups[g] if b is not None and not b.static and len(b.panels) > 1
        ]
        if faceted:
            # Layers on one map draw the same times: never pair different dates silently.
            keys = [p.key for p in faceted[0].panels]
            first = [p.title for p in faceted[0].panels]
            for b in faceted[1:]:
                if [_key(p.key) for p in b.panels] != [_key(k) for k in keys]:
                    raise UsageError(
                        f"layered traces show different times/steps ({', '.join(map(str, first))} "
                        f"vs {', '.join(str(p.title) for p in b.panels)}); select the same ones "
                        "in each layer (meta.source.isel / sel)"
                    )
            for _, st, b in groups[g]:
                # A layer with no date (terrain, a mask) may sit under every panel.
                if b is not None and not b.static and len(b.panels) == 1 and b.panels[0].title:
                    raise UsageError(
                        f"layer {st.get('uid')!r} is one map ({b.panels[0].title}) but another "
                        f"layer has {len(keys)} panels ({', '.join(map(str, first))}); select "
                        "the same times in each layer (meta.source.isel / sel)"
                    )
            plan.append(keys)
        else:
            plan.append([None])
    if len(order) > 1 and any(len(keys) > 1 for keys in plan):
        raise UsageError(
            "side-by-side map traces (different xaxis) must each already be a single map; "
            "select one time/step per input (meta.source.isel / sel) or aggregate first"
        )
    panels = []  # (group index or None for an empty cell, key)
    if len(order) > 1:
        # Unfaceted side-by-side traces keep their axis number as their cell,
        # so x, x2, x4 leaves cell 3 empty.
        panels = [
            (order.index(a), None) if a in order else (None, None) for a in range(1, order[-1] + 1)
        ]
    else:
        panels = [(0, k) for k in plan[0]]
    n = len(panels)
    if n > MANY_PANELS:
        print(
            f"Warning: {n} map panels; each will be small. Pick the ones you need with "
            'meta.source.isel (e.g. {"step": [0, 6, 12, 18]}) or aggregate first.',
            file=sys.stderr,
        )

    # Bind results per panel.
    panel_traces = [[] for _ in range(n)]  # (rank, trace dict, spec_trace, bound)
    panel_titles = [None] * n
    extents = [None] * n
    for p, (gi, key) in enumerate(panels):
        if gi is None:
            continue
        g = order[gi]
        for i, spec_trace, bound in [*groups[g], *floating]:
            uid = spec_trace.get("uid") or f"trace{i}"
            if bound is None:
                tr = copy.deepcopy(
                    {k: v for k, v in spec_trace.items() if k not in ("xaxis", "yaxis")}
                )
                tr["uid"] = safe_uid(f"{uid}-p{p + 1}" if n > 1 else uid)
                if any(spec_trace is f[1] for f in floating):
                    # One color on every panel, not the next colorway entry per copy.
                    fill_defaults(tr, ctx.theme["styles"]["map_marks"])
                panel_traces[p].append((_MAP_RANK[None], tr, spec_trace, None))
                continue
            if len(bound.panels) == 1:
                part = bound.panels[0]
            else:
                part = next(pp for pp in bound.panels if _key(pp.key) == _key(key))
            if part.title and panel_titles[p] is None:
                panel_titles[p] = part.title
            if not bound.static and bound.extent and extents[p] is None:
                extents[p] = bound.extent
            pid = f"{uid}-p{p + 1}" if n > 1 else uid
            for j, gen in enumerate(part.traces):
                tr = _concrete(
                    spec_trace, gen, pid if j == 0 or "uid_suffix" in gen else f"{pid}-{j}"
                )
                panel_traces[p].append((_MAP_RANK[bind_of(spec_trace)], tr, spec_trace, bound))
        if extents[p] is None:
            extents[p] = next(
                (b.extent for _, _, b in groups[g] if b is not None and b.extent), None
            )
        if extents[p] is None:
            raise UsageError("a map panel has no data extent; set layout.meta.geo.bbox")
    if len(order) > 1:
        # Side by side, a panel is named for its input (-i NAME, else file name),
        # and keeps its date too when the panels show different dates.
        dates = [t for t in panel_titles if t]
        differ = len(set(dates)) > 1
        for p, (gi, _) in enumerate(panels):
            if gi is None:
                continue
            bound_items = [
                (t, b) for _, t, b in groups[order[gi]] if b is not None and not b.static
            ]
            name = _input_label(ctx, bound_items[0][0]) if bound_items else None
            if name:
                date = panel_titles[p]
                panel_titles[p] = f"{name} · {date}" if differ and date else name

    # Color axes: explicit coloraxis wins; else traces showing the same quantity
    # (color title, palette, anomaly or not) share one, side by side included.
    axis_of = {}  # id(spec_trace) -> coloraxis name
    axis_members: dict[str, list] = {}
    next_axis = 1
    auto = {}
    for g in order:
        for _, spec_trace, bound in groups[g]:
            if bound is None or not bound.color_values or _uncolored(spec_trace):
                continue
            name = (
                spec_trace.get("coloraxis")
                if bound.color_key == "z"
                else (spec_trace.get("marker") or {}).get("coloraxis")
            )
            if not name:
                # One quantity is one CF standard_name in one unit: station "Surface air
                # temperature" and a forecast's "2 m temperature" share a bar. Anomalies
                # and spreads of it do not.
                title = bound.color_title or ""
                sn = (
                    bound.color_da.attrs.get("standard_name")
                    if bound.color_da is not None
                    else None
                )
                difference = bound.color_da is not None and "delta_" in str(
                    variable_units(bound.color_da) or ""
                )
                quantity = (
                    (
                        sn,
                        title.rpartition("[")[2],
                        "anomaly" in title,
                        "std. dev." in title,
                        difference,
                    )
                    if sn
                    else title
                )
                key = (
                    json.dumps(quantity),
                    json.dumps((spec_trace.get("meta") or {}).get("palette"), sort_keys=True),
                    # The trace's own scale and limits apply to that trace, not to every
                    # map of the same quantity (colorbar / showscale still style the shared bar).
                    json.dumps(
                        {
                            k: v
                            for k, v in _lift_color(spec_trace, bound.color_key).items()
                            if k not in ("colorbar", "showscale")
                        },
                        sort_keys=True,
                    ),
                    bound.color_da is not None and is_precip_anomaly(bound.color_da),
                )
                name = auto.get(key)
                if name is None:
                    name = "coloraxis" if next_axis == 1 else f"coloraxis{next_axis}"
                    next_axis += 1
                    auto[key] = name
            axis_of[id(spec_trace)] = name
    for p in range(n):
        for _, tr, spec_trace, bound in panel_traces[p]:
            if _uncolored(spec_trace):
                if tr.get("type") == "contour":
                    # Isobars over shading: plain black lines, no colorbar of their own.
                    fill_defaults(tr, {"showscale": False, **ctx.theme["styles"]["isobars"]})
                    contours = tr.setdefault("contours", {})
                    if (
                        bound is not None
                        and bound.color_da is not None
                        and is_sea_level_pressure(bound.color_da)
                        and not {"start", "end", "size"} & contours.keys()
                    ):
                        # Isobars every 4 hPa on multiples of 4 (1008, 1012, …), not
                        # Plotly's ~15 automatic levels at odd values.
                        size = float(
                            convert_values(
                                ISOBAR_INTERVAL_HPA, "hPa", variable_units(bound.color_da) or "hPa"
                            )[0]
                        )
                        values = np.concatenate([np.ravel(v) for v in bound.color_values])
                        tr["autocontour"] = False
                        contours.update(
                            start=math.floor(np.nanmin(values) / size) * size,
                            end=math.ceil(np.nanmax(values) / size) * size,
                            size=size,
                        )
                continue
            if bound is None or id(spec_trace) not in axis_of:
                continue
            name = axis_of[id(spec_trace)]
            if bound.color_key == "z" and "z" in tr:
                tr["coloraxis"] = name
            elif bound.color_key == "marker.color" and "color" in (tr.get("marker") or {}):
                tr["marker"]["coloraxis"] = name
            else:
                continue
            members = axis_members.setdefault(name, [])
            entry = next((m for m in members if m[0] is spec_trace), None)
            if entry is None:
                members.append((spec_trace, bound, [tr]))
            else:
                entry[2].append(tr)

    outlines = [
        b.extent for _, st, b in items if b is not None and bind_of(st) == "geojson" and b.extent
    ]
    if outlines and ctx.bbox is None and len(order) == 1:
        # An outline names the place of interest: frame it, with a margin.
        w, e = min(o[0] for o in outlines), max(o[1] for o in outlines)
        s, n_ = min(o[2] for o in outlines), max(o[3] for o in outlines)
        pad = 0.04 * max(e - w, n_ - s)
        extents = [[w - pad, e + pad, s - pad, n_ + pad] if x else None for x in extents]
    if len(order) > 1 and ctx.bbox is None:
        # Side-by-side maps of the same region share one window, so a coarse
        # grid next to a fine one is compared on equal footing.
        boxes = [e for e in extents if e]
        area = [(e[1] - e[0]) * (e[3] - e[2]) for e in boxes]
        overlap = all(
            max(0.0, min(a[1], b[1]) - max(a[0], b[0]))
            * max(0.0, min(a[3], b[3]) - max(a[2], b[2]))
            >= 0.5 * min(area[i], area[j])
            for i, a in enumerate(boxes)
            for j, b in enumerate(boxes)
            if i < j
        )
        if overlap:
            union = [
                min(e[0] for e in boxes),
                max(e[1] for e in boxes),
                min(e[2] for e in boxes),
                max(e[3] for e in boxes),
            ]
            extents = [union if e else None for e in extents]

    # Grid and canvas follow the maps' shape; colorbars go beside the rows or
    # under the columns they colour when the panels split that way.
    aspect = float(np.median([(e[1] - e[0]) / max(e[3] - e[2], 1e-6) for e in extents if e]))
    grid_in = user_layout.pop("grid", None) or {}
    rows, cols = choose_grid(n, aspect, grid_in.get("rows"), grid_in.get("columns"))
    # Trace-level colorbar / showscale lift onto the trace's color axis, under the
    # spec's layout.<coloraxis>. On a shared axis one trace asking for the bar shows
    # it: showscale false on the other traces only means "not drawn from me".
    lifted_of, asked = {}, {}
    for name, members in axis_members.items():
        lifted = {}
        for spec_trace, bound, _ in members:
            lifted = deep_merge(lifted, _lift_color(spec_trace, bound.color_key))
        if any(st.get("showscale") is True for st, _, _ in members):
            lifted["showscale"] = True
        lifted_of[name] = lifted
        asked[name] = deep_merge(lifted, user_layout.get(name) or {})
    panels_of = {}
    for name in axis_members:
        if asked[name].get("showscale") is False:
            continue
        panels_of[name] = [
            p for p in range(n) for _, _, st, _ in panel_traces[p] if axis_of.get(id(st)) == name
        ]
    panels_of = dict(sorted(panels_of.items(), key=lambda item: min(item[1])))
    # A bar the spec positions itself (colorbar x or y) stays where it was put; only
    # the other bars are placed beside or under the maps.
    placed = {name for name in panels_of if {"x", "y"} & (asked[name].get("colorbar") or {}).keys()}
    auto_of = {name: ps for name, ps in panels_of.items() if name not in placed}
    placed_h = {name for name in placed if asked[name]["colorbar"].get("orientation") == "h"}
    mode = colorbar_mode(auto_of, cols)
    if mode in ("right", "rows") and aspect > WIDE_MAP:
        # A strip (a Pacific band, a polar belt) is too short for a vertical bar's title.
        mode = "below"
    if mode in ("right", "rows") and any(
        (asked[name].get("colorbar") or {}).get("orientation") == "h" for name in auto_of
    ):
        # A horizontal bar asked for in the spec gets room under the maps.
        mode = "below"
    layout, domains = map_layout(
        rows,
        cols,
        aspect,
        titled=title_lines(user_layout.get("title")),
        panel_titles=any(panel_titles),
        right=(bool(auto_of) and mode in ("right", "rows")) or bool(placed - placed_h),
        below=(bool(auto_of) and mode in ("columns", "below")) or bool(placed_h),
        user={**user_layout, "grid": grid_in},
    )
    slots = place_colorbars(mode, auto_of, domains)
    # No-data cells show the plot background (the theme's maps style): a neutral grey,
    # not the template's tint, darker than a diverging zero or a dry class, so a missing
    # cell never reads as zero.
    layout.update(copy.deepcopy(ctx.theme["styles"]["maps"]))
    layout["annotations"] = []
    for p in range(n):
        s = axis_suffix(p)
        if extents[p] is None:
            # An empty cell keeps a hidden axis so annotations can still target it,
            # painted with the page colour so the template's panel background
            # does not show as a blank box.
            d = domains[p]
            layout[f"xaxis{s}"] = {"visible": False, "domain": [d[0], d[1]], "anchor": f"y{s}"}
            layout[f"yaxis{s}"] = {"visible": False, "domain": [d[2], d[3]], "anchor": f"x{s}"}
            layout.setdefault("shapes", []).append(
                {
                    "type": "rect",
                    "xref": f"x{s} domain",
                    "yref": f"y{s} domain",
                    "x0": 0,
                    "x1": 1,
                    "y0": 0,
                    "y1": 1,
                    "fillcolor": layout_value(user_layout, "paper_bgcolor") or "white",
                    "opacity": 1,
                    "line": {"width": 0},
                    "layer": "above",
                }
            )
            continue
        layout[f"xaxis{s}"], layout[f"yaxis{s}"] = map_axes(p, extents[p], domains[p])
        if panel_titles[p]:
            layout["annotations"].append(panel_title_annotation(p, panel_titles[p]))
    for name, members in axis_members.items():
        contours = [tr for _, b, trs in members for tr in trs if tr.get("type") == "contour"]
        for tr in contours:
            fill_defaults(tr, {"contours": {"coloring": "fill"}})
        axis = _resolve_coloraxis(
            name, members, user_layout.get(name), ctx, contour_traces=contours
        )
        if name in slots:
            fill_defaults(axis["colorbar"], slots[name])
            bar = axis["colorbar"]
            user_bar = (user_layout.get(name) or {}).get("colorbar") or {}
            if bar.get("orientation") == "h" and bar.get("ticktext") and "ticktext" not in user_bar:
                # Class edges crowd a short horizontal bar: label every k-th edge.
                room = bar.get("len", 1) * layout.get("width", 800) / len(bar["ticktext"])
                widest = max(len(t) for t in bar["ticktext"]) * 7 + 8
                k = math.ceil(widest / room)
                if k > 1:
                    bar["ticktext"] = [
                        t if i % k == 0 else "" for i, t in enumerate(bar["ticktext"])
                    ]
        elif name not in placed:
            axis["showscale"] = False
        layout[name] = deep_merge(axis, lifted_of[name])

    # Overlays, then assemble each panel's traces in draw order.
    settings = overlay_settings(lmeta.get("overlays"))
    if outlines and settings.get("admin1") is None:
        # The outline file is the boundary to show; Natural Earth's would double it.
        settings["admin1"] = False
    data = []
    for p in range(n):
        s = axis_suffix(p)
        if extents[p] is None:
            # plotly.js only builds axes a trace uses; keep the empty cell's axis alive.
            data.append(
                {
                    "type": "scatter",
                    "x": [None],
                    "y": [None],
                    "xaxis": f"x{s}",
                    "yaxis": f"y{s}",
                    "hoverinfo": "skip",
                    "showlegend": False,
                    "uid": f"empty-p{p + 1}",
                }
            )
            continue
        # A quarter of a panel pixel, in degrees: finer detail never shows.
        panel_px = (domains[p][1] - domains[p][0]) * (
            layout["width"] - layout["margin"]["l"] - layout["margin"]["r"]
        )
        tolerance = 0.25 * (extents[p][1] - extents[p][0]) / max(panel_px, 1.0)
        for name, geoms in overlay_geoms(extents[p], settings):
            xs, ys = geoms_to_xy(geoms, rings_only=name == "lakes", tolerance=tolerance)
            style = copy.deepcopy(ctx.theme["overlays"][name])
            if isinstance(settings.get(name), dict):
                style = deep_merge(style, settings[name])
            tr = {
                "type": "scatter",
                "mode": "lines",
                "x": xs,
                "y": ys,
                "hoverinfo": "skip",
                "showlegend": False,
                "uid": f"overlay-{name}-{p + 1}",
                **style,
            }
            panel_traces[p].append((_MAP_RANK["overlay"], tr, None, None))
        for _, tr, _, _ in sorted(panel_traces[p], key=lambda item: item[0]):
            tr["xaxis"], tr["yaxis"] = f"x{s}", f"y{s}"
            if not tr.get("name"):
                # An unnamed added trace (city markers, a box) would read "trace 6".
                tr.setdefault("showlegend", False)
            data.append(tr)
    return data, layout


def _key(value):
    arr = np.asarray(value)
    if arr.dtype.kind in "Mm":
        return int(
            arr.astype("datetime64[ns]" if arr.dtype.kind == "M" else "timedelta64[ns]").astype(
                "int64"
            )
        )
    return str(value)


# ----------------------------------------------------------------- chart figure


def _assemble_chart(items, user_layout):
    data = []
    layout = {}
    axis_titles: dict[str, dict] = {}
    for i, spec_trace, bound in items:
        uid = spec_trace.get("uid") or f"trace{i}"
        xa, ya = spec_trace.get("xaxis") or "x", spec_trace.get("yaxis") or "y"
        if bound is None:
            tr = copy.deepcopy(spec_trace)
            tr["uid"] = safe_uid(uid)
            data.append(tr)
            continue
        for j, gen in enumerate(bound.panels[0].traces):
            tr = _concrete(spec_trace, gen, uid if j == 0 or "uid_suffix" in gen else f"{uid}-{j}")
            if tr.get("type") != "barpolar":
                tr["xaxis"], tr["yaxis"] = xa, ya
            data.append(tr)
        layout = deep_merge(layout, bound.layout)
        xs, ys = xa[1:], ya[1:]
        if bound.axis_titles.get("x") is not None:
            axis_titles.setdefault(f"xaxis{xs}", {"title": {"text": bound.axis_titles["x"]}})
        if bound.axis_titles.get("y") is not None:
            axis_titles.setdefault(f"yaxis{ys}", {"title": {"text": bound.axis_titles["y"]}})
        if bound.axis_titles.get("y_nonnegative"):
            axis_titles.setdefault(f"yaxis{ys}", {}).setdefault("rangemode", "nonnegative")
        if bound.x_tickformat:
            # Seasonal overlays sit on a stand-in year; show day and month only.
            axis = axis_titles.setdefault(f"xaxis{xs}", {})
            axis.setdefault("tickformat", bound.x_tickformat)
            axis.setdefault("hoverformat", bound.x_tickformat)
        if bound.title:
            layout.setdefault("title", {"text": bound.title})
    layout.update(axis_titles)
    # Room above the plot only for what is there: a title, a legend row.
    lines = title_lines(user_layout.get("title") or layout.get("title"))
    named = [tr for tr in data if tr.get("name") and tr.get("showlegend") is not False]
    top = 20 if not lines else 60 + 24 * (lines - 1)
    # Legend order follows the inputs even with filled bands (Plotly reverses those).
    layout["legend"] = {**(layout.get("legend") or {}), "traceorder": "normal"}
    wants_legend = "showlegend" in user_layout or any(tr.get("showlegend") for tr in data)
    if len(named) < 2 and not wants_legend:
        layout["showlegend"] = False
    elif not any(tr.get("type") == "barpolar" for tr in data):
        layout["legend"].update(orientation="h", x=0, xanchor="left", y=1.02, yanchor="bottom")
        # One row of room per row the legend wraps to, so it never runs into the title.
        plot_px = (user_layout.get("width") or 700) - 160
        entries_px = sum(7 * len(str(tr["name"])) + 45 for tr in named)
        top += 24 * max(1, math.ceil(entries_px / max(plot_px, 200)))
    layout["margin"] = {"t": top}
    if lines:
        # Plotly centres the title in the top margin; a tall margin (legend rows) would push
        # it down into the legend. Pin it to the top instead (a spec title.y still wins).
        layout["title"] = {
            **(layout.get("title") or {}),
            "yref": "container",
            "y": 1,
            "yanchor": "top",
            "pad": {"t": 12},
        }
    for name, axis in user_layout.items():
        if name.startswith("yaxis") and isinstance(axis, dict) and axis.get("overlaying"):
            # Plotly syncs an overlaid axis to the left grid (ticks at 1.01, 2.02, …);
            # give it round ticks of its own and no second grid.
            layout[name] = {"tickmode": "auto", "showgrid": False, **(layout.get(name) or {})}
    return data, layout
