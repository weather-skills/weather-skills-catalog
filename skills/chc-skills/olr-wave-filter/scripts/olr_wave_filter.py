# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "weather-skills-core>=0.4.0",
#   "aiohttp",
#   "cftime>=1.6",
#   "dynamical-catalog==1.0.1",
#   "fsspec",
#   "icechunk",
#   "netcdf4",
#   "numpy",
#   "pandas",
#   "scipy",
#   "xarray",
#   "zarr",
# ]
# ///
"""Filter an ensemble OLR forecast into MJO, Kelvin, ER, MRG/TD and low-frequency signals."""

from __future__ import annotations

import sys
from pathlib import Path

from weather_skills_core import DataError, Dataset, UsageError, weather_skill

# Auto-populated by the version-bump CI workflow. Do not edit manually.
_SKILL_VERSION = "0.0.3"

OBS_SOURCE = "https://storage.googleapis.com/africa-forecasting-data/era5"
CACHE_DIR = Path.home() / ".cache" / "chc-skills" / "era5"
DYNAMICAL_DATASET = "ecmwf-ifs-ens-forecast-46-day-daily-1-5-degree"
DYNAMICAL_OLR = "net_long_wave_radiation_flux_top_of_atmosphere"
OLR_NAMES = ("olr", DYNAMICAL_OLR, "ttr", "top_net_thermal_radiation", "mtnlwrf")

CLIM_YEARS = (2006, 2022)  # annual cycle fit (the reforecast years ERA5 fully covers)
STD_YEARS = (2007, 2021)  # amplitude scale (a year off each end of the filtered archive)
STD_HALF_WINDOW = 60  # calendar days either side of the forecast's middle
N_HARMONICS = 4
BETA_LAT = 15  # control-variate beta of the lead bias is fitted over 15S-15N
MAX_LEAD_SMOOTHING = 7  # +- days, reached by lead day 15
SERIES_DAYS = 1461  # lead-in + forecast, then zeros
TAPER_DAYS = 365  # the first year of the lead-in is tapered to zero
AMP_LAT = 5  # A and B (amplitude, phase) are taken over 5S-5N
DAY1_FALLBACK = 3  # an ERA5T-gap day may use the lead 1..3 forecast covering it
MAX_GAP_DAYS = 15
REFORECAST_YEARS = range(2006, 2025)
MEMBER_BATCH_CELLS = 48  # members x latitudes filtered per FFT batch

# Janiga et al. (2018) bands: zonal wavenumber (positive = eastward), period (days),
# and the running-mean high-pass (days) applied first (None for the low-frequency
# band, which is what that high-pass removes). Kelvin and ER are further limited
# to between the shallow-water dispersion curves of equivalent depth h (m), as in
# Wheeler and Kiladis (1999) and the NCICS monitor.
WAVES = {
    "low": {"label": "Low", "k": (-10, 10), "period": (100, SERIES_DAYS), "highpass": None},
    "mjo": {"label": "MJO", "k": (0, 9), "period": (20, 100), "highpass": 100},
    "kelvin": {
        "label": "Kelvin",
        "k": (1, 14),
        "period": (2.5, 20),
        "highpass": 20,
        "h": (8, 90),
        "dispersion": "kelvin",
    },
    "er": {
        "label": "ER",
        "k": (-10, -1),
        "period": (10, 100),
        "highpass": 100,
        "h": (8, 90),
        "dispersion": "er",
    },
    "mrg_td": {"label": "MRG/TD", "k": (-20, 0), "period": (2.5, 10), "highpass": 20},
}

EARTH_RADIUS = 6.371e6  # m
BETA = 2 * 7.292e-5 / EARTH_RADIUS  # m-1 s-1 at the equator
GRAVITY = 9.81


# ---------------------------------------------------------------------------
# Input
# ---------------------------------------------------------------------------


def _pick_dim(ds, preferred):
    from weather_skills_core.standard_dataset import names_for

    return next((n for n in names_for(preferred) if n in ds.dims), None)


