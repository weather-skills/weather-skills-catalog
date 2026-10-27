"""End-to-end tests for plot --animate."""

import base64
import json
import re

import numpy as np
import pytest
from skill_conftest import (
    load_skill,
    make_forecast,
    make_gridded,
    make_point_obs,
    run_skill,
    write_zarr,
)

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


def test_output_must_be_html_or_gif(grid, animation_fn, capsys):
    with pytest.raises(SystemExit):
        run_skill(animation_fn, "--animate", "-i", grid, "-o", "v.png")
    assert "must end in .html or .gif" in capsys.readouterr().err


def test_gif_one_frame_per_time(grid, animation_fn, tmp_path, capsys):
    from PIL import Image
    from weather_skills_core.provenance import load_figure_history

    out = tmp_path / "v.gif"
    spec = {"layout": {"meta": {"animation": {"duration": 120}}}}
    run_skill(animation_fn, "--animate", "-i", grid, "--spec", json.dumps(spec), "-o", str(out))
    assert "plot hash:" in capsys.readouterr().out
    with Image.open(out) as img:
        assert img.n_frames == 4
        assert img.info["duration"] == 120
        assert img.info["loop"] == 0
        firsts = []
        for i in range(img.n_frames):
            img.seek(i)
            firsts.append(img.convert("RGB").tobytes())
    # The field changes every frame.
    assert len(set(firsts)) == 4
    assert load_figure_history(out)


def test_gif_of_constant_field(tmp_path, animation_fn):
    from PIL import Image

    ds = make_gridded(n_time=4, name="t2m")
    ds["t2m"].values = np.ones(ds["t2m"].shape)
    ds["t2m"].attrs.update(units="degree_Celsius", standard_name="air_temperature")
    out = tmp_path / "v.gif"
    run_skill(
        animation_fn, "--animate", "-i", str(write_zarr(ds, tmp_path / "c.zarr")), "-o", str(out)
    )
    # Only the titles change, so the frames carry no trace data.
    with Image.open(out) as img:
        assert img.n_frames == 4


def test_gif_needs_animate(grid, animation_fn, capsys):
    with pytest.raises(SystemExit):
        run_skill(animation_fn, "-i", grid, "-o", "v.gif")
    assert "add --animate" in capsys.readouterr().err


def test_single_frame_refused(tmp_path, animation_fn, capsys):
    path = write_zarr(make_gridded(n_time=1), tmp_path / "one.zarr")
    with pytest.raises(SystemExit):
        run_skill(animation_fn, "--animate", "-i", str(path), "-o", str(tmp_path / "v.html"))
    assert "nothing to animate" in capsys.readouterr().err


def base_of(text):
    """The ``data`` and ``layout`` plotly writes into ``Plotly.newPlot``."""
    decoder = json.JSONDecoder()
    start = text.index("[", text.index("Plotly.newPlot("))
    data, end = decoder.raw_decode(text, start)
    layout, _ = decoder.raw_decode(text, text.index("{", end))
    return data, layout


@pytest.fixture
def edge(tmp_path):
    path = tmp_path / "edge.geojson"
    path.write_text(
        json.dumps(
            {"type": "Polygon", "coordinates": [[[10, 1], [13, 1], [13, 3], [10, 3], [10, 1]]]}
        )
    )
    return str(path)


def test_outline_layer_on_every_frame(grid, edge, animation_fn, tmp_path, capsys):
    run_skill(
        animation_fn, "--animate", "-i", grid, "--layer", f"outline:{edge}", "--dump-spec", "-"
    )
    spec = json.loads(capsys.readouterr().out)
    assert [t["uid"] for t in spec["data"]] == ["0", "outline1"]
    out = tmp_path / "v.html"
    run_skill(animation_fn, "--animate", "-i", grid, "--layer", f"outline:{edge}", "-o", str(out))
    text, frames = figure_of(out)
    data, _ = base_of(text)
    # The outline is a base trace no frame replaces, so it stays drawn on every frame.
    outline = data.index(next(t for t in data if t["uid"] == "outline1-p1"))
    assert all(outline not in f["traces"] for f in frames)


def test_mask_layer(grid, edge, animation_fn, capsys):
    run_skill(animation_fn, "--animate", "-i", grid, "--layer", f"mask:{edge}", "--dump-spec", "-")
    spec = json.loads(capsys.readouterr().out)
    assert spec["layout"]["meta"]["geo"]["mask_geojson"] == edge


