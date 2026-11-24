"""Tests for iod-enso-fetch (mocked download; no live network)."""

from __future__ import annotations

import io
import json
from datetime import date
from urllib.error import HTTPError

import numpy as np
import pytest
import xarray as xr
from conftest import load_skill, run_skill
from PIL import Image
from weather_skills_core import DataError


def _png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), color=(1, 2, 3)).save(buf, format="PNG")
    return buf.getvalue()


def _palette_png_bytes(pink=(255, 234, 234), blue=(226, 226, 255)):
    """BoM-like 8-bit palette PNG: pink upper half, blue lower half."""
    img = Image.new("P", (400, 300))
    palette = [0] * (256 * 3)
    palette[0:3] = pink
    palette[3:6] = blue
    img.putpalette(palette)
    pixels = img.load()
    for y in range(300):
        for x in range(400):
            pixels[x, y] = 0 if y < 150 else 1
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue(), pink, blue


FORECAST_PAYLOAD = {
    "data": {
        "mean": {"Aug 2026": "NaN", "Sep 2026": 0.6},
        "frequency": {
            "Aug 2026": {"below −0.4": 0.0, "neutral": 0.0, "above 0.4": 0.0},
            "Sep 2026": {"below −0.4": 0.0, "neutral": 5.05, "above 0.4": 94.95},
        },
    }
}
RONI_SAMPLE = "SEAS   YR  ANOM\nDJF  1950 -1.19\nNDJ  1950 -99.90\nDJF  1951 -0.80\n"
CPC_MONTHLY_SAMPLE = "YR   MTH   ANOM\n1949  12  -1.03\n1950   1  -1.34\n2026  10   2.16\n"
CPC_WEEKLY_SAMPLE = (
    "Weekly Relative SST data starts week centered on 9Sept1981\n\n"
    "                Nino1+2      Nino3      Nino34     Nino4\n"
    " Week             SSTA       SSTA       SSTA       SSTA\n"
    " 09SEP1981        -0.4       -0.1       -0.1       -0.1\n"
    " 04NOV2026         4.7        3.5        2.3        0.2\n"
)


@pytest.fixture
def mod():
    return load_skill("iod-enso-fetch", "fetch")


@pytest.fixture
def served(mod, monkeypatch):
    """Serve ``body`` for every download; record each request's url and referer."""
    seen = {"body": _png_bytes(), "urls": [], "referers": []}

    def fake_read(url, *, referer, accept):
        seen["urls"].append(url)
        seen["referers"].append(referer)
        return seen["body"], "text/plain"

    monkeypatch.setattr(mod, "_read_url", fake_read)
    monkeypatch.setattr(mod, "_archive_dates", lambda: seen.get("dates", []))
    return seen


# --- observation figures ---------------------------------------------------


@pytest.mark.parametrize(
    ("index", "path"),
    [
        ("iod", "/IDCK000072/iod1.png"),
        ("enso", "/IDCK000072/rnino_3.4.png"),
        ("relative-nino3.4", "/IDCK000072/rnino_3.4.png"),
        ("nino3.4", "/IDCK000072/nino3_4.png"),
        ("soi", "/IDCKGSM000/soi30.png"),
    ],
)
def test_fetch_observation_png(mod, served, tmp_path, index, path):
    out = tmp_path / "g.png"
    run_skill(mod.fetch, "--index", index, "-o", str(out))
    assert served["urls"] == [mod.BOM + path]
    assert served["referers"] == [mod.OBS_PAGE]
    assert out.read_bytes()[:8] == mod._PNG_SIGNATURE


def test_fetch_keeps_palette_fills_after_stamp(mod, served, tmp_path):
    served["body"], pink, blue = _palette_png_bytes()
    out = tmp_path / "iod.png"
    run_skill(mod.fetch, "--index", "iod", "-o", str(out))
    rgb = Image.open(out).convert("RGB")
    assert rgb.getpixel((20, 20)) == pink
    assert rgb.getpixel((20, 280)) == blue


def test_fetch_rejects_non_png(mod, served, tmp_path):
    served["body"] = b"<html>"
    with pytest.raises(SystemExit) as exc:
        run_skill(mod.fetch, "--index", "iod", "-o", str(tmp_path / "g.png"))
    assert exc.value.code != 0


