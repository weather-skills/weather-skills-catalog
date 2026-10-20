"""End-to-end tests for plot --mediogram."""

import json

import pytest
from skill_conftest import load_skill, make_forecast, run_skill, write_zarr

mod = load_skill("plot", "plot")
POINT = {"layout": {"meta": {"geo": {"point": {"lat": 1.2, "lon": 10.9}}}}}


@pytest.fixture(scope="module")
def med_fn():
    return mod.plot


@pytest.fixture
def inputs(tmp_path):
    fc = write_zarr(make_forecast(n_step=8, members=5, fill=3.0), tmp_path / "fc.zarr")
    mc = write_zarr(make_forecast(n_step=8, members=9, fill=1.0), tmp_path / "mc.zarr")
    return str(fc), str(mc)


def test_spec_has_two_boxes_and_a_mean_line(inputs, med_fn, capsys):
    run_skill(
        med_fn,
        "--mediogram",
        "-i",
        inputs[0],
        "-i",
        inputs[1],
        "--spec",
        json.dumps(POINT),
        "--dump-spec",
        "-",
    )
    spec = json.loads(capsys.readouterr().out)
    assert [(t["uid"], t["type"]) for t in spec["data"]] == [
        ("forecast", "box"),
        ("mclimate", "box"),
        ("forecast-mean", "scatter"),
    ]
    # Every common step up to 15: both inputs have 8.
    assert spec["data"][0]["meta"]["source"]["isel"] == {"step": list(range(8))}
    assert spec["layout"]["title"]["text"].startswith("Mediogram: Precipitation at lat=1, lon=11")
    assert spec["layout"]["boxmode"] == "group"


def test_renders(inputs, med_fn, tmp_path):
    out = tmp_path / "m.png"
    run_skill(
        med_fn,
        "--mediogram",
        "-i",
        inputs[0],
        "-i",
        inputs[1],
        "--spec",
        json.dumps(POINT),
        "-o",
        str(out),
    )
    assert out.is_file()


def test_point_required(inputs, med_fn, capsys):
    with pytest.raises(SystemExit):
        run_skill(med_fn, "--mediogram", "-i", inputs[0], "-i", inputs[1], "-o", "x.png")
    assert "geo" in capsys.readouterr().err


def test_needs_number_and_step(tmp_path, med_fn, capsys):
    fc = write_zarr(make_forecast(n_step=3), tmp_path / "fc.zarr")
    with pytest.raises(SystemExit):
        run_skill(
            med_fn,
            "--mediogram",
            "-i",
            str(fc),
            "-i",
            str(fc),
            "--spec",
            json.dumps(POINT),
            "-o",
            "x.png",
        )
    assert "'number' and 'step'" in capsys.readouterr().err
