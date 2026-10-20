# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "weather-skills-core>=0.1.1",
#   "fsspec",
#   "aiohttp",
#   "cftime>=1.6",
#   "netcdf4",
#   "numpy",
#   "xarray",
# ]
# ///
"""Fetch one CHC SubC multi-model-ensemble outlook for an init date as a weather-skills Zarr."""

from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, date, datetime, timedelta

import numpy as np
import xarray as xr
from weather_skills_core import DataError, UsageError, weather_skill
from weather_skills_core.cf import stamp_cf_attrs
from weather_skills_core.standard_utils import bbox_subset, ensure_normalized_longitude
from weather_skills_core.units import stamp_precip_amounts, to_standard_units

# Auto-populated by the version-bump CI workflow. Do not edit manually.
_SKILL_VERSION = "0.0.6"

# Public GCS mirror of data.chc.ucsb.edu (same paths under chc-mirror/).
_BUCKET = "sheerwater-public-datalake"
_MIRROR_PREFIX = "chc-mirror/experimental/SubC"
_GCS_API = f"https://storage.googleapis.com/storage/v1/b/{_BUCKET}/o"
_GCS_MEDIA = f"https://storage.googleapis.com/{_BUCKET}"
SUBC_BASE = f"{_GCS_MEDIA}/{_MIRROR_PREFIX}"
CHC_BASE = "https://data.chc.ucsb.edu/experimental/SubC"
_USER_AGENT = "weather-skills/subc-mme-fetch"
DEFAULT_WORKERS = 4
_HTTP_TIMEOUT = 60

# Outlook id → (lead folder on server, filename lead tag, lead days).
OUTLOOKS: dict[str, tuple[str, str, int]] = {
    "7d": ("07_day", "7d", 7),
    "15d": ("15_day", "15d", 15),
    "30d": ("30_day", "30d", 30),
}
OUTLOOK_CHOICES = tuple(OUTLOOKS)

# Six published global MME map variables (mean + anomaly each).
VARIABLES: tuple[str, ...] = ("pr", "tas", "tasmax", "tasmin", "tdps", "ts")

# Human long_name per variable (the colorbar label); anomalies append " anomaly".
LONG_NAMES: dict[str, str] = {
    "pr": "Precipitation",
    "tas": "Near-surface air temperature",
    "tasmax": "Maximum near-surface air temperature",
    "tasmin": "Minimum near-surface air temperature",
    "tdps": "Dewpoint temperature",
    "ts": "Surface temperature",
}

KIND_MEAN = "mean"
KIND_ANOM = "anom"


def _get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        raise DataError(f"GCS listing failed for {url!r}: HTTP {exc.code} {exc.reason}") from None
    except urllib.error.URLError as exc:
        raise DataError(f"GCS listing failed for {url!r}: {exc.reason}") from None


def _object_key(folder: str, kind: str, var: str, lead_tag: str, init: date) -> str:
    name = f"mme_{kind}_{var}_{lead_tag}_{init.strftime('%Y%m%d')}.nc"
    return f"{_MIRROR_PREFIX}/{folder}/global/archive/{name}"


def _object_url(key: str) -> str:
    return f"{_GCS_MEDIA}/{urllib.parse.quote(key, safe='/')}"


def _url(folder: str, kind: str, var: str, lead_tag: str, init: date) -> str:
    return _object_url(_object_key(folder, kind, var, lead_tag, init))


def _archive_list_prefix(folder: str, lead_tag: str, var: str) -> str:
    return f"{_MIRROR_PREFIX}/{folder}/global/archive/mme_mean_{var}_{lead_tag}_"


def _candidate_urls(url: str) -> list[str]:
    """GCS mirror first; CHC origin if the URL is on the mirror prefix."""
    urls = [url]
    if url.startswith(SUBC_BASE):
        urls.append(CHC_BASE + url[len(SUBC_BASE) :])
    return urls


def _open_remote(url: str) -> xr.Dataset:
    """Open a SubC NetCDF. GCS mirror first, then CHC origin.

    Uses fsspec ``simplecache`` so netCDF4 opens a local materialized copy.
    """
    import fsspec

    last_missing: Exception | None = None
    for i, candidate in enumerate(_candidate_urls(url)):
        opts: dict = {}
        if i:
            opts["https"] = {"client_kwargs": {"headers": {"User-Agent": _USER_AGENT}}}
        try:
            path = fsspec.open_local(f"simplecache::{candidate}", **opts)
            return xr.open_dataset(path, engine="netcdf4")
        except FileNotFoundError as exc:
            last_missing = exc
            continue
        except Exception as exc:
            raise DataError(f"failed to open {candidate}: {exc}") from exc
    raise DataError(f"SubC archive file not found: {url}") from last_missing


