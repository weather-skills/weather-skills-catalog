# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "weather-skills-core>=0.4.0",
#   "cfgrib",
#   "cftime>=1.6",
#   # eccodeslib carries the native libeccodes that cfgrib loads on macOS/Linux;
#   # eccodes drops this as a transitive dep via PyPI metadata (ecmwf/eccodes-python#150),
#   # so it is declared directly. Windows bundles the library in eccodes itself, and
#   # eccodeslib has no Windows wheels.
#   "eccodeslib; sys_platform != 'win32'",
#   "gcsfs",
#   "numpy",
#   "pandas",
#   "scipy",
#   "xarray",
# ]
# ///
"""Fetch the DWD ICON-EPS global ensemble from DWD open data as a weather-skills Zarr."""

# Sources: the public gs://africa-forecasting-data archive, written daily by the
# ECMWF-S2S4AFRICA pipeline (download_icon_eps.py: 00 UTC runs over Africa, linear onto
# 0.25 deg, one store per DWD folder at data/<date>/ICON_eps_<folder>_<date>.zarr, values
# accumulated since init), is read first when it holds the whole request; otherwise the
# run comes from DWD open data. Both go through the same output transform below.
#
# Source-to-output transform declaration (everything else passes through):
# - Grid: DWD publishes ICON-EPS global only on its triangular (icosahedral) R02B07 grid,
#   1,310,720 cells with no coordinates in the GRIB. Cell centres come from the run's
#   `clat`/`clon` files, and every field is regridded onto a regular lat/lon grid
#   (`--target-resolution`, default 0.25 deg): linear barycentric interpolation over a
#   Delaunay triangulation of the cell centres (`--regrid linear`), or the nearest cell
#   (`--regrid nearest`). Targets outside the triangulation (poles) take the nearest cell.
# - Variable names: DWD folder names map to the cfgrib-style short names below
#   (tot_prec -> precip, t_2m -> t2m, ...); the decoded GRIB name is replaced by that token.
#   Archive stores carry the same names, except `tp` -> `precip`; their GRIB_*,
#   `standard_name` ("unknown") and `coordinates` attrs are dropped, keeping `units` and
#   `long_name`.
# - Units: GRIB `**` exponents are respelled (`kg m**-2` -> `kg m-2`, same unit).
#   Archive precip is `mm` (accumulated since init) and temperatures `degC`; the same
#   conversions below apply.
#   `precip` (kg m-2 accumulated since init) becomes a `mm day-1` rate over [step, next step)
#   by deaccumulation, labelled at the interval start; the last lead is dropped.
#   `t2m`, `d2m` K -> degree_Celsius; `sp` Pa -> hPa (core standard units).
# - long_name `Total precipitation` on `precip`.
# - Latitude is reversed to run south to north (DWD regrid and archive are north first).

import bz2
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from weather_skills_core import DataError, UsageError, weather_skill
from weather_skills_core.cf import stamp_cf_attrs
from weather_skills_core.standard_utils import is_transient
from weather_skills_core.units import (
    convert_dataarray,
    normalize_unit_strings,
    precip_amounts_to_rates,
    stamp_data_interval,
    to_standard_units,
)

# Auto-populated by the version-bump CI workflow. Do not edit manually.
_SKILL_VERSION = "0.0.5"

_BASE_URL = "https://opendata.dwd.de/weather/nwp/icon-eps/grib"
_FIELD_FILE = "icon-eps_global_icosahedral_single-level_{init}_{step:03d}_{folder}.grib2.bz2"
_GRID_FILE = "icon-eps_global_icosahedral_time-invariant_{init}_{folder}.grib2.bz2"
_HTTP_TIMEOUT = 120
_RETRIES = 4

# Public archive of past runs (anonymous read). Only 00 UTC runs, linear onto 0.25 deg
# over the N/W/S/E box below.
_ARCHIVE_BUCKET = "africa-forecasting-data"
_ARCHIVE_STORE = "data/{date}/ICON_eps_{folder}_{date}.zarr"
_ARCHIVE_EXTENT = (22.5, -21.0, -34.5, 55.5)
_ARCHIVE_RESOLUTION = 0.25
_ARCHIVE_PROBE_DAYS = 60  # date folders checked, newest first, for the newest archived run
_EPS = 1e-6
# Leads DWD publishes for a 00 UTC run; the archive must hold every requested one of these.
_DWD_LEADS = frozenset((*range(49), *range(51, 73, 3), *range(78, 121, 6), *range(132, 181, 12)))