def test_observation_rejects_date(mod, tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        run_skill(
            mod.fetch, "--index", "iod", "--date", "2024-06-08", "-o", str(tmp_path / "a.png")
        )
    assert exc.value.code == 2
    assert "current-only" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--format", "data", "-o", "x.png"], "Zarr"),
        (["--format", "figure", "-o", "x.zarr"], "PNG"),
    ],
)
def test_format_suffix_mismatch(mod, tmp_path, monkeypatch, capsys, args, message):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exc:
        run_skill(mod.fetch, "--index", "iod", *args)
    assert exc.value.code == 2
    assert message in capsys.readouterr().err


# --- observation data ------------------------------------------------------


@pytest.mark.parametrize(
    ("index", "path"),
    [
        ("iod", "/IDCK000072/iod_1.txt"),
        ("enso", "/IDCK000072/rnino_3.4.txt"),
        ("nino3.4", "/IDCK000072/nino_3.4.txt"),
        ("soi", "/IDCKGSM000/soi.txt"),
    ],
)
def test_fetch_bom_data_zarr(mod, served, tmp_path, index, path):
    served["body"] = b"20080728,20080803,0.01\n20080804,20080810,0.22\n"
    out = tmp_path / "d.zarr"
    run_skill(mod.fetch, "--index", index, "-o", str(out))
    ds = xr.open_zarr(out, consolidated=True)
    assert served["urls"] == [mod.BOM + path]
    assert float(ds[mod.INDICES[index][2]].isel(time=1)) == pytest.approx(0.22)


def test_bom_dataset_weekly(mod):
    ds = mod._obs_dataset("iod", "20080728,20080803,0.01\n20080804,20080810,-0.20\n")
    assert list(ds["time"].values) == [
        np.datetime64("2008-08-03", "ns"),
        np.datetime64("2008-08-10", "ns"),
    ]
    assert float(ds["iod_mode_index"].isel(time=1)) == pytest.approx(-0.20)
    assert ds["iod_mode_index"].attrs["units"] == "degree_Celsius"
    assert ds["iod_mode_index"].attrs["data_interval"] == "7 day"
    assert ds.attrs["weather_skills_source"] == "bom"


def test_bom_dataset_soi_daily(mod):
    ds = mod._obs_dataset("soi", "20260801,20260830,-14.6\n20260802,20260831,-14.6\n")
    assert ds["soi"].attrs["units"] == "1"
    assert ds["soi"].attrs["data_interval"] == "1 day"


def test_obs_dataset_rejects_html_and_bad_rows(mod):
    with pytest.raises(DataError, match="HTML"):
        mod._obs_dataset("iod", "<!DOCTYPE html>")
    with pytest.raises(DataError, match="line 1"):
        mod._obs_dataset("iod", "20080728,2008xx03,0.01\n")
    with pytest.raises(DataError, match="line 2"):
        mod._obs_dataset("cpc-relative-nino3.4-monthly", "YR MTH ANOM\n1950 1\n")
    with pytest.raises(DataError, match="no values"):
        mod._obs_dataset("roni", "SEAS YR ANOM\n")


def test_roni_dataset(mod):
    ds = mod._obs_dataset("roni", RONI_SAMPLE)
    assert list(ds["time"].values) == [
        np.datetime64("1950-01-01", "ns"),
        np.datetime64("1950-12-01", "ns"),
        np.datetime64("1951-01-01", "ns"),
    ]
    assert list(ds["time_bounds"].values[0]) == [
        np.datetime64("1949-12-01", "ns"),
        np.datetime64("1950-03-01", "ns"),
    ]
    assert ds["time_bounds"].values[1, 1] == np.datetime64("1951-02-01", "ns")
    assert float(ds["roni"][0]) == pytest.approx(-1.19)
    assert np.isnan(ds["roni"][1])
    assert "data_interval" not in ds["roni"].attrs
    assert ds.attrs["weather_skills_source"] == "noaa-cpc"


def test_cpc_monthly_dataset(mod):
    ds = mod._obs_dataset("cpc-relative-nino3.4-monthly", CPC_MONTHLY_SAMPLE)
    assert list(ds["time"].values) == [
        np.datetime64("1949-12-01", "ns"),
        np.datetime64("1950-01-01", "ns"),
        np.datetime64("2026-10-01", "ns"),
    ]
    assert ds["time_bounds"].values[0, 1] == np.datetime64("1950-01-01", "ns")
    assert list(ds["relative_nino34"].values) == [-1.03, -1.34, 2.16]
    assert "data_interval" not in ds["relative_nino34"].attrs


