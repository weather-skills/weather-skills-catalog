"""Palette logic and its Plotly form; the color tables themselves live in the theme.

Every fixed table (CHC precip classes and masters, SPI, verify, wind rose) is
in ``themes/default.json``. A class palette is ``{colors, bounds}`` with
``colors`` packed as under + classes + over. This module crops the precip
windows (``ppt_daily`` … ``ppt_season``, ``ppt_anom_*``) from the theme's
``ppt_master`` / ``ppt_anom_master``, so a theme that recolors a master
recolors every window. ``discretize`` turns a field into class indices plus a
stepped Plotly colorscale so uneven bounds (0, 1, 2, 5, 10 … mm) get equal
colorbar slots, which a linear Plotly colorscale cannot do on raw values.
"""

from __future__ import annotations

import numpy as np
from weather_skills_core.errors import UsageError
from weather_skills_core.units import classify_variable, ureg, variable_units

from weather_skills_plotting.kinds import aggregation_days, default_kind

# Totals windows: the master's classes up to this many mm. An anomaly window
# keeps the anomaly master's classes within ± the same amount.
PRECIP_WINDOW_VMAX = {
    "ppt_daily": 50.0,
    "ppt_week": 200.0,
    "ppt_month": 400.0,
    "ppt_season": 1000.0,
}


PRECIP_WINDOW_ORDER = tuple(PRECIP_WINDOW_VMAX)


PRECIP_ANOMALY_WINDOW_ORDER = tuple(n.replace("ppt_", "ppt_anom_", 1) for n in PRECIP_WINDOW_ORDER)


def precip_window_name(days: float | None) -> str:
    """Pick a nested precip colorbar window from ``aggregation_period`` days."""
    if days is None:
        return "ppt_week"
    if days < 2:
        return "ppt_daily"
    if days <= 10:
        # A dekad is the week window, as chc-plot-theme picks it.
        return "ppt_week"
    if days < 40:
        return "ppt_month"
    return "ppt_season"


# Palette names that follow the field's ``aggregation_period`` like the
# automatic precip default, but skip detection (so an all-positive anomaly
# still gets the diverging scale). Value: whether it is the anomaly scale.
DEFAULT_PRECIP_PALETTES = {"default_precip": False, "default_precip_anom": True}


def resolve_default_precip(value, das, palettes: dict):
    """``default_precip`` / ``default_precip_anom`` → the window covering every field in ``das``.

    The window (``ppt_daily`` … ``ppt_season``, or ``ppt_anom_*``) follows each
    field's ``aggregation_period``. A rate with no period is an amount per its
    unit's time step: mm/day is a daily amount, mm/h an hourly one (still the
    daily window). Any other value, or a theme palette of that name, comes back as is.
    """
    key = value.lower() if isinstance(value, str) else None
    if key not in DEFAULT_PRECIP_PALETTES or any(k.lower() == key for k in palettes):
        return value
    days = []
    for d in das:
        if d is None:
            continue
        step = None
        try:
            step = (
                (ureg.Quantity(1, "mm") / ureg.Quantity(1, variable_units(d))).to("day").magnitude
            )
        except Exception:  # noqa: BLE001 — not a length-per-time rate
            pass
        days.append(aggregation_days(d) or step)
    window = max((precip_window_name(d) for d in days or [None]), key=PRECIP_WINDOW_ORDER.index)
    return window.replace("ppt_", "ppt_anom_", 1) if DEFAULT_PRECIP_PALETTES[key] else window