def _normalize_field(ds: xr.Dataset, *, kind: str, var: str) -> xr.DataArray:
    """Rename Y/X → latitude/longitude and mme_mean/mme_anom → output var name."""
    rename = {}
    if "Y" in ds.dims or "Y" in ds.coords:
        rename["Y"] = "latitude"
    if "X" in ds.dims or "X" in ds.coords:
        rename["X"] = "longitude"
    if "latitude" not in rename.values() and "lat" in ds.dims:
        rename["lat"] = "latitude"
    if "longitude" not in rename.values() and "lon" in ds.dims:
        rename["lon"] = "longitude"
    ds = ds.rename(rename) if rename else ds

    src = "mme_mean" if kind == KIND_MEAN else "mme_anom"
    if src not in ds.data_vars:
        raise DataError(f"expected data variable {src!r}; got {list(ds.data_vars)}")
    out_name = var if kind == KIND_MEAN else f"{var}_anomaly"
    return ds[src].rename(out_name).load()


def _load_one(folder: str, kind: str, var: str, lead_tag: str, init: date):
    url = _url(folder, kind, var, lead_tag, init)
    with _open_remote(url) as raw:
        da = _normalize_field(raw, kind=kind, var=var)
    return kind, var, da


def _list_init_dates(folder: str, lead_tag: str, var: str) -> list[date]:
    """List archive init dates for one variable/outlook via the GCS JSON API."""
    prefix = _archive_list_prefix(folder, lead_tag, var)
    suffix = re.compile(re.escape(prefix) + r"(\d{8})\.nc$")
    found: set[date] = set()
    token = None
    while True:
        params: dict[str, str] = {"prefix": prefix, "maxResults": "1000"}
        if token:
            params["pageToken"] = token
        url = f"{_GCS_API}?{urllib.parse.urlencode(params)}"
        payload = _get_json(url)
        for item in payload.get("items", []):
            match = suffix.match(item.get("name", ""))
            if not match:
                continue
            raw = match.group(1)
            found.add(date(int(raw[:4]), int(raw[4:6]), int(raw[6:8])))
        token = payload.get("nextPageToken")
        if not token:
            break
    return sorted(found)


def _probe_latest(outlook: str, variables: list[str]) -> date | None:
    """Latest init present for every requested variable under one outlook."""
    folder, lead_tag, _days = OUTLOOKS[outlook]
    per_var: list[set[date]] = []
    for var in variables:
        dates = set(_list_init_dates(folder, lead_tag, var))
        if not dates:
            return None
        per_var.append(dates)
    common = set.intersection(*per_var) if per_var else set()
    return max(common) if common else None


def _resolve_vars(variable) -> list[str]:
    vars_wanted = list(dict.fromkeys(variable)) if variable else list(VARIABLES)
    unknown = [v for v in vars_wanted if v not in VARIABLES]
    if unknown:
        raise UsageError(f"unknown variable(s) {unknown}; choose from: {', '.join(VARIABLES)}")
    return vars_wanted


