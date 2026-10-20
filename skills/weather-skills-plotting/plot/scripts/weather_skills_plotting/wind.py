"""Wind u/v detection, speed, arrow thinning, wind-rose histograms, xy pairing."""

from __future__ import annotations

import numpy as np
from weather_skills_core.cf import cf_dim
from weather_skills_core.errors import UsageError
from weather_skills_core.units import units_equal, variable_units

from weather_skills_plotting.geodata import plain

WIND_ROSE_SECTORS = 16


WIND_SPEED_EDGES_MS = [0.0, 2.0, 4.0, 6.0, 8.0, 10.0, 12.0]


_UV_NAME_PAIRS = (
    ("u10", "v10"),
    ("u100", "v100"),
    ("10u", "10v"),
    ("uas", "vas"),
    ("ua", "va"),
    ("u", "v"),
    ("eastward_wind", "northward_wind"),
    ("10m_u_component_of_wind", "10m_v_component_of_wind"),
    ("100m_u_component_of_wind", "100m_v_component_of_wind"),
    ("u_component_of_wind", "v_component_of_wind"),
    ("uwind", "vwind"),
    ("uwnd", "vwnd"),
)


_SAMPLE_DIM_NAMES = {"step", "number", "point_id", "station_id", "valid_time"}


QUIVER_TARGET_SPACING_DEG = 1.5


QUIVER_ARROW_LEN_SPACING = 1.5


QUIVER_STEP = 1


def _wind_component_role(da):
    """``'u'`` / ``'v'`` from CF ``standard_name``, or None."""
    sn = da.attrs.get("standard_name")
    if not isinstance(sn, str) or not sn.strip():
        return None
    key = sn.strip().lower()
    if "eastward" in key and "wind" in key:
        return "u"
    if "northward" in key and "wind" in key:
        return "v"
    return None


def _infer_uv_partner(name, *, want_v):
    """Guess the complementary u/v variable name, or None."""
    pairs = dict(_UV_NAME_PAIRS)
    inv = {v: u for u, v in _UV_NAME_PAIRS}
    if want_v:
        if name in pairs:
            return pairs[name]
        swapped = name.replace("eastward", "northward").replace("u_component", "v_component")
        if swapped != name:
            return swapped
        if name.startswith("u"):
            return "v" + name[1:]
        return None
    if name in inv:
        return inv[name]
    swapped = name.replace("northward", "eastward").replace("v_component", "u_component")
    if swapped != name:
        return swapped
    if name.startswith("v"):
        return "u" + name[1:]
    return None


def resolve_uv(ds, u_variable, v_variable):
    """Eastward/northward variable names from flags, CF attrs, or common names."""
    names = list(ds.data_vars)
    if u_variable and u_variable not in ds:
        raise UsageError(
            f"meta.source.u {u_variable!r} is not in this input (have {names}). u and v must be in "
            "one input: merge the two files into one Zarr first"
        )
    if v_variable and v_variable not in ds:
        raise UsageError(
            f"meta.source.v {v_variable!r} is not in this input (have {names}). u and v must be in "
            "one input: merge the two files into one Zarr first"
        )
    if u_variable and v_variable:
        return u_variable, v_variable
    if u_variable:
        partner = _infer_uv_partner(u_variable, want_v=True)
        if partner and partner in ds:
            return u_variable, partner
        raise UsageError(
            f"meta.source.u {u_variable!r} is set but no northward partner was found; "
            "set meta.source.v too"
        )
    if v_variable:
        partner = _infer_uv_partner(v_variable, want_v=False)
        if partner and partner in ds:
            return partner, v_variable
        raise UsageError(
            f"meta.source.v {v_variable!r} is set but no eastward partner was found; "
            "set meta.source.u too"
        )
    u_cf, v_cf = [], []
    for name in names:
        role = _wind_component_role(ds[name])
        if role == "u":
            u_cf.append(name)
        elif role == "v":
            v_cf.append(name)
    if len(u_cf) == 1 and len(v_cf) == 1:
        return u_cf[0], v_cf[0]
    present = set(names)
    matches = [(u, v) for u, v in _UV_NAME_PAIRS if u in present and v in present]
    if matches:
        return matches[0]
    raise UsageError(
        "u/v plot needs eastward (u) and northward (v) wind components; "
        f"could not auto-detect them in {names}. Set meta.source.u and meta.source.v; u and v "
        "must be in one input (merge separate u and v files into one Zarr first)."
    )


def speed_units_display(da):
    raw = variable_units(da)
    if not raw:
        return "m/s"
    if units_equal(raw, "m s-1"):
        return "m/s"
    return raw


def wind_speed_da(u_da, v_da):
    """Speed from eastward/northward components, with a Wind speed label."""
    import xarray as xr

    u_da = plain(u_da)
    v_da = plain(v_da)
    speed = xr.apply_ufunc(np.hypot, u_da, v_da, keep_attrs=False)
    # keep_attrs=False also strips coordinate attrs (an init time must stay one).
    speed = speed.assign_coords({name: u_da[name] for name in speed.coords if name in u_da.coords})
    units = variable_units(u_da) or "m s-1"
    speed.name = "speed"
    speed.attrs.update(long_name="Wind speed", units=units, standard_name="wind_speed")
    return speed