# `-v` token -> DWD folder name. Most used first.
VARIABLES: dict[str, str] = {
    "precip": "tot_prec",
    "t2m": "t_2m",
    "d2m": "td_2m",
    "r2": "relhum_2m",
    "u10": "u_10m",
    "v10": "v_10m",
    "fg10": "vmax_10m",
    "tcc": "clct",
    "sp": "ps",
}
DEFAULT_VARIABLES = ["precip"]
_DEWPOINT = frozenset({"d2m"})
_LONG_NAMES = {"precip": "Total precipitation"}
# Variable names in the archive stores that differ from the output names.
_ARCHIVE_NAMES = {"precip": "tp"}


def _canonical_name(token: str) -> str | None:
    if token in VARIABLES:
        return token
    if token == "tp":
        return "precip"
    for short, folder in VARIABLES.items():
        if token == folder:
            return short
    return None


def _resolve_variables(raw: list[str] | None) -> list[str]:
    tokens = raw or list(DEFAULT_VARIABLES)
    unknown = [token for token in tokens if _canonical_name(token) is None]
    if unknown:
        raise UsageError(
            f"unknown variable(s): {', '.join(unknown)}.\n"
            f"Available (most used first): {', '.join(VARIABLES)} "
            f"(DWD names {', '.join(VARIABLES.values())} also accepted)"
        )
    resolved: list[str] = []
    for token in tokens:
        name = _canonical_name(token)
        if name not in resolved:
            resolved.append(name)
    return resolved


def _parse_hours(value: str, flag: str) -> int:
    """A pandas timedelta string (168h, 7D) as whole hours."""
    import pandas as pd

    try:
        hours = pd.Timedelta(value) / pd.Timedelta(hours=1)
    except ValueError:
        raise UsageError(f"{flag} {value!r} is not a timedelta (168h, 7D)") from None
    if hours <= 0 or hours != int(hours):
        raise UsageError(f"{flag} must be a positive whole number of hours, got {value!r}")
    return int(hours)


def _http_get(url: str) -> bytes | None:
    """GET with retries on transient errors; None on 404."""
    for attempt in range(_RETRIES):
        try:
            with urllib.request.urlopen(url, timeout=_HTTP_TIMEOUT) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            err = exc
        except (urllib.error.URLError, TimeoutError) as exc:
            err = exc
        if attempt == _RETRIES - 1 or not is_transient(err):
            raise DataError(
                f"DWD open data request failed for {url} ({err}); this is a transport "
                "problem, retry later or lower --workers."
            ) from None
        time.sleep(5 * (attempt + 1))
    return None


def _list_folder(run: int, folder: str) -> str:
    body = _http_get(f"{_BASE_URL}/{run:02d}/{folder}/")
    if body is None:
        raise DataError(f"no {folder!r} folder for the {run:02d} UTC run on {_BASE_URL}.")
    return body.decode("utf-8", errors="replace")


def _published(run: int, folder: str) -> dict[str, list[int]]:
    """Init (YYYYMMDDHH) -> sorted leads (hours) listed for one variable folder."""
    found: dict[str, set[int]] = {}
    pattern = rf"single-level_(\d{{10}})_(\d{{3}})_{re.escape(folder)}\.grib2\.bz2"
    for init, step in re.findall(pattern, _list_folder(run, folder)):
        found.setdefault(init, set()).add(int(step))
    return {init: sorted(steps) for init, steps in found.items()}


def _init_iso(init: str) -> str:
    """YYYYMMDDHH (UTC) -> YYYY-MM-DD."""
    return f"{init[:4]}-{init[4:6]}-{init[6:8]}"


def _choose_steps(available: list[int], max_lead: int, interval: int) -> list[int]:
    """Published leads that are multiples of ``interval`` up to ``max_lead``.

    ICON-EPS output thins out with lead time (hourly to 48 h, 3-hourly to 72 h,
    6-hourly to 120 h, then 12-hourly), so wanted leads the server lacks are skipped.
    """
    wanted = list(range(0, max_lead + 1, interval))
    steps = [s for s in wanted if s in set(available)]
    skipped = [s for s in wanted if s not in steps and s <= max(available, default=0)]
    if skipped:
        print(f"Not published at these leads, skipped: {skipped} h", file=sys.stderr)
    if steps and steps[-1] < max_lead:
        print(
            f"Last available lead is {steps[-1]} h (requested --max-lead {max_lead} h); "
            "06/18 UTC runs end at 120 h and a run still uploading is incomplete.",
            file=sys.stderr,
        )
    return steps


