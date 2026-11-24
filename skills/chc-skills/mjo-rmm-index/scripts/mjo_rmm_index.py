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
#   "xarray",
#   "zarr",
# ]
# ///
"""Compute the Wheeler-Hendon RMM (MJO) index for every member of an ensemble forecast."""

from __future__ import annotations

import sys
from functools import reduce
from pathlib import Path

from weather_skills_core import DataError, Dataset, UsageError, weather_skill

# Auto-populated by the version-bump CI workflow. Do not edit manually.
_SKILL_VERSION = "0.0.3"

OBS_SOURCE = "https://storage.googleapis.com/africa-forecasting-data/era5"
CACHE_DIR = Path.home() / ".cache" / "chc-skills" / "era5"
BOM_URL = "https://www.bom.gov.au/clim_data/IDCKGEM000/rmm.74toRealtime.txt"
BOM_FILE = "rmm.74toRealtime.txt"
DYNAMICAL_DATASET = "ecmwf-ifs-ens-forecast-46-day-daily-1-5-degree"
DYNAMICAL_OLR = "net_long_wave_radiation_flux_top_of_atmosphere"
OLR_NAMES = ("olr", DYNAMICAL_OLR, "ttr", "top_net_thermal_radiation", "mtnlwrf")
WIND_NAMES = ("wind_u", "u", "u_component_of_wind")

FIELDS = ("olr", "u850", "u200")
LEVELS = {"u850": 850, "u200": 200}
LAT_MAX = 15  # every field is averaged over 15S-15N
CLIM_YEARS = (2006, 2022)  # annual cycle and EOFs (the years both ERA5 archives cover)
N_HARMONICS = 4
MAX_LEAD_SMOOTHING = 7
MEAN_DAYS = 120  # running mean removed as interannual variability
DAY1_FALLBACK = 3
MAX_GAP_DAYS = 15
REFORECAST_YEARS = range(2006, 2025)


# ---------------------------------------------------------------------------
# Forecast inputs
# ---------------------------------------------------------------------------


def _pick_dim(ds, preferred):
    from weather_skills_core.standard_dataset import names_for

    return next((n for n in names_for(preferred) if n in ds.dims), None)


def _band_forecast(ds, variable, names, what, select=None):
    """One forecast variable -> (number, step, longitude) 15S-15N mean, plus init and steps."""
    import numpy as np
    import pandas as pd
    from weather_skills_core.standard_dataset import detect_spatial_dims
    from weather_skills_core.standard_utils import ensure_normalized_longitude

    if variable is None:
        variable = next((n for n in names if n in ds.data_vars), None)
        if variable is None:
            raise UsageError(
                f"no {what} variable ({', '.join(names)}) in the --{what} input; available: "
                f"{', '.join(ds.data_vars)}"
            )
    elif variable not in ds.data_vars:
        raise UsageError(
            f"{variable!r} not in the --{what} input; available: {', '.join(ds.data_vars)}"
        )
    step_dim = _pick_dim(ds, "prediction_timedelta")
    if step_dim is None or "time" not in ds.coords or ds["time"].ndim != 0:
        raise UsageError(f"--{what} must be one forecast: a scalar `time` init plus a `step` dim")
    lat_dim, lon_dim = detect_spatial_dims(ds)
    da = ds[variable].rename({lat_dim: "latitude", lon_dim: "longitude", step_dim: "step"})
    if select is not None:
        da = select(da)
    member_dim = _pick_dim(da.to_dataset(name="v"), "member")
    if member_dim is None:
        da = da.expand_dims(number=[0])
    elif member_dim != "number":
        da = da.rename({member_dim: "number"})
    da = ensure_normalized_longitude(da, "longitude").sortby("longitude")
    lat = da.latitude.values
    band = np.abs(lat) <= LAT_MAX + 1e-6
    if lat.max() < LAT_MAX - 2 or lat.min() > -LAT_MAX + 2:
        raise UsageError(
            f"--{what} must cover {LAT_MAX}S-{LAT_MAX}N; it covers {lat.min():g}..{lat.max():g}"
        )
    lon = da.longitude.values
    spacing = float(np.median(np.diff(lon)))
    if abs(len(lon) * spacing - 360) > spacing / 2:
        raise UsageError(
            f"the RMM needs every longitude; re-fetch --{what} with --bbox 15/-180/-15/180"
        )
    da = da.isel(latitude=np.flatnonzero(band)).mean("latitude")
    extra = [d for d in da.dims if d not in ("number", "step", "longitude")]
    if extra:
        raise UsageError(f"{variable} in --{what} has extra dims {extra}; select them away first")
    da = da.transpose("number", "step", "longitude").load()
    if np.isnan(da.values).any():
        raise DataError(f"the --{what} forecast has missing values (still publishing?)")
    days = pd.to_timedelta(da.step.values) / pd.Timedelta(days=1)
    if days[0] != 0 or not np.allclose(np.diff(days), 1):
        raise UsageError(
            f"--{what} needs consecutive daily steps from step 0; got {list(days[:3])}..."
        )
    return da, pd.Timestamp(ds["time"].values).normalize()


