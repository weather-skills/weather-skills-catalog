"""Text a figure shows: ``14 Sept '26`` dates, panel titles, axis labels,
product names, variable names and unit spellings.

On-disk attrs stay CF (weather-skills-core); these only shape what a figure shows.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
from weather_skills_core.errors import UsageError
from weather_skills_core.provenance import HISTORY_ATTR, SOURCE_ATTR, origin_skill
from weather_skills_core.units import (
    DATA_INTERVAL_ATTR,
    PRECIP_AMOUNT_LONG_NAME,
    STANDARD,
    classify_variable,
    looks_like_rate_display_name,
    parse_aggregation_period,
    units_equal,
    ureg,
    variable_units,
)

from weather_skills_plotting.geodata import is_cftime_axis
from weather_skills_plotting.kinds import aggregation_days, is_precip_anomaly, is_statistic

# A variable described as covering the hours before its time stamp.
_TRAILING = re.compile(r"\b(last|previous|past) \d+ ?(h|hours?)\b", re.I)

# Sept not Sep — the usual meteorological short form.
_MONTHS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sept",
    "Oct",
    "Nov",
    "Dec",
)


def _ymd(value):
    """Return ``(year, month, day)`` from a datetime-like, or None."""
    if value is None:
        return None
    if hasattr(value, "year") and hasattr(value, "month") and hasattr(value, "day"):
        try:
            year, month, day = int(value.year), int(value.month), int(value.day)
            if 1 <= month <= 12 and 1 <= day <= 31:
                return year, month, day
        except (TypeError, ValueError):
            pass
    try:
        import numpy as np

        arr = np.asarray(value)
        if arr.dtype.kind == "M":
            sample = np.asarray(arr.reshape(-1)[0]).astype("datetime64[D]")
            text = str(np.datetime_as_string(sample, unit="D"))
            return int(text[0:4]), int(text[5:7]), int(text[8:10])
    except (TypeError, ValueError, IndexError):
        pass
    text = str(value)
    if len(text) >= 10 and text[4:5] == "-" and text[7:8] == "-":
        try:
            return int(text[0:4]), int(text[5:7]), int(text[8:10])
        except ValueError:
            return None
    return None


def format_plot_date(value, *, year=True):
    """Figure date: ``14 Sept '26``. Set ``year=False`` for day-of-year ticks."""
    ymd = _ymd(value)
    if ymd is None:
        return str(value)
    y, month, day = ymd
    mon = _MONTHS[month - 1]
    if not year:
        return f"{day} {mon}"
    return f"{day} {mon} '{y % 100:02d}"


def format_plot_time(value):
    """Figure date and UTC hour for sub-daily data: ``6 Oct '26 03:00``."""
    try:
        stamp = np.datetime_as_string(np.asarray(value).astype("datetime64[m]"), unit="m")
    except (TypeError, ValueError):
        if hasattr(value, "hour") and hasattr(value, "minute"):
            return f"{format_plot_date(value)} {int(value.hour):02d}:{int(value.minute):02d}"
        return format_plot_date(value)
    return f"{format_plot_date(value)} {str(stamp)[11:16]}"


def _spacing_seconds(all_steps):
    """Median positive spacing of a datetime / timedelta axis in seconds, or None."""
    arr = np.asarray(all_steps)
    if arr.size < 2 or arr.dtype.kind not in "Mm":
        return None
    unit = "datetime64[s]" if arr.dtype.kind == "M" else "timedelta64[s]"
    diffs = np.diff(arr.astype(unit).astype("int64"))
    positive = diffs[diffs > 0]
    return float(np.median(positive)) if positive.size else None


def _hours(td) -> str:
    seconds = int(np.asarray(td).astype("timedelta64[s]").astype("int64"))
    return f"+{seconds // 3600}h" if seconds % 3600 == 0 else f"+{seconds / 3600:g}h"