def _forecast_olr(ds, variable, lat_max):
    """Input forecast -> (olr (number, step, latitude, longitude) ndarray-backed, init)."""
    import numpy as np
    import pandas as pd
    from weather_skills_core.standard_dataset import detect_spatial_dims
    from weather_skills_core.standard_utils import ensure_normalized_longitude

    if variable is None:
        variable = next((n for n in OLR_NAMES if n in ds.data_vars), None)
        if variable is None:
            if len(ds.data_vars) != 1:
                raise UsageError(
                    f"pass -v: no OLR variable ({', '.join(OLR_NAMES)}) in the input; "
                    f"available: {', '.join(ds.data_vars)}"
                )
            variable = next(iter(ds.data_vars))
    if variable not in ds.data_vars:
        raise UsageError(
            f"variable {variable!r} not in input; available: {', '.join(ds.data_vars)}"
        )
    units = str(ds[variable].attrs.get("units", "")).replace(" ", "").replace("**", "")
    if units.replace("^", "") not in ("Wm-2", "W/m2"):
        raise UsageError(
            f"{variable} has units {ds[variable].attrs.get('units')!r}; a daily-mean flux in "
            "W m-2 is needed (run deaccumulate on accumulated J m-2 fields first)"
        )

    lat_dim, lon_dim = detect_spatial_dims(ds)
    step_dim = _pick_dim(ds, "prediction_timedelta")
    if step_dim is None or "time" not in ds.coords or ds["time"].ndim != 0:
        raise UsageError(
            "input must be one forecast: a scalar `time` init plus a `step` lead dim "
            "(as dynamical-fetch / ecmwf-fetch write)"
        )
    da = ds[variable].rename({lat_dim: "latitude", lon_dim: "longitude", step_dim: "step"})
    member_dim = _pick_dim(da.to_dataset(), "member")
    if member_dim is None:
        da = da.expand_dims(number=[0])
    elif member_dim != "number":
        da = da.rename({member_dim: "number"})
    da = ensure_normalized_longitude(da, "longitude").sortby("longitude")
    extra = [d for d in da.dims if d not in ("number", "step", "latitude", "longitude")]
    if extra:
        raise UsageError(f"{variable} has extra dims {extra}; select them away first")

    lon = da.longitude.values
    spacing = float(np.median(np.diff(lon)))
    if abs(len(lon) * spacing - 360) > spacing / 2:
        raise UsageError(
            "the wavenumber-frequency filter needs every longitude; re-fetch the forecast "
            "with --bbox 30/-180/-30/180 (or no --bbox)"
        )
    lat = da.latitude.values
    keep = np.abs(lat) <= lat_max + 1e-6
    reach = AMP_LAT - abs(float(np.median(np.diff(lat)))) if len(lat) > 1 else AMP_LAT
    if not keep.any() or lat[keep].max() < reach or lat[keep].min() > -reach:
        raise UsageError(
            f"the forecast must cover {AMP_LAT}S-{AMP_LAT}N within --lat-max {lat_max:g}; "
            f"it covers latitudes {lat.min():g}..{lat.max():g}"
        )
    da = da.isel(latitude=np.flatnonzero(keep))

    steps = pd.to_timedelta(da.step.values)
    days = steps / pd.Timedelta(days=1)
    if len(steps) < 2 or days[0] != 0 or not np.allclose(np.diff(days), 1):
        raise UsageError(
            "the forecast needs consecutive daily steps from step 0 (daily means labelled at the "
            "start of each day, as dynamical-fetch writes for "
            f"{DYNAMICAL_DATASET}); got steps {list(days[:3])}..."
        )
    init = pd.Timestamp(ds["time"].values).normalize()
    da = da.transpose("number", "step", "latitude", "longitude").load()
    values = da.values.astype(np.float64)
    if np.isnan(values).any():
        raise DataError(
            "the forecast has missing values (init still publishing?); use the previous init"
        )
    if np.nanmean(values) < 0:  # net longwave at the top of the atmosphere: negative upward
        values = -values
    return da.copy(data=values), init


# ---------------------------------------------------------------------------
# Observations (ERA5 archive in the public bucket) and gap days (dynamical.org)
# ---------------------------------------------------------------------------


def _obs_path(source, name):
    """Local path of one archive file: as is from a folder, else a cached download."""
    if "://" not in source:
        path = Path(source) / name
        return path if path.is_file() else None
    import fsspec

    url = f"{source.rstrip('/')}/{name}"
    try:
        local = fsspec.open_local(
            f"filecache::{url}",
            filecache={"cache_storage": str(CACHE_DIR), "check_files": True},
        )
    except FileNotFoundError:
        return None
    except Exception as exc:
        raise DataError(
            f"could not download {url} ({exc}); check the network or pass --obs-source DIR"
        ) from exc
    return Path(local)


