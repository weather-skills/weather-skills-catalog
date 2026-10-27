"""Several 1-D series on one shared time axis: labels and warnings."""

from __future__ import annotations

from pathlib import Path

from weather_skills_core.cf import auto_variable
from weather_skills_core.units import units_equal, variable_units

from weather_skills_plotting.cli import input_path, warn
from weather_skills_plotting.labels import dataset_display_label

MAX_INPUTS = 26


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


def _by_yaxis(spec: dict, datasets: dict) -> dict:
    """``{y-axis: [(uid, input key, ds, variable), …]}`` for the series each axis draws."""
    by_axis = {}
    for trace in spec.get("data") or []:
        meta = trace.get("meta") or {}
        source = meta.get("source") or {}
        key = str(source.get("input"))
        ds = datasets.get(key)
        if ds is None:
            continue
        var = meta.get("y") or source.get("variable") or auto_variable(ds)
        axis = trace.get("yaxis") or "y"
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