def _download_grib(url: str, dest: Path) -> Path:
    body = _http_get(url)
    if body is None:
        raise DataError(f"{url} is not on the server (DWD keeps only ~24 h of runs).")
    dest.write_bytes(bz2.decompress(body))
    return dest


def _open_values(path: Path):
    """The single field in a DWD GRIB as (DataArray on (number?, values))."""
    import xarray as xr

    with xr.open_dataset(path, engine="cfgrib", backend_kwargs={"indexpath": ""}) as ds:
        if not ds.data_vars:
            raise DataError(f"DWD GRIB {path.name} contained no field.")
        return ds[next(iter(ds.data_vars))].load()


def _fetch_grid(run: int, init: str, tmp: Path):
    """Cell-centre latitudes and longitudes (degrees) of the ICON-EPS grid."""
    import numpy as np

    coords = []
    for folder in ("clat", "clon"):
        url = f"{_BASE_URL}/{run:02d}/{folder}/" + _GRID_FILE.format(init=init, folder=folder)
        values = _open_values(_download_grib(url, tmp / f"{folder}.grib2")).values
        coords.append(np.asarray(values, dtype="float64"))
    clat, clon = coords
    if np.nanmax(np.abs(clat)) <= np.pi / 2 + 1e-6:  # radians in some ICON grid files
        clat, clon = np.rad2deg(clat), np.rad2deg(clon)
    return clat, clon


def _fetch_field(run: int, folder: str, init: str, step: int, tmp: Path):
    """One lead of one variable: DataArray on (number, values) with GRIB attrs and ``time``."""
    url = f"{_BASE_URL}/{run:02d}/{folder}/" + _FIELD_FILE.format(
        init=init, step=step, folder=folder
    )
    path = _download_grib(url, tmp / f"{folder}_{step:03d}.grib2")
    try:
        da = _open_values(path)
    finally:
        path.unlink(missing_ok=True)
    if "number" not in da.dims:
        da = da.expand_dims("number")
    return da


def _archive_url(day: str, folder: str) -> str:
    return f"gs://{_ARCHIVE_BUCKET}/" + _ARCHIVE_STORE.format(date=day, folder=folder)


def _archive_error(exc: Exception) -> DataError:
    return DataError(
        f"reading gs://{_ARCHIVE_BUCKET} failed ({exc}); this is a transport problem, "
        "retry later or use --source dwd."
    )


def _archive_fs():
    import gcsfs

    return gcsfs.GCSFileSystem(token="anon")


def _archive_has(fs, day: str, folder: str) -> bool:
    path = _archive_url(day, folder).removeprefix("gs://")
    return fs.exists(f"{path}/zarr.json") or fs.exists(f"{path}/.zmetadata")


def _archive_open(day: str, folder: str):
    """The archived store of one 00 UTC init (YYYY-MM-DD) and DWD folder, opened lazily;
    None when the archive has no such store."""
    import xarray as xr

    try:
        if not _archive_has(_archive_fs(), day, folder):
            return None
        return xr.open_zarr(
            _archive_url(day, folder), consolidated=None, storage_options={"token": "anon"}
        )
    except Exception as exc:  # noqa: BLE001 - gcsfs/aiohttp raise many transport errors
        raise _archive_error(exc) from None


def _archive_newest(folder: str) -> str | None:
    """Newest init date (YYYY-MM-DD) with an archived store for ``folder``."""
    try:
        fs = _archive_fs()
        entries = fs.ls(f"{_ARCHIVE_BUCKET}/data/", detail=False)
        days = sorted(
            (
                d
                for d in (e.rstrip("/").rsplit("/", 1)[-1] for e in entries)
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d)
            ),
            reverse=True,
        )
        for day in days[:_ARCHIVE_PROBE_DAYS]:
            if _archive_has(fs, day, folder):
                return day
    except Exception as exc:  # noqa: BLE001 - as in _archive_open
        raise _archive_error(exc) from None
    return None


def _step_hours(ds) -> list[int]:
    import numpy as np

    return sorted(int(h) for h in np.round(ds["step"].values / np.timedelta64(1, "h")))