def test_cpc_weekly_dataset(mod):
    ds = mod._obs_dataset("cpc-relative-nino3.4-weekly", CPC_WEEKLY_SAMPLE)
    assert list(ds["time"].values) == [
        np.datetime64("1981-09-09", "ns"),
        np.datetime64("2026-11-04", "ns"),
    ]
    assert list(ds["relative_nino34"].values) == [-0.1, 2.3]
    assert ds["relative_nino34"].attrs["data_interval"] == "7 day"


@pytest.mark.parametrize(
    ("index", "sample", "path", "n"),
    [
        ("roni", RONI_SAMPLE, "/RONI.ascii.txt", 3),
        ("cpc-relative-nino3.4-monthly", CPC_MONTHLY_SAMPLE, "/Rnino34.ascii.txt", 3),
        ("cpc-relative-nino3.4-weekly", CPC_WEEKLY_SAMPLE, "/rel_wksst9120.txt", 2),
    ],
)
def test_fetch_cpc_zarr(mod, served, tmp_path, index, sample, path, n):
    served["body"] = sample.encode()
    out = tmp_path / "c.zarr"
    run_skill(mod.fetch, "--index", index, "-o", str(out))
    assert served["urls"] == [mod.CPC + path]
    assert served["referers"] == [mod.CPC_PAGE]
    assert xr.open_zarr(out, consolidated=True).sizes["time"] == n


@pytest.mark.parametrize(
    "extra",
    [
        ["--product", "forecast", "-o", "x.zarr"],
        ["-o", "x.png"],
        ["--format", "figure", "-o", "x.zarr"],
    ],
)
@pytest.mark.parametrize(
    "index", ["roni", "cpc-relative-nino3.4-monthly", "cpc-relative-nino3.4-weekly"]
)
def test_cpc_rejects_figure_and_forecast(mod, tmp_path, monkeypatch, extra, index):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exc:
        run_skill(mod.fetch, "--index", index, *extra)
    assert exc.value.code == 2


# --- forecasts -------------------------------------------------------------


@pytest.mark.parametrize(
    ("index", "issue", "stem"),
    [
        ("iod", "2026-08-29", "20260829/plumes/sstOutlooks.iod"),
        ("enso", "2026-08-29", "20260829/plumes/sstOutlooks.rnino34"),
        ("relative-nino3.4", "2026-08-29", "20260829/plumes/sstOutlooks.rnino34"),
        ("enso", "2025-06-28", "20250628/plumes/sstOutlooks.nino34"),
        ("nino3.4", "2026-08-29", "20260829/plumes/sstOutlooks.nino34"),
    ],
)
def test_fetch_forecast_png(mod, served, tmp_path, index, issue, stem):
    served["dates"] = [date(2025, 6, 28), date(2026, 8, 29)]
    run_skill(
        mod.fetch, "--index", index, "--product", "forecast", "--date", issue,
        "-o", str(tmp_path / "f.png"),
    )  # fmt: skip
    assert served["urls"] == [f"{mod.FORECAST_ARCHIVE}/{stem}.hr.png"]
    assert served["referers"] == [mod.FORECAST_PAGE]


def test_fetch_forecast_snaps_to_earlier_issue(mod, served, tmp_path, capsys):
    served["dates"] = [date(2024, 6, 8), date(2024, 6, 22), date(2026, 8, 29)]
    run_skill(
        mod.fetch, "--index", "iod", "--product", "forecast", "--date", "2024-06-20",
        "-o", str(tmp_path / "f.png"),
    )  # fmt: skip
    assert "/20240608/" in served["urls"][0]
    assert "using 2024-06-08" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("index", "issue", "message"),
    [
        ("relative-nino3.4", "2025-06-28", "relative-nino3.4 starts"),
        ("soi", "2026-08-29", "no plume"),
        ("iod", "2018-01-01", "before the ACCESS-S"),
    ],
)
def test_forecast_usage_errors(mod, served, tmp_path, capsys, index, issue, message):
    served["dates"] = [date(2025, 6, 28), date(2026, 8, 29)]
    with pytest.raises(SystemExit) as exc:
        run_skill(
            mod.fetch, "--index", index, "--product", "forecast", "--date", issue,
            "-o", str(tmp_path / "f.png"),
        )  # fmt: skip
    assert exc.value.code == 2
    assert message in capsys.readouterr().err


