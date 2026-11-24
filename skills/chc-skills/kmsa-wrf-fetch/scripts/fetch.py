# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "weather-skills-core>=0.4.0",
#   "cftime>=1.6",
#   "gcsfs",
#   "netcdf4",
#   "numpy",
#   "xarray",
# ]
# ///
"""Read a KMSA WRF forecast run (7-day or daily) from the NWP archive as a weather-skills Zarr."""

import json
import os
import re
import sys
import tempfile
from pathlib import Path

from weather_skills_core import DataError, UsageError, weather_skill
from weather_skills_core.cf import stamp_cf_attrs
from weather_skills_core.standard_utils import bbox_subset, ensure_normalized_longitude
from weather_skills_core.units import (
    precip_amounts_to_rates,
    stamp_data_interval,
    stamp_precip_amounts,
    to_standard_units,
)

# Auto-populated by the version-bump CI workflow. Do not edit manually.
_SKILL_VERSION = "0.0.5"

# Daily mirror of /home/nwp1/LIBS2/{7_day,Daily_Data} on the KMSA NWP workstation, plus
# full_wrf/: whole WRF output files for single runs.
_DEFAULT_ROOT = "gs://sheerwater-datalake/kmsa-wrf"
# --product → archive folder under the root.
_PRODUCTS = {"7day": "7_day", "daily": "Daily_Data", "full": "full_wrf"}
# --product → file name pattern; group 1 is the YYYYMMDD run start date.
_RUN_RE = re.compile(r"^(\d{8})_to_(\d{8})_fcst\.nc$")
_FILE_RES = {"7day": _RUN_RE, "daily": _RUN_RE, "full": re.compile(r"^wrf_full_(\d{8})\.nc$")}


