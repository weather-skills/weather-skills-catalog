"""Tests for subc-mme-fetch (mocked remote opens; no live network)."""

from __future__ import annotations

from datetime import date

import numpy as np
import pytest
import xarray as xr
from conftest import load_skill, run_skill


def _fake_nc(*, kind: str, var: str, fill: float, window_days: int) -> xr.Dataset:
    """Minimal SubC-like in-memory Dataset."""
    lats = np.arange(-90.0, 91.0, 30.0)
    lons = np.arange(0.0, 360.0, 30.0)
    data = np.full((lats.size, lons.size), fill, dtype=np.float32)
    src = "mme_mean" if kind == "mean" else "mme_anom"
    ds = xr.Dataset(
        {src: (("Y", "X"), data)},
        coords={"Y": lats.astype(np.float32), "X": lons.astype(np.float32)},
        attrs={
            "window_start": "2025-12-01",
            "window_end": "2025-12-07",
            "window_days": window_days,
            "operation": "sum" if var == "pr" else "mean",
        },
    )
    ds[src].attrs["units"] = "mm" if var == "pr" else "K"
    if var == "pr":
        ds[src].attrs["standard_name"] = "precipitation_flux"
    kind_label = "mean" if kind == "mean" else "anomaly"
    ds[src].attrs["long_name"] = (
        f"MME {window_days}-day window {kind_label} (forecast mean minus climo)"
        if kind == "anom"
        else f"MME {window_days}-day window mean"
    )
    return ds


@pytest.fixture
def mod():
    return load_skill("subc-mme-fetch", "fetch")


def test_url_builder(mod):
    url = mod._url("07_day", "mean", "ts", "7d", date(2025, 12, 1))
    assert url.startswith("https://storage.googleapis.com/sheerwater-public-datalake/")
    assert url.endswith(
        "/chc-mirror/experimental/SubC/07_day/global/archive/mme_mean_ts_7d_20251201.nc"
    )


def test_normalize_field(mod):
    ds = _fake_nc(kind="anom", var="ts", fill=1.5, window_days=7)
    da = mod._normalize_field(ds, kind="anom", var="ts")
    assert da.name == "ts_anomaly"
    assert "latitude" in da.dims and "longitude" in da.dims


def test_fetch_one_outlook(mod, monkeypatch, tmp_path):
    init = date(2025, 12, 1)
    folder, lead_tag, days = mod.OUTLOOKS["7d"]
    by_url = {}
    for var in ("ts", "pr"):
        for kind in ("mean", "anom"):
            url = mod._url(folder, kind, var, lead_tag, init)
            fill = float(days) + (0.1 if kind == "anom" else 0.0)
            by_url[url] = _fake_nc(kind=kind, var=var, fill=fill, window_days=days)

    def fake_open(url: str):
        if url not in by_url:
            raise FileNotFoundError(url)
        return by_url[url]

    monkeypatch.setattr(mod, "_open_remote", fake_open)

    out = tmp_path / "out.zarr"
    run_skill(
        mod.fetch,
        "--date",
        "2025-12-01",
        "--outlook",
        "7d",
        "-v",
        "ts",
        "-v",
        "pr",
        "-o",
        str(out),
    )
    ds = xr.open_zarr(out, consolidated=True)
    assert set(ds.data_vars) == {"ts", "ts_anomaly", "pr", "pr_anomaly"}
    assert list(ds["step"].values) == [np.timedelta64(7, "D")]
    assert np.issubdtype(ds["step"].dtype, np.timedelta64)
    assert ds["time"].values == np.datetime64("2025-12-08", "ns")
    assert ds.attrs["initialization_date"] == "2025-12-01"
    assert ds.attrs["outlook_valid_date"] == "2025-12-08"
    assert ds.attrs["outlook"] == "7d"
    assert float(ds["ts"].mean()) == pytest.approx(7.0)
    assert float(ds["ts_anomaly"].mean()) == pytest.approx(7.1)
    assert ds["ts_anomaly"].attrs["long_name"] == "SubC MME 7d ts_anomaly"
    assert "7-day" not in ds["ts_anomaly"].attrs["long_name"]
    assert float(ds["pr"].mean()) == pytest.approx(7.0)
    assert float(ds["pr_anomaly"].mean()) == pytest.approx(7.1)
    assert ds["pr"].attrs["units"] == "mm"
    assert ds["pr_anomaly"].attrs["units"] == "mm"
    assert ds["pr"].attrs["standard_name"] == "lwe_thickness_of_precipitation_amount"
    assert ds["pr_anomaly"].attrs["standard_name"] == "lwe_thickness_of_precipitation_amount"


def test_missing_file_raises(mod, monkeypatch, tmp_path):
    from weather_skills_core import DataError

    def fake_open(url: str):
        raise DataError(f"SubC archive file not found: {url}")

    monkeypatch.setattr(mod, "_open_remote", fake_open)
    with pytest.raises(SystemExit) as excinfo:
        run_skill(
            mod.fetch,
            "--date",
            "2025-12-01",
            "--outlook",
            "7d",
            "-v",
            "ts",
            "-o",
            str(tmp_path / "x.zarr"),
        )
    assert excinfo.value.code == 1


def test_probe_latest_with_variable(mod, monkeypatch, capsys):
    def fake_list(folder, lead_tag, var):
        assert folder == "07_day"
        assert lead_tag == "7d"
        assert var == "ts"
        return [date(2025, 12, 1), date(2025, 12, 8)]

    monkeypatch.setattr(mod, "_list_init_dates", fake_list)
    run_skill(mod.fetch, "--outlook", "7d", "--probe-latest", "ts")
    assert capsys.readouterr().out.strip() == "2025-12-08"


def test_probe_latest_all_vars_intersection(mod, monkeypatch, capsys):
    def fake_list(folder, lead_tag, var):
        if var == "pr":
            return [date(2025, 12, 1), date(2025, 12, 8)]
        return [date(2025, 12, 1), date(2025, 12, 8), date(2025, 12, 15)]

    monkeypatch.setattr(mod, "_list_init_dates", fake_list)
    run_skill(mod.fetch, "--outlook", "15d", "--probe-latest")
    # Common max across all six variables (fake returns same for non-pr).
    assert capsys.readouterr().out.strip() == "2025-12-08"


def test_list_init_dates_parses_gcs_listing(mod, monkeypatch):
    prefix = mod._archive_list_prefix("07_day", "7d", "ts")

    def fake_get_json(url: str):
        assert "prefix=" in url
        return {
            "items": [
                {"name": f"{prefix}20251201.nc"},
                {"name": f"{prefix}20251208.nc"},
                {"name": f"{prefix}20251208.nc.bak"},  # ignored
            ]
        }

    monkeypatch.setattr(mod, "_get_json", fake_get_json)
    dates = mod._list_init_dates("07_day", "7d", "ts")
    assert dates == [date(2025, 12, 1), date(2025, 12, 8)]


def test_list_init_dates_paginates(mod, monkeypatch):
    prefix = mod._archive_list_prefix("07_day", "7d", "pr")
    calls = []

    def fake_get_json(url: str):
        calls.append(url)
        if "pageToken" not in url:
            return {
                "items": [{"name": f"{prefix}20251201.nc"}],
                "nextPageToken": "tok",
            }
        return {"items": [{"name": f"{prefix}20251208.nc"}]}

    monkeypatch.setattr(mod, "_get_json", fake_get_json)
    dates = mod._list_init_dates("07_day", "7d", "pr")
    assert dates == [date(2025, 12, 1), date(2025, 12, 8)]
    assert len(calls) == 2