def _archive_plan(names, day, run, bbox, resolution, regrid, lead, interval):
    """``(stores, None)`` when the archive holds the whole request, else
    ``(None, (error class, reason))``."""
    north, west, south, east = _ARCHIVE_EXTENT
    if run != 0:
        return None, (UsageError, "the archive holds only 00 UTC runs (--run 0)")
    if regrid != "linear":
        return None, (UsageError, "the archive is regridded linearly (--regrid linear)")
    if abs(resolution - _ARCHIVE_RESOLUTION) > _EPS:
        return None, (UsageError, f"the archive grid is {_ARCHIVE_RESOLUTION} deg")
    inside = bbox is not None and (
        bbox[0] <= north + _EPS
        and bbox[2] >= south - _EPS
        and bbox[1] >= west - _EPS
        and bbox[3] <= east + _EPS
    )
    if not inside:
        return None, (
            UsageError,
            (
                f"the archive covers only Africa; pass a --bbox inside "
                f"{north:g}/{west:g}/{south:g}/{east:g}"
            ),
        )
    wanted = [s for s in range(0, lead + 1, interval) if s in _DWD_LEADS]
    stores = {}
    for name in names:
        folder = VARIABLES[name]
        ds = _archive_open(day, folder)
        if ds is None:
            return None, (DataError, f"the archive has no {folder} store for {day}")
        have = set(_step_hours(ds))
        missing = [s for s in wanted if s not in have]
        if missing:
            return None, (
                DataError,
                (
                    f"the archived {folder} store for {day} lacks leads {missing} h "
                    f"(it has {sorted(have)} h)"
                ),
            )
        stores[name] = ds
    return stores, None


def _archive_field(ds, name: str, lead: int, interval: int, bbox):
    """One variable from an archived store: DataArray on (number, step, latitude, longitude),
    on the archive grid points inside ``bbox``."""
    import numpy as np
    import xarray as xr

    var = _ARCHIVE_NAMES.get(name, name)
    if var not in ds.data_vars:
        var = next(iter(ds.data_vars))
    # Only the leads DWD publishes, so a run reads the same from either source.
    steps = _choose_steps([h for h in _step_hours(ds) if h in _DWD_LEADS], lead, interval)
    if len(steps) < (2 if name == "precip" else 1):
        raise DataError(f"{name}: too few archived leads for the request; lower --step-interval.")
    north, west, south, east = bbox
    lat = ds["latitude"].values
    lat_sel = (
        slice(north + _EPS, south - _EPS) if lat[0] > lat[-1] else slice(south - _EPS, north + _EPS)
    )
    da = (
        ds[var]
        .sel(
            step=[np.timedelta64(s, "h").astype("timedelta64[ns]") for s in steps],
            latitude=lat_sel,
            longitude=slice(west - _EPS, east + _EPS),
        )
        .sortby("latitude", ascending=False)
        .transpose("number", "step", "latitude", "longitude")
    )
    if da.sizes["latitude"] == 0 or da.sizes["longitude"] == 0:
        raise UsageError(f"--bbox {bbox} holds no archive grid points; widen it.")
    out = xr.DataArray(
        np.asarray(da.values, dtype="float32"),
        dims=da.dims,
        coords={dim: da[dim].values for dim in da.dims},
        attrs={k: v for k, v in da.attrs.items() if k in ("units", "long_name")},
    )
    print(f"  {name} {len(steps)} leads from the archive", file=sys.stderr)
    return out


