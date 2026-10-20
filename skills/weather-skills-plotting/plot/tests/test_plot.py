"""End-to-end tests for the plot skill."""

import json

import pytest
import xarray as xr
from skill_conftest import (
    load_skill,
    make_forecast,
    make_gridded,
    make_point_obs,
    run_skill,
    write_zarr,
)
from weather_skills_core.provenance import load_figure_history

plot_mod = load_skill("plot", "plot")


@pytest.fixture(scope="module")
def plot_fn():
    return plot_mod.plot


def _dump(plot_fn, capsys, *argv):
    run_skill(plot_fn, *argv, "--dump-spec", "-")
    return json.loads(capsys.readouterr().out)


def test_dump_spec_defaults_by_data_shape(tmp_path, plot_fn, capsys):
    grid = write_zarr(make_gridded(), tmp_path / "grid.zarr")
    pts = write_zarr(make_point_obs(), tmp_path / "pts.zarr")
    spec = _dump(plot_fn, capsys, "-i", str(grid), "-i", str(pts))
    a, b = spec["data"]
    assert a == {"uid": "0", "type": "heatmap", "meta": {"source": {"input": "0"}}}
    assert b["meta"]["bind"] == "points" and b["xaxis"] == "x2"
    meta = spec["layout"]["meta"]
    assert meta["version"] == 3 and meta["skill"] == "plot"
    assert meta["inputs"] == {"0": str(grid), "1": str(pts)}


def test_inputs_named_on_the_command_line(tmp_path, plot_fn, capsys):
    grid = write_zarr(make_gridded(), tmp_path / "grid.zarr")
    pts = write_zarr(make_point_obs(), tmp_path / "pts.zarr")
    spec = _dump(plot_fn, capsys, "-i", f"{grid}=OBS", "-i", str(pts))
    assert [t["uid"] for t in spec["data"]] == ["OBS", "1"]
    assert spec["data"][0]["meta"] == {"source": {"input": "OBS"}}
    assert spec["layout"]["meta"]["inputs"] == {"OBS": str(grid), "1": str(pts)}

    spec = _dump(plot_fn, capsys, "--layer", f"heatmap:{grid}=RAIN", "--layer", f"scatter:{pts}")
    assert [t["uid"] for t in spec["data"]] == ["RAIN", "1"]


@pytest.mark.parametrize(
    ("argv", "match"),
    [
        (["-i", "x.zarr=bad-name"], "PATH=NAME"),
        (["-i", "=OBS"], "PATH=NAME"),
        (["--layer", "heatmap:x.zarr=a.b"], "PATH=NAME"),
    ],
)
def test_bad_input_name(plot_fn, capsys, argv, match):
    with pytest.raises(SystemExit):
        run_skill(plot_fn, *argv, "-o", "x.png")
    assert match in capsys.readouterr().err


def test_duplicate_input_names(tmp_path, plot_fn, capsys):
    grid = write_zarr(make_gridded(), tmp_path / "grid.zarr")
    with pytest.raises(SystemExit):
        run_skill(plot_fn, "-i", f"{grid}=1", "-i", str(grid), "-o", str(tmp_path / "x.png"))
    assert "two inputs have the id '1'" in capsys.readouterr().err


def test_dump_merges_user_spec(tmp_path, plot_fn, capsys):
    grid = write_zarr(make_gridded(), tmp_path / "grid.zarr")
    user = {"data": [{"uid": "0", "type": "contour"}], "layout": {"title": {"text": "Hi"}}}
    spec = _dump(plot_fn, capsys, "-i", str(grid), "--spec", json.dumps(user))
    assert spec["data"][0]["type"] == "contour"
    assert spec["data"][0]["meta"] == {"source": {"input": "0"}}
    assert spec["layout"]["title"] == {"text": "Hi"}


def test_dumped_spec_replays_without_file_flags(tmp_path, plot_fn, capsys):
    grid = write_zarr(make_gridded(n_time=1), tmp_path / "grid.zarr")
    spec = _dump(plot_fn, capsys, "-i", str(grid))
    out = tmp_path / "replay.png"
    run_skill(plot_fn, "--spec", json.dumps(spec), "-o", str(out))
    assert out.is_file()


def test_png_render_with_provenance_and_qa(tmp_path, plot_fn, capsys):
    grid = write_zarr(make_gridded(n_time=2), tmp_path / "grid.zarr")
    out = tmp_path / "map.png"
    run_skill(
        plot_fn,
        "-i",
        str(grid),
        "-o",
        str(out),
        "--spec",
        '{"layout": {"title": {"text": "Precip"}}}',
    )
    text = capsys.readouterr().out
    assert "plot hash:" in text and "data: not null (0 24/24 finite)" in text
    history = load_figure_history(out)
    assert history and history[-1]["skill"] == "plot"


