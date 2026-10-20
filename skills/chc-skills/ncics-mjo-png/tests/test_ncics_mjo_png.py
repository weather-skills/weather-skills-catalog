"""Tests for ncics-mjo-png (mocked download; no live network)."""

from __future__ import annotations

import io
from datetime import date
from pathlib import Path

import pytest
from conftest import load_skill, run_skill
from PIL import Image
from weather_skills_core import DataError, UsageError


def _valid_png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), color=(1, 2, 3)).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def mod():
    return load_skill("ncics-mjo-png", "fetch")


def test_default_map_url_is_africa_olr_cfs_7day(mod):
    url = mod._image_url("map", "olr", "cfs", "all", "africa", 7, "wide", 200)
    assert url == f"{mod.IMG_BASE}/map/olr.cfs.all.africa.7.png"


def test_default_hovmoller_url_is_tropics_wide(mod):
    url = mod._image_url("hovmoller", "olr", "cfs", "all", "africa", 7, "wide", 200)
    assert url == f"{mod.IMG_BASE}/hov/olr.cfs.wide.png"


@pytest.mark.parametrize(
    ("variable", "level", "stem"),
    [
        ("olr", 200, "olr"),
        ("uwnd", 850, "uwnd850"),
        ("chi", 200, "chi200"),
        ("chi200", 850, "chi200"),  # complete token wins over --level
        ("ushear", 200, "uShear"),
        ("u850", 200, "uwnd850"),
        ("pwat", 850, "pwat"),
    ],
)
def test_filename_variable(mod, variable, level, stem):
    assert mod._filename_variable(variable, level) == stem


@pytest.mark.parametrize(
    ("product", "kwargs", "suffix"),
    [
        ("hov", {"latitude": "tropics"}, "/hov/olr.cfs.wide.png"),
        ("hovmuller", {"latitude": "eqtr"}, "/hov/olr.cfs.eqtr.png"),
        ("map", {"region": "western-hemisphere"}, "/map/olr.cfs.all.west.7.png"),
        ("maps", {"wave": "kelvin", "days": 10}, "/map/olr.cfs.kelvin.africa.10.png"),
    ],
)
def test_aliases(mod, product, kwargs, suffix):
    params = {
        "product": product,
        "variable": "olr",
        "algorithm": "cfs",
        "wave": "all",
        "region": "africa",
        "days": 7,
        "latitude": "wide",
        "level": 200,
    }
    params.update(kwargs)
    assert mod._image_url(**params).endswith(suffix)


def test_archive_url_uses_v2_snapshot_tree(mod):
    url = mod._image_url(
        "map",
        "olr",
        "cfs",
        "all",
        "africa",
        7,
        "wide",
        200,
        snapshot=date(2024, 12, 30),
    )
    assert url == (f"{mod.ARCHIVE_BASE}/2024/2024-12-30/v2/map/olr.cfs.all.africa.7.png")
    hov = mod._image_url(
        "hovmoller",
        "olr",
        "cfs",
        "all",
        "africa",
        7,
        "wide",
        200,
        snapshot=date(2024, 12, 30),
    )
    assert hov.endswith("/archive/2024/2024-12-30/v2/hov/olr.cfs.wide.png")


def test_resolve_archive_date_exact_and_snap(mod, monkeypatch):
    monkeypatch.setattr(
        mod,
        "_list_year_dates",
        lambda year: {
            2024: [date(2024, 12, 23), date(2024, 12, 30)],
            2023: [date(2023, 12, 25)],
        }.get(year, []),
    )
    assert mod._resolve_archive_date(date(2024, 12, 30)) == date(2024, 12, 30)
    assert mod._resolve_archive_date(date(2024, 12, 28)) == date(2024, 12, 23)
    assert mod._resolve_archive_date(date(2024, 1, 2)) == date(2023, 12, 25)


def test_resolve_archive_date_too_early(mod, monkeypatch):
    monkeypatch.setattr(mod, "_list_year_dates", lambda year: [])
    with pytest.raises(DataError, match="no NCICS MJO archive snapshot"):
        mod._resolve_archive_date(date(2010, 1, 1))


def test_parse_year_listing_hrefs(mod, monkeypatch):
    html = """
    <a href="2026-09-01/">2026-09-01/</a>
    <a href="/pub/mjo/archive/2026/2026-08-31/">2026-08-31/</a>
    <a href="gdas_tcvitals_2026.txt">skip</a>
    """
    monkeypatch.setattr(mod, "_get_text", lambda url: html)
    assert mod._list_year_dates(2026) == [date(2026, 8, 31), date(2026, 9, 1)]


def test_sst_algorithm_rejected_for_maps(mod):
    with pytest.raises(UsageError, match="Hovmöller-only"):
        mod._image_url("map", "olr", "sst", "all", "africa", 7, "wide", 200)