def axis_label(text):
    """Sentence-case an axis label; map lon/lat shorthand to Longitude/Latitude."""
    if text is None:
        return text
    s = str(text).strip()
    if not s:
        return s
    known = {
        "lon": "Longitude",
        "lat": "Latitude",
        "longitude": "Longitude",
        "latitude": "Latitude",
        "valid time": "Valid time",
        "calendar day": "Calendar day",
        "time": "Time",
        "step": "Step",
        "forecast step": "Forecast step",
    }
    key = s.lower()
    if key in known:
        return known[key]
    if s[:1].islower():
        return s[:1].upper() + s[1:]
    return s


def resolve_axis_label(override, default):
    """Use ``override`` verbatim when set; otherwise sentence-case ``default``."""
    if override is not None and str(override).strip() != "":
        return str(override)
    return axis_label(default)


def is_datetime_axis(values):
    """True when ``values`` are calendar dates (datetime64 or cftime)."""
    import numpy as np

    arr = np.asarray(values)
    if arr.dtype.kind == "M":
        return True
    if arr.size == 0:
        return False
    first = arr.reshape(-1)[0]
    return hasattr(first, "year") and hasattr(first, "month")


def format_plot_date_range(start, end):
    """Inclusive range: ``1–7 Sept '26``, ``28 Aug–3 Sept '26``, ``28 Dec '25–3 Jan '26``."""
    a = _ymd(start)
    b = _ymd(end)
    if a is None or b is None:
        return f"{format_plot_date(start)}–{format_plot_date(end)}"
    ay, am, ad = a
    by, bm, bd = b
    a_mon, b_mon = _MONTHS[am - 1], _MONTHS[bm - 1]
    a_yr, b_yr = f"'{ay % 100:02d}", f"'{by % 100:02d}"
    if ay == by and am == bm:
        return f"{ad}–{bd} {a_mon} {a_yr}"
    if ay == by:
        return f"{ad} {a_mon}–{bd} {b_mon} {b_yr}"
    return f"{ad} {a_mon} {a_yr}–{bd} {b_mon} {b_yr}"


def format_step(value):
    """Lead-time or calendar label: ``+3d`` or ``1 Jan '26``."""
    arr = np.asarray(value)
    if arr.dtype.kind == "M":
        return format_plot_date(value)
    if arr.dtype.kind == "m":
        days = int(arr.astype("timedelta64[D]").astype("int64").reshape(-1)[0])
        return f"+{days}d"
    if hasattr(value, "year") and hasattr(value, "month") and hasattr(value, "day"):
        return format_plot_date(value)
    return str(value)


def calendar_bin_width(da, all_steps):
    """Days in a left-labeled multi-day calendar bin, or None for a single date."""
    days = aggregation_days(da)
    if days is not None and days >= 2:
        return float(days)
    arr = np.asarray(all_steps)
    if arr.size < 2:
        return None
    if arr.dtype.kind == "M":
        try:
            diffs = np.diff(arr.astype("datetime64[ns]").astype("int64"))
        except (TypeError, ValueError):
            return None
        positive = diffs[diffs > 0]
        if positive.size == 0:
            return None
        median_ns = float(np.median(positive))
        if median_ns < 2 * 86_400_000_000_000:
            return None
        return median_ns / 86_400_000_000_000
    if is_cftime_axis(arr):
        ordered = np.sort(arr)
        deltas = [
            abs((ordered[i + 1] - ordered[i]).total_seconds()) for i in range(ordered.size - 1)
        ]
        if not deltas:
            return None
        seconds = float(np.median(deltas))
        if seconds < 2 * 86_400:
            return None
        return seconds / 86_400
    return None


