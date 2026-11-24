"""Correctness tests for kmsa-wrf-fetch (synthetic WRF runs in a local or fake GCS root)."""

import shutil
import sys
import types
from datetime import datetime

import cftime
import numpy as np
import pytest
import xarray as xr
from conftest import load_skill, run_skill
from weather_skills_core.provenance import load_history

LATS = (-1.5, -1.0, -0.5)
LONS = (36.0, 36.5, 37.0, 37.5)


@pytest.fixture(scope="module")
def fetch_mod():
    return load_skill("kmsa-wrf-fetch", "fetch")


def write_run(path, start, n_time=5, rain_per_step=0.75, t2=295.0, rain_at_start=0.0):
    """A WRF run file in the archive layout: accumulated rainc/rainnc, t2, dummy lev."""
    # The archive's "standard" calendar is Julian before 1582: encode with cftime.
    hours0 = cftime.date2num(
        datetime.fromisoformat(start), "hours since 1-1-1 00:00:00", "standard"
    )
    hours = hours0 + 3.0 * np.arange(n_time)
    shape = (n_time, 1, len(LATS), len(LONS))
    acc = rain_at_start + rain_per_step * np.arange(n_time, dtype="float32")
    acc = np.broadcast_to(acc[:, None, None, None], shape).astype("float32")
    ds = xr.Dataset(
        {
            "rainc": (("time", "lev", "lat", "lon"), acc / 3),
            "rainnc": (("time", "lev", "lat", "lon"), acc * 2 / 3),
            "t2": (("time", "lev", "lat", "lon"), np.full(shape, t2, dtype="float32")),
        },
        coords={"time": hours, "lev": [1000.0], "lat": list(LATS), "lon": list(LONS)},
    )
    ds["time"].attrs.update(units="hours since 1-1-1 00:00:00", calendar="standard")
    path.parent.mkdir(parents=True, exist_ok=True)
    ds.to_netcdf(path)
    return path


def test_7day_run_becomes_rates_and_celsius(tmp_path, fetch_mod):
    write_run(tmp_path / "7_day/20261013_to_20261020_fcst.nc", "2026-10-13T06")
    out = tmp_path / "wrf.zarr"

    run_skill(fetch_mod.fetch, "--root", str(tmp_path), "--date", "2026-10-13", "-o", str(out))

    with xr.open_zarr(out, consolidated=True) as ds:
        assert ds["time"].values == np.datetime64("2026-10-13T06:00", "ns")
        assert ds.sizes["step"] == 4  # de-accumulation drops the last time
        assert ds["step"].values[0] == np.timedelta64(0, "ns")
        assert ds["tp"].attrs["units"] == "mm day-1"
        # 0.75 mm per 3 hours → 6 mm day-1
        np.testing.assert_allclose(ds["tp"].values, 6.0, rtol=1e-5)
        assert ds["t2m"].attrs["units"] == "degree_Celsius"
        np.testing.assert_allclose(ds["t2m"].values, 295.0 - 273.15, rtol=1e-5)
        assert ds.attrs["weather_skills_source"] == "kmsa-wrf:7_day/20261013_to_20261020_fcst.nc"
    assert load_history(out)[-1]["skill"] == "kmsa-wrf-fetch"


def test_daily_product_variable_and_bbox(tmp_path, fetch_mod):
    write_run(tmp_path / "Daily_Data/20261014_to_20261015_fcst.nc", "2026-10-14T03")
    out = tmp_path / "wrf.zarr"

    run_skill(
        fetch_mod.fetch,
        *("--root", str(tmp_path), "--product", "daily", "--date", "2026-10-14"),
        *("--bbox", "-0.75/36.25/-1.25/37.25", "-v", "tp", "-o", str(out)),
    )

    with xr.open_zarr(out, consolidated=True) as ds:
        assert list(ds.data_vars) == ["tp"]
        assert ds["latitude"].values.tolist() == [-1.0]
        assert ds["longitude"].values.tolist() == [36.5, 37.0]


def test_full_product_passes_wrf_fields_through(tmp_path, fetch_mod):
    src = write_run(tmp_path / "src.nc", "2026-10-26T03")
    with xr.open_dataset(src, decode_times=False) as ds:
        full = ds.load()
    full["u10"] = full["t2"] * 0 + 4.0
    full["u"] = (("time", "lev_2", "lat", "lon"), np.full((5, 3, len(LATS), len(LONS)), 7.0))
    (tmp_path / "full_wrf").mkdir()
    full.to_netcdf(tmp_path / "full_wrf/wrf_full_20261026.nc")
    out = tmp_path / "wrf.zarr"

    run_skill(
        fetch_mod.fetch,
        *("--root", str(tmp_path), "--product", "full", "--date", "2026-10-26"),
        *("-v", "tp", "-v", "u10", "-v", "u", "-o", str(out)),
    )

    with xr.open_zarr(out, consolidated=True) as ds:
        assert set(ds.data_vars) == {"tp", "u10", "u"}
        assert ds.sizes["step"] == 4
        np.testing.assert_allclose(ds["tp"].values, 6.0, rtol=1e-5)
        assert ds["u10"].dims == ("step", "lev", "latitude", "longitude")
        np.testing.assert_allclose(ds["u10"].values, 4.0)
        assert ds["u"].dims == ("step", "lev_2", "latitude", "longitude")
        assert ds.attrs["weather_skills_source"] == "kmsa-wrf:full_wrf/wrf_full_20261026.nc"


