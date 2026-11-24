"""Correctness tests for ecmwf-04deg-fetch (GCS listing and file access mocked)."""

from datetime import date

import numpy as np
import pytest
import xarray as xr
from conftest import load_skill, run_skill
from weather_skills_core.provenance import load_history

INIT = date(2026, 10, 26)
MEMBERS = 3
LEADS = 46


@pytest.fixture(scope="module")
def fetch_mod():
    return load_skill("ecmwf-04deg-fetch", "fetch")


def chc_file():
    """A small stand-in for a CHC file: `pr` (mm/day) on M, L, Y, X with float32 coords."""
    lats = np.arange(8.0, -8.01, -0.4, dtype="float32")
    lons = np.arange(30.0, 46.01, 0.4, dtype="float32")
    # Lead day L carries the value L, member M adds 100*M.
    values = (
        np.arange(LEADS, dtype="float32")[None, :, None, None]
        + 100.0 * np.arange(MEMBERS, dtype="float32")[:, None, None, None]
        + np.zeros((1, 1, lats.size, lons.size), dtype="float32")
    )
    return xr.Dataset(
        {"pr": (("M", "L", "Y", "X"), values, {"units": "mm/day"})},
        coords={
            "M": np.arange(MEMBERS, dtype="int16"),
            "L": np.arange(LEADS, dtype="int16"),
            "Y": lats,
            "X": lons,
        },
        attrs={"forecast_initialization_date": INIT.isoformat()},
    )


@pytest.fixture
def mocked(fetch_mod, monkeypatch):
    path = fetch_mod._archive_path(INIT)
    monkeypatch.setattr(fetch_mod, "_latest_init", lambda: (INIT, path))
    monkeypatch.setattr(fetch_mod, "_resolve_init", lambda d: (INIT, path))
    monkeypatch.setattr(fetch_mod, "_open_source", lambda p: chc_file())
    return path


def test_fetch_layout_units_and_history(tmp_path, fetch_mod, mocked):
    out = tmp_path / "k.zarr"
    run_skill(fetch_mod.fetch, "--bbox", "5.5/33.5/-5/42.5", "--max-lead", "14D", "-o", str(out))

    with xr.open_zarr(out, consolidated=True) as ds:
        tp = ds["tp"]
        assert tp.dims == ("number", "step", "latitude", "longitude")
        assert tp.attrs["units"] == "mm day-1"
        assert tp.attrs["data_interval"] == "1 day"
        assert ds.sizes["step"] == 14 and ds.sizes["number"] == MEMBERS
        assert ds["step"].values[0] == np.timedelta64(0, "ns")
        assert ds["step"].values[-1] == np.timedelta64(13, "D")
        assert ds["time"].values == np.datetime64("2026-10-26T00:00", "ns")
        assert ds["time"].attrs["standard_name"] == "forecast_reference_time"
        assert ds["valid_time"].values[1] == np.datetime64("2026-10-27T00:00", "ns")
        np.testing.assert_allclose(tp.isel(number=0).mean(["latitude", "longitude"]), range(14))
        np.testing.assert_allclose(tp.isel(number=2, step=0), 200.0)
        assert float(ds["latitude"].max()) <= 5.5 and float(ds["latitude"].min()) >= -5.0
        assert float(ds["longitude"].min()) >= 33.5 and float(ds["longitude"].max()) <= 42.5
        assert ds["latitude"].values[0] < ds["latitude"].values[-1]
        assert ds.attrs["weather_skills_source"] == "sheerwater-ecmwf-0_4:20261026"
    assert load_history(out)[-1]["skill"] == "ecmwf-04deg-fetch"


def test_default_keeps_all_leads(tmp_path, fetch_mod, mocked):
    out = tmp_path / "all.zarr"
    run_skill(fetch_mod.fetch, "--bbox", "1/36/-1/38", "-o", str(out))
    with xr.open_zarr(out, consolidated=True) as ds:
        assert ds.sizes["step"] == LEADS


def test_probe_latest_prints_init(fetch_mod, mocked, capsys):
    run_skill(fetch_mod.fetch, "--probe-latest")
    assert capsys.readouterr().out.strip() == INIT.isoformat()