def _daily_means(x):
    """Instants at 0, 1, 2, ... days along axis 1 -> the mean of each consecutive pair."""
    return 0.5 * (x[:, :-1] + x[:, 1:])


def _forecast_fields(olr_ds, wind_ds, variable, wind_variable, wind_daily_mean):
    """{field: (number, n_days, lon)} anomaly inputs, plus init, lons and members."""
    import numpy as np
    from weather_skills_core.standard_dataset import names_for

    olr, init = _band_forecast(olr_ds, variable, OLR_NAMES, "olr")
    if olr.values.mean() < 0:  # net longwave at the top of the atmosphere: negative upward
        olr = -olr
    vdim = next((n for n in names_for("vertical") if n in wind_ds.dims), None)
    if vdim is None:
        raise UsageError("--wind needs a pressure-level dim (`vertical`) with 850 and 200 hPa")
    fields = {"olr": olr}
    for name, level in LEVELS.items():
        levels = wind_ds[vdim].values
        if not np.isclose(levels, level).any():
            raise UsageError(f"--wind has no {level} hPa level (levels: {list(levels)})")

        def pick(da, level=level):
            return da.sel({vdim: level}, method="nearest", drop=True)

        u, wind_init = _band_forecast(wind_ds, wind_variable, WIND_NAMES, "wind", select=pick)
        if wind_init != init:
            raise UsageError(
                f"--olr init {init:%Y-%m-%d} and --wind init {wind_init:%Y-%m-%d} differ"
            )
        fields[name] = u
    members = reduce(np.intersect1d, (fields[f].number.values for f in FIELDS))
    if not len(members):
        raise UsageError("--olr and --wind share no ensemble members")
    lons = olr.longitude.values
    out = {}
    for f in FIELDS:
        da = fields[f].sel(number=members)
        if not np.allclose(da.longitude.values, lons):
            raise UsageError("--olr and --wind must be on the same longitude grid")
        x = da.values.astype(np.float64)
        out[f] = x if f == "olr" or wind_daily_mean else _daily_means(x)
    n = min(x.shape[1] for x in out.values())
    return {f: x[:, :n] for f, x in out.items()}, init, lons, members


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


def _on_lons(da, lons, what):
    from weather_skills_core.standard_utils import ensure_normalized_longitude

    da = ensure_normalized_longitude(da, "longitude")
    try:
        da = da.sel(longitude=lons, method="nearest", tolerance=0.01)
    except KeyError as exc:
        raise UsageError(
            f"the forecast is not on the 1.5 degree grid of {what}; fetch {DYNAMICAL_DATASET} "
            "or regrid with the coarsen skill (--resolution 1.5)"
        ) from exc
    return da.assign_coords(longitude=lons)


