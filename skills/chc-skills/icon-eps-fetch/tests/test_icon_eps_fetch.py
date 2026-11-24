"""Correctness tests for icon-eps-fetch (DWD listing, downloads, GRIB decoding and the
gs://africa-forecasting-data archive mocked)."""

import numpy as np
import pytest
import xarray as xr
from conftest import load_skill, run_skill
from weather_skills_core.provenance import load_history

INIT = "2026102100"
# ICON-EPS publishing pattern: hourly to 48 h, 3-hourly to 72 h, 6-hourly to 120 h,
# then 12-hourly to 180 h.
LEADS = (
    list(range(49)) + list(range(51, 73, 3)) + list(range(78, 121, 6)) + list(range(132, 181, 12))
)
MEMBERS = 3
RATE_MM_DAY = 2.0


@pytest.fixture(scope="module")
def fetch_mod():
    return load_skill("icon-eps-fetch", "fetch")


@pytest.fixture
def grid():
    """Scattered cell centres over East Africa, standing in for the triangular grid."""
    rng = np.random.default_rng(0)
    clat = rng.uniform(-12.0, 12.0, 6000)
    clon = rng.uniform(28.0, 48.0, 6000)
    return clat, clon


@pytest.fixture
def mocked(fetch_mod, grid, monkeypatch):
    clat, clon = grid
    requested = []

    def fake_field(run, folder, init, step, tmp):
        requested.append((folder, step))
        if folder == "tot_prec":
            values = np.full((MEMBERS, clat.size), RATE_MM_DAY * step / 24.0)
            units = "kg m**-2"
        else:
            values = np.full((MEMBERS, clat.size), 300.0)
            units = "K"
        return xr.DataArray(
            values,
            dims=("number", "values"),
            coords={"number": np.arange(1, MEMBERS + 1)},
            attrs={"units": units, "GRIB_name": "x"},
        )

    monkeypatch.setattr(fetch_mod, "_published", lambda run, folder: {INIT: LEADS})
    monkeypatch.setattr(fetch_mod, "_fetch_grid", lambda run, init, tmp: (clat, clon))
    monkeypatch.setattr(fetch_mod, "_fetch_field", fake_field)
    # Empty archive unless a test provides stores.
    monkeypatch.setattr(fetch_mod, "_archive_open", lambda day, folder: None)
    monkeypatch.setattr(fetch_mod, "_archive_newest", lambda folder: None)
    return requested


def archive_store(day, folder, steps):
    """An archived ECMWF-S2S4AFRICA store: 0.25 deg, latitude descending, values
    accumulated since init (tp in mm, temperatures in degC)."""
    lats = np.round(np.arange(10.0, -10.01, -0.25), 4)
    lons = np.round(np.arange(30.0, 50.01, 0.25), 4)
    step = np.array([np.timedelta64(h, "h") for h in steps]).astype("timedelta64[ns]")
    hours = (step / np.timedelta64(1, "h"))[None, :, None, None]
    name, units = {"tot_prec": ("tp", "mm"), "t_2m": ("t2m", "degC")}[folder]
    values = RATE_MM_DAY * hours / 24.0 if name == "tp" else np.full_like(hours, 20.0)
    values = np.broadcast_to(values, (MEMBERS, len(step), len(lats), len(lons))).astype("float32")
    da = xr.DataArray(
        values,
        dims=("number", "step", "latitude", "longitude"),
        coords={
            "number": np.arange(1, MEMBERS + 1),
            "step": step,
            "latitude": lats,
            "longitude": lons,
            "time": np.datetime64(f"{day}T00:00", "ns"),
        },
        attrs={"units": units, "GRIB_name": "x", "standard_name": "unknown"},
    )
    return da.to_dataset(name=name)


@pytest.fixture
def archive(fetch_mod, mocked, monkeypatch):
    """Archive holding 2026-10-01 (older than DWD keeps) and the DWD init 2026-10-21."""
    opened = []
    stores = {("2026-10-01", "tot_prec"), ("2026-10-21", "tot_prec"), ("2026-10-01", "t_2m")}

    def fake_open(day, folder):
        opened.append((day, folder))
        if (day, folder) not in stores:
            return None
        steps = range(0, 169, 24) if folder == "tot_prec" else range(0, 169, 6)
        return archive_store(day, folder, steps)

    monkeypatch.setattr(fetch_mod, "_archive_open", fake_open)
    monkeypatch.setattr(fetch_mod, "_archive_newest", lambda folder: "2026-10-21")
    return opened