def _on_grid(da, lats, lons, what):
    """Select da at exactly the forecast's latitudes and longitudes."""
    from weather_skills_core.standard_utils import ensure_normalized_longitude

    da = ensure_normalized_longitude(da, "longitude")
    try:
        da = da.sel(latitude=lats, longitude=lons, method="nearest", tolerance=0.01)
    except KeyError as exc:
        raise UsageError(
            f"the forecast is not on the 1.5 degree grid of {what}; fetch "
            f"{DYNAMICAL_DATASET} or regrid with the coarsen skill (--resolution 1.5)"
        ) from exc
    return da.assign_coords(latitude=lats, longitude=lons)


def _read_obs(source, years, lats, lons):
    """ERA5 daily OLR (time, latitude, longitude) with a `source` coord, for `years`."""
    import xarray as xr

    parts = []
    for year in years:
        path = _obs_path(source, f"era5_olr_{year}.nc")
        if path is None:
            continue
        with xr.open_dataset(path, engine="netcdf4") as f:
            da = _on_grid(f["olr"], lats, lons, "the ERA5 archive")
            parts.append(da.astype("float32").load())
    if not parts:
        raise DataError(f"no ERA5 OLR archive files (era5_olr_<YYYY>.nc) found at {source}")
    obs = xr.concat(parts, dim="time").sortby("time")
    return obs.assign_coords(source=("time", obs.source.values.astype("U12")))


def _gap_forecasts(days, lats, lons):
    """Ensemble-mean lead-1 (else lead 2..3) forecast OLR for each of `days`."""
    if not len(days):
        return []
    import dynamical_catalog
    import numpy as np
    import pandas as pd

    try:
        olr = dynamical_catalog.open(DYNAMICAL_DATASET, chunks=None)[DYNAMICAL_OLR]
    except Exception as exc:
        raise DataError(
            f"could not open {DYNAMICAL_DATASET} on dynamical.org for the {len(days)} days "
            f"ERA5T hasn't reached ({exc}); retry, or pick an init a few days older"
        ) from exc
    inits = olr.get_index("init_time")
    out = []
    for day in days:
        for lead in range(1, DAY1_FALLBACK + 1):
            init = day - pd.Timedelta(days=lead - 1)
            if init not in inits:
                continue
            fc = olr.sel(init_time=init, lead_time=pd.Timedelta(days=lead))
            fc = _on_grid(fc, lats, lons, DYNAMICAL_DATASET).mean("ensemble_member").values
            if not np.isnan(fc).any():
                out.append((-fc if np.mean(fc) < 0 else fc, lead))
                break
        else:
            raise DataError(
                f"no {DYNAMICAL_DATASET} run covers {day:%Y-%m-%d} at leads 1-{DAY1_FALLBACK}; "
                "pick an init a few days older"
            )
        print(f"  {day:%Y-%m-%d}: not in ERA5 yet, using ECMWF lead {lead}", file=sys.stderr)
    return out


def _leadin(obs, init, n_days, lats, lons):
    """(values (n_days, lat, lon), gap leads (n_days,), 0 = ERA5) before init."""
    import numpy as np
    import pandas as pd

    days = pd.date_range(init - pd.Timedelta(days=n_days), init - pd.Timedelta(days=1), freq="D")
    have = obs.get_index("time")
    if days[0] < have.min():
        raise DataError(
            f"the ERA5 archive starts {have.min():%Y-%m-%d}; the {n_days}-day lead-in needs "
            f"{days[0]:%Y-%m-%d} (shorten --leadin-days)"
        )
    last = have.max()
    holes = days[(days <= last) & ~days.isin(have)]
    if len(holes):
        raise DataError(
            f"the ERA5 archive is missing {len(holes)} lead-in days "
            f"({holes[0]:%Y-%m-%d}..{holes[-1]:%Y-%m-%d}); pick another init"
        )
    gap = days[days > last]
    if len(gap) > MAX_GAP_DAYS:
        raise DataError(
            f"the ERA5 archive ends {last:%Y-%m-%d}, {len(gap)} days before this init (at most "
            f"{MAX_GAP_DAYS} are filled from forecasts); pick an init up to "
            f"{last + pd.Timedelta(days=MAX_GAP_DAYS + 1):%Y-%m-%d}"
        )
    values = np.empty((n_days, len(lats), len(lons)))
    leads = np.zeros(n_days, dtype=int)
    n_have = n_days - len(gap)
    values[:n_have] = obs.sel(time=days[:n_have]).values
    for i, (fc, lead) in enumerate(_gap_forecasts(gap, lats, lons)):
        values[n_have + i] = fc
        leads[n_have + i] = lead
    return values, leads


