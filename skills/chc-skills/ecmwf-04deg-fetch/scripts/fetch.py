# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "weather-skills-core>=0.4.0",
#   "cftime>=1.6",
#   "gcsfs",
#   "google-auth",
#   "h5netcdf",
#   "h5py",
#   "numpy",
#   "pandas",
#   "xarray",
#   "zarr>=3",
# ]
# ///
"""Fetch a CHC 0.4 degree ECMWF daily precipitation ensemble from the Sheerwater datalake."""

# Source-to-output transform declaration (everything else passes through):
# - Dims: CHC's `M`, `L`, `Y`, `X` -> `number`, `step`, `latitude`, `longitude`.
# - Variable name: `pr` -> `tp`, as ecmwf-fetch names ECMWF precipitation.
# - Units: `mm/day` -> `mm day-1` (same unit, CF/udunits spelling). Values are untouched:
#   each lead is the rain of the 24 h starting at init + L days, i.e. a daily rate.
# - `step`: the integer lead-day index `L` becomes `L` days (timedelta), labelled at the
#   start of its 24 h interval; `data_interval = 1 day`. Scalar `time` is the init (from
#   the object name), `valid_time = time + step`.
# - Coordinates: float32 lat/lon -> float64 rounded to 4 decimals (0.4000001 -> 0.4);
#   int16 `number` -> int64. Latitude is written ascending by core, longitude stays [-180, 180).
# - standard_name `lwe_precipitation_rate` and long_name `Total precipitation` on `tp`.

import json
import os
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from datetime import date as _date

from weather_skills_core import DataError, UsageError, weather_skill
from weather_skills_core.cf import stamp_cf_attrs
from weather_skills_core.standard_utils import bbox_subset
from weather_skills_core.units import stamp_data_interval, to_standard_units

# Auto-populated by the version-bump CI workflow. Do not edit manually.
_SKILL_VERSION = "0.0.6"

_BUCKET = "sheerwater-datalake"
_PREFIX = "ecmwf-0_4/forecast"
# The archive keeps each init as a zarr store; inits archived before the switch to zarr
# may still exist only as CHC's netCDF file.
_OBJECT_RE = re.compile(r"pr_IFS-SUBS-0\.4_(\d{8})\.(zarr|daily\.nc)$")
_VARIABLES = {"tp": "pr", "pr": "pr", "precip": "pr"}
_MAX_LEADS = 46
# netCDF fallback: HDF5 chunks are (101 members, 1 lead, 50 lat, 150 lon), ~1 MB
# compressed each; blocks this size keep a bbox read to a few range requests per chunk.
_BLOCK_SIZE = 8 * 2**20

# Env vars holding a raw service-account key JSON, checked in this order:
# GCLOUD_SERVICE_CREDENTIALS is the name runners store the shared GCP key under;
# NEURAL_GCM_SERVICE_CREDENTIALS is the one pbc-fetch and neuralgcm-fetch read.
_CREDENTIAL_ENVS = ("GCLOUD_SERVICE_CREDENTIALS", "NEURAL_GCM_SERVICE_CREDENTIALS")

_AUTH_MSG = (
    f"cannot read gs://{_BUCKET}/{_PREFIX} (private GCS). Set "
    "GCLOUD_SERVICE_CREDENTIALS (or NEURAL_GCM_SERVICE_CREDENTIALS) to the full "
    "service-account key JSON (no file needed), GOOGLE_APPLICATION_CREDENTIALS to a "
    "service-account JSON file path, or "
    "run `gcloud auth application-default login` (a plain `gcloud auth login` also "
    "works when the gcloud CLI is on PATH)."
)

_FS = {}