class _Regridder:
    """Icosahedral cell centres -> regular lat/lon grid; weights built once, reused."""

    def __init__(self, clat, clon, lats, lons, method: str, margin: float = 1.0):
        import numpy as np
        from scipy.spatial import Delaunay, cKDTree

        clon = ((clon + 180.0) % 360.0) - 180.0
        north, south = lats.max() + margin, lats.min() - margin
        west, east = lons.min() - margin, lons.max() + margin
        # Pad with copies shifted by +-360 deg so a box at the dateline (or the whole
        # globe) is covered by the planar triangulation.
        src_idx, src_lon = [], []
        for shift in (-360.0, 0.0, 360.0):
            lon_s = clon + shift
            keep = np.where((clat >= south) & (clat <= north) & (lon_s >= west) & (lon_s <= east))[
                0
            ]
            src_idx.append(keep)
            src_lon.append(lon_s[keep])
        self.index = np.concatenate(src_idx)
        points = np.column_stack([np.concatenate(src_lon), clat[self.index]])
        if len(points) < 3:
            raise DataError("no ICON-EPS cells inside --bbox; widen it.")

        lon2d, lat2d = np.meshgrid(lons, lats)
        targets = np.column_stack([lon2d.ravel(), lat2d.ravel()])
        self.shape = (len(lats), len(lons))

        # Nearest cell on the sphere (3-D unit vectors), used by `nearest` and as the
        # fallback for targets outside the triangulation.
        def xyz(lon, lat):
            lon, lat = np.deg2rad(lon), np.deg2rad(lat)
            return np.column_stack(
                [np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)]
            )

        _, nearest = cKDTree(xyz(points[:, 0], points[:, 1])).query(
            xyz(targets[:, 0], targets[:, 1])
        )
        vertices = np.repeat(nearest[:, None], 3, axis=1)
        weights = np.zeros((len(targets), 3), dtype="float32")
        weights[:, 0] = 1.0
        if method == "linear":
            tri = Delaunay(points)
            simplex = tri.find_simplex(targets)
            inside = simplex >= 0
            transform = tri.transform[simplex[inside]]
            bary = np.einsum("nij,nj->ni", transform[:, :2], targets[inside] - transform[:, 2])
            weights[inside] = np.column_stack([bary, 1.0 - bary.sum(axis=1)])
            vertices[inside] = tri.simplices[simplex[inside]]
            outside = int((~inside).sum())
            if outside:
                print(
                    f"{outside} target points outside the triangulation take the nearest cell",
                    file=sys.stderr,
                )
        self.vertices, self.weights = vertices, weights

    def __call__(self, values):
        """(..., n_cells) on the full ICON grid -> (..., nlat, nlon)."""
        sub = values[..., self.index]
        out = (sub[..., self.vertices] * self.weights).sum(axis=-1)
        return out.reshape(values.shape[:-1] + self.shape)


def _target_axes(bbox, resolution: float):
    import numpy as np

    north, west, south, east = bbox if bbox is not None else (90.0, -180.0, -90.0, 180.0)
    if bbox is None:
        east = 180.0 - resolution  # [-180, 180)
    lats = np.round(np.arange(north, south - resolution / 2, -resolution), 6)
    lons = np.round(np.arange(west, east + resolution / 2, resolution), 6)
    if lats.size == 0 or lons.size == 0:
        raise UsageError(f"--bbox {bbox} holds no {resolution} deg grid points.")
    return lats, lons


def _to_celsius(da):
    if str(da.attrs.get("units") or "") in {"degree_Celsius", "degC", "celsius"}:
        return da
    converted, _ = convert_dataarray(da, "degree_Celsius")
    converted.attrs["units"] = "degree_Celsius"
    return converted


def _standardize(ds):
    """CF attrs + standard units; deaccumulate `precip` into rates."""
    normalize_unit_strings(ds)
    stamp_cf_attrs(ds)
    for name, long_name in _LONG_NAMES.items():
        if name in ds.data_vars:
            ds[name].attrs["long_name"] = long_name
    ds = to_standard_units(ds)
    for name in _DEWPOINT:
        if name in ds.data_vars:
            ds[name] = _to_celsius(ds[name])
    ds = precip_amounts_to_rates(ds)
    return stamp_data_interval(ds)


def _dwd_fields(names, run, init, published, lats, lons, regrid, lead, interval, workers):
    """Download and regrid every requested variable of one DWD run."""
    import numpy as np
    import xarray as xr

    fields = {}
    with tempfile.TemporaryDirectory(prefix="icon-eps-fetch-") as tmpdir:
        tmp = Path(tmpdir)
        clat, clon = _fetch_grid(run, init, tmp)
        regridder = _Regridder(clat, clon, lats, lons, regrid)

        for name in names:
            folder = VARIABLES[name]
            available = (
                published[init]
                if folder == VARIABLES[names[0]]
                else (_published(run, folder).get(init, []))
            )
            steps = _choose_steps(available, lead, interval)
            if len(steps) < (2 if name == "precip" else 1):
                raise DataError(
                    f"{folder}: too few leads for init {init} (have {available[:5]}...); "
                    "lower --step-interval or wait for the run to finish uploading."
                )

            def one(step, folder=folder, name=name):
                da = _fetch_field(run, folder, init, step, tmp)
                print(f"  {name} +{step:03d} h", file=sys.stderr)
                return xr.DataArray(
                    regridder(np.asarray(da.values, dtype="float32")),
                    dims=("number", "latitude", "longitude"),
                    coords={"number": da["number"].values, "latitude": lats, "longitude": lons},
                    attrs={k: v for k, v in da.attrs.items() if not k.startswith("GRIB_")},
                ).expand_dims(step=[np.timedelta64(step, "h").astype("timedelta64[ns]")])

            with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
                parts = list(pool.map(one, steps))
            fields[name] = xr.concat(parts, dim="step")
    return fields