def test_fetch_precip_is_daily_rates_with_history(tmp_path, fetch_mod, mocked):
    out = tmp_path / "icon.zarr"
    run_skill(fetch_mod.fetch, "--bbox", "5/34/-5/42", "-o", str(out))

    assert sorted({s for _, s in mocked}) == list(range(0, 169, 24))
    with xr.open_zarr(out, consolidated=True) as ds:
        precip = ds["precip"]
        assert precip.attrs["units"] == "mm day-1"
        assert precip.attrs.get("data_interval")
        assert ds.sizes["number"] == MEMBERS
        assert ds.sizes["step"] == 7
        assert ds["step"].values[0] == np.timedelta64(0, "ns")
        np.testing.assert_allclose(precip.values, RATE_MM_DAY, rtol=1e-5)
        assert ds["time"].values == np.datetime64("2026-10-21T00:00", "ns")
        assert float(ds["latitude"].max()) == 5.0 and float(ds["longitude"].min()) == 34.0
        assert bool((ds["latitude"].diff("latitude") > 0).all())  # south to north
        assert ds.attrs["weather_skills_source"] == f"dwd-icon-eps:00/{INIT}"
    assert load_history(out)[-1]["skill"] == "icon-eps-fetch"


def test_dwd_names_nearest_and_kelvin(tmp_path, fetch_mod, mocked):
    out = tmp_path / "icon_t2m.zarr"
    run_skill(
        fetch_mod.fetch,
        *("--bbox", "2/36/-2/40", "-v", "t_2m", "--regrid", "nearest"),
        *("--step-interval", "6h", "--max-lead", "168h", "-o", str(out)),
    )
    with xr.open_zarr(out, consolidated=True) as ds:
        assert list(ds.data_vars) == ["t2m"]
        assert ds["t2m"].attrs["units"] == "degree_Celsius"
        np.testing.assert_allclose(ds["t2m"].values, 300.0 - 273.15, rtol=1e-5)
        hours = (ds["step"].values / np.timedelta64(1, "h")).astype(int).tolist()
    # 6-hourly leads stop at 120 h; after that only the 12-hourly ones exist
    assert hours == list(range(0, 121, 6)) + [132, 144, 156, 168]


def test_tp_is_an_alias_for_precip(tmp_path, fetch_mod, mocked):
    out = tmp_path / "icon_tp.zarr"
    run_skill(fetch_mod.fetch, "--bbox", "2/36/-2/40", "-v", "tp", "-o", str(out))
    with xr.open_zarr(out, consolidated=True) as ds:
        assert list(ds.data_vars) == ["precip"]


def test_unknown_variable_exits_2(tmp_path, fetch_mod, mocked):
    with pytest.raises(SystemExit) as exc:
        run_skill(fetch_mod.fetch, "-v", "nope", "-o", str(tmp_path / "x.zarr"))
    assert exc.value.code == 2


def test_date_not_on_server_exits_1(tmp_path, fetch_mod, mocked):
    with pytest.raises(SystemExit) as exc:
        run_skill(fetch_mod.fetch, "--date", "2026-10-01", "-o", str(tmp_path / "x.zarr"))
    assert exc.value.code == 1


def test_probe_latest_prints_date(fetch_mod, mocked, capsys):
    run_skill(fetch_mod.fetch, "--probe-latest")
    assert capsys.readouterr().out.strip() == "2026-10-21"


def test_linear_regridder_is_exact_for_linear_fields(fetch_mod, grid):
    clat, clon = grid
    lats = np.arange(5.0, -5.01, -0.5)
    lons = np.arange(34.0, 42.01, 0.5)
    regrid = fetch_mod._Regridder(clat, clon, lats, lons, "linear")
    field = (2.0 * clat + 0.5 * clon)[None, :]
    out = regrid(field)[0]
    lon2d, lat2d = np.meshgrid(lons, lats)
    np.testing.assert_allclose(out, 2.0 * lat2d + 0.5 * lon2d, atol=1e-3)


def test_targets_outside_the_cells_take_the_nearest(fetch_mod, grid):
    clat, clon = grid
    # 12.5N lies north of every cell centre, outside the triangulation
    regrid = fetch_mod._Regridder(clat, clon, np.array([12.5]), np.array([38.0]), "linear")
    field = np.arange(clat.size, dtype="float64")[None, :]
    nearest = np.argmin((clat - 12.5) ** 2 + (clon - 38.0) ** 2)
    assert regrid(field)[0, 0, 0] == field[0, nearest]