def _read_obs(source, years, lons):
    """{field: ERA5 daily 15S-15N mean (time, longitude)} for `years`."""
    import xarray as xr

    parts = {f: [] for f in FIELDS}
    for year in years:
        path = _obs_path(source, f"era5_olr_{year}.nc")
        if path is not None:
            with xr.open_dataset(path, engine="netcdf4") as f:
                olr = f["olr"]
                band = olr.isel(latitude=abs(olr.latitude.values) <= LAT_MAX + 1e-6)
                band = band.mean("latitude")
                parts["olr"].append(_on_lons(band, lons, "the ERA5 archive").load())
        path = _obs_path(source, f"era5_u_15S15N_{year}.nc")
        if path is not None:
            with xr.open_dataset(path, engine="netcdf4") as f:
                for name in LEVELS:
                    parts[name].append(_on_lons(f[name], lons, "the ERA5 archive").load())
    obs = {}
    for name, files in parts.items():
        if not files:
            raise DataError(f"no ERA5 {name} archive files found at {source}")
        obs[name] = xr.concat(files, dim="time").sortby("time").astype("float64")
    return obs


class _Dynamical:
    """Lazily opened dynamical.org stores for the days ERA5T hasn't reached."""

    def __init__(self):
        self.stores = {}

    def _open(self, group):
        if group not in self.stores:
            import dynamical_catalog

            try:
                kwargs = {"group": group} if group else {}
                self.stores[group] = dynamical_catalog.open(
                    DYNAMICAL_DATASET, chunks=None, **kwargs
                )
            except Exception as exc:
                raise DataError(
                    f"could not open {DYNAMICAL_DATASET} on dynamical.org for the days ERA5T "
                    f"hasn't reached ({exc}); retry, or pick an init a few days older"
                ) from exc
        return self.stores[group]

    def day(self, field, day, lons):
        """Ensemble-mean 15S-15N field for `day` from the lead 1 (else 2..3) forecast."""
        import numpy as np
        import pandas as pd

        if field == "olr":
            da = self._open(None)[DYNAMICAL_OLR]
        else:
            da = self._open("pressure_level")["wind_u"].sel(pressure_level=LEVELS[field])
        inits = da.get_index("init_time")
        for lead in range(1, DAY1_FALLBACK + 1):
            init = day - pd.Timedelta(days=lead - 1)
            if init not in inits:
                continue
            if field == "olr":
                x = da.sel(init_time=init, lead_time=pd.Timedelta(days=lead))
            else:  # the mean of the 00 UTC instants either side of the day
                x = da.sel(
                    init_time=init, lead_time=[pd.Timedelta(days=lead - 1), pd.Timedelta(days=lead)]
                )
                x = x.mean("lead_time")
            x = x.sel(latitude=slice(LAT_MAX, -LAT_MAX)).mean(("latitude", "ensemble_member"))
            x = _on_lons(x, lons, DYNAMICAL_DATASET).values
            if not np.isnan(x).any():
                print(
                    f"  {field} {day:%Y-%m-%d}: not in ERA5 yet, using ECMWF lead {lead}",
                    file=sys.stderr,
                )
                return (-x if field == "olr" and x.mean() < 0 else x), lead
        raise DataError(
            f"no {DYNAMICAL_DATASET} run covers {day:%Y-%m-%d} at leads 1-{DAY1_FALLBACK}; "
            "pick an init a few days older"
        )


def _leadin(obs, days, lons, dynamical, field):
    """(values (n_days, lon), gap leads (n_days,), 0 = ERA5) of one field."""
    import numpy as np
    import pandas as pd

    have = obs.get_index("time")
    if days[0] < have.min():
        raise DataError(
            f"the ERA5 {field} archive starts {have.min():%Y-%m-%d}, after {days[0]:%Y-%m-%d}"
        )
    last = have.max()
    holes = days[(days <= last) & ~days.isin(have)]
    if len(holes):
        raise DataError(
            f"the ERA5 {field} archive is missing {len(holes)} lead-in days "
            f"({holes[0]:%Y-%m-%d}..{holes[-1]:%Y-%m-%d}); pick another init"
        )
    gap = days[days > last]
    if len(gap) > MAX_GAP_DAYS:
        raise DataError(
            f"the ERA5 {field} archive ends {last:%Y-%m-%d}, {len(gap)} days before this init; "
            f"pick an init up to {last + pd.Timedelta(days=MAX_GAP_DAYS + 1):%Y-%m-%d}"
        )
    values = np.empty((len(days), len(lons)))
    leads = np.zeros(len(days), dtype=int)
    n_have = len(days) - len(gap)
    values[:n_have] = obs.sel(time=days[:n_have]).values
    for i, day in enumerate(gap):
        values[n_have + i], leads[n_have + i] = dynamical.day(field, day, lons)
    return values, leads