def test_fetch_forecast_data_zarr(mod, served, tmp_path):
    served["dates"] = [date(2026, 8, 29)]
    served["body"] = json.dumps(FORECAST_PAYLOAD).encode()
    out = tmp_path / "f.zarr"
    run_skill(mod.fetch, "--index", "iod", "--product", "forecast", "-o", str(out))
    ds = xr.open_zarr(out, consolidated=True)
    assert served["urls"] == [f"{mod.FORECAST_ARCHIVE}/20260829/plumes/sstOutlooks.iod.json"]
    assert float(ds["iod_mode_index"].isel(time=1)) == pytest.approx(0.6)
    assert float(ds["prob_above"].isel(time=1)) == pytest.approx(94.95)


def test_forecast_dataset(mod):
    ds = mod._forecast_dataset("iod", date(2026, 8, 29), FORECAST_PAYLOAD)
    assert list(ds["time"].values) == [
        np.datetime64("2026-08-01", "ns"),
        np.datetime64("2026-09-01", "ns"),
    ]
    assert np.isnan(ds["iod_mode_index"][0])
    assert ds["time_bounds"].values[1, 1] == np.datetime64("2026-10-01", "ns")
    assert ds["time"].attrs["bounds"] == "time_bounds"
    assert ds["prob_above"].attrs["units"] == "percent"
    assert np.datetime64(ds["init_time"].values, "ns") == np.datetime64("2026-08-29", "ns")


def test_forecast_dataset_rejects_missing_bucket(mod):
    payload = {"data": {"mean": {"Sep 2026": 0.6}, "frequency": {"Sep 2026": {"neutral": 5.0}}}}
    with pytest.raises(DataError, match="malformed"):
        mod._forecast_dataset("iod", date(2026, 8, 29), payload)


def test_archive_dates(mod, monkeypatch):
    payload = {
        "archive_index": {
            "data": {"index": [{"init_date": "2026-08-29"}, {"init_date": "2024-06-08"}]}
        }
    }
    monkeypatch.setattr(mod, "_read_url", lambda *a, **k: (json.dumps(payload).encode(), ""))
    assert mod._archive_dates() == [date(2024, 6, 8), date(2026, 8, 29)]


# --- probe -----------------------------------------------------------------


def test_probe_latest_forecast(mod, served, capsys):
    served["dates"] = [date(2026, 8, 22), date(2026, 8, 29)]
    run_skill(mod.fetch, "--index", "iod", "--product", "forecast", "--probe-latest")
    assert capsys.readouterr().out.strip() == "2026-08-29"


@pytest.mark.parametrize(
    ("index", "header", "expected"),
    [
        ("iod", "Mon, 31 Aug 2026 06:00:00 GMT", "2026-08-31"),
        ("roni", "Tue, 03 Nov 2026 19:31:48 GMT", "2026-11-03"),
        ("iod", None, "none"),
    ],
)
def test_probe_latest_observation(mod, monkeypatch, capsys, index, header, expected):
    class Resp:
        headers = {"Last-Modified": header} if header else {}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(mod.urllib.request, "urlopen", lambda req, timeout=None: Resp())
    run_skill(mod.fetch, "--index", index, "--probe-latest")
    assert capsys.readouterr().out.strip() == expected


def test_probe_latest_rejects_a_value(mod, capsys):
    with pytest.raises(SystemExit) as exc:
        run_skill(mod.fetch, "--index", "iod", "--probe-latest", "iod")
    assert exc.value.code == 2
    assert "takes no value" in capsys.readouterr().err


# --- HTTP ------------------------------------------------------------------


def test_read_url_sends_browser_headers_and_cache_buster(mod, monkeypatch):
    seen = {}

    class Resp:
        def __init__(self):
            self.headers = {"Content-Type": "image/png"}

        def read(self):
            return b"x"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["ua"] = req.headers.get("User-agent")
        seen["referer"] = req.headers.get("Referer")
        return Resp()

    monkeypatch.setattr(mod.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(mod.time, "time", lambda: 1788374783)
    assert mod._read_url("https://h/iod1.png", referer=mod.OBS_PAGE, accept="*/*") == (
        b"x",
        "image/png",
    )
    assert seen["url"] == "https://h/iod1.png?1788374783"
    assert "Mozilla" in seen["ua"]
    assert seen["referer"] == mod.OBS_PAGE


def test_read_url_http_error(mod, monkeypatch):
    def boom(req, timeout=None):
        raise HTTPError(req.full_url, 403, "Forbidden", hdrs=None, fp=None)

    monkeypatch.setattr(mod.urllib.request, "urlopen", boom)
    with pytest.raises(DataError, match="HTTP 403"):
        mod._read_url("https://h/iod1.png", referer=mod.OBS_PAGE, accept="*/*")