def test_layers_animate_like_a_still_map(grid, edge, animation_fn, tmp_path):
    pts = make_point_obs(n_time=4)
    pts["precip"].values = np.arange(4.0)[None, :] * np.ones(pts["precip"].shape)
    pts = str(write_zarr(pts, tmp_path / "pts.zarr"))
    out = tmp_path / "v.html"
    layers = [
        "--layer",
        f"heatmap:{grid}",
        "--layer",
        f"contour:{grid}",
        "--layer",
        f"scatter:{pts}",
    ]
    run_skill(animation_fn, "--animate", *layers, "--layer", f"outline:{edge}", "-o", str(out))
    text, frames = figure_of(out)
    data, _ = base_of(text)
    uids = [t["uid"] for t in data]
    # Heatmap, contour and stations change frame to frame; the outline stays put.
    moving = sorted(uids.index(f"{u}-p1") for u in ("0", "1", "2"))
    assert len(frames) == 4 and all(f["traces"] == moving for f in frames)
    assert uids.index("3-p1") not in frames[0]["traces"]


def test_dumped_animation_replays_as_animation(grid, animation_fn, tmp_path, capsys):
    run_skill(animation_fn, "--animate", "-i", grid, "--dump-spec", "-")
    spec = capsys.readouterr().out
    assert json.loads(spec)["layout"]["meta"]["skill"] == "plot --animate"
    out = tmp_path / "v.html"
    run_skill(animation_fn, "--spec", spec, "-o", str(out))
    assert "animation: 4 frames" in capsys.readouterr().err


def test_several_inputs_refused(grid, animation_fn, capsys):
    with pytest.raises(SystemExit):
        run_skill(animation_fn, "--animate", "-i", grid, "-i", grid, "-o", "v.html")
    assert "stack the inputs with --layer" in capsys.readouterr().err


def test_slider_starts_right_of_play_pause(grid, animation_fn, tmp_path):
    out = tmp_path / "v.html"
    spec = {"layout": {"title": {"text": "Rain", "subtitle": {"text": "East Africa"}}}}
    run_skill(animation_fn, "--animate", "-i", grid, "--spec", json.dumps(spec), "-o", str(out))
    _, layout = base_of(out.read_text())
    (menu,), (slider,) = layout["updatemenus"], layout["sliders"]
    # Both sit on one row under the map; the slider's left pad clears the two buttons.
    assert menu["x"] == slider["x"] == 0 and menu["y"] == slider["y"] == 0
    assert slider["len"] == 1 and slider["pad"]["l"] >= 150


def test_filled_contour_frames_share_levels(grid, animation_fn, tmp_path):
    # Plotly's automatic levels follow each frame's values; the colorbar must not.
    out = tmp_path / "v.html"
    spec = {"data": [{"uid": "0", "type": "contour"}]}
    run_skill(animation_fn, "--animate", "-i", grid, "-o", str(out), "--spec", json.dumps(spec))
    _, frames = figure_of(out)
    levels = {json.dumps(f["data"][0]["contours"], sort_keys=True) for f in frames}
    assert len(levels) == 1
    contours = json.loads(levels.pop())
    assert contours["start"] <= 0 and contours["end"] >= 3
    assert all(f["data"][0]["autocontour"] is False for f in frames)


def test_changing_scatter_is_scattergl_in_html(tmp_path, animation_fn):
    # Plotly.animate blanks the heatmap while it redraws a changing SVG scatter.
    ds = make_gridded(n_time=3, name="u10")
    ds["v10"] = ds["u10"] * 0 + 1.0
    ds["u10"].values = np.arange(3.0)[:, None, None] * np.ones(ds["u10"].shape) + 1
    for v in ("u10", "v10"):
        ds[v].attrs.update(units="m s-1")
    wind = str(write_zarr(ds, tmp_path / "wind.zarr"))
    out = tmp_path / "v.html"
    run_skill(animation_fn, "--animate", "--layer", f"quiver:{wind}", "-o", str(out))
    text, frames = figure_of(out)
    data, _ = base_of(text)
    moving = frames[0]["traces"]
    assert any("arrows" in data[i]["uid"] for i in moving)
    for i in moving:
        if "arrows" in data[i]["uid"]:
            assert data[i]["type"] == "scattergl"
            assert {f["data"][f["traces"].index(i)]["type"] for f in frames} == {"scattergl"}
