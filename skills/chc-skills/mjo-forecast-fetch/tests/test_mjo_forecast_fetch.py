"""Tests for mjo-forecast-fetch (mocked download; no live network)."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from conftest import load_skill, run_skill
from PIL import Image
from weather_skills_core import UsageError


def _valid_png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), color=(1, 2, 3)).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def mod():
    return load_skill("mjo-forecast-fetch", "fetch")


@pytest.mark.parametrize(
    ("model", "bias", "filename"),
    [
        ("gefs", True, "GEFS_BC.png"),
        ("gefs", False, "GEFS.png"),
        ("gefs-extended", True, "GMON.png"),  # BC absent → raw fallback
        ("gefs-extended", False, "GMON.png"),
        ("cfs", True, "NCFS.png"),
        ("cmc", True, "CANM.png"),
        ("jma", True, "JMAN.png"),
        ("jma", False, "JMAN.png"),
        ("ecmwf", True, "ECMF_BC.png"),
        ("ecmwf", False, "ECMF.png"),
        ("ecmwf-extended-range", True, "EMON_BC.png"),
        ("ecmwf-extended-range", False, "EMON.png"),
        ("bom", True, "BOMM_BC.png"),
        ("bom", False, "BOMM.png"),
    ],
)
def test_image_url_mapping(mod, model, bias, filename):
    url = mod._image_url(model, bias)
    assert url == f"{mod.IMG_BASE}/{filename}"


def test_cfs_raw_unavailable(mod):
    with pytest.raises(UsageError, match="no raw diagram"):
        mod._image_url("cfs", False)


def test_fetch_writes_png_default_bias_corrected(mod, monkeypatch, tmp_path):
    out = tmp_path / "mjo.png"
    seen = {}

    def fake_download(url, dest):
        seen["url"] = url
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(_valid_png_bytes())

    monkeypatch.setattr(mod, "_download", fake_download)
    run_skill(mod.fetch, "--model", "gefs", "-o", str(out))
    assert Path(out).exists()
    assert seen["url"].endswith("/GEFS_BC.png")
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_fetch_bias_corrected_no(mod, monkeypatch, tmp_path):
    out = tmp_path / "mjo.png"
    seen = {}

    def fake_download(url, dest):
        seen["url"] = url
        dest.write_bytes(_valid_png_bytes())

    monkeypatch.setattr(mod, "_download", fake_download)
    run_skill(
        mod.fetch,
        "--model",
        "bom",
        "--no-bias-corrected",
        "-o",
        str(out),
    )
    assert seen["url"].endswith("/BOMM.png")


def test_unknown_model_raises(mod):
    with pytest.raises(UsageError, match="unknown --model"):
        mod._image_url("gfs", True)


def test_all_page_models_registered(mod):
    assert set(mod.MODEL_CHOICES) == {
        "gefs",
        "gefs-extended",
        "cfs",
        "cmc",
        "jma",
        "ecmwf",
        "ecmwf-extended-range",
        "bom",
    }
