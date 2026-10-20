"""What a field is: precipitation, an anomaly, SPI, temperature, pressure, a spread.

``default_kind`` names the ``theme["defaults"]`` entry that colors a field
when the spec names no palette; the other checks are its parts, also used by
labels and color ranges.
"""

from __future__ import annotations

import numpy as np
from weather_skills_core.errors import UsageError
from weather_skills_core.units import classify_variable, parse_aggregation_period, variable_units


def aggregation_days(da) -> float | None:
    """Return stamped ``aggregation_period`` in days, or None."""
    period = da.attrs.get("aggregation_period")
    if not (isinstance(period, str) and period.strip()):
        return None
    try:
        return float(parse_aggregation_period(period).to("day").magnitude)
    except UsageError:
        return None


def is_precip(da) -> bool:
    kind = classify_variable(
        da.name or "",
        units=variable_units(da),
        standard_name=da.attrs.get("standard_name"),
    )
    return kind in ("precip", "precip_amount")


def is_precip_anomaly(da) -> bool:

    if not is_precip(da):
        return False
    name = f"{da.name or ''} {da.attrs.get('long_name') or ''}".lower()
    if "anomal" in name:
        return True
    values = da.data
    values = getattr(values, "magnitude", values)  # a pint quantity: its numbers
    sample = np.asarray(values, dtype=float).ravel()
    finite = sample[np.isfinite(sample)]
    # Real negatives, not the -1e-6 rounding a deaccumulated total can carry.
    return bool(finite.size) and bool(np.nanmin(finite) < -0.01)


def is_precip_poa(da) -> bool:
    """Percent-of-normal precip (CHC ``ppt_poa``)."""
    name = f"{da.name or ''} {da.attrs.get('long_name') or ''}".lower()
    tokens = name.replace("_", " ").replace("-", " ").split()
    if "percent of" in name or "pct of" in name or "poa" in tokens:
        return True
    units = (variable_units(da) or "").strip().lower()
    return units in {"%", "percent"}


def is_temperature(da) -> bool:
    """Air, surface or sea temperature (not an anomaly): drawn cool to warm."""
    text = " ".join(str(da.attrs.get(k) or "") for k in ("standard_name", "long_name")).lower()
    return "temperature" in text and "anomal" not in text and "anomal" not in str(da.name).lower()


# Standard-atmosphere sea-level pressure: the white middle of the pressure scale.
STANDARD_SEA_LEVEL_PRESSURE_HPA = 1013.25
# Isobar spacing of a surface analysis chart (Met Office, NOAA WPC).
ISOBAR_INTERVAL_HPA = 4


def is_sea_level_pressure(da) -> bool:
    """Sea-level pressure in pressure units (not surface pressure, not an anomaly)."""
    standard_name = da.attrs.get("standard_name")
    kind = classify_variable(da.name or "", units=variable_units(da), standard_name=standard_name)
    surface = standard_name == "surface_air_pressure" or "surface" in str(da.name).lower()
    return kind == "pressure" and not surface and "anomal" not in str(da.name).lower()


def is_statistic(da) -> bool:
    """A spread or variance (``cell_methods`` from summarize-dim): not a precip amount."""
    methods = str(da.attrs.get("cell_methods") or "")
    return "standard_deviation" in methods or "variance" in methods


def is_spi(da) -> bool:
    name = f"{da.name or ''} {da.attrs.get('long_name') or ''}".lower()
    tokens = name.replace("_", " ").replace("-", " ").split()
    return "spi" in tokens or "standardized precipitation" in name


def default_kind(da, *, diverging: bool = False) -> str:
    """The field kind whose ``theme["defaults"]`` entry colors ``da``.

    Precipitation kinds and wind speed (CF ``standard_name: wind_speed``) come
    from the field itself. Other fields are ``diverging`` (the color range
    straddles zero), sea-level pressure, temperature, or ``sequential``. A
    spread or variance is never a precip amount.
    """
    if da is not None and da.attrs.get("standard_name") == "wind_speed":
        return "wind_speed"
    if da is not None and not is_statistic(da):
        if is_spi(da):
            return "spi"
        if is_precip_poa(da):
            return "precip_poa"
        if is_precip_anomaly(da):
            return "precip_anomaly"
        if is_precip(da):
            return "precip"
    if diverging:
        return "diverging"
    if da is not None and not is_statistic(da) and is_sea_level_pressure(da):
        return "sea_level_pressure"
    if da is not None and is_temperature(da):
        return "temperature"
    return "sequential"