def test_html_output(tmp_path, plot_fn):
    grid = write_zarr(make_gridded(n_time=1), tmp_path / "grid.zarr")
    out = tmp_path / "map.html"
    run_skill(plot_fn, "-i", str(grid), "-o", str(out))
    html = out.read_text()
    assert "weather_skills_history" in html
    assert "Plotly.newPlot" in html


def test_series_of_a_forecast(tmp_path, plot_fn):
    # An area mean, as summarize-dim --dim latitude --dim longitude writes it.
    fc = write_zarr(
        make_forecast(members=3).mean(["latitude", "longitude"], keep_attrs=True),
        tmp_path / "fc.zarr",
    )
    out = tmp_path / "ts.png"
    run_skill(
        plot_fn,
        "-i",
        str(fc),
        "-o",
        str(out),
        "--spec",
        json.dumps(
            {
                "data": [
                    {
                        "uid": "0",
                        "type": "scatter",
                        "meta": {
                            "bind": "series",
                            "along": "number",
                            "band": [10, 90],
                        },
                    }
                ]
            }
        ),
    )
    assert out.is_file()


def test_scatter_of_two_variables(tmp_path, plot_fn, capsys):
    ds = xr.Dataset(
        {"dmi": (("year",), [1.9, -0.2, -0.3]), "precip": (("year",), [210.0, 60.0, 45.0])},
        coords={"year": [2019, 2020, 2021]},
    )
    ds["dmi"].attrs["units"] = "degree_Celsius"
    ds["precip"].attrs["units"] = "mm"
    path = write_zarr(ds, tmp_path / "iod_rain.zarr")
    user = {"data": [{"uid": "0", "meta": {"x": "dmi", "y": "precip"}}]}
    spec = _dump(plot_fn, capsys, "-i", str(path), "--spec", json.dumps(user))
    assert spec["data"][0]["meta"] == {"source": {"input": "0"}, "x": "dmi", "y": "precip"}
    run_skill(plot_fn, "-i", str(path), "-o", str(tmp_path / "xy.png"), "--spec", json.dumps(user))
    assert "data: not null (0 3/3 finite)" in capsys.readouterr().out


def test_layers(tmp_path, plot_fn, capsys):
    grid = write_zarr(make_gridded(n_time=1), tmp_path / "grid.zarr")
    pts = write_zarr(make_point_obs(n_time=1), tmp_path / "pts.zarr")
    edge = tmp_path / "edge.geojson"
    edge.write_text(
        json.dumps({"type": "Polygon", "coordinates": [[[9, 0], [14, 0], [14, 4], [9, 4], [9, 0]]]})
    )
    argv = [
        "--layer",
        f"heatmap:{grid}",
        "--layer",
        f"scatter:{pts}",
        "--layer",
        f"outline:{edge}",
        "--layer",
        f"mask:{edge}",
    ]
    spec = _dump(plot_fn, capsys, *argv)
    assert [t["uid"] for t in spec["data"]] == ["0", "1", "2"]
    assert spec["layout"]["meta"]["geo"]["mask_geojson"] == str(edge)
    run_skill(plot_fn, *argv, "-o", str(tmp_path / "layers.png"))


@pytest.mark.parametrize(
    "argv, match",
    [
        (["--layer", "heatmap"], "KIND:PATH"),
        (["--layer", "pie:x.zarr"], "KIND:PATH"),
    ],
)
def test_bad_layer_flag(plot_fn, capsys, argv, match):
    with pytest.raises(SystemExit):
        run_skill(plot_fn, *argv, "-o", "x.png")
    assert match in capsys.readouterr().err


def test_errors_are_actionable(tmp_path, plot_fn, capsys):
    grid = write_zarr(make_gridded(n_time=1), tmp_path / "grid.zarr")
    bad = [
        ('{"inputs": [{"variable": "tp"}]}', "are not allowed"),
        ('{"data": [{"uid": "0", "colorscal": "Blues"}]}', "Did you mean"),
        ('{"layout": {"meta": {"geo": {"region": "Kenya"}}}}', "resolve-region"),
    ]
    for spec, match in bad:
        with pytest.raises(SystemExit):
            run_skill(plot_fn, "-i", str(grid), "-o", str(tmp_path / "x.png"), "--spec", spec)
        assert match in capsys.readouterr().err


def test_help_lists_meta_reference(plot_fn, capsys):
    with pytest.raises(SystemExit):
        run_skill(plot_fn, "--help")
    out = capsys.readouterr().out
    for needle in ("BINDS", "data[].meta.source", "layout.meta", "RECIPES", "plotly.com"):
        assert needle in out


