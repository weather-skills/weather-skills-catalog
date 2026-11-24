"""Tests for olr-wave-filter against a synthetic ERA5 archive (no network)."""

from __future__ import annotations

import numpy as np
import pytest
import xarray as xr
from conftest import load_skill, make_forecast, run_skill, write_era5_archive, write_zarr

LATS = (15.0, 7.5, 0.0, -7.5, -15.0)
LONS = tuple(float(x) for x in range(-180, 180, 30))
VAR = "net_long_wave_radiation_flux_top_of_atmosphere"


@pytest.fixture(scope="module")
def mod():
    return load_skill("olr-wave-filter", "olr_wave_filter")


@pytest.fixture(scope="module")
def archive(tmp_path_factory):
    return write_era5_archive(tmp_path_factory.mktemp("era5"), LATS, LONS, end="2026-02-28")


def _forecast(tmp_path, init="2026-03-01", n_step=10, members=3, lons=LONS, units="W m-2"):
    ds = make_forecast(n_step=n_step, lats=LATS, lons=lons, name=VAR, init=init, members=members)
    ds = ds.assign_coords(step=np.array([np.timedelta64(d, "D") for d in range(n_step)]))
    rng = np.random.default_rng(1)
    ds[VAR].values = -(240 + rng.normal(0, 10, ds[VAR].shape))  # net flux: negative upward
    ds[VAR].attrs = {"units": units}
    return write_zarr(ds, tmp_path / "fc.zarr")


def _run(mod, archive, inp, out, *extra):
    run_skill(
        mod.olr_wave_filter,
        "-i",
        str(inp),
        "--obs-source",
        str(archive),
        "--leadin-days",
        "400",
        "--window-before",
        "10",
        "-o",
        str(out),
        *extra,
    )
    return xr.open_zarr(out, consolidated=True)


def test_filters_every_member(mod, archive, tmp_path):
    result = _run(mod, archive, _forecast(tmp_path), tmp_path / "waves.zarr")
    for w in ("low", "mjo", "kelvin", "er", "mrg_td"):
        assert result[f"olr_{w}"].dims == ("number", "time", "latitude", "longitude")
        assert result[f"olr_{w}_obs_only"].dims == ("time", "latitude", "longitude")
        assert result[f"olr_{w}_clim_amp_p90"].dims == ("phase", "longitude")
        assert result[f"olr_{w}"].attrs["amplitude_std_a"] > 0
    assert dict(result.sizes)["number"] == 3
    assert dict(result.sizes)["time"] == 20
    assert int(result.is_forecast.sum()) == 10
    assert result.attrs["forecast_reference_time"] == "2026-03-01"
    anom = result.olr_anomaly.load()
    observed = anom.isel(time=~result.is_forecast.values)
    assert float(abs(observed - observed.isel(number=0)).max()) == 0.0
    # the net flux was sign-flipped to OLR: anomalies are small, not ~ -480 W m-2
    assert float(abs(anom.isel(time=result.is_forecast.values).mean())) < 20


def test_wave_subset(mod, archive, tmp_path):
    result = _run(mod, archive, _forecast(tmp_path), tmp_path / "w.zarr", "--waves", "kelvin")
    assert "olr_kelvin" in result and "olr_mjo" not in result


def test_regional_forecast_rejected(mod, archive, tmp_path):
    inp = _forecast(tmp_path, lons=LONS[:6])
    with pytest.raises(SystemExit) as exc:
        _run(mod, archive, inp, tmp_path / "w.zarr")
    assert exc.value.code == 2


def test_accumulated_units_rejected(mod, archive, tmp_path):
    inp = _forecast(tmp_path, units="J m-2")
    with pytest.raises(SystemExit) as exc:
        _run(mod, archive, inp, tmp_path / "w.zarr")
    assert exc.value.code == 2


def test_init_beyond_archive(mod, archive, tmp_path):
    inp = _forecast(tmp_path, init="2026-04-01")  # 32 days past the archive's end
    with pytest.raises(SystemExit) as exc:
        _run(mod, archive, inp, tmp_path / "w.zarr")
    assert exc.value.code == 1