def precip_window_palette(name: str, palettes: dict) -> dict:
    """Crop the theme's master to the window ``name`` (``ppt_week``, ``ppt_anom_month`` …).

    Totals keep ``ppt_master``'s classes up to the window's mm; anomalies keep
    ``ppt_anom_master``'s within ± that (month 400 mm → ±300 mm). Same color is
    always the same millimetres. Under and over are the master's neighbouring
    classes, or its own under / over at the ends.
    """
    anomaly = name.startswith("ppt_anom_")
    totals = name.replace("ppt_anom_", "ppt_", 1)
    if totals not in PRECIP_WINDOW_VMAX:
        raise UsageError(f"unknown precip window {name!r}")
    master_name = "ppt_anom_master" if anomaly else "ppt_master"
    master = parse_palette(palettes[master_name], loc=f"palettes.{master_name}", registry={})
    under, classes, over = _class_slots(master) if master.get("bounds") else (None, [], None)
    if under is None or over is None:
        raise UsageError(
            f"palettes.{master_name} needs bounds and an under and over color "
            "(colors packed as under + one per class + over)"
        )
    colors, master_bounds = [under, *classes, over], master["bounds"]
    vmax = PRECIP_WINDOW_VMAX[totals]
    bounds = [b for b in master_bounds if abs(b) <= vmax]
    if len(bounds) < 2:
        raise UsageError(f"precip window {name!r} has no classes at ±{vmax:g} mm")
    i0, i1 = master_bounds.index(bounds[0]), master_bounds.index(bounds[-1])
    return {"colors": list(colors[i0 : i1 + 2]), "bounds": list(bounds)}


def along_dim(da, along: str | None) -> str | None:
    """Resolve ``along`` to a dim on ``da``, including ontology aliases (member/number)."""
    from weather_skills_core.standard_dataset import ALIASES, names_for

    if not along:
        return None
    if along in da.dims:
        return along
    preferred = ALIASES.get(along, along)
    return next((name for name in names_for(preferred) if name in da.dims), None)


ALONG_COLOR_SAME = "same"


ALONG_COLOR_CYCLE = "cycle"


_ALONG_COLOR_ALIASES = {
    "same": ALONG_COLOR_SAME,
    "shared": ALONG_COLOR_SAME,
    "cycle": ALONG_COLOR_CYCLE,
    "distinct": ALONG_COLOR_CYCLE,
}


def parse_along_color(value) -> str:
    """``traces[].along_color``: ``same`` (default) or ``cycle``."""
    if value is None or value is False or value == "":
        return ALONG_COLOR_SAME
    raw = str(value).strip().lower().replace("_", "-")
    if raw in _ALONG_COLOR_ALIASES:
        return _ALONG_COLOR_ALIASES[raw]
    raise UsageError(
        "traces[].along_color must be 'same' (one color for every along member) "
        f"or 'cycle' (a distinct color per value); got {value!r}."
    )


def along_member_label(value) -> str:
    """Short legend label for one coordinate along ``traces[].along``."""

    if value is None:
        return ""
    arr = np.asarray(value)
    item = arr.reshape(-1)[0] if arr.size else value
    kind = np.asarray(item).dtype.kind
    if kind == "M" or isinstance(item, np.datetime64):
        try:
            return str(np.datetime_as_string(np.asarray(item, dtype="datetime64[ns]"), unit="D"))
        except (TypeError, ValueError):
            return str(item).strip()
    if isinstance(item, (np.integer, int)) and not isinstance(item, bool):
        return str(int(item))
    if isinstance(item, (np.floating, float)):
        num = float(item)
        return str(int(num)) if num.is_integer() else str(num)
    return str(item).strip()


def parse_band(value) -> tuple[float, float] | None:
    """Parse ``--band`` / spec ``band`` as two percentiles, default ``10,90``."""
    if value is None or value is False or value == "":
        return None
    if value is True:
        return (10.0, 90.0)
    if isinstance(value, dict):
        q = value.get("q") or value.get("percentiles")
        if q is None:
            lo = value.get("low", 10)
            hi = value.get("high", 90)
            value = (lo, hi)
        else:
            value = q
    if isinstance(value, (list, tuple)):
        if len(value) != 2:
            raise UsageError("traces[].band must be two percentiles, e.g. [10, 90]")
        lo, hi = float(value[0]), float(value[1])
    else:
        raw = str(value).strip().lower().replace("q", "")
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        if len(parts) != 2:
            raise UsageError("traces[].band must be two percentiles, e.g. [10, 90]")
        lo, hi = float(parts[0]), float(parts[1])
    if not 0 <= lo < hi <= 100:
        raise UsageError(
            f"traces[].band percentiles must satisfy 0 ≤ low < high ≤ 100; got {lo},{hi}"
        )
    return (lo, hi)