def test_refuses_unknown_variable(tmp_path, fetch_mod, capsys):
    write_run(tmp_path / "7_day/20261013_to_20261020_fcst.nc", "2026-10-13T06")

    with pytest.raises(SystemExit) as exc:
        run_skill(
            fetch_mod.fetch,
            *("--root", str(tmp_path), "--date", "2026-10-13", "-v", "u10"),
            *("-o", str(tmp_path / "x.zarr")),
        )
    assert exc.value.code != 0
    assert "no variable u10 in the 7day product" in capsys.readouterr().err


def test_probe_latest(tmp_path, fetch_mod, capsys):
    for day in ("20261012", "20261014", "20261013"):
        write_run(
            tmp_path / f"7_day/{day}_to_20261021_fcst.nc", f"{day[:4]}-{day[4:6]}-{day[6:]}T06"
        )
    (tmp_path / "7_day/20261015_to_20261022_fcst.txt").write_text("not a run")

    run_skill(fetch_mod.fetch, "--root", str(tmp_path), "--probe-latest")

    assert capsys.readouterr().out.strip() == "2026-10-14"


def test_refuses_mislabeled_file(tmp_path, fetch_mod, capsys):
    write_run(tmp_path / "7_day/20251112_to_20251119_fcst.nc", "2025-11-11T03")

    with pytest.raises(SystemExit) as exc:
        run_skill(
            fetch_mod.fetch,
            *("--root", str(tmp_path), "--date", "2025-11-12", "-o", str(tmp_path / "x.zarr")),
        )
    assert exc.value.code != 0
    assert "mislabeled" in capsys.readouterr().err


def test_refuses_already_deaccumulated_file(tmp_path, fetch_mod, capsys):
    write_run(
        tmp_path / "Daily_Data/20240429_to_20240430_fcst.nc", "2024-04-29T00", rain_at_start=2
    )

    with pytest.raises(SystemExit) as exc:
        run_skill(
            fetch_mod.fetch,
            *("--root", str(tmp_path), "--product", "daily", "--date", "2024-04-29"),
            *("-o", str(tmp_path / "x.zarr")),
        )
    assert exc.value.code != 0
    assert "not zero at the first time" in capsys.readouterr().err


def test_refuses_missing_date(tmp_path, fetch_mod, capsys):
    write_run(tmp_path / "7_day/20261013_to_20261020_fcst.nc", "2026-10-13T06")

    with pytest.raises(SystemExit) as exc:
        run_skill(
            fetch_mod.fetch,
            *("--root", str(tmp_path), "--date", "2019-01-01", "-o", str(tmp_path / "x.zarr")),
        )
    assert exc.value.code != 0
    assert "no 7day run starting 2019-01-01" in capsys.readouterr().err


def test_reads_gcs_root(tmp_path, fetch_mod, monkeypatch):
    src = write_run(tmp_path / "src/7_day/20261013_to_20261020_fcst.nc", "2026-10-13T06")
    seen = {}

    class FakeGCS:
        def __init__(self, token=None):
            seen["token"] = token

        def ls(self, folder, detail=False):
            seen["ls"] = folder
            return [f"{folder[5:]}/{src.name}", f"{folder[5:]}/20261013_to_20261020_fcst.txt"]

        def get(self, remote, local):
            seen["get"] = remote
            shutil.copy(src, local)

    monkeypatch.setitem(sys.modules, "gcsfs", types.SimpleNamespace(GCSFileSystem=FakeGCS))
    monkeypatch.setenv("GCLOUD_SERVICE_CREDENTIALS", '{"type": "service_account"}')
    out = tmp_path / "wrf.zarr"

    run_skill(fetch_mod.fetch, "--date", "2026-10-13", "-o", str(out))

    assert seen["token"] == {"type": "service_account"}
    assert seen["ls"] == "gs://sheerwater-datalake/kmsa-wrf/7_day"
    assert seen["get"] == f"gs://sheerwater-datalake/kmsa-wrf/7_day/{src.name}"
    with xr.open_zarr(out, consolidated=True) as ds:
        assert ds.sizes["step"] == 4
