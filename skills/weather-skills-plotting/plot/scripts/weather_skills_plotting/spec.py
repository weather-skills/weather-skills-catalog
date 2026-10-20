"""The plot spec: a standard Plotly figure (``{"data": [...], "layout": {...}}``).

Plotly validates every key except ``meta``, which Plotly accepts on any trace
and on ``layout`` without looking inside. The weather-skills bindings live
there: which Zarr and variable a trace reads (``trace.meta``), and figure-wide
map settings (``layout.meta``). This module checks those two blocks, merges a
``--spec`` onto the spec a skill builds from its files, and dumps it.
"""

from __future__ import annotations

import copy
import json
import re
import sys
from pathlib import Path

from weather_skills_core.errors import UsageError
from weather_skills_core.standard_utils import parse_bbox

from weather_skills_plotting.geodata import OVERLAY_NAMES

SPEC_VERSION = 3

TOP_KEYS = frozenset({"data", "layout"})

# How a trace's arrays are filled from a dataset. See ``bind.py``.
BINDS = frozenset(
    {"field", "speed", "arrows", "points", "series", "xy", "samples", "windrose", "geojson"}
)
# Bind used when ``meta.bind`` is unset, by Plotly trace type.
DEFAULT_BIND = {
    "heatmap": "field",
    "contour": "field",
    "scatter": "series",
    "scattergl": "series",
    "bar": "series",
    "box": "samples",
    "barpolar": "windrose",
}
MAP_BINDS = frozenset({"field", "speed", "arrows", "points", "geojson"})

TRACE_META_KEYS = frozenset(
    {
        "bind",
        "source",
        "x",
        "y",
        "facet",
        "along",
        "along_color",
        "band",
        "members",
        "align",
        "palette",
        "arrows",
    }
)
SOURCE_KEYS = frozenset(
    {"input", "variable", "isel", "sel", "u", "v", "geojson", "mask_geojson", "point"}
)
ARROW_KEYS = frozenset({"step", "scale"})
LAYOUT_META_KEYS = frozenset(
    {"version", "skill", "inputs", "geo", "overlays", "export", "animation"}
)
GEO_KEYS = frozenset({"bbox", "mask_geojson", "point"})
EXPORT_KEYS = frozenset({"scale"})
ANIMATION_KEYS = frozenset({"duration", "transition"})
# Layout lists that merge by ``name`` (Plotly's own item identity).
NAMED_LAYOUT_LISTS = ("annotations", "shapes", "images")

_PLACE_NAME_KEYS = frozenset({"region", "country", "place", "name", "county", "admin"})

SPEC_HELP_HINT = (
    "Run with --help for the meta keys and recipes; every other key is standard Plotly "
    "(https://plotly.com/python/reference/). To edit the spec a command builds, run it "
    "with --dump-spec - in place of --spec and pass the edited JSON back"
)


# --------------------------------------------------------------------- loading


