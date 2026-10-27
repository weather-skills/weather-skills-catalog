"""Stdout QA for a written plot PNG: pixel hash + whether plotted data is finite."""

from __future__ import annotations

import hashlib
import sys


def hash_png_pixels(path) -> str:
    """sha256 of RGB pixels. Provenance tEXt does not change this; a corner mark does."""
    from PIL import Image

    with Image.open(path) as img:
        payload = img.convert("RGB").tobytes()
    return hashlib.sha256(payload).hexdigest()


def _iter_datasets(datasets):
    if datasets is None:
        return
    items = datasets.values() if isinstance(datasets, dict) else datasets
    if hasattr(datasets, "data_vars") and not isinstance(datasets, dict):
        items = (datasets,)
    for ds in items:
        if ds is None:
            continue
        if hasattr(ds, "data_vars"):
            yield ds
        inner = getattr(ds, "datasets", None)
        if inner:
            yield from _iter_datasets(inner)
        one = getattr(ds, "ds", None)
        if one is not None and one is not ds:
            yield from _iter_datasets(one)


def _numeric_values(da):
    import numpy as np

    if hasattr(da, "pint") and getattr(da.pint, "units", None) is not None:
        da = da.pint.dequantify()
    values = np.asarray(getattr(da, "values", da))
    if values.dtype.kind not in "biufc":
        return None
    return values


def data_var_counts(datasets) -> list[tuple[str, int, int]]:
    """``(name, n_finite, n)`` for each numeric data variable."""
    import numpy as np

    rows = []
    seen: set[int] = set()
    for ds in _iter_datasets(datasets):
        key = id(ds)
        if key in seen:
            continue
        seen.add(key)
        for name, da in ds.data_vars.items():
            values = _numeric_values(da)
            if values is None:
                continue
            n = int(values.size)
            n_finite = int(np.isfinite(values).sum()) if n else 0
            rows.append((str(name), n_finite, n))
    return rows


def data_status(datasets) -> tuple[str, str]:
    """Return ``(status, detail)``.

    ``status`` is ``not null`` when any numeric data variable has a finite
    value, ``NULL`` when every such variable is all-NaN / empty, or
    ``not checked`` when there is nothing to scan.
    """
    rows = data_var_counts(datasets)
    if not rows:
        return "not checked", "no numeric data variables"
    detail = ", ".join(f"{name} {finite}/{n} finite" for name, finite, n in rows)
    if any(finite > 0 for _name, finite, _n in rows):
        return "not null", detail
    return "NULL", detail


def figure_counts(fig) -> list[tuple[str, int, int]]:
    """``(trace, n_finite, n)`` for what the figure draws, one row per input trace.

    Counts the plotted selection (a heatmap's ``z``, station colours, a
    series' ``y``), not the whole input file; base-map overlays are skipped.
    """
    import numpy as np

    rows: dict[str, list[int]] = {}
    for tr in fig.data:
        uid = str(tr.uid or "")
        if uid.startswith(("overlay-", "empty-")) or getattr(tr, "hoverinfo", None) == "skip":
            continue
        if getattr(tr, "z", None) is not None:
            raw = tr.z
        elif getattr(getattr(tr, "marker", None), "color", None) is not None and not isinstance(
            tr.marker.color, str
        ):
            raw = tr.marker.color
        elif getattr(tr, "y", None) is not None:
            raw = tr.y
            # Names on y (a dot chart by county): the values are on x.
            if getattr(tr, "x", None) is not None and np.asarray(raw).dtype.kind in "OUS":
                raw = tr.x
        elif getattr(tr, "r", None) is not None:
            raw = tr.r
        else:
            continue
        try:
            values = np.asarray(raw, dtype=float).ravel()
        except (TypeError, ValueError):
            continue
        # One row per input: 0, 1, … or its NAME (not 0-3 or 0-1-station_id-TA00057).
        name = uid.split("-")[0] or "trace"
        count = rows.setdefault(name, [0, 0])
        count[0] += int(np.isfinite(values).sum())
        count[1] += values.size
    return [(name, f, n) for name, (f, n) in rows.items()]


def report_figure(path, datasets=None, *, file=None, fig=None) -> str:
    """Print ``plot hash`` and ``data:`` lines. Returns the pixel hash."""
    dest = sys.stdout if file is None else file
    digest = hash_png_pixels(path)
    rows = figure_counts(fig) if fig is not None else []
    if rows:
        detail = ", ".join(f"{name} {finite}/{n} finite" for name, finite, n in rows)
        status = "not null" if any(finite for _, finite, _ in rows) else "NULL"
    else:
        status, detail = data_status(datasets)
    print(f"plot hash: {digest}", file=dest)
    print(f"data: {status} ({detail})", file=dest)
    if status == "NULL":
        print(
            "data is all NaN — inspect-zarr the input before treating this figure as valid",
            file=dest,
        )
    return digest