def check_color_bound_counts(colors, bounds, under=None, over=None, *, loc="palette"):
    """``colors`` is one per class, or under+classes+over packed in one list."""
    n_bins = len(bounds) - 1
    if n_bins < 1:
        raise UsageError(f"{loc}.bounds needs at least two strictly increasing stops")
    n_colors = len(colors)
    if under is not None or over is not None:
        if n_colors != n_bins:
            raise UsageError(
                f"{loc}.colors must have {n_bins} class colors when under/over are set "
                f"(one per interval between {len(bounds)} bounds); got {n_colors}"
            )
        return
    if n_colors == n_bins or n_colors == n_bins + 2:
        return
    raise UsageError(
        f"{loc}.colors must have {n_bins} class colors, or {n_bins + 2} "
        f"(under + classes + over); got {n_colors} colors and {len(bounds)} bounds"
    )


PALETTE_KEYS = frozenset({"colors", "bounds", "under", "over", "labels"})


def builtin_palettes(palettes: dict) -> dict:
    """The precip windows, cropped from the masters in the theme's ``palettes``."""
    return {
        name: precip_window_palette(name, palettes)
        for name in (*PRECIP_WINDOW_ORDER, *PRECIP_ANOMALY_WINDOW_ORDER)
    }


def parse_palette(value, *, registry: dict, loc="meta.palette") -> dict:
    """A palette name, a list of colors, or ``{colors, bounds, under, over}``.

    ``registry`` is the theme's palettes (``load_theme``) that names resolve in.
    """
    palettes = registry
    if isinstance(value, str):
        key = next((k for k in palettes if k.lower() == value.lower()), None)
        if key is None:
            raise UsageError(
                f"{loc} {value!r} is not a known palette; known: "
                f"{', '.join(sorted({*palettes, *DEFAULT_PRECIP_PALETTES}))}. "
                "For a Plotly colorscale name (Viridis, RdBu, YlGn, …) set the trace's "
                "colorscale instead; a palette from a theme file needs that --theme-file"
            )
        return {"name": key, **parse_palette(palettes[key], loc=f"{loc} {key!r}", registry={})}
    if isinstance(value, list):
        value = {"colors": value}
    if not isinstance(value, dict):
        raise UsageError(f"{loc} must be a palette name, a color list, or an object")
    unknown = sorted(set(value) - PALETTE_KEYS - {"name"})
    if unknown:
        raise UsageError(
            f"{loc}: unknown keys {', '.join(unknown)}; allowed: {', '.join(sorted(PALETTE_KEYS))}"
        )
    colors = value.get("colors")
    if not isinstance(colors, list) or len(colors) < 2:
        raise UsageError(f"{loc}.colors must list at least two colors")
    out = {"colors": [str(c) for c in colors]}
    bounds = value.get("bounds")
    if bounds is not None:
        try:
            nums = [float(b) for b in bounds]
        except (TypeError, ValueError) as exc:
            raise UsageError(f"{loc}.bounds must be numbers") from exc
        if len(nums) < 2 or any(a >= b for a, b in zip(nums, nums[1:], strict=False)):
            raise UsageError(f"{loc}.bounds needs at least two strictly increasing numbers")
        check_color_bound_counts(
            out["colors"], nums, value.get("under"), value.get("over"), loc=loc
        )
        out["bounds"] = nums
    for key in ("under", "over"):
        if value.get(key) is not None:
            out[key] = str(value[key])
    labels = value.get("labels")
    if labels is not None:
        n_classes = len(out.get("bounds") or []) - 1
        if not isinstance(labels, list) or len(labels) != n_classes:
            raise UsageError(f"{loc}.labels needs one label per class ({n_classes}) and bounds")
        out["labels"] = [str(label) for label in labels]
    if value.get("name"):
        out["name"] = str(value["name"])
    return out


def flag_palette(da, colorway: list, hits: list) -> dict | None:
    """One class per CF ``flag_values`` entry, labelled with ``flag_meanings``.

    Three flags (verify's disagree / below / hit) take ``hits``, the theme's
    red / white / green; any other count takes ``colorway`` in order.
    """
    raw = da.attrs.get("flag_values")
    if raw is None:
        return None
    values = np.asarray(raw, dtype=float).ravel()
    if values.size < 2:
        return None
    order = np.argsort(values)
    values = values[order]
    meanings = str(da.attrs.get("flag_meanings") or "").split()
    if len(meanings) == values.size:
        labels = [meanings[i].replace("_", " ") for i in order]
    else:
        labels = [f"{v:g}" for v in values]
    colors = (
        list(hits)
        if values.size == 3
        else [colorway[i % len(colorway)] for i in range(values.size)]
    )
    mids = ((values[:-1] + values[1:]) / 2).tolist()
    bounds = [values[0] - 0.5, *mids, values[-1] + 0.5]
    return {"name": "flags", "colors": colors, "bounds": bounds, "labels": labels}