def format_calendar_panel(value, bin_width=None):
    """``14 Sept '26``, or ``4–10 Aug '26`` for multi-day left-edge bins."""
    import datetime as _dt

    if bin_width is None:
        return format_plot_date(value)
    try:
        if hasattr(bin_width, "to"):
            days = float(bin_width.to("day").magnitude)
        elif hasattr(bin_width, "total_seconds"):
            days = float(bin_width.total_seconds()) / 86_400
        else:
            days = float(bin_width)
    except (TypeError, ValueError, AttributeError):
        return format_plot_date(value)
    if days <= 1.5:
        return format_plot_date(value)
    ymd = _ymd(value)
    if 27.5 <= days <= 31.5 and days != 30.0 and ymd is not None:
        # A month, however it is stamped (CMIP6 puts it mid-month): "Jan 2090".
        # An exact "30 day" window keeps its date range.
        return f"{_MONTHS[ymd[1] - 1]} {ymd[0]}"

    offset_days = int(round(days)) - 1
    if hasattr(value, "calendar"):
        try:
            end = value + _dt.timedelta(days=offset_days)
        except (TypeError, ValueError):
            return format_plot_date(value)
        return format_plot_date_range(value, end)

    try:
        start = np.asarray(value, dtype="datetime64[D]")
        end = start + np.timedelta64(offset_days, "D")
        return format_plot_date_range(start, end)
    except (TypeError, ValueError):
        return format_step(value)


def panel_title(da, sdim, step_value, all_steps):
    """Human panel label: calendar range, forecast valid window, or ``<sdim>=…``."""
    step_arr = np.asarray(all_steps)
    value_arr = np.asarray(step_value)
    if step_arr.dtype.kind == "m" and "time" in da.coords and getattr(da["time"], "ndim", 1) == 0:
        fallback = f"{sdim}={format_step(step_value)}"
        try:
            time_val = np.asarray(da["time"].values)
            start = time_val + np.asarray(step_value)
            dt = None
            interval = da.attrs.get(DATA_INTERVAL_ATTR)
            if isinstance(interval, str) and interval.strip():
                try:
                    seconds = float(parse_aggregation_period(interval).to("second").magnitude)
                    dt = np.timedelta64(int(round(seconds)), "s")
                except (TypeError, ValueError, UsageError):
                    dt = None
            days = aggregation_days(da)
            if dt is None and days:
                dt = np.timedelta64(int(round(days * 86_400)), "s")
            if dt is None:
                dt = step_arr[1] - step_arr[0] if step_arr.size > 1 else np.timedelta64(1, "D")
            spacing = _spacing_seconds(step_arr)
            if days is not None and days < 1:
                # A sub-daily mean (6 hour precip): its window, then the lead it starts at.
                end = start + dt
                lead = f"{_hours(step_value)[:-1]}–{_hours(np.asarray(step_value) + dt)[1:]}"
                return (
                    f"{format_plot_time(start)}–{str(end.astype('datetime64[m]'))[11:16]} ({lead})"
                )
            if (spacing is not None and spacing < 86_400) or dt < np.timedelta64(1, "D"):
                return f"{format_plot_time(start)} ({_hours(step_value)})"
            if _TRAILING.search(str(da.attrs.get("long_name") or "")):
                # "… in the last 24 hours": the step labels the end of its window.
                start = start - dt
            end = start + dt
            whole_days = dt / np.timedelta64(1, "D")
            if whole_days >= 1 and float(whole_days).is_integer():
                last = (start + dt - np.timedelta64(1, "D")).astype("datetime64[D]")
                if whole_days == 1:
                    return format_plot_date(start)
                return format_plot_date_range(start.astype("datetime64[D]"), last)
            return f"{format_plot_date(start)} until {format_plot_date(end)}"
        except Exception:  # noqa: BLE001
            return fallback
    if value_arr.dtype.kind == "M" or hasattr(step_value, "calendar"):
        spacing = _spacing_seconds(step_arr)
        if spacing is not None and spacing < 86_400:
            return format_plot_time(step_value)
        if spacing is None and not format_plot_time(step_value).endswith(" 00:00"):
            # One map at 12 UTC is a time of day, not a whole day.
            return format_plot_time(step_value)
        return format_calendar_panel(step_value, calendar_bin_width(da, all_steps))
    if sdim == "number":
        return f"Member {format_step(step_value)}"
    units = str(da[sdim].attrs.get("units") or "") if sdim in da.coords else ""
    if not units and sdim in ("vertical", "level", "pressure_level", "isobaricInhPa"):
        units = "hPa"
    if units and value_arr.dtype.kind in "iuf":
        # A pressure level reads "850 hPa", not "vertical=850".
        return f"{float(value_arr):g} {units}"
    return f"{sdim}={format_step(step_value)}"