@weather_skill(name="kmsa-wrf-fetch", version=_SKILL_VERSION)
@weather_skill.argument(
    "--product",
    default="7day",
    choices=list(_PRODUCTS),
    help=(
        "7day: the 7- to 8-day run (7_day/); daily: the 3-day run (Daily_Data/); full: every "
        "WRF output field of a run (full_wrf/wrf_full_YYYYMMDD.nc). Default 7day."
    ),
)
@weather_skill.argument(
    "--root",
    default=_DEFAULT_ROOT,
    help=(
        "gs:// prefix or local folder holding 7_day/, Daily_Data/ and full_wrf/. "
        f"Default: {_DEFAULT_ROOT}."
    ),
)
@weather_skill.argument("--date", help="Run start date, as in the archive filename.")
@weather_skill.argument("--bbox")
@weather_skill.argument(
    "--variable",
    "-v",
    action="append",
    help=(
        "tp (total precipitation) or t2m (2 m temperature); with --product full also any WRF "
        "field by its name in the file (u10, psfc, qvapor, ...). Repeatable. Default: all."
    ),
)
@weather_skill.argument(
    "--probe-latest",
    action="store_true",
    probe=True,
    help="Print the latest run start date in the archive (YYYY-MM-DD, or none) and exit.",
)
def fetch(product, root, date, bbox, variable, probe_latest, **kwargs):
    """Read a KMSA WRF forecast run (7-day or daily) from the NWP archive as a weather-skills Zarr."""
    import numpy as np
    import xarray as xr

    if not probe_latest and date is None:
        raise UsageError("--date is required unless --probe-latest is set")

    folder = f"{root.rstrip('/')}/{_PRODUCTS[product]}"
    remote = folder.startswith("gs://")
    if remote:
        import gcsfs

        # GCLOUD_SERVICE_CREDENTIALS (key JSON), else GOOGLE_APPLICATION_CREDENTIALS / ADC.
        creds_json = os.environ.get("GCLOUD_SERVICE_CREDENTIALS")
        try:
            token = json.loads(creds_json) if creds_json else None
        except json.JSONDecodeError as exc:
            raise DataError(f"GCLOUD_SERVICE_CREDENTIALS is not valid JSON ({exc}).") from None
        try:
            fs = gcsfs.GCSFileSystem(token=token)
            names = [key.rsplit("/", 1)[-1] for key in fs.ls(folder, detail=False)]
        except FileNotFoundError:
            raise DataError(f"archive folder not found: {folder}") from None
        except Exception as exc:  # noqa: BLE001 — surface auth / network failures cleanly
            raise DataError(
                f"cannot read {folder} (private GCS). Set GCLOUD_SERVICE_CREDENTIALS to the "
                "full service-account key JSON (no file needed), GOOGLE_APPLICATION_CREDENTIALS "
                "to a service-account JSON file path, or run "
                f"`gcloud auth application-default login`. ({exc})"
            ) from None
    else:
        local = Path(folder).expanduser()
        if not local.is_dir():
            raise DataError(f"archive folder not found: {local}")
        names = [p.name for p in local.iterdir()]
    # YYYYMMDD run start date → file name, for every well-named NetCDF.
    runs = {m.group(1): m.group(0) for m in map(_FILE_RES[product].match, names) if m}

    if probe_latest:
        latest = max(runs) if runs else None
        print(f"{latest[:4]}-{latest[4:6]}-{latest[6:]}" if latest else "none")
        return

    stamp = date.strftime("%Y%m%d")
    if stamp not in runs:
        raise DataError(
            f"no {product} run starting {date.isoformat()} in {folder}. "
            "Use --probe-latest for the newest run."
        )
    name = runs[stamp]
    print(f"Reading {folder}/{name}", file=sys.stderr)

    # Read lazily and load only after the variable and bbox subset: full files are ~4 GB.
    tmp = tempfile.TemporaryDirectory()
    if remote:
        path = Path(tmp.name) / name
        fs.get(f"{folder}/{name}", str(path))
    else:
        path = local / name
    raw = xr.open_dataset(path, decode_times=False)
    # Units "hours since 1-1-1" make xarray warn about the ambiguous year; pad it.
    raw["time"].attrs["units"] = "hours since 0001-01-01 00:00:00"
    ds = xr.decode_cf(raw)

    missing = [v for v in ("rainc", "rainnc", "t2") if v not in ds.data_vars]
    if missing:
        raise DataError(
            f"{name} lacks {', '.join(missing)}; only runs in the current archive "
            "format (rainc, rainnc and t2, from August 2023) are supported."
        )
    times = ds["time"].values
    if times.size < 2 or not (np.diff(times) > np.timedelta64(0, "h")).all():
        raise DataError(f"{name} needs two or more strictly increasing times.")
    init = times[0]
    if str(init)[:10] != date.isoformat():
        raise DataError(
            f"{name} holds the run that starts {str(init)[:16]}, not on "
            f"{date.isoformat()}; the file is mislabeled in the archive."
        )
    rain = ds["rainc"] + ds["rainnc"]
    if float(rain.isel(time=0).max()) != 0.0:
        raise DataError(
            f"{name}: rain is not zero at the first time, so it is not accumulated "
            "from the run start (some archive files were already de-accumulated and "
            "split by day); it cannot be read as a run."
        )

    out = xr.Dataset(
        {
            "tp": rain.isel(lev=0, drop=True),
            "t2m": ds["t2"].isel(lev=0, drop=True),
        }
    )
    if product == "full":
        # Every WRF field as it is in the file, under its WRF name.
        out = out.merge(ds)
    out = out.rename({"lat": "latitude", "lon": "longitude"})
    out = out.assign_coords(step=("time", times - init)).swap_dims({"time": "step"})
    out = out.drop_vars("time").assign_coords(time=init)
    out["tp"].attrs = {"units": "mm", "long_name": "Total precipitation"}
    out["t2m"].attrs = {"units": "K", "long_name": "2 metre temperature"}
    out["time"].attrs.update(standard_name="forecast_reference_time", axis="T")
    out["step"].attrs.update(
        standard_name="forecast_period", long_name="time since forecast_reference_time"
    )
    if variable:
        unknown = sorted(set(variable) - set(out.data_vars))
        if unknown:
            raise UsageError(
                f"no variable {', '.join(unknown)} in the {product} product; "
                f"it has {', '.join(out.data_vars)}."
            )
        out = out[variable]

    if bbox is not None:
        out = bbox_subset(out, bbox)
    else:
        out = ensure_normalized_longitude(out)
    out = out.load()
    raw.close()
    tmp.cleanup()

    last_lead = (times[-1] - init) / np.timedelta64(1, "h")
    print(
        f"Run start {str(init)[:16]}Z, {times.size} 3-hourly times to lead {last_lead:.0f} h",
        file=sys.stderr,
    )

    out.attrs.update(
        Conventions="CF-1.13",
        title=f"KMSA WRF {product} forecast",
        weather_skills_source=f"kmsa-wrf:{_PRODUCTS[product]}/{name}",
    )
    stamp_cf_attrs(out)
    stamp_precip_amounts(out)
    out = to_standard_units(out)
    out = precip_amounts_to_rates(out)
    out = stamp_data_interval(out)
    return out


if __name__ == "__main__":
    fetch()