def load_spec(value) -> dict:
    """A spec from a mapping, inline JSON, or a JSON file path."""
    if value is None:
        return {}
    if isinstance(value, dict):
        data = copy.deepcopy(value)
    else:
        raw = str(value).strip()
        if not raw:
            raise UsageError("plot spec is empty")
        if raw[:1] not in "{[":
            path = Path(raw)
            try:
                is_file = path.is_file()
            except OSError:
                is_file = False
            if not is_file:
                raise UsageError(
                    f"plot spec {raw!r} is not a file or a JSON object; pass inline JSON "
                    "or a plain file path (no @)"
                )
            raw = path.read_text(encoding="utf-8")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise UsageError(f"plot spec is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise UsageError("plot spec must be a JSON object with data and/or layout")
    check_shape(data)
    return data


def parse_plot_spec(value):
    """Argparse converter for ``--spec``."""
    import argparse

    try:
        return load_spec(value)
    except UsageError as exc:
        raise argparse.ArgumentTypeError(f"{exc}. {SPEC_HELP_HINT}") from None


def check_shape(data: dict) -> None:
    """Top level is ``data`` / ``layout`` only."""
    unknown = sorted(set(data) - TOP_KEYS)
    if unknown:
        raise UsageError(
            f"plot spec top-level key(s) {', '.join(unknown)} are not allowed; a spec is a "
            'Plotly figure {"data": [traces], "layout": {…}}'
        )
    if not isinstance(data.get("data", []), list):
        raise UsageError("plot spec data must be a list of trace objects")
    if not isinstance(data.get("layout", {}), dict):
        raise UsageError("plot spec layout must be an object")
    title = (data.get("layout") or {}).get("title")
    text = title.get("text") if isinstance(title, dict) else title
    if isinstance(text, str) and "<br" in text.lower():
        # Only a subtitle gets room above the plot; a <br> line runs into the legend or panels.
        raise UsageError(
            "plot spec layout.title.text has a <br>; put the second line in "
            'layout.title.subtitle: {"title": {"text": "…", "subtitle": {"text": "…"}}}'
        )
    for i, trace in enumerate(data.get("data") or []):
        if not isinstance(trace, dict):
            raise UsageError(f"plot spec data[{i}] must be an object")
        if "uid" in trace and not isinstance(trace["uid"], str):
            raise UsageError(f"plot spec data[{i}].uid must be a string")


# --------------------------------------------------------------------- merging


def deep_merge(base: dict, overlay: dict) -> dict:
    """New dict: nested dicts merge, anything else replaces."""
    out = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def fill_defaults(target: dict, defaults: dict) -> dict:
    """Set every key of ``defaults`` that ``target`` leaves unset (in place)."""
    for key, value in defaults.items():
        if value is None:
            continue
        current = target.get(key)
        if current is None:
            target[key] = copy.deepcopy(value)
        elif isinstance(current, dict) and isinstance(value, dict):
            fill_defaults(current, value)
    return target


def _merge_named(base: list, overlay: list, loc: str) -> list:
    """Items with a ``name`` replace-merge the same-named item; others append."""
    out = [copy.deepcopy(item) for item in base]
    for item in overlay:
        if not isinstance(item, dict):
            raise UsageError(f"plot spec {loc} entries must be objects")
        name = item.get("name")
        idx = next(
            (i for i, cur in enumerate(out) if name is not None and cur.get("name") == name),
            None,
        )
        if idx is None:
            out.append(copy.deepcopy(item))
        else:
            out[idx] = deep_merge(out[idx], item)
    return out


def merge_traces(base: list, overlay: list) -> list:
    """Overlay traces merge onto base traces by ``uid``, else by position.

    A ``uid`` the base does not have appends a new trace, so ``--spec`` can
    add plain Plotly traces (city markers, a box outline) next to the
    data-bound ones.
    """
    out = [copy.deepcopy(t) for t in base]
    used = set()
    for i, trace in enumerate(overlay):
        uid = trace.get("uid")
        if uid is not None:
            idx = next((j for j, t in enumerate(out) if t.get("uid") == uid), None)
            if idx is None:
                if not set(trace) & {"type", "meta", "x", "y", "z", "lat", "lon", "r", "theta"}:
                    # Only styling for a uid nothing has: a typo, not a new trace.
                    have = [str(t.get("uid")) for t in out if t.get("uid") is not None]
                    near = [h for h in have if h.lower() == str(uid).lower()]
                    raise UsageError(
                        f"--spec data[{i}] uid {uid!r} matches no trace (uids: "
                        f"{', '.join(have) or 'none'})"
                        + (f"; did you mean {near[0]!r}?" if near else "")
                        + "; a new trace needs a type"
                    )
                out.append(copy.deepcopy(trace))
                continue
        else:
            idx = i if i < len(out) else None
            if idx is None:
                have = ", ".join(str(t.get("uid")) for t in out) or "none"
                raise UsageError(
                    f"--spec data[{i}] has no uid and the figure has only {len(out)} trace(s) "
                    f"(uids: {have}); give a new trace a uid to add it"
                )
        used.add(idx)
        out[idx] = deep_merge(out[idx], trace)
    return out


def merge_spec(base: dict, overlay: dict | None) -> dict:
    """Deep-merge ``overlay`` onto ``base``; the overlay wins."""
    base = copy.deepcopy(base or {})
    if not overlay:
        return base
    out = {"data": merge_traces(base.get("data") or [], overlay.get("data") or [])}
    layout = dict(base.get("layout") or {})
    over_layout = dict(overlay.get("layout") or {})
    for key in NAMED_LAYOUT_LISTS:
        if key in over_layout:
            layout[key] = _merge_named(
                layout.get(key) or [], over_layout.pop(key) or [], f"layout.{key}"
            )
    out["layout"] = deep_merge(layout, over_layout)
    return out


# ------------------------------------------------------------------ validation


def _check_keys(obj, allowed, loc: str) -> None:
    if not isinstance(obj, dict):
        raise UsageError(f"plot spec {loc} must be an object")
    for key in obj:
        if key in allowed:
            continue
        hint = ""
        if "." in key:
            head, _, rest = key.partition(".")
            hint = f'; a dotted name is a nested path: write {{"{head}": {{"{rest}": …}}}}'
        elif loc.endswith("geo") and key in _PLACE_NAME_KEYS:
            hint = (
                "; geo takes coordinates only — run resolve-region for a named place and "
                "pass its bbox as layout.meta.geo.bbox or its polygon as mask_geojson"
            )
        raise UsageError(
            f"plot spec {loc}.{key} is not a known key; allowed here: "
            f"{', '.join(sorted(allowed))}{hint}"
        )


def _check_source(source, loc: str) -> None:
    _check_keys(source, SOURCE_KEYS, loc)
    for key in ("isel", "sel"):
        if source.get(key) is not None and not isinstance(source[key], dict):
            raise UsageError(f"plot spec {loc}.{key} must be an object of dim: value")
    point = source.get("point")
    if point is not None:
        if not isinstance(point, dict) or set(point) != {"lat", "lon"}:
            raise UsageError(f'plot spec {loc}.point must be {{"lat": …, "lon": …}}')


def check_trace_meta(meta, loc: str, palettes: dict) -> None:
    from weather_skills_plotting.palettes import (
        DEFAULT_PRECIP_PALETTES,
        parse_palette,
    )

    _check_keys(meta, TRACE_META_KEYS, loc)
    bind = meta.get("bind")
    if bind is not None and bind not in BINDS:
        raise UsageError(f"plot spec {loc}.bind {bind!r} must be one of {', '.join(sorted(BINDS))}")
    if meta.get("source") is not None:
        _check_source(meta["source"], f"{loc}.source")
    for key in ("x", "y"):
        if meta.get(key) is not None and not isinstance(meta[key], str):
            raise UsageError(f"plot spec {loc}.{key} must be a variable name")
    if (meta.get("x") is None) != (meta.get("y") is None):
        raise UsageError(f"plot spec {loc}: a scatter of two variables needs both x and y")
    if meta.get("arrows") is not None:
        _check_keys(meta["arrows"], ARROW_KEYS, f"{loc}.arrows")
    if meta.get("along_color") not in (None, "same", "cycle"):
        raise UsageError(f"plot spec {loc}.along_color must be 'same' or 'cycle'")
    band = meta.get("band")
    if band is not None and not (
        isinstance(band, list) and len(band) == 2 and all(isinstance(b, (int, float)) for b in band)
    ):
        raise UsageError(f"plot spec {loc}.band must be a percentile pair such as [10, 90]")
    if band is not None and not meta.get("along"):
        raise UsageError(
            f"plot spec {loc}.band needs {loc}.along (the dim to take percentiles over)"
        )
    if meta.get("members") not in (None, True, False):
        raise UsageError(f"plot spec {loc}.members must be true or false")
    if meta.get("align") not in (None, "dayofyear"):
        raise UsageError(f"plot spec {loc}.align must be 'dayofyear'")
    palette = meta.get("palette")
    if palette is not None and str(palette).lower() not in DEFAULT_PRECIP_PALETTES:
        # Names resolve against the theme in use.
        parse_palette(palette, loc=f"{loc}.palette", registry=palettes)


def check_layout_meta(meta) -> None:
    _check_keys(meta, LAYOUT_META_KEYS, "layout.meta")
    version = meta.get("version")
    if version is not None and version != SPEC_VERSION:
        raise UsageError(
            f"plot spec layout.meta.version {version!r} is not supported; expected {SPEC_VERSION}"
        )
    inputs = meta.get("inputs")
    if inputs is not None and not (
        isinstance(inputs, dict) and all(isinstance(v, str) for v in inputs.values())
    ):
        raise UsageError(
            'plot spec layout.meta.inputs must map input ids to paths: {"0": "x.zarr"}'
        )
    geo = meta.get("geo")
    if geo is not None:
        _check_keys(geo, GEO_KEYS, "layout.meta.geo")
        bbox = geo.get("bbox")
        if isinstance(bbox, str):
            # resolve-region prints "N/W/S/E"; take it as printed.
            parse_bbox(bbox)
        elif bbox is not None and not (isinstance(bbox, list) and len(bbox) == 4):
            raise UsageError('plot spec layout.meta.geo.bbox must be [N, W, S, E] or "N/W/S/E"')
        point = geo.get("point")
        if point is not None and (not isinstance(point, dict) or set(point) != {"lat", "lon"}):
            raise UsageError('plot spec layout.meta.geo.point must be {"lat": …, "lon": …}')
        if geo.get("mask_geojson") is not None and not isinstance(geo["mask_geojson"], str):
            raise UsageError(
                "plot spec layout.meta.geo.mask_geojson must be a GeoJSON file path; it only "
                "blanks cells outside the polygon. To draw the boundary add a trace with "
                'meta.bind "geojson"'
            )
    overlays = meta.get("overlays")
    if overlays is not None and not isinstance(overlays, bool):
        if not isinstance(overlays, dict):
            raise UsageError(
                "plot spec layout.meta.overlays must be true, false, or an object such as "
                '{"rivers": false, "borders": {"line": {"width": 2}}}'
            )
        for key, value in overlays.items():
            if key not in OVERLAY_NAMES:
                raise UsageError(
                    f"plot spec layout.meta.overlays.{key} is not a known overlay; "
                    f"allowed: {', '.join(OVERLAY_NAMES)}"
                )
            if not isinstance(value, (bool, dict)):
                raise UsageError(
                    f"plot spec layout.meta.overlays.{key} must be true, false, or a "
                    "Plotly scatter style object"
                )
    if meta.get("export") is not None:
        _check_keys(meta["export"], EXPORT_KEYS, "layout.meta.export")
    animation = meta.get("animation")
    if animation is not None:
        _check_keys(animation, ANIMATION_KEYS, "layout.meta.animation")
        for key, value in animation.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                raise UsageError(
                    f"plot spec layout.meta.animation.{key} must be milliseconds >= 0; "
                    f"got {value!r}"
                )


def check_meta(spec: dict, palettes: dict) -> None:
    """Check every ``meta`` block (Plotly does not look inside them)."""
    seen = set()
    for i, trace in enumerate(spec.get("data") or []):
        uid = trace.get("uid")
        if uid is not None:
            if uid in seen:
                raise UsageError(f"plot spec data[] uid {uid!r} appears more than once")
            seen.add(uid)
        if str(trace.get("type", "")).startswith(
            ("scattergeo", "scattermap", "choropleth", "densitymap")
        ):
            raise UsageError(
                f"plot spec data[{i}].type {trace['type']!r}: maps here are plain x/y axes "
                '(x = longitude, y = latitude). Use "type": "scatter" with x / y for markers '
                "and lines, and layout.shapes for boxes"
            )
        meta = trace.get("meta")
        if meta is not None:
            check_trace_meta(meta, f"data[{i}].meta", palettes)
    meta = (spec.get("layout") or {}).get("meta")
    if meta is not None:
        check_layout_meta(meta)


_VALID_PROP_RE = re.compile(r"^    ([A-Za-z_][A-Za-z0-9_]*)\s*$")


def plotly_error(exc: Exception, loc: str = "") -> UsageError:
    """Shorten a Plotly validation error: the bad key, a suggestion, the valid keys."""
    text = str(exc).strip()
    head, _, tail = text.partition("Valid properties:")
    names = [m.group(1) for line in tail.splitlines() if (m := _VALID_PROP_RE.match(line))]
    lines = [line for line in head.splitlines() if line.strip()]
    msg = "\n".join(lines[:12])
    if names:
        msg += f"\nAllowed here: {', '.join(names)}"
    elif len(lines) > 12:
        msg += "\n…"
    if "'colorbar'" in head and "Layout" in head:
        msg += (
            '\nA colorbar belongs to a color axis: {"layout": {"coloraxis": {"colorbar": {…}}}} '
            "(coloraxis2, … for a second bar)"
        )
    if "'bbox'" in head and "Geo" in head:
        msg += '\nThe map window is layout.meta.geo.bbox: {"layout": {"meta": {"geo": {"bbox": [N, W, S, E]}}}}'
    prefix = f"plot spec {loc}: " if loc else "plot spec: "
    return UsageError(
        prefix + msg + "\nKeys are standard Plotly: https://plotly.com/python/reference/"
    )


def validate(spec: dict, theme: dict | None = None) -> dict:
    """Check the meta blocks, then let Plotly validate everything else.

    ``theme`` is ``load_theme()``'s (read here when None): palette names resolve in
    its palettes, and its template becomes ``weather_skills``.
    """
    import plotly.graph_objects as go

    from weather_skills_plotting.layout import register_templates
    from weather_skills_plotting.themes import load_theme

    theme = theme or load_theme()
    check_shape(spec)
    check_meta(spec, theme["palettes"])
    # Our template names must exist before Plotly checks layout.template.
    register_templates(theme)
    try:
        go.Figure(spec)
    except ValueError as exc:
        raise plotly_error(exc) from None
    return spec


# --------------------------------------------------------------------- dumping


DUMP_SPEC_HELP = (
    "Print (bare or '-') or write (a path) the merged spec this command would draw, as a "
    "Plotly figure without data arrays, and skip drawing. Edit it and pass it back as --spec."
)
SPEC_ARGUMENT_HELP = (
    "Plotly figure JSON (inline or a path), deep-merged onto the figure built from the "
    "input files. data[] merges by uid (else position); layout.annotations / shapes merge "
    "by name. Dataset bindings live in each trace's meta. Example: "
    '{"data": [{"uid": "0", "type": "contour"}], "layout": {"title": {"text": "Week 1"}}}'
)


def dump_spec(spec: dict, dest) -> str:
    """Write ``spec`` as JSON to ``dest`` (``-`` is stdout)."""
    text = json.dumps(spec, indent=2, default=str) + "\n"
    if str(dest) == "-":
        sys.stdout.write(text)
    else:
        out = Path(dest)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"Wrote spec: {dest}", file=sys.stderr)
    return text


def dump_spec_dest(value):
    """``--dump-spec``: ``None`` (draw), ``"-"`` (stdout), or a path."""
    if value is None or value is False:
        return None
    if str(value).strip() == "":
        return "-"
    return value