def timeseries_axis(da, sdim):
    """X coord and xlabel. Forecast ``step`` + scalar init → valid times."""
    if sdim != "step" or "time" not in da.coords:
        return da[sdim].values, sdim
    time_coord = da["time"]
    if getattr(time_coord, "ndim", 1) != 0:
        return da[sdim].values, sdim
    steps = np.asarray(da["step"].values)
    if steps.dtype.kind != "m":
        return da[sdim].values, sdim
    init = np.asarray(time_coord.values)
    if init.dtype.kind != "M":
        return da[sdim].values, sdim
    return (init + steps).astype("datetime64[ns]"), "Valid time"


def period_axis_title(da, default):
    """A date axis of multi-day totals says what each date starts: "Week starting"."""
    days = aggregation_days(da)
    if not days or days < 2:
        return default
    if 27.5 <= days <= 31.5 and days != 30.0:
        return "Month"
    names = {7: "Week starting", 10: "Dekad starting"}
    return names.get(round(days), f"{days:g}-day period starting")


_DATE_TAIL = re.compile(r"[_-]\d{4}-\d{2}-\d{2}(?:[_-]\d{2}-\d{2}-\d{2})?$")
_VAR_TAIL = re.compile(r"[_-](?:precip|tp)$", re.I)


def _prettify_token(text: str) -> str:
    # Product ids are mostly acronyms (ECMWF, IMERG, CHIRPS), so every word is upper-cased.
    return " ".join(p.upper() for p in re.split(r"[_-]+", text.strip()) if p) or text


def label_from_history(ds) -> str | None:
    raw = ds.attrs.get(HISTORY_ATTR)
    if not raw:
        return None
    try:
        history = json.loads(raw) if isinstance(raw, str) else list(raw)
    except (TypeError, ValueError):
        return None
    skill = origin_skill(history)
    return _prettify_token(skill.removesuffix("-fetch")) if skill else None


# Words a catalog id carries that a panel title does not need.
_ID_NOISE = re.compile(r"[-_](forecast|analysis)(?=[-_]|$)|[-_]\d+[-_](day|hour)s?.*$", re.I)


def label_from_source_token(token: str) -> str:
    """Product name from a source token: ``scheme:product/…``, a path, or a bare id."""
    text = token.strip()
    if ":" in text:
        head, _, tail = text.partition(":")
        # The product id is the first path segment after the scheme.
        text = re.split(r"[/\\]", (tail or head).strip())[0].removesuffix(".zarr")
    elif "/" in text or "\\" in text or text.endswith(".zarr"):
        text = Path(text).stem
    text = _VAR_TAIL.sub("", _DATE_TAIL.sub("", text))
    # nasa-imerg-analysis-late → NASA IMERG LATE; ecmwf-ifs-ens-forecast-15-day-0-25-degree → ECMWF IFS ENS
    text = _ID_NOISE.sub("", text) or text
    return _prettify_token(text)


def dataset_display_label(ds, fallback) -> str:
    """Short product name from ``weather_skills_source``, provenance, or ``fallback``."""
    for token in (ds.attrs.get(SOURCE_ATTR), ds.encoding.get("source")):
        if isinstance(token, str) and (label := label_from_source_token(token)):
            return label
    return label_from_history(ds) or (fallback() if callable(fallback) else str(fallback))


