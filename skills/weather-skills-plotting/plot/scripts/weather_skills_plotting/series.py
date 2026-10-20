"""Several 1-D series on one shared time axis: labels, inherited settings, warnings."""

from __future__ import annotations

import copy
from pathlib import Path

from weather_skills_core.cf import auto_variable
from weather_skills_core.units import units_equal, variable_units

from weather_skills_plotting.cli import input_path, warn
from weather_skills_plotting.labels import dataset_display_label
from weather_skills_plotting.layout import axis_suffix

MAX_INPUTS = 26
# meta keys a later trace takes from data[0] when it leaves them unset.
INHERITED_META = ("along", "along_color", "band", "members", "align")
INHERITED_SOURCE = ("variable",)


def _size1(ds, *names):
    for name in names:
        if name in ds.coords and ds[name].size == 1:
            text = str(ds[name].values.reshape(-1)[0]).strip()
            if text:
                return text
    return None


def trace_label(ds, idx: int, key: str | None = None) -> str:
    """Legend label: the -i NAME, station id (and name), file stem, else the display label."""
    if key is not None and not str(key).isdigit():
        return str(key)
    station = _size1(ds, "station_id", "point_id")
    if station:
        name = _size1(ds, "name")
        return f"{station} {name}" if name and name.casefold() != station.casefold() else station
    path = input_path(ds)
    if path:
        return Path(path).stem
    return dataset_display_label(ds, f"input {idx + 1}")


def inherit_from_first(spec: dict, datasets: dict | None = None) -> dict:
    """Series settings on data[0] apply to every series that does not set its own.

    ``along`` (and its band and colours) passes on only to an input that has
    that dim: an ensemble's spread does not apply to a climatology line.
    """
    data = spec.get("data") or []
    if len(data) < 2:
        return spec
    first = data[0].get("meta") or {}
    for trace in data[1:]:
        meta = trace.setdefault("meta", {})
        source = meta.get("source") or {}
        ds = (datasets or {}).get(str(source.get("input")))
        along = first.get("along")
        # The dims of the variable this trace draws, not of the whole file.
        var = source.get("variable") or (first.get("source") or {}).get("variable")
        dims = ds[var].dims if ds is not None and var in ds else getattr(ds, "dims", ())
        lacks_along = along and ds is not None and along not in dims
        for key in INHERITED_META:
            if lacks_along and key in ("along", "along_color", "band", "members"):
                continue
            if key in first and key not in meta:
                meta[key] = copy.deepcopy(first[key])
        source = meta.setdefault("source", {})
        for key in INHERITED_SOURCE:
            if key in (first.get("source") or {}) and key not in source:
                source[key] = copy.deepcopy(first["source"][key])
    return spec


def _by_yaxis(spec: dict, datasets: dict) -> dict:
    """``{y-axis: [(uid, input key, ds, variable), …]}`` for the series each axis draws.

    Mirrors the chart layout: with ``layout.grid`` and no trace axes set, the
    k-th trace gets the k-th panel's y-axis.
    """
    grid = (spec.get("layout") or {}).get("grid") or {}
    cells = (grid.get("rows") or 1) * (grid.get("columns") or 1)
    data = spec.get("data") or []
    explicit = any(t.get("xaxis") or t.get("yaxis") for t in data)
    by_axis = {}
    for k, trace in enumerate(data):
        meta = trace.get("meta") or {}
        source = meta.get("source") or {}
        key = str(source.get("input"))
        ds = datasets.get(key)
        if ds is None:
            continue
        var = meta.get("y") or source.get("variable") or auto_variable(ds)
        axis = trace.get("yaxis") or "y"
        if cells > 1 and not explicit and k < cells:
            axis = f"y{axis_suffix(k)}"
        by_axis.setdefault(axis, []).append((trace.get("uid") or key, key, ds, var))
    return by_axis


def warn_disjoint_dates(spec: dict, datasets: dict) -> None:
    """Overlaid series whose dates never overlap are rarely what was meant; say so."""
    spans = {}
    for trace in spec.get("data") or []:
        if (trace.get("meta") or {}).get("align"):
            continue  # seasonal overlays put different years on one axis on purpose
        key = str(((trace.get("meta") or {}).get("source") or {}).get("input"))
        ds = datasets.get(key)
        if ds is None or "time" not in ds.coords or ds["time"].ndim != 1:
            continue
        times = ds["time"].values
        if times.dtype.kind == "M" and times.size:
            spans[trace.get("uid") or key] = (times.min(), times.max())
    items = list(spans.items())
    for i, (a, (a0, a1)) in enumerate(items):
        for b, (b0, b1) in items[i + 1 :]:
            if a1 < b0 or b1 < a0:
                warn(
                    f"series {a} ({str(a0)[:10]}–{str(a1)[:10]}) and {b} "
                    f"({str(b0)[:10]}–{str(b1)[:10]}) share no dates"
                )


def warn_mixed_units(spec: dict, datasets: dict) -> None:
    """Series on one y-axis in different units are drawn on one scale; say so."""
    for axis, entries in _by_yaxis(spec, datasets).items():
        items = [
            (uid, variable_units(ds[var]) if var in ds else None) for uid, _, ds, var in entries
        ]
        unknown = [str(uid) for uid, u in items if not u]
        items = [(uid, u) for uid, u in items if u]
        if items and unknown:
            warn(
                f"series {', '.join(unknown)} on {axis} have no units attr but share an axis "
                f"labelled in {items[0][1]!r}; check they are the same quantity"
            )
        if any(not units_equal(items[0][1], u) for _, u in items[1:]):
            detail = ", ".join(f"{uid} units={u!r}" for uid, u in items)
            warn(
                f"series on {axis} have different units ({detail}) but share one y-axis. "
                'Give each its own panel with layout.grid {"rows": N, "columns": 1, '
                '"pattern": "coupled"}, or a second axis with yaxis "y2"'
            )