# ---------------------------------------------------------------------------
# Anomalies: annual cycle and (optional) lead-dependent reforecast bias
# ---------------------------------------------------------------------------


def _harmonics(times):
    """Design matrix: 1, cos(k*phase), sin(k*phase) for k = 1..N_HARMONICS."""
    import numpy as np
    import pandas as pd

    days = (pd.DatetimeIndex(times) - pd.Timestamp("2000-01-01")).days.values
    phase = 2 * np.pi * days / 365.25
    cols = [np.ones_like(phase, dtype=float)]
    for k in range(1, N_HARMONICS + 1):
        cols += [np.cos(k * phase), np.sin(k * phase)]
    return np.column_stack(cols)


def _fit_annual_cycle(obs):
    """Mean + N_HARMONICS harmonics of obs (time, ...) over CLIM_YEARS, at every point.

    Returns a function times -> annual cycle ndarray (len(times), ...)."""
    import numpy as np

    fit = obs.sel(time=slice(str(CLIM_YEARS[0]), str(CLIM_YEARS[1])))
    if fit.sizes["time"] < 3650:
        raise DataError(
            f"the ERA5 archive has only {fit.sizes['time']} days in "
            f"{CLIM_YEARS[0]}-{CLIM_YEARS[1]} for the annual cycle"
        )
    shape = fit.shape[1:]
    coef, *_ = np.linalg.lstsq(
        _harmonics(fit.time.values),
        fit.values.reshape(fit.sizes["time"], -1).astype(np.float64),
        rcond=None,
    )
    return lambda times: (_harmonics(times) @ coef).reshape(len(times), *shape)


def _planette_olr(init, n_steps, lats, lons):
    """Planette ECMWF reforecast OLR near init's calendar day, every year 2006-2024:
    ensemble mean (init_time, step, latitude, longitude), step 0 = first forecast day."""
    import numpy as np
    import pandas as pd

    da = _open_planette("single")["olr"]
    da = da.isel(init_time=_reforecast_inits(da.get_index("init_time"), init))
    da = da.isel(step=slice(0, n_steps))
    print(f"Reading Planette reforecasts ({da.sizes['init_time']} inits)...", file=sys.stderr)
    da = _on_grid(da, lats, lons, "the Planette reforecasts").mean("number").load()
    if float(np.nanmean(da.values)) < 0:
        da = -da
    return da.assign_coords(step=pd.to_timedelta(da.step.values))


def _open_planette(group):
    import icechunk
    import xarray as xr

    try:
        storage = icechunk.s3_storage(
            bucket="planette-ifs-46-day", prefix="reforecasts", region="us-east-2", anonymous=True
        )
        store = icechunk.Repository.open(storage).readonly_session("main").store
        ds = xr.open_zarr(
            store,
            group=f"{group}/1p5latx1p5lon",
            consolidated=False,
            chunks=None,
            decode_timedelta=True,
        )
    except Exception as exc:
        raise DataError(
            f"could not open the Planette reforecast store ({exc}); retry or drop --bias-correct"
        ) from exc
    return ds.rename({"lead": "step", "lat": "latitude", "lon": "longitude"})


def _reforecast_inits(inits, init):
    """Indices of the reforecast init nearest init's month-day in each reforecast year."""
    import numpy as np
    import pandas as pd

    day = min(init.day, 28) if init.month == 2 else init.day
    picks = []
    for year in REFORECAST_YEARS:
        target = pd.Timestamp(year=year, month=init.month, day=day)
        i = int(np.argmin(np.abs((inits - target).days)))
        if abs((inits[i] - target).days) <= 4 and i not in picks:
            picks.append(i)
    if len(picks) < 10:
        raise DataError(f"only {len(picks)} reforecast years found near {init:%m-%d}")
    return picks