def combine_display_labels(labels: list[str]) -> str:
    """Join unique labels for a shared row title (e.g. one forecast row)."""
    return " / ".join(dict.fromkeys(label for label in labels if label))


def format_units_for_display(units: str | None) -> str:
    """Short figure-label spelling. On-disk attrs stay CF; colorbars do not.

    ``mm day-1`` / ``millimeter / day`` → ``mm/day``; ``degree_Celsius`` →
    ``°C``; other pint-parseable strings use CF compact form.
    """
    if not isinstance(units, str) or not units.strip() or units.strip() == "1":
        return ""
    raw = units.strip()
    if units_equal(raw, "delta_degC"):
        return "°C"
    for kind, nice in (("precip", "mm/day"), ("precip_amount", "mm"), ("temp", "°C")):
        if units_equal(raw, STANDARD[kind]["units"]):
            return nice
    try:
        text = format(ureg.Unit(raw), "cf").replace(" d-1", "/day").replace(" h-1", "/h")
    except Exception:  # noqa: BLE001 — leave unparseable strings alone
        return raw
    if text == "1":
        # Dimensionless: a ratio of like units (m3 m-3) says what it is; a bare 1 says nothing.
        return "" if raw.lower() in ("1", "dimensionless") else raw
    return text


_LABEL_NOISE = (
    (re.compile(r"^(daily|hourly|weekly|monthly)\s+", re.I), ""),
    (re.compile(r"^mean\s+", re.I), ""),
    (re.compile(r"\s+in the (last|previous) \d+ hours?$", re.I), ""),
    (re.compile(r"\s+at 2 met(re|er)s?", re.I), " (2 m)"),
    (re.compile(r"\s+at 10 met(re|er)s?", re.I), " (10 m)"),
)


def short_label(label: str) -> str:
    """Drop aggregation words a colorbar does not need ("Daily", "in the last 24 hours")."""
    out = label
    for pattern, repl in _LABEL_NOISE:
        out = pattern.sub(repl, out)
    out = out.strip()
    if not out or out == label:
        return label
    return out[:1].upper() + out[1:]


def variable_label_for_display(
    da, *, fallback: str | None = None, include_units: bool = True
) -> str:
    """Colorbar / axis label: ``long_name``, then ``GRIB_name``, then the name.

    Leftover rate-like names on precip amounts become ``Total precipitation``.
    Units are appended in display form (``mm/day``, ``°C``) when present.
    """
    label = None
    for key in ("long_name", "GRIB_name"):
        val = da.attrs.get(key)
        if isinstance(val, str) and val.strip():
            label = val.strip()
            break
    if label is None:
        name = da.name if isinstance(da.name, str) and da.name.strip() else None
        label = fallback or name or "value"
    kind = classify_variable(
        da.name or "",
        units=variable_units(da),
        standard_name=da.attrs.get("standard_name"),
    )
    if kind == "precip_amount" and looks_like_rate_display_name(label):
        label = PRECIP_AMOUNT_LONG_NAME
    label = short_label(label)
    if kind in ("precip", "precip_amount") and label.lower() == "total precipitation":
        label = "Precipitation"
    if is_statistic(da):
        # summarize-dim writes "Standard deviation of Total precipitation across number".
        m = re.match(r"standard deviation of (.+?)(?: across (.+))?$", label, re.I)
        if m:
            what = short_label(m.group(1))
            if what.lower() == "total precipitation":
                what = "Precipitation"
            label = f"{what} std. dev." + (f" ({m.group(2)})" if m.group(2) else "")
        elif "std" not in label.lower() and "deviation" not in label.lower():
            label = f"{label} std. dev."
    elif "anomal" not in label.lower() and (
        "anomal" in str(da.name).lower() or is_precip_anomaly(da)
    ):
        label = f"{label} anomaly"
    if not include_units:
        return label
    units = format_units_for_display(variable_units(da))
    if units:
        return f"{label} [{units}]"
    return label