def _read_bom(source):
    """BoM's RMM1/RMM2 as a DataFrame (x, y): the archive copy, else bom.gov.au."""
    import io
    from urllib.request import Request, urlopen

    import pandas as pd

    path = _obs_path(source, BOM_FILE)
    if path is not None:
        text = path.read_text()
    else:
        try:
            req = Request(BOM_URL, headers={"User-Agent": "Mozilla/5.0"})
            text = urlopen(req, timeout=120).read().decode()
        except Exception as exc:
            raise DataError(
                f"BoM's RMM ({BOM_FILE}) is not at {source} and {BOM_URL} failed ({exc}); "
                "retry, or pass --no-align-bom"
            ) from exc
    df = pd.read_csv(
        io.StringIO(text),
        skiprows=2,
        sep=r"\s+",
        header=None,
        usecols=range(5),
        names=["year", "month", "day", "x", "y"],
    )
    df = df.set_index(pd.to_datetime(df[["year", "month", "day"]]))[["x", "y"]]
    return df[(df.abs() < 100).all(axis=1)]


# ---------------------------------------------------------------------------
# Anomalies: annual cycle and (optional) lead-dependent reforecast bias
# ---------------------------------------------------------------------------


def _harmonics(times):
    import numpy as np
    import pandas as pd

    days = (pd.DatetimeIndex(times) - pd.Timestamp("2000-01-01")).days.values
    phase = 2 * np.pi * days / 365.25
    cols = [np.ones_like(phase, dtype=float)]
    for k in range(1, N_HARMONICS + 1):
        cols += [np.cos(k * phase), np.sin(k * phase)]
    return np.column_stack(cols)


def _fit_annual_cycle(obs):
    """Mean + N_HARMONICS harmonics of obs (time, lon) over CLIM_YEARS: times -> ndarray."""
    import numpy as np

    fit = obs.sel(time=slice(str(CLIM_YEARS[0]), str(CLIM_YEARS[1])))
    coef, *_ = np.linalg.lstsq(_harmonics(fit.time.values), fit.values, rcond=None)
    return lambda times: _harmonics(times) @ coef


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


def _planette_fields(init, n_days, lons):
    """{field: reforecast ensemble-mean 15S-15N (init_time, step, lon)}, step 0 = day 1."""
    import numpy as np

    def band_mean(da, n_steps):
        da = da.isel(init_time=_reforecast_inits(da.get_index("init_time"), init))
        da = da.isel(step=slice(0, n_steps))
        da = da.isel(latitude=np.abs(da.latitude.values) <= LAT_MAX + 1e-6).mean("latitude")
        return _on_lons(da, lons, "the Planette reforecasts").mean("number").load()

    print("Reading Planette OLR reforecasts...", file=sys.stderr)
    olr = band_mean(_open_planette("single")["olr"], n_days)
    x = olr.transpose("init_time", "step", "longitude").values.astype(np.float64)
    out = {"olr": (olr.init_time.values, -x if x.mean() < 0 else x)}
    print("Reading Planette u850/u200 reforecasts...", file=sys.stderr)
    u = _open_planette("pressure")["u"].sel(pressure_level=list(LEVELS.values()))
    u = band_mean(u, n_days + 1)  # 00 UTC instants, averaged in pairs into days
    for field, level in LEVELS.items():
        x = u.sel(pressure_level=level).transpose("init_time", "step", "longitude").values
        x = x.astype(np.float64)
        out[field] = (u.init_time.values, 0.5 * (x[:, :-1] + x[:, 1:]))
    return out


