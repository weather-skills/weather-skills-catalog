"""Tests for africa-itf (mocked download; no live network)."""

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


def _valid_jpeg_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), color=(10, 20, 30)).save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture
def mod():
    return load_skill("africa-itf", "fetch")


@pytest.mark.parametrize(
    ("location", "filename"),
    [
        ("africa", "itcz.jpg"),
        ("east-africa", "east.gif"),
        ("west-africa", "west.gif"),
    ],
)
def test_image_url_mapping(mod, location, filename):
    assert mod._image_url(location) == f"{mod.IMG_BASE}/{filename}"


def test_unknown_location_raises(mod):
    with pytest.raises(UsageError, match="unknown --location"):
        mod._image_url("sahel")


def test_fetch_africa_default_converts_jpeg_to_png(mod, monkeypatch, tmp_path):
    out = tmp_path / "africa.png"
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url

        class Resp:
            headers = {"Content-Type": "image/jpeg"}

            def read(self):
                return _valid_jpeg_bytes()

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        return Resp()

    monkeypatch.setattr(mod.urllib.request, "urlopen", fake_urlopen)
    run_skill(mod.fetch, "-o", str(out))
    assert out.exists()
    assert seen["url"].endswith("/itcz.jpg")
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_fetch_west_africa(mod, monkeypatch, tmp_path):
    out = tmp_path / "west.png"
    seen = {}

    def fake_download(url, dest):
        seen["url"] = url
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(_valid_png_bytes())

    monkeypatch.setattr(mod, "_download_as_png", fake_download)
    run_skill(mod.fetch, "--location", "west-africa", "-o", str(out))
    assert Path(out).exists()
    assert seen["url"].endswith("/west.gif")
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_fetch_east_africa(mod, monkeypatch, tmp_path):
    out = tmp_path / "east.png"
    seen = {}

    def fake_download(url, dest):
        seen["url"] = url
        dest.write_bytes(_valid_png_bytes())

    monkeypatch.setattr(mod, "_download_as_png", fake_download)
    run_skill(mod.fetch, "--location", "east-africa", "-o", str(out))
    assert seen["url"].endswith("/east.gif")


def test_location_files_match_page(mod):
    assert set(mod.LOCATION_CHOICES) == {"africa", "east-africa", "west-africa"}
    assert mod.LOCATION_FILES == {
        "africa": "itcz.jpg",
        "east-africa": "east.gif",
        "west-africa": "west.gif",
    }
