"""Tests for plot-wave-hovmoller on a small olr-wave-filter-shaped Zarr."""

from __future__ import annotations

import pytest
from conftest import load_skill, make_wave_dataset, run_skill, write_zarr
from weather_skills_core.provenance import load_figure_history


@pytest.fixture(scope="module")
def mod():
    return load_skill("plot-wave-hovmoller", "plot_wave_hovmoller")


@pytest.mark.parametrize("style", ["ncics", "amplitude", "contours", "probability"])
def test_styles_render(mod, tmp_path, style):
    inp = write_zarr(make_wave_dataset(), tmp_path / "waves.zarr")
    out = tmp_path / f"{style}.png"
    run_skill(mod.plot_wave_hovmoller, "-i", str(inp), "--style", style, "-o", str(out))
    assert out.stat().st_size > 0
    assert load_figure_history(out)[-1]["skill"] == "plot-wave-hovmoller"


def test_member_and_window(mod, tmp_path):
    inp = write_zarr(make_wave_dataset(), tmp_path / "waves.zarr")
    out = tmp_path / "m.png"
    run_skill(
        mod.plot_wave_hovmoller,
        "-i",
        str(inp),
        "--member",
        "2",
        "--weeks-before",
        "1",
        "--weeks-after",
        "1",
        "--highlight-lon",
        "none",
        "-o",
        str(out),
    )
    assert out.exists()


def test_missing_wave(mod, tmp_path):
    inp = write_zarr(make_wave_dataset().drop_vars("olr_er"), tmp_path / "waves.zarr")
    with pytest.raises(SystemExit) as exc:
        run_skill(
            mod.plot_wave_hovmoller, "-i", str(inp), "--waves", "er", "-o", str(tmp_path / "x.png")
        )
    assert exc.value.code == 2


def test_unknown_member(mod, tmp_path):
    inp = write_zarr(make_wave_dataset(), tmp_path / "waves.zarr")
    with pytest.raises(SystemExit) as exc:
        run_skill(
            mod.plot_wave_hovmoller, "-i", str(inp), "--member", "50", "-o", str(tmp_path / "x.png")
        )
    assert exc.value.code == 2