def test_mask_removing_every_station(tmp_path, plot_fn, capsys):
    pts = write_zarr(make_point_obs(n_time=1), tmp_path / "pts.zarr")
    far = tmp_path / "far.geojson"
    far.write_text(
        json.dumps({"type": "Polygon", "coordinates": [[[50, 50], [51, 50], [51, 51], [50, 50]]]})
    )
    with pytest.raises(SystemExit):
        run_skill(
            plot_fn,
            "--layer",
            f"scatter:{pts}",
            "--layer",
            f"mask:{far}",
            "-o",
            str(tmp_path / "x.png"),
        )
    assert "no stations remain" in capsys.readouterr().err


def test_provenance_records_spec_and_layers(tmp_path, plot_fn):
    grid = write_zarr(make_gridded(n_time=1), tmp_path / "grid.zarr")
    out = tmp_path / "p.png"
    spec = {"layout": {"title": {"text": "T"}}}
    run_skill(plot_fn, "--layer", f"heatmap:{grid}", "-o", str(out), "--spec", json.dumps(spec))
    args = load_figure_history(out)[-1]["args"]
    assert args["spec"] == spec
    assert args["layer"] == [f"heatmap:{grid}"]


# ------------------------------------------------------------ several series


def _area(ds):
    """An area mean, as summarize-dim --dim latitude --dim longitude writes it."""
    return ds.mean(["latitude", "longitude"], keep_attrs=True)


def test_series_share_one_axis_named_by_file(tmp_path, plot_fn, capsys):
    a = write_zarr(_area(make_gridded(n_time=3)), tmp_path / "chirps.zarr")
    b = write_zarr(_area(make_gridded(n_time=3, fill=2.0)), tmp_path / "imerg.zarr")
    spec = _dump(plot_fn, capsys, "-i", str(a), "-i", str(b))
    assert [(t["uid"], t["name"]) for t in spec["data"]] == [("0", "chirps"), ("1", "imerg")]
    assert not any("xaxis" in t or "yaxis" in t for t in spec["data"])

    from weather_skills_plotting import compile

    fig = compile(spec, {"0": xr.open_zarr(a), "1": xr.open_zarr(b)})
    assert {(t.xaxis, t.yaxis) for t in fig.data} == {("x", "y")}


def test_first_series_settings_apply_to_the_rest(tmp_path, plot_fn, capsys):
    a = write_zarr(_area(make_gridded(n_time=3)), tmp_path / "a.zarr")
    b = write_zarr(_area(make_gridded(n_time=3)), tmp_path / "b.zarr")
    user = {
        "data": [
            {"uid": "0", "meta": {"source": {"variable": "precip"}}},
            {"uid": "1", "type": "bar"},
        ]
    }
    spec = _dump(plot_fn, capsys, "-i", str(a), "-i", str(b), "--spec", json.dumps(user))
    assert spec["data"][1]["meta"]["source"]["variable"] == "precip"
    assert spec["data"][1]["type"] == "bar"
    out = tmp_path / "ts.png"
    run_skill(plot_fn, "-i", str(a), "-i", str(b), "-o", str(out), "--spec", json.dumps(user))
    assert out.is_file()


def test_along_passes_only_to_series_with_that_dim(tmp_path, plot_fn, capsys):
    fc = write_zarr(_area(make_forecast(members=4)), tmp_path / "fc.zarr")
    clim = write_zarr(_area(make_forecast(members=4)).isel(number=0), tmp_path / "clim.zarr")
    user = {"data": [{"uid": "0", "meta": {"along": "number", "band": [10, 90]}}]}
    spec = _dump(plot_fn, capsys, "-i", str(fc), "-i", str(clim), "--spec", json.dumps(user))
    assert "along" not in spec["data"][1]["meta"] and "band" not in spec["data"][1]["meta"]
    out = tmp_path / "ens.html"
    run_skill(plot_fn, "-i", str(fc), "-i", str(clim), "-o", str(out), "--spec", json.dumps(user))
    assert "Plotly.newPlot" in out.read_text()


def test_station_series_named_by_station_id(tmp_path, plot_fn, capsys):
    one = make_point_obs(n_points=2).isel(point_id=1)
    path = write_zarr(one, tmp_path / "tahmo.zarr")
    spec = _dump(plot_fn, capsys, "-i", str(path))
    assert spec["data"][0]["name"] == "S1"