def _lead_half_width(s):
    """Smoothing half-width (days) at 0-based lead index s: 0 on day 1, +-7 by day 15."""
    return min(MAX_LEAD_SMOOTHING, s // 2)


def _smooth_over_leads(x):
    import numpy as np

    out = np.empty_like(x)
    n = x.shape[0]
    for s in range(n):
        h = _lead_half_width(s)
        out[s] = x[max(0, s - h) : min(n, s + h + 1)].mean(axis=0)
    return out


def _lead_bias(rf, obs, annual_cycle, lats):
    """Lead-dependent model bias (step, lat, lon) from reforecast ensemble means rf
    (init_time, step, lat, lon): per lead, mean over years of (rf_anom - beta * obs_anom),
    the observed anomaly a control variate with beta fitted over 15S-15N, then smoothed
    over neighbouring leads (Janiga et al. 2018 bias step, reduced-noise variant)."""
    import numpy as np
    import pandas as pd

    steps = pd.to_timedelta(rf.step.values)
    have = obs.get_index("time")
    rf_anom, obs_anom = [], []
    for init in rf.init_time.values:
        valid = pd.Timestamp(init).normalize() + steps
        if not valid.isin(have).all():
            continue
        ac = annual_cycle(valid)
        rf_anom.append(rf.sel(init_time=init).values - ac)
        obs_anom.append(obs.sel(time=valid).values - ac)
    if len(rf_anom) < 10:
        raise DataError(f"only {len(rf_anom)} reforecast years have ERA5 to compare with")
    r, o = np.array(rf_anom), np.array(obs_anom)  # (year, step, lat, lon)
    w = np.where(np.abs(lats) <= BETA_LAT, np.cos(np.deg2rad(lats)), 0.0)[None, None, :, None]
    beta = (w * r * o).sum(axis=(0, 2, 3)) / (w * o**2).sum(axis=(0, 2, 3))
    bias = _smooth_over_leads((r - beta[None, :, None, None] * o).mean(axis=0))
    print(
        f"Lead bias from {len(rf_anom)} reforecast years; beta day 1 {beta[0]:.2f}, "
        f"day {len(beta)} {beta[-1]:.2f}",
        file=sys.stderr,
    )
    return bias


# ---------------------------------------------------------------------------
# Wavenumber-frequency filtering (Janiga et al. 2018 padded method)
# ---------------------------------------------------------------------------


def _highpass(x, n_valid, window):
    """x minus its centred `window`-day running mean over the first n_valid days of
    axis 0 (the windows shrink at both ends); the rest stays zero."""
    import numpy as np

    v = x[:n_valid]
    c = np.concatenate([np.zeros((1,) + v.shape[1:]), np.cumsum(v, axis=0)])
    i = np.arange(n_valid)
    lo = np.clip(i - window // 2, 0, n_valid)
    hi = np.clip(i + window // 2 + 1, 0, n_valid)
    mean = (c[hi] - c[lo]) / (hi - lo).reshape((-1,) + (1,) * (v.ndim - 1))
    out = np.zeros_like(x)
    out[:n_valid] = v - mean
    return out


def _taper(x):
    """Half cosine bell from 0 to 1 over the first TAPER_DAYS of axis 0."""
    import numpy as np

    w = np.ones(x.shape[0])
    w[:TAPER_DAYS] = 0.5 * (1 - np.cos(np.pi * np.arange(TAPER_DAYS) / TAPER_DAYS))
    return x * w.reshape((-1,) + (1,) * (x.ndim - 1))


def _dispersion_frequency(k_east, h, wave):
    """Shallow-water frequency (cycles per day) of a Kelvin or n=1 equatorial Rossby
    wave of zonal wavenumber k_east and equivalent depth h (m)."""
    import numpy as np

    c = np.sqrt(GRAVITY * h)
    k = k_east / EARTH_RADIUS
    if wave == "kelvin":
        omega = c * k
    else:
        omega = -BETA * k / (k**2 + 3 * BETA / c)
    return omega * 86400 / (2 * np.pi)


def _band_mask(n_time, n_lon, band):
    """Boolean (frequency, wavenumber) mask for an FFT over (time, lon).

    numpy's coefficient at frequency f > 0 and wavenumber index m is a wave
    exp(i(m*lon + 2*pi*f*t)), which moves westward for m > 0, so the eastward
    wavenumber is -m * sign(f). The mask is symmetric under (f, m) -> (-f, -m),
    which keeps the filtered field real."""
    import numpy as np

    f = np.fft.fftfreq(n_time)[:, None]
    m = np.fft.fftfreq(n_lon, 1 / n_lon)[None, :]
    k_east = -m * np.sign(f)
    k, period = band["k"], band["period"]
    mask = (
        (np.abs(f) >= 1 / period[1])
        & (np.abs(f) <= 1 / period[0])
        & (k_east >= k[0])
        & (k_east <= k[1])
    )
    if "h" in band:
        with np.errstate(divide="ignore", invalid="ignore"):
            lo = _dispersion_frequency(k_east, band["h"][0], band["dispersion"])
            hi = _dispersion_frequency(k_east, band["h"][1], band["dispersion"])
        mask &= (np.abs(f) >= lo) & (np.abs(f) <= hi)
    return mask


def _filter(series, n_valid, waves, keep):
    """series (time, ..., lon), non-zero for the first n_valid days, ->
    {wave: filtered[keep] as float32}. One spectrum per high-pass length."""
    import numpy as np
    import scipy.fft

    n_time, n_lon = series.shape[0], series.shape[-1]
    shape = (n_time,) + (1,) * (series.ndim - 2) + (n_lon,)
    out = {}
    for hp in dict.fromkeys(WAVES[w]["highpass"] for w in waves):
        pre = series if hp is None else _highpass(series, n_valid, hp)
        spectrum = scipy.fft.fft2(_taper(pre), axes=(0, -1), workers=-1)
        del pre
        for w in waves:
            if WAVES[w]["highpass"] != hp:
                continue
            mask = _band_mask(n_time, n_lon, WAVES[w]).reshape(shape)
            filtered = scipy.fft.ifft2(spectrum * mask, axes=(0, -1), workers=-1).real
            out[w] = filtered[keep].astype(np.float32)
        del spectrum
    return {w: out[w] for w in waves}


def _band_mean(x, lats, axis):
    """cos(lat)-weighted mean over |lat| <= AMP_LAT along `axis` of an ndarray."""
    import numpy as np

    w = np.where(np.abs(lats) <= AMP_LAT + 1e-6, np.cos(np.deg2rad(lats)), 0.0)
    return np.tensordot(x, w / w.sum(), axes=([axis], [0]))


def _phase_of(x, y):
    """Local phase 1..8 of (x, y) = (-A, -B): P5 (wet) centred on +x, P1 on -x, clockwise."""
    import numpy as np

    theta = np.degrees(np.arctan2(y, x))
    return (np.round(((180 - theta) % 360) / 45).astype(int) % 8) + 1


def _wave_climate(obs, lats, init, n_fc, waves):
    """Per wave, from the 5S-5N ERA5 archive filtered as one long series: the
    all-longitude std of A (filtered anomaly) and B (its time derivative), the
    per-longitude std, and per-phase 90th / 99th percentiles of the local amplitude,
    over STD_YEARS days within +-STD_HALF_WINDOW days of the forecast's middle."""
    import numpy as np
    import pandas as pd

    clim = obs.sel(time=slice(str(CLIM_YEARS[0]), str(CLIM_YEARS[1])))
    band = clim.isel(latitude=0, drop=True).copy(data=_band_mean(clim.values, lats, 1))
    anom = band.values - _fit_annual_cycle(band)(band.time.values)
    n = anom.shape[0]
    filtered = _filter(np.concatenate([anom, np.zeros_like(anom)]), n, waves, slice(0, n))

    times = band.get_index("time")
    mid = (init + pd.Timedelta(days=n_fc // 2)).dayofyear
    gap = np.abs(times.dayofyear - mid)
    use = (
        (np.minimum(gap, 365 - gap) <= STD_HALF_WINDOW)
        & (times.year >= STD_YEARS[0])
        & (times.year <= STD_YEARS[1])
    )
    out = {}
    for w, x in filtered.items():
        x = x.astype(np.float64)
        b = np.gradient(x, axis=0)
        x, b = x[use], b[use]
        local_a, local_b = x.std(axis=0), b.std(axis=0)
        xh, yh = -x / local_a, -b / local_b
        amp, phase = np.hypot(xh, yh), _phase_of(xh, yh)
        pct = np.stack(
            [
                np.nanpercentile(np.where(phase == p, amp, np.nan), [90, 99], axis=0)
                for p in range(1, 9)
            ]
        )  # (phase, 2, lon)
        out[w] = {
            "std_a": float(x.std()),
            "std_b": float(b.std()),
            "local_std_a": local_a,
            "local_std_b": local_b,
            "p90": pct[:, 0],
            "p99": pct[:, 1],
            "p90_all": np.percentile(amp, 90, axis=0),
        }
    print(
        f"Wave amplitude scale: ERA5 {STD_YEARS[0]}-{STD_YEARS[1]}, {use.sum()} days near day "
        f"{mid}; std of A: "
        + ", ".join(f"{WAVES[w]['label']} {v['std_a']:.1f}" for w, v in out.items()),
        file=sys.stderr,
    )
    return out


# ---------------------------------------------------------------------------
# Skill
# ---------------------------------------------------------------------------


@weather_skill(name="olr-wave-filter", version=_SKILL_VERSION)
@weather_skill.argument("-i", "--input", type=Dataset("forecast"), required=True, dest="ds")
@weather_skill.argument(
    "--variable",
    "-v",
    default=None,
    help=f"OLR or top-of-atmosphere net longwave variable (default: first of {', '.join(OLR_NAMES)}).",
)
@weather_skill.argument(
    "--waves",
    action="append",
    choices=list(WAVES),
    default=None,
    help="Wave band to filter; repeat for several (default: all).",
)
@weather_skill.argument(
    "--lat-max",
    type=float,
    default=15.0,
    help="Keep latitudes |lat| <= this (degrees, default 15).",
)
@weather_skill.argument(
    "--leadin-days",
    type=int,
    default=730,
    help="Observed days padded in front of the forecast (default 730).",
)
@weather_skill.argument(
    "--window-before",
    type=int,
    default=90,
    help="Observed days kept in the output before the init (default 90).",
)
@weather_skill.argument(
    "--bias-correct",
    action="store_true",
    help="Remove the lead-dependent model bias estimated from Planette ECMWF reforecasts.",
)
@weather_skill.argument(
    "--obs-source",
    default=OBS_SOURCE,
    help="ERA5 archive (era5_olr_<YYYY>.nc): base URL or local folder (default: public bucket).",
)
def olr_wave_filter(
    ds,
    variable,
    waves,
    lat_max,
    leadin_days,
    window_before,
    bias_correct,
    obs_source,
    **kwargs,
):
    """Filter an ensemble OLR forecast into MJO, Kelvin, ER, MRG/TD and low-frequency signals."""
    import numpy as np
    import pandas as pd
    import xarray as xr
    from weather_skills_core.units import dequantify_dataset

    waves = list(dict.fromkeys(waves or WAVES))
    if lat_max < AMP_LAT:
        raise UsageError(f"--lat-max must be at least {AMP_LAT}")
    if window_before < 1 or leadin_days < TAPER_DAYS + window_before:
        raise UsageError(
            f"need 1 <= --window-before and --leadin-days >= {TAPER_DAYS} + --window-before "
            "(the first year of the lead-in is tapered)"
        )

    fc, init = _forecast_olr(dequantify_dataset(ds), variable, lat_max)
    n_mem, n_fc = fc.sizes["number"], fc.sizes["step"]
    if leadin_days + n_fc > SERIES_DAYS - 100:
        raise UsageError(f"--leadin-days + forecast days must be below {SERIES_DAYS - 100}")
    lats, lons = fc.latitude.values, fc.longitude.values
    valid = init + pd.to_timedelta(fc.step.values)

    years = range(CLIM_YEARS[0], init.year + 1)
    print(f"Reading the ERA5 OLR archive {years[0]}-{years[-1]} from {obs_source}", file=sys.stderr)
    obs = _read_obs(obs_source, years, lats, lons)
    annual_cycle = _fit_annual_cycle(obs)
    leadin, gap_leads = _leadin(obs, init, leadin_days, lats, lons)

    bias = np.zeros((n_fc, len(lats), len(lons)))
    if bias_correct:
        b = _lead_bias(_planette_olr(init, n_fc, lats, lons), obs, annual_cycle, lats)
        bias[: len(b)] = b
        bias[len(b) :] = b[-1]
    leadin_times = pd.date_range(end=init - pd.Timedelta(days=1), periods=leadin_days, freq="D")
    obs_anom = leadin - annual_cycle(leadin_times)
    for i in np.flatnonzero(gap_leads):
        obs_anom[i] -= bias[gap_leads[i] - 1]
    fc_anom = fc.values - annual_cycle(valid)[None] - bias[None]

    n_obs, n_valid = leadin_days, leadin_days + n_fc
    keep = slice(n_obs - window_before, n_valid)
    n_out = keep.stop - keep.start
    shape = (n_mem, n_out, len(lats), len(lons))
    filtered = {w: np.empty(shape, np.float32) for w in waves}
    batch = max(1, MEMBER_BATCH_CELLS // len(lats))
    print(f"Filtering {n_mem} members x {len(waves)} waves...", file=sys.stderr)
    for start in range(0, n_mem, batch):
        stop = min(n_mem, start + batch)
        series = np.zeros((SERIES_DAYS, stop - start, len(lats), len(lons)))
        series[:n_obs] = obs_anom[:, None]
        series[n_obs:n_valid] = fc_anom[start:stop].transpose(1, 0, 2, 3)
        for w, x in _filter(series, n_valid, waves, keep).items():
            filtered[w][start:stop] = x.transpose(1, 0, 2, 3)
    obs_series = np.zeros((SERIES_DAYS, len(lats), len(lons)))
    obs_series[:n_obs] = obs_anom
    obs_only = _filter(obs_series, n_obs, waves, keep)
    climate = _wave_climate(obs, lats, init, n_fc, waves)

    unfiltered = np.empty(shape, np.float32)
    unfiltered[:, :window_before] = obs_anom[-window_before:][None]
    unfiltered[:, window_before:] = fc_anom

    times = pd.date_range(init - pd.Timedelta(days=window_before), periods=n_out, freq="D")
    full = ("number", "time", "latitude", "longitude")
    data = {
        "olr_anomaly": (
            full,
            unfiltered,
            {
                "units": "W m-2",
                "long_name": "OLR anomaly from the ERA5 annual cycle (unfiltered)",
            },
        )
    }
    for w in waves:
        band, label, c = WAVES[w], WAVES[w]["label"], climate[w]
        data[f"olr_{w}"] = (
            full,
            filtered[w],
            {
                "units": "W m-2",
                "long_name": f"{label}-filtered OLR anomaly",
                "zonal_wavenumber": str(band["k"]),
                "period_days": str(band["period"]),
                "highpass_days": str(band["highpass"]),
                "equivalent_depth_m": str(band.get("h")),
                "amplitude_std_a": c["std_a"],
                "amplitude_std_b": c["std_b"],
            },
        )
        data[f"olr_{w}_obs_only"] = (
            ("time", "latitude", "longitude"),
            obs_only[w],
            {
                "units": "W m-2",
                "long_name": f"{label}-filtered OLR anomaly, observations followed by zeros "
                "(no forecast)",
            },
        )
        for name, units, text in (
            ("local_std_a", "W m-2", "filtered OLR anomaly"),
            ("local_std_b", "W m-2 day-1", "time derivative of the filtered OLR anomaly"),
        ):
            data[f"olr_{w}_{name}"] = (
                ("longitude",),
                c[name].astype(np.float32),
                {
                    "units": units,
                    "long_name": f"ERA5 seasonal std of the {AMP_LAT}S-{AMP_LAT}N {label} {text}",
                },
            )
        for name, q in (("p90", 90), ("p99", 99)):
            data[f"olr_{w}_clim_amp_{name}"] = (
                ("phase", "longitude"),
                c[name].astype(np.float32),
                {
                    "units": "1",
                    "long_name": f"ERA5 seasonal {q}th percentile of the local {label} amplitude "
                    "per phase",
                },
            )
        data[f"olr_{w}_clim_amp_p90_all"] = (
            ("longitude",),
            c["p90_all"].astype(np.float32),
            {
                "units": "1",
                "long_name": f"ERA5 seasonal 90th percentile of the local {label} amplitude",
            },
        )

    out = xr.Dataset(
        data,
        coords={
            "number": fc.number.values,
            "time": times,
            "latitude": lats,
            "longitude": lons,
            "phase": np.arange(1, 9),
            "is_forecast": ("time", np.asarray(times >= init)),
        },
    )
    out["latitude"].attrs.update(standard_name="latitude", units="degrees_north", axis="Y")
    out["longitude"].attrs.update(standard_name="longitude", units="degrees_east", axis="X")
    out["time"].attrs.update(standard_name="time", axis="T")
    out["number"].attrs.update(long_name="ensemble member (0 = control)")
    out["phase"].attrs.update(long_name="local wave phase (1 dry ... 5 wet, clockwise)")
    out["is_forecast"].attrs.update(long_name="day is forecast (else ERA5 lead-in)")
    out.attrs.update(
        {
            "Conventions": ds.attrs.get("Conventions", "CF-1.13"),
            "forecast_reference_time": f"{init:%Y-%m-%d}",
            "method": "Janiga et al. (2018) padded wavenumber-frequency filtering",
            "annual_cycle": f"mean + {N_HARMONICS} harmonics, ERA5 {CLIM_YEARS[0]}-{CLIM_YEARS[1]}",
            "bias_correction": "Planette ECMWF reforecasts 2006-2024" if bias_correct else "none",
            "amplitude_latitudes": f"{AMP_LAT}S-{AMP_LAT}N",
            "leadin_days": leadin_days,
            "era5t_gap_days": int((gap_leads > 0).sum()),
        }
    )
    return out


if __name__ == "__main__":
    olr_wave_filter()