def _dwd_runs(run: int, folder: str) -> dict[str, list[int]]:
    published = _published(run, folder)
    if not published:
        raise DataError(
            f"no {run:02d} UTC ICON-EPS run is listed on {_BASE_URL} (a run may be "
            "mid-upload); try another --run or retry later."
        )
    return published


@weather_skill(
    name="icon-eps-fetch",
    version=_SKILL_VERSION,
)
@weather_skill.argument(
    "--date",
    help=(
        "Init date YYYY-MM-DD. Default: the newest init of --run on DWD (with --source "
        "archive, the newest archived run). DWD keeps only ~24 h of runs; older 00 UTC runs "
        "over Africa come from the gs://africa-forecasting-data archive."
    ),
)
@weather_skill.argument(
    "--source",
    default="auto",
    choices=("auto", "archive", "dwd"),
    help=(
        "auto (default): read the run from the gs://africa-forecasting-data archive when it "
        "holds the whole request, else download it from DWD. archive: archive only (past "
        "runs). dwd: DWD open data only."
    ),
)
@weather_skill.argument(
    "--run",
    type=int,
    default=0,
    choices=(0, 6, 12, 18),
    help="Init hour (UTC). 00/12 runs reach 180 h, 06/18 runs 120 h. Default 0.",
)
@weather_skill.argument("--bbox")
@weather_skill.argument(
    "--variable",
    "-v",
    action="append",
    help=(
        "Field to retrieve (repeatable). Most used first: precip, t2m, d2m, r2, u10, v10, "
        "fg10, tcc, sp. `tp` and DWD folder names (tot_prec, t_2m, ...) are accepted. "
        "Default precip."
    ),
)
@weather_skill.argument(
    "--max-lead",
    default="168h",
    help="Last lead to keep (pandas timedelta: 168h, 7D). Default 168h (one week).",
)
@weather_skill.argument(
    "--step-interval",
    default="24h",
    help=(
        "Keep the published leads that are multiples of this (pandas timedelta). Default "
        "24h: daily precip totals from 8 files per week. Use 6h for sub-daily fields."
    ),
)
@weather_skill.argument(
    "--target-resolution",
    type=float,
    default=0.25,
    help="Regular lat/lon grid spacing in degrees (native grid is ~20 km). Default 0.25.",
)
@weather_skill.argument(
    "--regrid",
    default="linear",
    choices=("linear", "nearest"),
    help="Interpolation from the triangular grid: linear (barycentric) or nearest cell.",
)
@weather_skill.argument(
    "--workers",
    type=int,
    default=4,
    help="Parallel downloads (default 4). Lower it if DWD throttles.",
)
@weather_skill.argument(
    "--probe-latest",
    nargs="?",
    const="",
    metavar="IDENT",
    probe=True,
    help=(
        "Print the newest init date of --run (YYYY-MM-DD, usable as --date) on stdout and "
        "exit. IDENT is ignored. Does not download fields."
    ),
)
def fetch(
    date,
    source,
    run,
    bbox,
    variable,
    max_lead,
    step_interval,
    target_resolution,
    regrid,
    workers,
    probe_latest,
    **kwargs,
):
    """Fetch the DWD ICON-EPS global ensemble (40 members), regridded from its triangular
    grid onto a regular lat/lon grid, as a weather-skills Zarr: from the
    gs://africa-forecasting-data archive of past 00 UTC runs over Africa when it holds the
    request, else from DWD open data."""
    names = _resolve_variables(variable)
    folder0 = VARIABLES[names[0]]

    if probe_latest is not None:
        if source == "archive":
            day = _archive_newest(folder0)
            if day is None:
                raise DataError(f"the archive holds no {folder0} store.")
            print(f"Latest archived run: {day.replace('-', '')} 00 UTC", file=sys.stderr)
            print(day)
            return
        newest = max(_dwd_runs(run, folder0))
        print(f"Latest run: {newest[:8]} {newest[8:]} UTC", file=sys.stderr)
        print(_init_iso(newest))
        return

    if target_resolution <= 0:
        raise UsageError("--target-resolution must be positive.")
    lead = _parse_hours(max_lead, "--max-lead")
    interval = _parse_hours(step_interval, "--step-interval")
    lats, lons = _target_axes(bbox, target_resolution)

    published = None
    if date is not None:
        init = f"{date:%Y%m%d}{run:02d}"
    elif source == "archive":
        if run != 0:
            raise UsageError("the archive holds only 00 UTC runs; use --run 0.")
        day = _archive_newest(folder0)
        if day is None:
            raise DataError(f"the archive holds no {folder0} store.")
        init = day.replace("-", "") + "00"
    else:
        published = _dwd_runs(run, folder0)
        init = max(published)

    stores, why_not = None, None
    if source != "dwd":
        try:
            stores, why_not = _archive_plan(
                names, _init_iso(init), run, bbox, target_resolution, regrid, lead, interval
            )
        except DataError as exc:
            if source == "archive":
                raise
            why_not = (DataError, str(exc))
        if stores is None and source == "archive":
            cls, reason = why_not
            raise cls(
                f"ICON-EPS {init[:8]} {run:02d} UTC is not readable from the archive: {reason}."
            )

    if stores is None:
        if published is None:
            published = _dwd_runs(run, folder0)
        if init not in published:
            newest = max(published)
            hint = (
                f" Not readable from the archive either: {why_not[1]}."
                if why_not
                else " Omit --date for the newest run."
            )
            raise DataError(
                f"ICON-EPS init {init[:8]} {run:02d} UTC is not on the DWD server; DWD keeps "
                f"only ~24 h of runs (newest: {newest[:8]} {newest[8:]} UTC).{hint}"
            )
        if why_not:
            print(f"Archive not used ({why_not[1]}); downloading from DWD.", file=sys.stderr)

    import numpy as np
    import xarray as xr

    init_time = np.datetime64(f"{_init_iso(init)}T{init[8:]}:00", "ns")

    if stores is not None:
        print(
            f"Reading ICON-EPS {init[:8]} {run:02d} UTC from gs://{_ARCHIVE_BUCKET}, "
            f"variables={','.join(names)}",
            file=sys.stderr,
        )
        fields = {name: _archive_field(stores[name], name, lead, interval, bbox) for name in names}
        retrieved = " ".join(_archive_url(_init_iso(init), VARIABLES[name]) for name in names)
    else:
        print(
            f"Fetching ICON-EPS {init[:8]} {run:02d} UTC, variables={','.join(names)}, "
            f"grid {target_resolution} deg {len(lats)}x{len(lons)} ({regrid})",
            file=sys.stderr,
        )
        fields = _dwd_fields(
            names, run, init, published, lats, lons, regrid, lead, interval, workers
        )
        retrieved = f"{_BASE_URL}/{run:02d}/"

    ds = xr.Dataset(fields).transpose("number", "step", "latitude", "longitude")
    if "precip" in ds.data_vars:
        # Accumulation is zero at the init by definition (the 0 h file can be empty).
        ds["precip"] = ds["precip"].where(ds["step"] != np.timedelta64(0, "ns"), 0.0)
    ds = ds.assign_coords(time=init_time)
    ds = ds.assign_coords(valid_time=("step", init_time + ds["step"].values))
    ds.attrs.update(
        Conventions="CF-1.13",
        weather_skills_source=f"dwd-icon-eps:{run:02d}/{init}",
        institution="Deutscher Wetterdienst (DWD)",
        license="CC BY 4.0; source: Deutscher Wetterdienst",
        regridding=(f"{regrid} from the ICON R02B07 triangular grid to {target_resolution} deg"),
        retrieved_from=retrieved,
    )
    ds = _standardize(ds)
    # Latitude runs south to north in every output.
    if "latitude" in ds.indexes and ds.indexes["latitude"].is_monotonic_decreasing:
        ds = ds.isel(latitude=slice(None, None, -1))
    return ds


if __name__ == "__main__":
    fetch()