def test_mixed_units_warns_on_one_axis_only(tmp_path, plot_fn, capsys):
    a = _area(make_gridded(n_time=2))
    b = _area(make_gridded(n_time=2))
    b["precip"].attrs["units"] = "mm"
    pa, pb = write_zarr(a, tmp_path / "a.zarr"), write_zarr(b, tmp_path / "b.zarr")
    run_skill(plot_fn, "-i", str(pa), "-i", str(pb), "--dump-spec", "-")
    assert "different units" in capsys.readouterr().err

    # Separate panels from layout.grid are separate y-axes: no warning.
    grid = {"layout": {"grid": {"rows": 2, "columns": 1, "pattern": "coupled"}}}
    out = tmp_path / "panels.png"
    run_skill(plot_fn, "-i", str(pa), "-i", str(pb), "-o", str(out), "--spec", json.dumps(grid))
    assert "different units" not in capsys.readouterr().err
    assert out.is_file()


def test_series_with_no_common_dates_warn(tmp_path, plot_fn, capsys):
    import numpy as np

    a = _area(make_gridded(n_time=2))
    b = a.assign_coords(time=np.array(["2027-01-01", "2027-01-02"], dtype="datetime64[ns]"))
    pa, pb = write_zarr(a, tmp_path / "a.zarr"), write_zarr(b, tmp_path / "b.zarr")
    run_skill(plot_fn, "-i", str(pa), "-i", str(pb), "--dump-spec", "-")
    assert "share no dates" in capsys.readouterr().err


def test_gridded_input_as_a_series_names_the_fix(tmp_path, plot_fn, capsys):
    a = write_zarr(make_gridded(n_time=2), tmp_path / "a.zarr")
    user = {"data": [{"uid": "0", "type": "scatter", "meta": {"bind": "series"}}]}
    with pytest.raises(SystemExit):
        run_skill(plot_fn, "-i", str(a), "-o", str(tmp_path / "x.png"), "--spec", json.dumps(user))
    assert "summarize-dim" in capsys.readouterr().err


def test_too_many_series(tmp_path, plot_fn, capsys):
    path = write_zarr(_area(make_gridded(n_time=2)), tmp_path / "a.zarr")
    argv = [arg for i in range(27) for arg in ("-i", f"{path}=s{i}")]
    with pytest.raises(SystemExit):
        run_skill(plot_fn, *argv, "--dump-spec", "-")
    assert "at most 26" in capsys.readouterr().err


def test_aggregated_precip_rate_is_drawn_as_a_rate(tmp_path, plot_fn, capsys):
    """Plot draws the file's values: a weekly mean rate stays mm/day, with a hint."""
    import numpy as np

    daily = _area(make_gridded(n_time=14))
    weekly = _area(make_gridded(n_time=2, fill=3.0)).assign_coords(
        time=np.array(["2026-01-01", "2026-01-08"], dtype="datetime64[ns]")
    )
    weekly["precip"].attrs["aggregation_period"] = "7 day"
    a = write_zarr(daily, tmp_path / "daily.zarr")
    b = write_zarr(weekly, tmp_path / "weekly.zarr")
    run_skill(plot_fn, *("-i", str(a), "-i", str(b), "-o", str(tmp_path / "ts.html")))
    err = capsys.readouterr().err
    assert "run convert-to-totals" in err
    assert "different units" not in err


def test_one_station_over_time_defaults_to_a_series(tmp_path, plot_fn, capsys):
    stn = make_point_obs().isel(point_id=[0])
    path = write_zarr(stn, tmp_path / "stn.zarr")
    spec = _dump(plot_fn, capsys, "-i", f"{path}=STN")
    (trace,) = spec["data"]
    assert trace["type"] == "scatter" and "bind" not in trace["meta"]
    assert trace["meta"]["source"]["isel"] == {"point_id": 0}
    assert trace["name"] == "STN"


def test_one_mode_at_a_time(tmp_path, plot_fn, capsys):
    grid = write_zarr(make_gridded(), tmp_path / "grid.zarr")
    with pytest.raises(SystemExit):
        run_skill(plot_fn, "--animate", "--mediogram", "-i", str(grid), "-o", "x.html")
    assert "pick one of" in capsys.readouterr().err


def test_dumped_animation_replays_as_an_animation(tmp_path, plot_fn, capsys):
    grid = write_zarr(make_gridded(n_time=3), tmp_path / "grid.zarr")
    dumped = tmp_path / "spec.json"
    run_skill(plot_fn, "--animate", "-i", str(grid), "-o", "x.html", "--dump-spec", str(dumped))
    capsys.readouterr()
    run_skill(plot_fn, "--spec", str(dumped), "-o", str(tmp_path / "v.html"))
    assert "animation: 3 frames" in capsys.readouterr().err
