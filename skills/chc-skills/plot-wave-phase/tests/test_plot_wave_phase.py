"""Tests for plot-wave-phase on a small olr-wave-filter-shaped Zarr."""

from __future__ import annotations

import pytest
from conftest import load_skill, make_wave_dataset, run_skill, write_zarr
from weather_skills_core.provenance import load_figure_history


@pytest.fixture(scope="module")
def mod():
    return load_skill("plot-wave-phase", "plot_wave_phase")


@pytest.mark.parametrize("kind", ["rose", "rose-daily"])
def test_kinds_render(mod, tmp_path, kind):
    inp = write_zarr(make_wave_dataset(n_fc=21), tmp_path / "waves.zarr")
    out = tmp_path / f"{kind}.png"
    run_skill(
        mod.plot_wave_phase,
        "-i",
        str(inp),
        "--kind",
        kind,
        "--wave",
        "mjo",
        "--lon",
        "40",
        "--days",
        "5",
        "--label",
        "Kenya",
        "-o",
        str(out),
    )
    assert out.stat().st_size > 0
    assert load_figure_history(out)[-1]["skill"] == "plot-wave-phase"


def test_missing_wave(mod, tmp_path):
    inp = write_zarr(make_wave_dataset().drop_vars("olr_kelvin"), tmp_path / "waves.zarr")
    with pytest.raises(SystemExit) as exc:
        run_skill(
            mod.plot_wave_phase, "-i", str(inp), "--wave", "kelvin", "-o", str(tmp_path / "x.png")
        )
    assert exc.value.code == 2


def test_rose_needs_a_week(mod, tmp_path):
    inp = write_zarr(make_wave_dataset(n_fc=5), tmp_path / "waves.zarr")
    with pytest.raises(SystemExit) as exc:
        run_skill(
            mod.plot_wave_phase, "-i", str(inp), "--kind", "rose", "-o", str(tmp_path / "x.png")
        )
    assert exc.value.code == 2