@weather_skill(name="subc-mme-fetch", version=_SKILL_VERSION)
@weather_skill.argument(
    "--date",
    help="Archive init date YYYY-MM-DD (required unless --probe-latest).",
)
@weather_skill.argument(
    "--outlook",
    required=True,
    choices=list(OUTLOOK_CHOICES),
    help="Outlook length to fetch: 7d, 15d, or 30d (one forecast per call).",
)
@weather_skill.argument("--bbox")
@weather_skill.argument(
    "--variable",
    "-v",
    action="append",
    help=(
        "Climate variable (repeatable): pr, tas, tasmax, tasmin, tdps, ts. "
        "Default: all six. For each selected variable both mean and anomaly "
        "are fetched. Prefer passing a variable to --probe-latest unless you "
        "need the latest date common to all variables."
    ),
)
@weather_skill.argument(
    "--workers",
    type=int,
    default=DEFAULT_WORKERS,
    help="Max concurrent remote opens (default 4).",
)
@weather_skill.argument(
    "--probe-latest",
    nargs="?",
    const="",
    metavar="VAR",
    probe=True,
    help=(
        "Print the latest available init YYYY-MM-DD for this --outlook on "
        "stdout and exit. Optional VAR restricts the probe to one climate "
        "variable (recommended). With no VAR, requires a date present for "
        "all six variables. Does not download fields."
    ),
)
def fetch(date, outlook, bbox, variable, workers, probe_latest, **kwargs):
    """Fetch one CHC SubC multi-model-ensemble outlook for an init date as a weather-skills Zarr."""
    # Each call returns a single-lead forecast envelope: scalar ``time`` is the
    # init (``--date``), ``step`` is ``0 days`` (the window start), and the window
    # length is each variable's ``aggregation_period``.
    if outlook not in OUTLOOKS:
        raise UsageError(
            f"unknown --outlook {outlook!r}; choose one of: {', '.join(OUTLOOK_CHOICES)}"
        )
    folder, lead_tag, outlook_days = OUTLOOKS[outlook]

    if probe_latest is not None:
        ident = probe_latest
        if ident:
            if ident not in VARIABLES:
                raise UsageError(
                    f"unknown --probe-latest variable {ident!r}; "
                    f"choose from: {', '.join(VARIABLES)}"
                )
            vars_to_probe = [ident]
        else:
            vars_to_probe = list(VARIABLES)
        latest = _probe_latest(outlook, vars_to_probe)
        print(latest.isoformat() if latest is not None else "none")
        if latest is not None:
            # SubC publishes irregularly; tell the caller when the newest window is already over.
            today = datetime.now(UTC).date()
            valid = latest + timedelta(days=outlook_days)
            age = (today - latest).days
            if valid < today:
                print(
                    f"warning: latest {outlook} init {latest} is {age} days old; its window "
                    f"ended {valid}, before today ({today}). It is not a forecast for the coming days.",
                    file=sys.stderr,
                )
            else:
                print(
                    f"latest {outlook} init {latest} is {age} days old; window runs to {valid}.",
                    file=sys.stderr,
                )
        return

    if date is None:
        raise UsageError("--date is required unless --probe-latest is set")
    init: date = date
    vars_wanted = _resolve_vars(variable)
    if workers < 1:
        raise UsageError("--workers must be >= 1")

    jobs = [(KIND_MEAN, var) for var in vars_wanted] + [(KIND_ANOM, var) for var in vars_wanted]

    results = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_load_one, folder, kind, var, lead_tag, init): (kind, var)
            for kind, var in jobs
        }
        for fut in as_completed(futures):
            results.append(fut.result())

    # Same forecast shape as the other fetchers: scalar `time` is the init and
    # `step` is the start of the one window it covers (0 days); the window length
    # is the `aggregation_period` stamped below.
    valid = init + timedelta(days=outlook_days)
    data_vars = {}
    for kind, var, da in results:
        out_name = var if kind == KIND_MEAN else f"{var}_anomaly"
        da = da.expand_dims(step=[np.timedelta64(0, "D")])
        data_vars[out_name] = da

    ds = xr.Dataset(data_vars)
    ds = ds.assign_coords(time=np.datetime64(init.isoformat(), "ns"))
    ds["time"].attrs.update(
        standard_name="forecast_reference_time",
        axis="T",
        long_name=f"SubC MME {outlook} outlook initialization",
    )
    ds["step"].attrs.update(
        standard_name="forecast_period",
        long_name="start of the outlook window (days from initialization)",
    )
    ds["latitude"].attrs.update(standard_name="latitude", units="degrees_north", axis="Y")
    ds["longitude"].attrs.update(standard_name="longitude", units="degrees_east", axis="X")

    ds = ds.sortby("latitude").sortby("longitude")

    if bbox is not None:
        # Pad by just under half a grid cell so every 1-degree cell that overlaps the bbox
        # is kept, not only those whose centre falls inside it (else a country's edge strip
        # is lost). bbox_subset is inclusive, so a full half cell would also keep cells that
        # only touch the box edge. The 1e-3 margin must exceed float32 coordinate resolution.
        pad = float(abs(ds["latitude"].values[1] - ds["latitude"].values[0])) / 2 - 1e-3
        north, west, south, east = bbox
        ds = bbox_subset(ds, (north + pad, west - pad, south - pad, east + pad))

    # Every field is one aggregate over the outlook window: precip a window sum (mm),
    # temperatures a window mean. Stamp the window so plot / chc-plot-theme can size palettes.
    period = f"{outlook_days} day"
    for name in ds.data_vars:
        base = name.removesuffix("_anomaly")
        long_name = LONG_NAMES[base] + (" anomaly" if name.endswith("_anomaly") else "")
        ds[name].attrs["long_name"] = long_name
        ds[name].attrs["comment"] = f"SubC MME {outlook} outlook, init {init.isoformat()}"
        ds[name].attrs["aggregation_period"] = period
        if base == "pr":
            ds[name].attrs.setdefault("units", "mm")
            ds[name].attrs["cell_methods"] = "time: sum"
            # Source NetCDFs mislabel 7-day window sums as precipitation_flux.
            ds[name].attrs.pop("standard_name", None)
        elif name.endswith("_anomaly"):
            # A temperature anomaly is a difference: delta_degC keeps to_standard_units
            # from applying the absolute K -> degC offset (-273.15).
            ds[name].attrs["units"] = "delta_degC"
            ds[name].attrs["cell_methods"] = "time: mean"
        else:
            ds[name].attrs.setdefault("units", "K")
            ds[name].attrs["cell_methods"] = "time: mean"

    ds.attrs["Conventions"] = "CF-1.13"
    ds.attrs["weather_skills_source"] = "chc-subc-mme"
    ds.attrs["outlook"] = outlook
    ds.attrs["initialization_date"] = init.isoformat()
    ds.attrs["outlook_valid_date"] = valid.isoformat()
    stamp_cf_attrs(ds)
    ds = ensure_normalized_longitude(ds, lon_dim="longitude")
    stamp_precip_amounts(ds)
    return to_standard_units(ds)


if __name__ == "__main__":
    fetch()