def test_unknown_variable_exits_2(tmp_path, fetch_mod, mocked):
    with pytest.raises(SystemExit) as exc:
        run_skill(fetch_mod.fetch, "-v", "t2m", "-o", str(tmp_path / "x.zarr"))
    assert exc.value.code == 2


def test_bad_max_lead_exits_2(tmp_path, fetch_mod, mocked):
    with pytest.raises(SystemExit) as exc:
        run_skill(fetch_mod.fetch, "--max-lead", "36h", "-o", str(tmp_path / "x.zarr"))
    assert exc.value.code == 2


class ArchiveFS:
    """Stand-in GCS holding the given object paths (zarr stores as their zarr.json)."""

    def __init__(self, objects):
        self.objects = set(objects)

    def exists(self, path):
        return path in self.objects

    def ls(self, prefix, detail=False):
        names = {p[len(prefix) + 1 :].split("/")[0] for p in self.objects if p.startswith(prefix)}
        if not names:
            raise FileNotFoundError(prefix)
        return [f"{prefix}/{n}" for n in sorted(names)]


def test_latest_prefers_complete_zarr_then_nc(fetch_mod, monkeypatch):
    zarr26 = fetch_mod._archive_path(date(2026, 10, 26))
    nc26 = fetch_mod._archive_path(date(2026, 10, 26), "daily.nc")
    nc25 = fetch_mod._archive_path(date(2026, 10, 25), "daily.nc")
    zarr25 = fetch_mod._archive_path(date(2026, 10, 25))
    fs = ArchiveFS([f"{zarr26}/zarr.json", nc26, nc25, f"{zarr25}/pr/c/0/0/0/0"])
    monkeypatch.setattr(fetch_mod, "_gcs", lambda asynchronous=False: fs)

    assert fetch_mod._resolve_init(date(2026, 10, 26)) == (date(2026, 10, 26), zarr26)
    # 10-25's zarr upload never finished (no root zarr.json): its netCDF file is read
    assert fetch_mod._resolve_init(date(2026, 10, 25)) == (date(2026, 10, 25), nc25)

    fs.objects.discard(f"{zarr26}/zarr.json")
    fs.objects.discard(nc26)
    fs.objects.add(f"{zarr26}/pr/c/0/0/0/0")

    class Today(date):
        @classmethod
        def today(cls):
            return date(2026, 10, 27)

    monkeypatch.setattr(fetch_mod, "_date", Today)
    assert fetch_mod._latest_init() == (date(2026, 10, 25), nc25)


def test_missing_init_exits_1(tmp_path, fetch_mod, monkeypatch):
    class FakeFS:
        def exists(self, path):
            return False

    monkeypatch.setattr(fetch_mod, "_gcs", lambda asynchronous=False: FakeFS())
    monkeypatch.setattr(fetch_mod, "_list_inits", lambda years: {})
    with pytest.raises(SystemExit) as exc:
        run_skill(fetch_mod.fetch, "--date", "2020-01-01", "-o", str(tmp_path / "x.zarr"))
    assert exc.value.code == 1


def test_key_json_env_order(fetch_mod, monkeypatch):
    for name in fetch_mod._CREDENTIAL_ENVS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NEURAL_GCM_SERVICE_CREDENTIALS", '{"client_email": "neural"}')
    assert fetch_mod._token() == {"client_email": "neural"}
    monkeypatch.setenv("GCLOUD_SERVICE_CREDENTIALS", '{"client_email": "gcloud"}')
    assert fetch_mod._token() == {"client_email": "gcloud"}


def test_bad_key_json_names_the_variable_not_the_value(fetch_mod, monkeypatch):
    monkeypatch.setenv("GCLOUD_SERVICE_CREDENTIALS", "not-json-secret")
    with pytest.raises(fetch_mod.DataError) as exc:
        fetch_mod._token()
    assert "GCLOUD_SERVICE_CREDENTIALS" in str(exc.value)
    assert "not-json-secret" not in str(exc.value)
