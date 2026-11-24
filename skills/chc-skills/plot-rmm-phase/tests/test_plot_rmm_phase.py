"""Tests for plot-rmm-phase on a small mjo-rmm-index-shaped Zarr."""

from __future__ import annotations

import pytest
from conftest import load_skill, make_rmm_dataset, run_skill, write_zarr
from weather_skills_core.provenance import load_figure_history


@pytest.fixture(scope="module")
def mod():
    return load_skill("plot-rmm-phase", "plot_rmm_phase")


def test_phase_diagram(mod, tmp_path):
    inp = write_zarr(make_rmm_dataset(), tmp_path / "rmm.zarr")
    out = tmp_path / "rmm.png"
    run_skill(mod.plot_rmm_phase, "-i", str(inp), "-o", str(out))
    assert out.stat().st_size > 0
    assert load_figure_history(out)[-1]["skill"] == "plot-rmm-phase"


def test_options(mod, tmp_path):
    inp = write_zarr(make_rmm_dataset(), tmp_path / "rmm.zarr")
    out = tmp_path / "rmm.png"
    run_skill(
        mod.plot_rmm_phase,
        "-i",
        str(inp),
        "--member-days",
        "2,7",
        "--obs-days",
        "5",
        "--no-bom",
        "--title",
        "MJO",
        "-o",
        str(out),
    )
    assert out.exists()


def test_member_day_out_of_range(mod, tmp_path):
    inp = write_zarr(make_rmm_dataset(n_fc=10), tmp_path / "rmm.zarr")
    with pytest.raises(SystemExit) as exc:
        run_skill(
            mod.plot_rmm_phase, "-i", str(inp), "--member-days", "20", "-o", str(tmp_path / "x.png")
        )
    assert exc.value.code == 2