def default_palette(da, *others, theme: dict, diverging: bool = False) -> dict:
    """The palette ``da`` gets when the spec names none.

    CF flags get one class per flag. Every other field gets the theme's
    ``defaults`` entry for its kind (``default_kind``). ``default_precip`` /
    ``default_precip_anom`` pick the window that covers the period of ``da``
    and ``others`` (the fields sharing its colorbar). A Plotly colorscale name
    comes back as ``{"name", "colorscale"}``.
    """
    colorway = theme["template"]["layout"]["colorway"]
    hits = parse_palette(theme["palettes"]["hits"], loc="palettes.hits", registry={})["colors"]
    flags = flag_palette(da, colorway, hits) if da is not None else None
    if flags is not None:
        return flags
    kind = default_kind(da, diverging=diverging)
    if kind == "precip_anomaly" and theme["defaults"][kind] == "default_precip_anom":
        rate = classify_variable(
            da.name or "", units=variable_units(da), standard_name=da.attrs.get("standard_name")
        )
        if rate == "precip" and not aggregation_days(da):
            # A change in rate (a CMIP6 future-minus-historical, mm/day) is not a period
            # total: the mm anomaly classes would leave it all in the white ±10 class.
            return {"name": "BrBG", "colorscale": "BrBG"}
    value = theme["defaults"][kind]
    palettes = theme["palettes"]
    value = resolve_default_precip(value, (da, *others), palettes)
    if isinstance(value, str) and not any(k.lower() == value.lower() for k in palettes):
        return {"name": value, "colorscale": value}
    return parse_palette(value, registry=palettes)


def continuous_colorscale(colors) -> list:
    """Evenly spaced Plotly colorscale through ``colors``."""
    colors = list(colors)
    n = len(colors) - 1
    return [[i / n, c] for i, c in enumerate(colors)]


def _class_slots(palette: dict):
    """``(under, classes, over)`` from packed or explicit under/over colors."""
    colors, bounds = palette["colors"], palette["bounds"]
    n_classes = len(bounds) - 1
    if len(colors) == n_classes + 2 and "under" not in palette and "over" not in palette:
        return colors[0], colors[1:-1], colors[-1]
    return palette.get("under"), colors[:n_classes], palette.get("over")


def discretize(values, palette: dict):
    """Map values onto class slots for a bounded palette.

    Returns ``(slots, scale)`` where ``slots`` is a float array of slot indices
    (NaN stays NaN) and ``scale`` holds ``colorscale``, ``cmin``, ``cmax`` and
    the colorbar ``tickvals`` / ``ticktext`` at the class edges.
    """
    bounds = palette["bounds"]
    under, classes, over = _class_slots(palette)
    m = len(bounds)
    offset = 1 if under else 0
    z = np.asarray(values, dtype=float)
    k = np.digitize(z, bounds)
    slots = offset + np.clip(k, 1, m - 1) - 1.0
    if under:
        slots = np.where(k == 0, 0.0, slots)
    if over:
        slots = np.where(k == m, offset + m - 1.0, slots)
    slots = np.where(np.isfinite(z), slots, np.nan)
    colors = ([under] if under else []) + list(classes) + ([over] if over else [])
    n = len(colors)
    colorscale = []
    for i, color in enumerate(colors):
        colorscale += [[i / n, color], [(i + 1) / n, color]]
    scale = {
        "colorscale": colorscale,
        "cmin": -0.5,
        "cmax": n - 0.5,
        "tickvals": [offset + j - 0.5 for j in range(m)],
        "ticktext": [f"{b:g}" for b in bounds],
    }
    if palette.get("labels"):
        # Named classes (CF flags): label each class at its centre, not the edges.
        scale["tickvals"] = [offset + i for i in range(len(classes))]
        scale["ticktext"] = list(palette["labels"])
    return slots, scale
