"""Tests for iod-mode-index on forecast and observation envelopes."""

from __future__ import annotations

import numpy as np
import pytest
import xarray as xr
from conftest import load_skill, make_forecast, make_gridded, run_skill, write_zarr


@pytest.fixture
def mod():
    return load_skill("iod-mode-index", "iod")


def _fill_iod_pattern(ds, name="ts_anomaly", west_val=2.0, east_val=-1.0):
    """Set anomaly: west box = west_val, east box = east_val, elsewhere 0."""
    lat = ds["latitude"]
    lon = ds["longitude"]
    # Handle 0..360 or -180..180.
    lon360 = xr.where(lon < 0, lon + 360, lon)
    west = (lon360 >= 50) & (lon360 <= 70) & (lat >= -10) & (lat <= 10)
    east = (lon360 >= 90) & (lon360 <= 110) & (lat >= -10) & (lat <= 0)
    base = xr.zeros_like(ds[name], dtype=np.float64)
    # Broadcast masks onto data dims.
    data = base + west.astype(float) * west_val + east.astype(float) * east_val
    ds[name] = data
    ds[name].attrs.update(units="degree_Celsius", long_name="temperature anomaly")
    return ds


def test_iod_on_forecast(mod, tmp_path):
    # Grid covering both IOD boxes.
    lats = np.arange(-15.0, 16.0, 5.0)
    lons = np.arange(40.0, 121.0, 5.0)
    ds = make_forecast(
        n_step=2,
        lats=tuple(lats.tolist()),
        lons=tuple(lons.tolist()),
        name="ts_anomaly",
        fill=0.0,
    )
    ds = _fill_iod_pattern(ds, west_val=2.0, east_val=-1.0)
    inp = write_zarr(ds, tmp_path / "fc.zarr")
    out = tmp_path / "iod.zarr"
    run_skill(mod.iod, "-i", str(inp), "-v", "ts_anomaly", "-o", str(out))
    result = xr.open_zarr(out, consolidated=True)
    assert set(result.data_vars) == {
        "iod_mode_index",
        "west_indian_ocean_average_anomaly",
        "east_indian_ocean_average_anomaly",
    }
    assert "step" in result.dims
    assert "latitude" not in result.dims
    # DMI ≈ 2 - (-1) = 3
    assert float(result["west_indian_ocean_average_anomaly"].isel(step=0)) == pytest.approx(
        2.0, abs=0.05
    )
    assert float(result["east_indian_ocean_average_anomaly"].isel(step=0)) == pytest.approx(
        -1.0, abs=0.05
    )
    assert float(result["iod_mode_index"].isel(step=0)) == pytest.approx(3.0, abs=0.1)


def test_iod_on_observations(mod, tmp_path):
    lats = np.arange(-15.0, 16.0, 5.0)
    lons = np.arange(40.0, 121.0, 5.0)
    ds = make_gridded(
        n_time=2,
        lats=tuple(lats.tolist()),
        lons=tuple(lons.tolist()),
        name="sst_anomaly",
        fill=0.0,
    )
    ds = _fill_iod_pattern(ds, name="sst_anomaly", west_val=1.0, east_val=0.5)
    inp = write_zarr(ds, tmp_path / "obs.zarr")
    out = tmp_path / "iod_obs.zarr"
    run_skill(mod.iod, "-i", str(inp), "-v", "sst_anomaly", "-o", str(out))
    result = xr.open_zarr(out, consolidated=True)
    assert "time" in result.dims
    assert float(result["iod_mode_index"].isel(time=0)) == pytest.approx(0.5, abs=0.1)


def test_missing_variable(mod, tmp_path):
    ds = make_gridded(name="sst")
    inp = write_zarr(ds, tmp_path / "bad.zarr")
    with pytest.raises(SystemExit):
        run_skill(mod.iod, "-i", str(inp), "-v", "ts_anomaly", "-o", str(tmp_path / "o.zarr"))