def test_past_run_comes_from_the_archive(tmp_path, fetch_mod, mocked, archive):
    out = tmp_path / "icon_past.zarr"
    run_skill(fetch_mod.fetch, "--date", "2026-10-01", "--bbox", "5/34/-5/42", "-o", str(out))

    assert mocked == []  # nothing downloaded from DWD
    with xr.open_zarr(out, consolidated=True) as ds:
        precip = ds["precip"]
        assert precip.attrs["units"] == "mm day-1"
        assert "GRIB_name" not in precip.attrs
        assert precip.attrs.get("standard_name") != "unknown"
        assert ds.sizes["step"] == 7
        np.testing.assert_allclose(precip.values, RATE_MM_DAY, rtol=1e-5)
        assert ds["time"].values == np.datetime64("2026-10-01T00:00", "ns")
        assert float(ds["latitude"][0]) == -5.0 and float(ds["latitude"][-1]) == 5.0
        assert float(ds["longitude"][0]) == 34.0 and float(ds["longitude"][-1]) == 42.0
        assert ds.attrs["weather_skills_source"] == "dwd-icon-eps:00/2026100100"
        assert ds.attrs["retrieved_from"].startswith(
            "gs://africa-forecasting-data/data/2026-10-01/"
        )


def test_archive_is_tried_first_for_the_newest_run(tmp_path, fetch_mod, mocked, archive):
    out = tmp_path / "icon_new.zarr"
    run_skill(fetch_mod.fetch, "--bbox", "5/34/-5/42", "-o", str(out))
    assert archive == [("2026-10-21", "tot_prec")]
    assert mocked == []


def test_archive_temperatures_are_celsius(tmp_path, fetch_mod, mocked, archive):
    out = tmp_path / "icon_t2m.zarr"
    run_skill(
        fetch_mod.fetch,
        *("--date", "2026-10-01", "--bbox", "2/36/-2/40", "-v", "t2m"),
        *("--step-interval", "6h", "-o", str(out)),
    )
    with xr.open_zarr(out, consolidated=True) as ds:
        assert ds["t2m"].attrs["units"] == "degree_Celsius"
        np.testing.assert_allclose(ds["t2m"].values, 20.0, rtol=1e-5)
        hours = (ds["step"].values / np.timedelta64(1, "h")).astype(int).tolist()
    # the archive is 6-hourly throughout; only the leads DWD publishes are kept
    assert hours == list(range(0, 121, 6)) + [132, 144, 156, 168]


@pytest.mark.parametrize(
    "extra",
    [
        ("--regrid", "nearest"),  # archive is linear
        ("--target-resolution", "0.1"),  # archive is 0.25 deg
        ("--step-interval", "6h"),  # archived tp is 24-hourly
        ("--bbox", "40/34/-5/42"),  # outside the archive box
    ],
)
def test_auto_falls_back_to_dwd_when_the_archive_cannot_serve(
    tmp_path, fetch_mod, mocked, archive, extra
):
    argv = ["--bbox", "5/34/-5/42", *extra] if extra[0] != "--bbox" else list(extra)
    run_skill(fetch_mod.fetch, *argv, "-o", str(tmp_path / "x.zarr"))
    assert mocked  # downloaded from DWD


def test_source_dwd_never_reads_the_archive(tmp_path, fetch_mod, mocked, archive):
    run_skill(
        fetch_mod.fetch, "--source", "dwd", "--bbox", "5/34/-5/42", "-o", str(tmp_path / "x.zarr")
    )
    assert archive == [] and mocked


def test_source_archive_missing_date_exits_1(tmp_path, fetch_mod, mocked, archive):
    with pytest.raises(SystemExit) as exc:
        run_skill(
            fetch_mod.fetch,
            *("--source", "archive", "--date", "2026-09-01", "--bbox", "5/34/-5/42"),
            *("-o", str(tmp_path / "x.zarr")),
        )
    assert exc.value.code == 1
    assert mocked == []


def test_source_archive_rejects_other_runs_and_global(tmp_path, fetch_mod, mocked, archive):
    for argv in (("--run", "12", "--bbox", "5/34/-5/42"), ()):
        with pytest.raises(SystemExit) as exc:
            run_skill(
                fetch_mod.fetch,
                *("--source", "archive", "--date", "2026-10-01", *argv),
                *("-o", str(tmp_path / "x.zarr")),
            )
        assert exc.value.code == 2


def test_old_date_outside_archive_box_names_both_sources(
    tmp_path, fetch_mod, mocked, archive, capsys
):
    with pytest.raises(SystemExit) as exc:
        run_skill(fetch_mod.fetch, "--date", "2026-10-01", "-o", str(tmp_path / "x.zarr"))
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "DWD" in err and "archive covers only Africa" in err


def test_probe_latest_archive(fetch_mod, mocked, archive, capsys):
    run_skill(fetch_mod.fetch, "--probe-latest", "--source", "archive")
    assert capsys.readouterr().out.strip() == "2026-10-21"