def _gcloud_token():
    """Access token of the gcloud CLI's login, or None when gcloud is unavailable."""
    exe = shutil.which("gcloud")
    if exe is None:
        return None
    try:
        result = subprocess.run(
            [exe, "auth", "print-access-token"], capture_output=True, text=True, check=True
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip() or None


def _token():
    """gcsfs token: the service-account key JSON in GCLOUD_SERVICE_CREDENTIALS or
    NEURAL_GCM_SERVICE_CREDENTIALS, then None for GOOGLE_APPLICATION_CREDENTIALS /
    Application Default Credentials, then the gcloud CLI's login.
    """
    for name in _CREDENTIAL_ENVS:
        creds_json = os.environ.get(name, "").strip()
        if not creds_json:
            continue
        try:
            return json.loads(creds_json)
        except json.JSONDecodeError as exc:
            raise DataError(
                f"{name} is not valid JSON (line {exc.lineno}, column {exc.colno}); it must "
                "hold the full service-account key JSON."
            ) from None
    import google.auth
    import google.auth.exceptions

    try:
        google.auth.default()
        return None
    except google.auth.exceptions.DefaultCredentialsError:
        access = _gcloud_token()
        if access is None:
            raise DataError(_AUTH_MSG) from None
        import google.oauth2.credentials

        return google.oauth2.credentials.Credentials(access)


def _gcs(asynchronous=False):
    """Authenticated GCS filesystem (an asynchronous one for zarr, which takes it as is)."""
    if asynchronous not in _FS:
        import gcsfs

        token = _token() if "token" not in _FS else _FS["token"]
        _FS["token"] = token
        try:
            kwargs = {"asynchronous": asynchronous}
            if token is not None:
                kwargs["token"] = token
            _FS[asynchronous] = gcsfs.GCSFileSystem(**kwargs)
        except Exception as exc:  # noqa: BLE001 — surface auth failures cleanly
            raise DataError(f"{_AUTH_MSG} ({type(exc).__name__})") from None
    return _FS[asynchronous]


def _gcs_error(action: str, exc: Exception) -> DataError:
    """Classify a GCS failure: auth (401/403) or transport."""
    text = str(exc)
    if any(code in text for code in ("401", "403", "Permission", "Anonymous caller")):
        return DataError(f"{action}: access denied. {_AUTH_MSG}")
    return DataError(f"{action} failed ({type(exc).__name__}: {text[:200]}); retry later.")


def _archive_path(init: _date, suffix: str = "zarr") -> str:
    return f"{_BUCKET}/{_PREFIX}/{init:%Y}/pr_IFS-SUBS-0.4_{init:%Y%m%d}.{suffix}"


def _exists(path: str) -> bool:
    try:
        return _gcs().exists(path)
    except DataError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _gcs_error(f"checking gs://{path}", exc) from None


def _usable(kinds: dict):
    """The path to read for one init: a complete zarr store (its root zarr.json is
    uploaded last), else the netCDF file; None when neither is usable."""
    if "zarr" in kinds and _exists(f"{kinds['zarr']}/zarr.json"):
        return kinds["zarr"]
    return kinds.get("nc")


def _list_inits(years) -> dict:
    """{init date: {'zarr' or 'nc': path}} of what the archive's year folders hold."""
    found = {}
    for year in sorted(set(years)):
        prefix = f"{_BUCKET}/{_PREFIX}/{year}"
        try:
            entries = _gcs().ls(prefix, detail=False)
        except FileNotFoundError:
            continue
        except DataError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise _gcs_error(f"listing gs://{prefix}", exc) from None
        for path in entries:
            path = str(path).rstrip("/")
            match = _OBJECT_RE.search(path)
            if match:
                c = match.group(1)
                kind = "zarr" if match.group(2) == "zarr" else "nc"
                init = _date(int(c[:4]), int(c[4:6]), int(c[6:8]))
                found.setdefault(init, {})[kind] = path
    return found


def _latest_init():
    """Newest usable archived init: this year's, else last year's."""
    year = datetime.now(UTC).year
    for years in ([year], [year - 1]):
        inits = _list_inits(years)
        for init in sorted(inits, reverse=True):
            path = _usable(inits[init])
            if path is not None:
                return init, path
    raise DataError(
        f"no 0.4 degree forecasts in gs://{_BUCKET}/{_PREFIX}/{year - 1}/ or {year}/; "
        "the archive may be empty or unreachable."
    )


def _resolve_init(init):
    """(init date, object path) for --date, or the newest archived init without it."""
    if init is None:
        return _latest_init()
    kinds = {"zarr": _archive_path(init), "nc": _archive_path(init, "daily.nc")}
    if not _exists(kinds["nc"]):
        del kinds["nc"]
    path = _usable(kinds)
    if path is not None:
        return init, path
    inits = sorted(_list_inits([init.year - 1, init.year, init.year + 1]))
    span = f"{inits[0]} to {inits[-1]}" if inits else "none near that date"
    raise DataError(
        f"no 0.4 degree forecast for init {init.isoformat()} in gs://{_BUCKET}/{_PREFIX}/ "
        f"(archived inits: {span}; the archive starts when archiving began and has one "
        "init per day CHC published). Omit --date or use --probe-latest."
    )


def _open_source(path: str):
    """The archived forecast, opened lazily: the zarr store (chunks fetched concurrently)
    or the netCDF4 file (HTTP range reads)."""
    import xarray as xr

    try:
        if path.endswith(".zarr"):
            from zarr.storage import FsspecStore

            store = FsspecStore(_gcs(asynchronous=True), path=path, read_only=True)
            return xr.open_zarr(store, consolidated=True, decode_times=False, chunks=None)
        handle = _gcs().open(path, "rb", block_size=_BLOCK_SIZE)
        return xr.open_dataset(handle, engine="h5netcdf", decode_times=False)
    except DataError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _gcs_error(f"opening gs://{path}", exc) from None


def _parse_lead_days(value: str) -> int:
    """--max-lead as whole days (pandas timedelta: 46D, 14D, 336h)."""
    import pandas as pd

    try:
        days = pd.Timedelta(value) / pd.Timedelta(days=1)
    except ValueError:
        raise UsageError(f"--max-lead {value!r} is not a timedelta (46D, 14D, 336h)") from None
    if days <= 0 or days != int(days):
        raise UsageError(f"--max-lead must be a positive whole number of days, got {value!r}")
    return int(days)


def _to_forecast(raw, init, n_leads):
    """CHC's (M, L, Y, X) `pr` -> the weather-skills forecast layout, loaded in one read."""
    import numpy as np
    import xarray as xr

    missing = [d for d in ("M", "L", "Y", "X") if d not in raw.dims]
    if "pr" not in raw.data_vars or missing:
        raise DataError(
            "archived forecast is not the CHC layout (expected `pr` on M, L, Y, X; have "
            f"{', '.join(raw.data_vars)} on {', '.join(raw.dims)})."
        )
    raw = raw[["pr"]].rename({"M": "number", "L": "step", "Y": "latitude", "X": "longitude"})
    if n_leads > raw.sizes["step"]:
        print(
            f"The forecast has {raw.sizes['step']} lead days (requested {n_leads}).",
            file=sys.stderr,
        )
    raw = raw.isel(step=slice(0, n_leads))
    print(
        f"Reading {raw.sizes['number']} members x {raw.sizes['step']} lead days on "
        f"{raw.sizes['latitude']}x{raw.sizes['longitude']} cells",
        file=sys.stderr,
    )
    try:
        pr = raw["pr"].load()
    except Exception as exc:  # noqa: BLE001
        raise _gcs_error("reading the forecast", exc) from None

    init_ns = np.datetime64(init.isoformat(), "ns")
    steps = (pr["step"].values.astype("int64") * np.timedelta64(1, "D")).astype("timedelta64[ns]")
    tp = xr.DataArray(
        pr.values.astype("float32"),
        dims=("number", "step", "latitude", "longitude"),
        coords={
            "number": pr["number"].values.astype("int64"),
            "step": steps,
            "latitude": np.round(pr["latitude"].values.astype("float64"), 4),
            "longitude": np.round(pr["longitude"].values.astype("float64"), 4),
        },
        attrs={"units": "mm day-1", "long_name": "Total precipitation"},
    )
    ds = xr.Dataset({"tp": tp}).assign_coords(time=init_ns)
    ds["time"].attrs.update(
        standard_name="forecast_reference_time", long_name="initial time of forecast"
    )
    return ds.assign_coords(valid_time=("step", init_ns + steps))


@weather_skill(
    name="ecmwf-04deg-fetch",
    version=_SKILL_VERSION,
)
@weather_skill.argument(
    "--date",
    help=(
        "Init date YYYY-MM-DD. Default: the newest archived init (CHC posts init X on day "
        "X; the archive copies it at ~03 UTC on X+1)."
    ),
)
@weather_skill.argument("--bbox")
@weather_skill.argument(
    "--variable",
    "-v",
    action="append",
    help="Field to retrieve. Only tp (daily precipitation; `pr` is accepted). Default tp.",
)
@weather_skill.argument(
    "--max-lead",
    default=f"{_MAX_LEADS}D",
    help=(
        "Keep the lead days whose 24 h interval ends by this lead (pandas timedelta: 14D, "
        f"336h). Default {_MAX_LEADS}D, the whole forecast."
    ),
)
@weather_skill.argument(
    "--probe-latest",
    nargs="?",
    const="",
    metavar="IDENT",
    probe=True,
    help=(
        "Print the newest archived init date (YYYY-MM-DD, usable as --date) on stdout and "
        "exit. IDENT is ignored. Does not download fields."
    ),
)
def fetch(date, bbox, variable, max_lead, probe_latest, **kwargs):
    """Fetch one CHC 0.4 degree ECMWF daily precipitation ensemble (101 members, 46 lead
    days, 60S-60N) from the private archive gs://sheerwater-datalake/ecmwf-0_4/forecast/
    as a weather-skills forecast Zarr. Reads only the chunks inside --bbox."""
    if probe_latest is not None:
        init, _ = _latest_init()
        print(init.isoformat())
        return

    unknown = [v for v in (variable or []) if v not in _VARIABLES]
    if unknown:
        raise UsageError(
            f"unknown variable(s): {', '.join(unknown)}. Available: tp (alias pr, precip)."
        )
    n_leads = _parse_lead_days(max_lead)

    init, path = _resolve_init(date)
    print(f"Init {init.isoformat()}: gs://{path}", file=sys.stderr)
    raw = _open_source(path)
    try:
        if bbox is not None:
            raw = bbox_subset(raw, bbox, lat_dim="Y", lon_dim="X")
        ds = _to_forecast(raw, init, n_leads)
    finally:
        raw.close()

    ds.attrs.update(
        Conventions="CF-1.13",
        title="CHC standardized ECMWF 0.4-degree daily ensemble",
        source="ECMWF IFS sub-seasonal ensemble (IFS-SUBS-0.4), standardized by CHC",
        weather_skills_source=f"sheerwater-ecmwf-0_4:{init:%Y%m%d}",
        license="Licensed ECMWF data; private. Do not publish the raw fields.",
    )
    stamp_cf_attrs(ds)
    ds = to_standard_units(ds)
    return stamp_data_interval(ds, period="1 day")


if __name__ == "__main__":
    fetch()