def test_sst_algorithm_ok_for_hovmoller(mod):
    url = mod._image_url("hovmoller", "olr", "sst", "all", "africa", 7, "wide", 200)
    assert url.endswith("/hov/olr.sst.wide.png")


def test_unknown_variable_raises(mod):
    with pytest.raises(UsageError, match="unknown --variable"):
        mod._filename_variable("sst", 200)


def test_unknown_region_raises(mod):
    with pytest.raises(UsageError, match="unknown --region"):
        mod._image_url("map", "olr", "cfs", "all", "sahel", 7, "wide", 200)


def test_fetch_writes_png_default_map(mod, monkeypatch, tmp_path):
    out = tmp_path / "olr.png"
    seen = {}

    def fake_download(url, dest):
        seen["url"] = url
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(_valid_png_bytes())

    monkeypatch.setattr(mod, "_download", fake_download)
    run_skill(mod.fetch, "-o", str(out))
    assert Path(out).exists()
    assert seen["url"] == f"{mod.IMG_BASE}/map/olr.cfs.all.africa.7.png"
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_fetch_hovmoller(mod, monkeypatch, tmp_path):
    out = tmp_path / "hov.png"
    seen = {}

    def fake_download(url, dest):
        seen["url"] = url
        dest.write_bytes(_valid_png_bytes())

    monkeypatch.setattr(mod, "_download", fake_download)
    run_skill(
        mod.fetch,
        "--product",
        "hovmoller",
        "--variable",
        "chi",
        "--latitude",
        "eqtr",
        "-o",
        str(out),
    )
    assert seen["url"].endswith("/hov/chi200.cfs.eqtr.png")


def test_fetch_archive_date(mod, monkeypatch, tmp_path):
    out = tmp_path / "hist.png"
    seen = {}

    monkeypatch.setattr(mod, "_list_year_dates", lambda year: [date(2024, 12, 30)])

    def fake_download(url, dest):
        seen["url"] = url
        dest.write_bytes(_valid_png_bytes())

    monkeypatch.setattr(mod, "_download", fake_download)
    run_skill(mod.fetch, "--date", "2024-12-30", "-o", str(out))
    assert seen["url"] == (f"{mod.ARCHIVE_BASE}/2024/2024-12-30/v2/map/olr.cfs.all.africa.7.png")


def test_fetch_snaps_to_prior_snapshot(mod, monkeypatch, tmp_path):
    out = tmp_path / "snap.png"
    seen = {}

    monkeypatch.setattr(
        mod,
        "_list_year_dates",
        lambda year: [date(2024, 12, 23), date(2024, 12, 30)] if year == 2024 else [],
    )

    def fake_download(url, dest):
        seen["url"] = url
        dest.write_bytes(_valid_png_bytes())

    monkeypatch.setattr(mod, "_download", fake_download)
    run_skill(mod.fetch, "--date", "2024-12-28", "-o", str(out))
    assert "2024-12-23/v2/map/" in seen["url"]


def test_probe_latest(mod, monkeypatch, capsys):
    class _Now:
        @staticmethod
        def now(tz=None):
            from datetime import UTC, datetime

            return datetime(2026, 9, 2, tzinfo=UTC)

    monkeypatch.setattr(mod, "datetime", _Now)
    monkeypatch.setattr(
        mod,
        "_list_year_dates",
        lambda year: [date(2026, 9, 1)] if year == 2026 else [],
    )
    run_skill(mod.fetch, "--probe-latest")
    assert capsys.readouterr().out.strip() == "2026-09-01"


def test_probe_latest_rejects_a_value(mod, capsys):
    with pytest.raises(SystemExit) as exc:
        run_skill(mod.fetch, "--probe-latest", "olr")
    assert exc.value.code == 2
    assert "takes no value" in capsys.readouterr().err


def test_catalog_tokens_match_ncics_archive(mod):
    assert set(mod.PRODUCTS) == {"map", "hovmoller"}
    assert set(mod.ALGORITHMS) == {"cfs", "orig", "notc", "sst"}
    assert set(mod.WAVES) == {"all", "tc", "sum", "low", "mjo", "er", "kelvin", "mtd"}
    assert set(mod.REGIONS) == {
        "africa",
        "global",
        "west",
        "east",
        "pacific",
        "atlantic",
        "indonesia",
        "asia",
    }
    assert set(mod.LATITUDES) == {"wide", "eqtr", "north", "south", "north2"}
    assert set(mod.DAYS) == {1, 2, 3, 5, 7, 10}
    assert "uShear" in mod.COMPLETE_VARS
    assert "uwnd850" in mod.COMPLETE_VARS
