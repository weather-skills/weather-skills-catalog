# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "weather-skills-core @ git+https://github.com/rhiza-research/weather-skills-core@main",
#   "cftime>=1.6",
#   "numpy",
#   "xarray",
#   "zarr",
# ]
# ///
"""Compute the Indian Ocean Dipole Mode Index from a temperature-anomaly field."""

from __future__ import annotations

import xarray as xr
from weather_skills_core import Dataset, UsageError, weather_skill
from weather_skills_core.standard_dataset import detect_spatial_dims
from weather_skills_core.standard_utils import bbox_subset, latitude_weights
from weather_skills_core.units import dequantify_dataset

# Auto-populated by the version-bump CI workflow. Do not edit manually.
_SKILL_VERSION = "0.0.4"

# Classic IOD / Dipole Mode Index boxes (Saji et al.).
# West: 50–70E, 10S–10N. East: 90–110E, 10S–0.
WEST_BBOX = (10.0, 50.0, -10.0, 70.0)  # N/W/S/E
EAST_BBOX = (0.0, 90.0, -10.0, 110.0)


def _box_mean(da: xr.DataArray, ds: xr.Dataset, bbox, lat_dim: str, lon_dim: str):
    """Lat-weighted mean of ``da`` over a N/W/S/E box; preserves non-spatial dims."""
    boxed = bbox_subset(ds[[da.name]], bbox, lat_dim=lat_dim, lon_dim=lon_dim)
    field = boxed[da.name]
    weights = latitude_weights(boxed[lat_dim])
    return field.weighted(weights).mean(dim=(lat_dim, lon_dim), keep_attrs=True)


@weather_skill(name="iod-mode-index", version=_SKILL_VERSION)
@weather_skill.argument("-i", "--input", type=Dataset("spatial"), required=True, dest="ds")
@weather_skill.argument(
    "--variable",
    "-v",
    required=True,
    help="Pre-computed temperature anomaly variable (e.g. ts_anomaly, sst_anomaly).",
)
def iod(ds, variable, **kwargs):
    """West − East Dipole Mode Index plus the two box-mean anomalies."""
    if variable not in ds.data_vars:
        raise UsageError(
            f"variable {variable!r} not in input; available: {', '.join(ds.data_vars)}"
        )

    # Weighted means divide by dimensionless weights; offset units (°C) are ambiguous
    # under pint, so work in plain arrays and re-attach units attrs on output.
    ds = dequantify_dataset(ds)
    lat_dim, lon_dim = detect_spatial_dims(ds)
    da = ds[variable]

    west = _box_mean(da, ds, WEST_BBOX, lat_dim, lon_dim)
    east = _box_mean(da, ds, EAST_BBOX, lat_dim, lon_dim)
    index = west - east

    units = da.attrs.get("units", "degree_Celsius")
    out = xr.Dataset(
        {
            "iod_mode_index": index,
            "west_indian_ocean_average_anomaly": west,
            "east_indian_ocean_average_anomaly": east,
        }
    )
    out["iod_mode_index"].attrs.update(
        long_name="Indian Ocean Dipole Mode Index (west minus east)",
        units=units,
        comment="DMI = west_indian_ocean_average_anomaly - east_indian_ocean_average_anomaly",
    )
    out["west_indian_ocean_average_anomaly"].attrs.update(
        long_name="West Indian Ocean average temperature anomaly (50E-70E, 10S-10N)",
        units=units,
    )
    out["east_indian_ocean_average_anomaly"].attrs.update(
        long_name="East Indian Ocean average temperature anomaly (90E-110E, 10S-0)",
        units=units,
    )

    # Preserve scalar / non-spatial coords from the input (e.g. forecast init time).
    for name, coord in ds.coords.items():
        if name in (lat_dim, lon_dim):
            continue
        if (
            name not in out.coords
            and name not in out.dims
            and (coord.ndim == 0 or set(coord.dims).isdisjoint({lat_dim, lon_dim}))
        ):
            out = out.assign_coords({name: coord})

    out.attrs["Conventions"] = ds.attrs.get("Conventions", "CF-1.13")
    return out


if __name__ == "__main__":
    iod()