def wind_speed_cbar_label(u_da):
    units_disp = speed_units_display(u_da)
    blob = " ".join(str(u_da.attrs.get(key) or "") for key in ("long_name", "GRIB_name")).lower()
    if "anomal" in blob:
        return f"Wind speed anomaly [{units_disp}]"
    return f"Wind speed [{units_disp}]"


def _mean_axis_spacing(values, axis):
    """Mean absolute spacing along one axis of a 1-D or 2-D coordinate."""

    values = np.asarray(values, dtype=float)
    if values.ndim == 0 or values.shape[axis] < 2:
        return None
    delta = np.diff(values, axis=axis)
    delta = delta[np.isfinite(delta)]
    if delta.size == 0:
        return None
    return float(np.mean(np.abs(delta)))


def native_spacing_deg(lat, lon):
    """Finest mean lat/lon spacing in degrees, or None if it cannot be measured."""

    lat = np.asarray(lat)
    lon = np.asarray(lon)
    if lat.ndim == 1 and lon.ndim == 1:
        spacings = [_mean_axis_spacing(lat, 0), _mean_axis_spacing(lon, 0)]
    else:
        spacings = [
            _mean_axis_spacing(lat, 0),
            _mean_axis_spacing(lon, 1 if lon.ndim > 1 else 0),
        ]
    candidates = [s for s in spacings if s is not None and s > 0]
    return min(candidates) if candidates else None


def quiver_step(lat, lon, requested=None, target_spacing=QUIVER_TARGET_SPACING_DEG):
    """Stride for quiver arrows.

    ``plot_wind_and_sst_anomaly`` uses ``quiver_step=1`` on the native S2S
    ~1.5° grid. When ``requested`` is set, use that. Otherwise thin finer
    grids (GFS 0.25°, ERA5) to about 1.5° so basin maps match that look.
    """
    if requested is not None:
        if requested < 1:
            raise UsageError("quiver.step must be >= 1")
        return int(requested)
    spacing = native_spacing_deg(lat, lon)
    if spacing is None:
        return QUIVER_STEP
    return max(QUIVER_STEP, int(round(target_spacing / spacing)))


def subsample_quiver(lon, lat, u, v, step):
    """Native-grid u/v subsample, matching plot_wind_and_sst_anomaly."""

    lon = np.asarray(lon)
    lat = np.asarray(lat)
    u = np.asarray(u)
    v = np.asarray(v)
    if lon.ndim == 1 and lat.ndim == 1:
        lon, lat = np.meshgrid(lon, lat)
    step = max(1, int(step))
    return lon[::step, ::step], lat[::step, ::step], u[::step, ::step], v[::step, ::step]


def is_sample_dim(da, dim):
    """True if ``dim`` is flattened into wind-rose samples rather than indexed."""
    if dim in _SAMPLE_DIM_NAMES:
        return True
    for cf_name in ("latitude", "longitude", "time"):
        if cf_dim(da, cf_name) == dim:
            return True
    return False


def flat_numeric(da):
    """Raveled float samples, stripping a pint wrapper if present."""

    if getattr(da.pint, "units", None) is not None:
        da = da.pint.dequantify()
    return np.asarray(da.values, dtype=float).reshape(-1)


def uv_to_speed_fromdir(u, v):
    """Speed and meteorological FROM direction in degrees (0=N, 90=E)."""

    u = np.asarray(u, dtype=float)
    v = np.asarray(v, dtype=float)
    speed = np.hypot(u, v)
    fromdir = (np.degrees(np.arctan2(-u, -v)) + 360.0) % 360.0
    return speed, fromdir


def speed_edges(speed, units):
    """Speed-bin edges. Standard 2 m/s classes when units are m/s, else 6 linear bins."""

    vmax = float(np.nanmax(speed)) if speed.size else 0.0
    ms = bool(units) and units_equal(units, "m s-1")
    if ms:
        return np.asarray([*WIND_SPEED_EDGES_MS, np.inf], dtype=float)
    if not np.isfinite(vmax) or vmax <= 0:
        return np.array([0.0, 1.0], dtype=float)
    return np.linspace(0.0, vmax, 7)


def speed_bin_labels(edges):

    labels = []
    n = len(edges) - 1
    for i in range(n):
        lo = float(edges[i])
        hi = edges[i + 1]
        if np.isinf(hi):
            labels.append(f"≥{lo:g}")
        else:
            labels.append(f"{lo:g}–{hi:g}")
    return labels


def wind_rose_hist(speed, direction, speed_edges, nsector=WIND_ROSE_SECTORS):
    """2D histogram ``(nsector, nspeed)``. Sector 0 is North-centered."""

    offset = 180.0 / nsector
    shifted = (np.asarray(direction, dtype=float) + offset) % 360.0
    dir_edges = np.linspace(0.0, 360.0, nsector + 1)
    hist, _, _ = np.histogram2d(shifted, speed, bins=[dir_edges, speed_edges])
    return hist
