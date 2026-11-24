"""Tests for mjo-rmm-index against a synthetic ERA5 archive (no network)."""

from __future__ import annotations

import numpy as np
import pytest
import xarray as xr
from conftest import load_skill, make_forecast, run_skill, write_era5_archive, write_zarr

LATS = (15.0, 7.5, 0.0, -7.5, -15.0)
LONS = tuple(float(x) for x in range(-180, 180, 30))
OLR = "net_long_wave_radiation_flux_top_of_atmosphere"


@pytest.fixture(scope="module")
def mod():
    return load_skill("mjo-rmm-index", "mjo_rmm_index")


@pytest.fixture(scope="module")
def archive(tmp_path_factory):
    return write_era5_archive(tmp_path_factory.mktemp("era5"), LATS, LONS, end="2026-02-28")


def _steps(n):
    return np.array([np.timedelta64(d, "D") for d in range(n)])


def _inputs(tmp_path, n_fc=10, members=3, wind_init="2026-03-01"):
    rng = np.random.default_rng(2)
    olr = make_forecast(
        n_step=n_fc, lats=LATS, lons=LONS, name=OLR, init="2026-03-01", members=members
    )
    olr = olr.assign_coords(step=_steps(n_fc))
    olr[OLR].values = -(240 + rng.normal(0, 10, olr[OLR].shape))
    olr[OLR].attrs = {"units": "W m-2"}

    wind = make_forecast(
        n_step=n_fc + 1, lats=LATS, lons=LONS, name="wind_u", init=wind_init, members=members
    )
    wind = wind.assign_coords(step=_steps(n_fc + 1))
    u = wind["wind_u"].expand_dims(vertical=[850, 200], axis=2).copy()
    u.values = rng.normal(0, 3, u.shape)
    wind = wind.drop_vars("wind_u").assign(wind_u=u)
    wind["wind_u"].attrs = {"units": "m s-1"}
    wind["vertical"].attrs = {"units": "hPa", "positive": "down"}
    return write_zarr(olr, tmp_path / "olr.zarr"), write_zarr(wind, tmp_path / "u.zarr")


def _run(mod, archive, olr, wind, out, *extra):
    run_skill(
        mod.mjo_rmm_index,
        "--olr",
        str(olr),
        "--wind",
        str(wind),
        "--obs-source",
        str(archive),
        "-o",
        str(out),
        *extra,
    )
    return xr.open_zarr(out, consolidated=True)


def test_rmm_per_member(mod, archive, tmp_path):
    olr, wind = _inputs(tmp_path)
    result = _run(mod, archive, olr, wind, tmp_path / "rmm.zarr")
    assert result.rmm1.dims == ("time", "number")
    assert dict(result.sizes) == {"time": 50, "number": 3}
    assert int(result.is_forecast.sum()) == 10
    assert set(np.unique(result.phase_mean.values)) <= set(range(1, 9))
    assert "bom_correlation" in result.attrs
    observed = result.rmm1.isel(time=~result.is_forecast.values).load()
    assert float(abs(observed - observed.isel(number=0)).max()) < 1e-5
    assert not np.isnan(result.rmm1_bom.isel(time=0))


def test_no_bom_alignment(mod, archive, tmp_path):
    olr, wind = _inputs(tmp_path)
    result = _run(
        mod, archive, olr, wind, tmp_path / "rmm.zarr", "--no-align-bom", "--obs-days", "5"
    )
    assert dict(result.sizes)["time"] == 15
    assert "bom_correlation" not in result.attrs
    assert bool(np.isnan(result.rmm1_bom).all())


def test_mismatched_inits(mod, archive, tmp_path):
    olr, wind = _inputs(tmp_path, wind_init="2026-02-27")
    with pytest.raises(SystemExit) as exc:
        _run(mod, archive, olr, wind, tmp_path / "rmm.zarr")
    assert exc.value.code == 2