def _smooth_over_leads(x):
    import numpy as np

    out = np.empty_like(x)
    n = x.shape[0]
    for s in range(n):
        h = min(MAX_LEAD_SMOOTHING, s // 2)
        out[s] = x[max(0, s - h) : min(n, s + h + 1)].mean(axis=0)
    return out


def _lead_bias(inits, rf, obs, annual_cycle, n_days):
    """Lead bias (n_days, lon): per lead, mean over years of (rf_anom - beta * obs_anom),
    smoothed over neighbouring leads, padded with the last lead to n_days."""
    import numpy as np
    import pandas as pd

    have = obs.get_index("time")
    r, o = [], []
    for init, x in zip(inits, rf, strict=True):
        valid = pd.Timestamp(init).normalize() + pd.to_timedelta(np.arange(x.shape[0]), unit="D")
        if not valid.isin(have).all():
            continue
        ac = annual_cycle(valid)
        r.append(x - ac)
        o.append(obs.sel(time=valid).values - ac)
    if len(r) < 10:
        raise DataError(f"only {len(r)} reforecast years have ERA5 to compare with")
    r, o = np.array(r), np.array(o)
    beta = (r * o).sum(axis=(0, 2)) / (o**2).sum(axis=(0, 2))
    bias = _smooth_over_leads((r - beta[None, :, None] * o).mean(axis=0))
    if len(bias) < n_days:
        bias = np.concatenate([bias, np.repeat(bias[-1:], n_days - len(bias), axis=0)])
    return bias[:n_days]


# ---------------------------------------------------------------------------
# RMM
# ---------------------------------------------------------------------------


def _minus_running_mean(x):
    """x (time, ...) minus the mean of the MEAN_DAYS days before each day; the first
    MEAN_DAYS days have no full window and are dropped."""
    import numpy as np

    c = np.cumsum(np.concatenate([np.zeros_like(x[:1]), x]), axis=0)
    return x[MEAN_DAYS:] - (c[MEAN_DAYS:-1] - c[: -MEAN_DAYS - 1]) / MEAN_DAYS


def _eof_axes(clim, times):
    """clim {field: ERA5 anomalies (time, lon)} -> per-field normalisation, the combined
    field's mean, the two leading EOFs scaled to unit-std PCs, the observed index, and the
    explained variance."""
    import numpy as np
    import pandas as pd

    x = {f: _minus_running_mean(clim[f]) for f in FIELDS}
    norm = {f: float(x[f].std()) for f in FIELDS}
    X = np.hstack([x[f] / norm[f] for f in FIELDS])
    centre = X.mean(axis=0)
    _, s, vt = np.linalg.svd(X - centre, full_matrices=False)
    var = s**2 / (s**2).sum()
    eofs = vt[:2] / ((X - centre) @ vt[:2].T).std(axis=0)[:, None]
    index = pd.DataFrame((X - centre) @ eofs.T, index=times[MEAN_DAYS:], columns=["x", "y"])
    return norm, centre, eofs, index, var[:2]


def _align_to(ours, bom):
    """Orthogonal 2x2 R (rotation, maybe + reflection) best mapping ours onto BoM's RMM."""
    import numpy as np

    both = ours.join(bom, how="inner", rsuffix="_bom")
    if len(both) < 365:
        raise DataError(f"only {len(both)} days in common with BoM's RMM; pass --no-align-bom")
    u, _, vt = np.linalg.svd(both[["x", "y"]].values.T @ both[["x_bom", "y_bom"]].values)
    return u @ vt


def _correlation(ours, bom):
    import numpy as np

    both = ours.join(bom, how="inner", rsuffix="_bom")
    a, b = both[["x", "y"]].values, both[["x_bom", "y_bom"]].values
    corr = (a * b).sum() / np.sqrt((a**2).sum() * (b**2).sum())
    return float(corr), float(np.sqrt(((a - b) ** 2).sum(axis=1).mean())), len(both)


def _phase(x, y):
    """MJO phase 1-8 of (RMM1, RMM2): 45-degree sectors, phase 1 from 180 degrees anticlockwise."""
    import numpy as np

    return (np.floor((np.degrees(np.arctan2(y, x)) + 180) / 45).astype(int) % 8) + 1


@weather_skill(name="mjo-rmm-index", version=_SKILL_VERSION)
@weather_skill.argument(
    "--olr",
    type=Dataset("forecast"),
    required=True,
    help="Forecast OLR (or top net longwave) Zarr covering 15S-15N at every longitude.",
)
@weather_skill.argument(
    "--wind",
    type=Dataset("forecast"),
    required=True,
    help="Forecast zonal wind Zarr with 850 and 200 hPa on a `vertical` dim, 15S-15N.",
)
@weather_skill.argument(
    "--variable",
    "-v",
    default=None,
    help=f"OLR variable in --olr (default: first of {', '.join(OLR_NAMES)}).",
)
@weather_skill.argument(
    "--wind-variable",
    default=None,
    help=f"Zonal wind variable in --wind (default: first of {', '.join(WIND_NAMES)}).",
)
@weather_skill.argument(
    "--wind-daily-mean",
    action="store_true",
    help="--wind steps are already daily means (default: 00 UTC instants, averaged in pairs).",
)
@weather_skill.argument(
    "--obs-days", type=int, default=40, help="Observed days kept before the init (default 40)."
)
@weather_skill.argument(
    "--bias-correct",
    action="store_true",
    help="Remove the lead-dependent model bias estimated from Planette ECMWF reforecasts.",
)
@weather_skill.argument(
    "--no-align-bom",
    action="store_true",
    help="Skip rotating the EOF axes onto BoM's RMM1/RMM2 (phases are then not WH04's).",
)
@weather_skill.argument(
    "--obs-source",
    default=OBS_SOURCE,
    help="ERA5 archive (era5_olr_<YYYY>.nc, era5_u_15S15N_<YYYY>.nc, rmm.74toRealtime.txt): "
    "base URL or local folder (default: public bucket).",
)
def mjo_rmm_index(
    olr,
    wind,
    variable,
    wind_variable,
    wind_daily_mean,
    obs_days,
    bias_correct,
    no_align_bom,
    obs_source,
    **kwargs,
):
    """Compute the Wheeler-Hendon RMM (MJO) index for every member of an ensemble forecast."""
    import numpy as np
    import pandas as pd
    import xarray as xr
    from weather_skills_core.units import dequantify_dataset

    if obs_days < 1:
        raise UsageError("--obs-days must be at least 1")
    fc, init, lons, members = _forecast_fields(
        dequantify_dataset(olr), dequantify_dataset(wind), variable, wind_variable, wind_daily_mean
    )
    n_fc = fc["olr"].shape[1]
    valid = init + pd.to_timedelta(np.arange(n_fc), unit="D")

    years = range(CLIM_YEARS[0], init.year + 1)
    print(f"Reading the ERA5 archive {years[0]}-{years[-1]} from {obs_source}", file=sys.stderr)
    obs = _read_obs(obs_source, years, lons)
    leadin_days = pd.date_range(
        end=init - pd.Timedelta(days=1), periods=MEAN_DAYS + obs_days, freq="D"
    )
    rf = _planette_fields(init, n_fc, lons) if bias_correct else None
    dynamical = _Dynamical()

    clim, series = {}, {}
    clim_times = pd.date_range(f"{CLIM_YEARS[0]}-01-01", f"{CLIM_YEARS[1]}-12-31", freq="D")
    for f in FIELDS:
        missing = clim_times[~clim_times.isin(obs[f].get_index("time"))]
        if len(missing):
            raise DataError(f"the ERA5 {f} archive lacks {len(missing)} days of {CLIM_YEARS}")
        annual_cycle = _fit_annual_cycle(obs[f])
        bias = np.zeros((n_fc, len(lons)))
        if rf is not None:
            bias = _lead_bias(*rf[f], obs[f], annual_cycle, n_fc)
        clim[f] = obs[f].sel(time=clim_times).values - annual_cycle(clim_times)
        leadin, leads = _leadin(obs[f], leadin_days, lons, dynamical, f)
        anom = leadin - annual_cycle(leadin_days)
        for i in np.flatnonzero(leads):
            anom[i] -= bias[leads[i] - 1]
        fc_anom = fc[f] - annual_cycle(valid)[None] - bias[None]  # (member, step, lon)
        series[f] = np.concatenate(
            [
                np.broadcast_to(anom[:, None], (len(anom), len(members), len(lons))),
                fc_anom.transpose(1, 0, 2),
            ]
        )

    norm, centre, eofs, obs_index, var = _eof_axes(clim, clim_times)
    align, bom = np.eye(2), None
    if not no_align_bom:
        bom = _read_bom(obs_source)
        align = _align_to(obs_index, bom)
        obs_index[["x", "y"]] = obs_index[["x", "y"]].values @ align
        corr = _correlation(obs_index, bom)
        print(
            f"EOF-period index vs BoM RMM: correlation {corr[0]:.2f}, RMSE {corr[1]:.2f} "
            f"({corr[2]} days)",
            file=sys.stderr,
        )

    X = np.concatenate([_minus_running_mean(series[f]) / norm[f] for f in FIELDS], axis=-1)
    rmm = (X - centre) @ eofs.T @ align  # (time, member, 2)
    times = pd.date_range(init - pd.Timedelta(days=obs_days), periods=rmm.shape[0], freq="D")
    is_fc = np.asarray(times >= init)
    mean = rmm.mean(axis=1)
    bom_xy = np.full((len(times), 2), np.nan)
    if bom is not None:
        bom_xy = bom.reindex(times).values
        bom_xy[is_fc] = np.nan

    def var1(values, long_name, units="1", dims=("time",)):
        return dims, values, {"long_name": long_name, "units": units}

    out = xr.Dataset(
        {
            "rmm1": var1(rmm[..., 0].astype("float32"), "RMM1 per member", dims=("time", "number")),
            "rmm2": var1(rmm[..., 1].astype("float32"), "RMM2 per member", dims=("time", "number")),
            "rmm1_mean": var1(mean[:, 0], "RMM1 of the ensemble mean"),
            "rmm2_mean": var1(mean[:, 1], "RMM2 of the ensemble mean"),
            "amplitude_mean": var1(
                np.hypot(mean[:, 0], mean[:, 1]), "RMM amplitude of the ensemble mean"
            ),
            "phase_mean": var1(
                _phase(mean[:, 0], mean[:, 1]).astype("int8"),
                "MJO phase (1-8) of the ensemble mean",
            ),
            "rmm1_bom": var1(bom_xy[:, 0], "BoM RMM1 on the observed days"),
            "rmm2_bom": var1(bom_xy[:, 1], "BoM RMM2 on the observed days"),
        },
        coords={
            "time": times,
            "number": members,
            "is_forecast": ("time", is_fc, {"long_name": "day is forecast (else ERA5 lead-in)"}),
        },
    )
    out["time"].attrs.update(standard_name="time", axis="T")
    out["number"].attrs.update(long_name="ensemble member (0 = control)")
    out.attrs = {
        "Conventions": olr.attrs.get("Conventions", "CF-1.13"),
        "forecast_reference_time": f"{init:%Y-%m-%d}",
        "description": "RMM index (Wheeler and Hendon 2004): ERA5 EOFs of 15S-15N OLR, u850 and "
        "u200"
        + ("" if no_align_bom else ", rotated onto BoM's RMM1/RMM2")
        + "; observed days are the same for every member",
        "eof_years": f"{CLIM_YEARS[0]}-{CLIM_YEARS[1]}",
        "eof_explained_variance": f"{var[0]:.3f}, {var[1]:.3f}",
        "normalisation": ", ".join(f"{f} {norm[f]:.2f}" for f in FIELDS),
        "bias_correction": "Planette ECMWF reforecasts 2006-2024" if bias_correct else "none",
    }
    if bom is not None:
        out.attrs.update(
            bom_correlation=round(corr[0], 3), bom_rmse=round(corr[1], 3), bom_days=corr[2]
        )
    return out


if __name__ == "__main__":
    mjo_rmm_index()
