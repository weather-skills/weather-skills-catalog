"""End-to-end tests for plot --animate."""

import base64
import json
import re

import numpy as np
import pytest
from skill_conftest import load_skill, make_forecast, make_gridded, run_skill, write_zarr

mod = load_skill("plot", "plot")


@pytest.fixture(scope="module")
def animation_fn():
    return mod.plot


@pytest.fixture
def grid(tmp_path):
    # Temperature, not precipitation: the precipitation palette bins z into classes.
    ds = make_gridded(n_time=4, name="t2m")
    ds["t2m"].values = np.arange(4.0)[:, None, None] * np.ones(ds["t2m"].shape)
    ds["t2m"].attrs.update(units="degree_Celsius", standard_name="air_temperature")
    return str(write_zarr(ds, tmp_path / "grid.zarr"))


def figure_of(html_path):
    """The figure JSON plotly writes into ``Plotly.newPlot`` and ``addFrames``."""
    text = html_path.read_text()
    frames = re.search(r"Plotly\.addFrames\([^,]+,\s*(\[.*?\])\);", text, re.S)
    return text, json.loads(frames.group(1)) if frames else []


def first_z(z):
    """First cell of a trace's ``z``, whether a nested list or plotly's base64 typed array."""
    if isinstance(z, dict):
        return float(np.frombuffer(base64.b64decode(z["bdata"]), dtype=z["dtype"])[0])
    return z[0][0]


def test_dump_spec_is_one_heatmap(grid, animation_fn, capsys):
    run_skill(animation_fn, "--animate", "-i", grid, "--dump-spec", "-")
    spec = json.loads(capsys.readouterr().out)
    assert [(t["uid"], t["type"]) for t in spec["data"]] == [("0", "heatmap")]


def test_one_frame_per_time_with_controls(grid, animation_fn, tmp_path, capsys):
    out = tmp_path / "v.html"
    run_skill(animation_fn, "--animate", "-i", grid, "-o", str(out))
    assert "animation: 4 frames" in capsys.readouterr().err
    text, frames = figure_of(out)
    assert [f["name"] for f in frames] == ["0", "1", "2", "3"]
    # Each frame carries only the field, whose values change frame to frame.
    assert all(f["traces"] == [0] for f in frames)
    assert [first_z(f["data"][0]["z"]) for f in frames] == [0.0, 1.0, 2.0, 3.0]
    assert '"updatemenus"' in text and '"sliders"' in text
    # Arrays are stored as 32-bit floats to keep the HTML small.
    assert '"dtype":"f4"' in text and '"dtype":"f8"' not in text
    assert "\\u25b6 Play" in text


def test_frames_share_one_color_axis(grid, animation_fn, tmp_path):
    out = tmp_path / "v.html"
    run_skill(animation_fn, "--animate", "-i", grid, "-o", str(out))
    _, frames = figure_of(out)
    assert {f["data"][0].get("coloraxis") for f in frames} == {"coloraxis"}


def test_animation_speed_and_user_slider(grid, animation_fn, tmp_path):
    out = tmp_path / "v.html"
    spec = {
        "layout": {
            "meta": {"animation": {"duration": 120}},
            "sliders": [{"steps": [{"label": "mine", "method": "animate", "args": [["0"]]}]}],
        }
    }
    run_skill(animation_fn, "--animate", "-i", grid, "--spec", json.dumps(spec), "-o", str(out))
    text, _ = figure_of(out)
    assert '"duration":120' in text.replace(" ", "")
    assert '"label":"mine"' in text.replace(" ", "")


def test_sub_daily_titles_show_hour_and_lead(tmp_path, animation_fn):
    ds = make_forecast(n_step=3)
    ds = ds.assign_coords(
        step=np.array([0, 3, 6], dtype="timedelta64[h]").astype("timedelta64[ns]")
    )
    path = write_zarr(ds, tmp_path / "fc.zarr")
    out = tmp_path / "v.html"
    run_skill(animation_fn, "--animate", "-i", str(path), "-o", str(out))
    _, frames = figure_of(out)
    titles = [
        next(a["text"] for a in f["layout"]["annotations"] if a.get("name") == "panel-title-1")
        for f in frames
    ]
    assert titles == ["1 Jan '26 00:00 (+0h)", "1 Jan '26 03:00 (+3h)", "1 Jan '26 06:00 (+6h)"]


def test_output_must_be_html(grid, animation_fn, capsys):
    with pytest.raises(SystemExit):
        run_skill(animation_fn, "--animate", "-i", grid, "-o", "v.png")
    assert "must end in .html" in capsys.readouterr().err


def test_single_frame_refused(tmp_path, animation_fn, capsys):
    path = write_zarr(make_gridded(n_time=1), tmp_path / "one.zarr")
    with pytest.raises(SystemExit):
        run_skill(animation_fn, "--animate", "-i", str(path), "-o", str(tmp_path / "v.html"))
    assert "nothing to animate" in capsys.readouterr().err
