"""End-to-end tests for the plot skill: Jsonnet spec, @ references, outputs."""

import json
import os
import re
from pathlib import Path

import numpy as np
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

from weather_skills_plotting.bind import SCALED_KEYS

plot_mod = load_skill("plot", "plot")
RECIPES = Path(__file__).resolve().parents[1] / "recipes"


@pytest.fixture(scope="module")
def plot_fn():
    return plot_mod.plot


def _series(name, values, start="2026-01-01"):
    times = np.arange(np.datetime64(start), np.datetime64(start) + np.timedelta64(len(values), "D"))
    ds = xr.Dataset({name: ("time", np.asarray(values, float))}, coords={"time": times})
    ds[name].attrs["units"] = "mm day-1"
    return ds


def _geojson(path):
    ring = [[10, 1], [13, 1], [13, 3], [10, 3], [10, 1]]
    path.write_text(json.dumps({"type": "Polygon", "coordinates": [ring]}))
    return path


def _inputs(tmp_path):
    """A synthetic file for every NAME the recipes use."""
    rng = np.random.default_rng(0)
    grid = make_gridded(n_time=3)
    grid["precip"].values = rng.uniform(0, 40, grid["precip"].shape)
    fc = make_forecast(n_step=4, lats=(1.0, 2.0, 3.0), lons=(10.0, 11.0, 12.0))
    fc["precip"].values = rng.uniform(0, 30, fc["precip"].shape)
    ens = xr.Dataset(
        {"precip": (("number", "time", "latitude", "longitude"), rng.uniform(0, 20, (3, 5, 2, 2)))},
        coords={
            "number": [0, 1, 2],
            "time": np.arange(np.datetime64("2026-03-10"), np.datetime64("2026-03-15")),
            "latitude": [1.0, 2.0],
            "longitude": [10.0, 11.0],
        },
    )
    quant = xr.Dataset(
        {"precip": (("quantile", "time"), np.array([[1.0, 2, 3], [5, 6, 7]]))},
        coords={
            "quantile": [0.1, 0.9],
            "time": np.arange(np.datetime64("2026-01-01"), np.datetime64("2026-01-04")),
        },
    )
    wind = make_gridded(
        n_time=1, lats=(1.0, 2.0, 3.0, 4.0), lons=(10.0, 11.0, 12.0, 13.0), name="wind_speed"
    )
    wind["wind_speed"].values = rng.uniform(0, 12, wind["wind_speed"].shape)
    wind["wind_from_direction"] = wind["wind_speed"] * 0 + 270.0
    rh = xr.Dataset(
        {"r": (("step", "vertical", "latitude", "longitude"), rng.uniform(20, 90, (2, 3, 2, 4)))},
        coords={
            "step": np.array([0, 6], dtype="timedelta64[h]").astype("timedelta64[ns]"),
            "time": np.datetime64("2026-10-30T12:00:00", "ns"),
            "vertical": [1000.0, 850.0, 500.0],
            "latitude": [-1.0, 0.0],
            "longitude": [34.0, 35.0, 36.0, 37.0],
        },
    )
    rh["latitude"].attrs.update(standard_name="latitude", units="degrees_north")
    rh["longitude"].attrs.update(standard_name="longitude", units="degrees_east")
    cty = xr.Dataset(
        {"precip": ("county", [12.0, 30.0, 4.0])},
        coords={"county": ["Kisumu", "Turkana", "Nairobi"]},
    )
    idx = _series("precip", [100, 200, 150]).assign(nino34=("time", [-1.0, 0.8, 1.6]))
    stations = make_point_obs(n_time=3, n_points=3).rename(point_id="station_id")
    stations["precip"].values = rng.uniform(0, 40, stations["precip"].shape)
    week = make_gridded(n_time=1)
    week["precip"].values = rng.uniform(0, 120, week["precip"].shape)
    hits = make_gridded(n_time=1, name="event_hit")
    hits["event_hit"].values = rng.choice([-1.0, 0.0, 1.0], hits["event_hit"].shape)
    hits["event_hit"].attrs["units"] = "1"
    ef = make_forecast(n_step=3, members=5)
    ef["precip"].values = rng.uniform(0, 30, ef["precip"].shape)
    mc = ef.copy(data={"precip": rng.uniform(0, 20, ef["precip"].shape)})
    stn_wind = make_point_obs(n_time=6, n_points=1, name="wind_speed").rename(point_id="station_id")
    stn_wind["wind_speed"].values = rng.uniform(0, 10, stn_wind["wind_speed"].shape)
    stn_wind["wind_from_direction"] = stn_wind["wind_speed"] * 0 + 45.0
    stn_wind["wind_from_direction"].attrs["units"] = "degree"
    stn_wind["wind_speed"].attrs.update(units="m s-1", standard_name="wind_speed")
    msl = make_gridded(n_time=1, lats=(0.0, 5.0, 10.0), lons=(30.0, 35.0, 40.0), name="msl")
    msl["msl"].values = np.array([[[1004.0, 1008, 1012], [1010, 1016, 1020], [1006, 1012, 1018]]])
    msl["msl"].attrs.update(units="hPa", standard_name="air_pressure_at_mean_sea_level")

    def centre(value, lat, lon):
        # Two candidate centres; the second is not a centre in this map (NaN), as
        # local-extrema writes it.
        ds = xr.Dataset(
            {"msl": (("station_id", "time"), [[value], [np.nan]])},
            coords={
                "station_id": ["C1", "C2"],
                "time": msl.time.values,
                "latitude": ("station_id", [lat, 2.0]),
                "longitude": ("station_id", [lon, 33.0]),
            },
        )
        ds["msl"].attrs["units"] = "hPa"
        return ds

    for ds in (ens, quant, cty):
        ds[list(ds.data_vars)[0]].attrs["units"] = "mm day-1"
    idx["nino34"].attrs["units"] = "K"
    files = {
        "OBS": grid,
        "STN": stations,
        "FC": fc,
        "ENS": ens,
        "MEAN": ens.mean("number"),
        "Q": quant,
        "AREA": _series("precip", [2.0, 4.0, 1.0]),
        "FCA": ens.isel(latitude=0, longitude=0, drop=True),
        "C": _series("precip_avg", [4.0, 6.0, 5.0]).assign(precip_std=("time", [1.0, 2.0, 1.5])),
        "LOW": _series("precip_avg", [3.0, 4.0, 3.5]),
        "Y2024": _series("precip", [1, 5, 2, 8], start="2024-03-01"),
        "Y2025": _series("precip", [3, 0, 4, 6], start="2025-03-01"),
        "WD": wind,
        "RH": rh,
        "CTY": cty,
        "IDX": idx,
        "OW": week,
        "FC1": week.copy(data={"precip": rng.uniform(0, 120, week["precip"].shape)}),
        "FC2": week.copy(data={"precip": rng.uniform(0, 120, week["precip"].shape)}),
        "V1": hits,
        "V2": hits.copy(data={"event_hit": rng.choice([-1.0, 0.0, 1.0], hits["event_hit"].shape)}),
        "EF": ef,
        "MC": mc,
        "EFM": ef.mean("number", keep_attrs=True),
        "WS": stn_wind,
        "MSL": msl,
        "HI": centre(1020.0, 5.0, 40.0),
        "LO": centre(1004.0, 0.0, 30.0),
    }
    files["C"]["precip_std"].attrs["units"] = "mm day-1"
    paths = {name: write_zarr(ds, tmp_path / f"{name}.zarr") for name, ds in files.items()}
    paths["REGION"] = _geojson(tmp_path / "region.geojson")
    return paths, files


def _recipe_inputs(recipe: Path, paths: dict) -> list[str]:
    """``-i PATH=NAME`` for each NAME on the recipe's usage line."""
    usage = next(line for line in recipe.read_text().splitlines() if "plot.py" in line)
    names = re.findall(r"-i \S+=(\w+)", usage)
    assert names, f"{recipe.name}: usage line names no inputs"
    argv = []
    for name in names:
        argv += ["-i", f"{paths[name]}={name}"]
    for const in re.findall(r"--const (\S+)", usage):
        argv += ["--const", const]
    return argv


@pytest.mark.parametrize("recipe", sorted(RECIPES.glob("*.jsonnet")), ids=lambda p: p.stem)
def test_every_recipe_draws_only_stored_values(tmp_path, plot_fn, capsys, recipe):
    """Each recipe renders, and every data value it draws is a value stored in its input."""
    from weather_skills_plotting.bind import GeoLines, resolve
    from weather_skills_plotting.geo import load_geojson
    from weather_skills_plotting.render import SpecArg, evaluate, input_metadata

    paths, files = _inputs(tmp_path)
    argv = _recipe_inputs(recipe, paths)
    suffix = ".gif" if "frames:" in recipe.read_text() else ".png"
    out = tmp_path / f"{recipe.stem}{suffix}"
    run_skill(plot_fn, *argv, "--spec", str(recipe), "-o", str(out))
    assert out.exists() and "data: not null" in capsys.readouterr().out

    names = [
        a.split("=")[-1] for flag, a in zip(argv[::2], argv[1::2], strict=True) if flag == "-i"
    ]
    values = {n: load_geojson(paths[n]) if n == "REGION" else files[n] for n in names}
    meta = {n: input_metadata(v) for n, v in values.items()}
    sources = {n: GeoLines(v) if isinstance(v, list) else v for n, v in values.items()}
    consts = [c for flag, c in zip(argv[::2], argv[1::2], strict=True) if flag == "--const"]
    sources["const"] = {c.split("=")[0]: float(c.split("=")[1]) for c in consts}
    resolved, found = resolve(evaluate(SpecArg(str(recipe)), meta, sources["const"]), sources)
    for path, ref in found.items():
        name = ref[1:].split(".")[0]
        # Pixel sizes (marker.size, standoff) may scale stored values; they aren't data.
        if path.endswith(SCALED_KEYS):
            continue
        # Labels (.dt / .attrs), display limits and base-map lines are not stored data.
        if (
            name not in files
            or ".dt." in ref
            or ref.endswith(("min()", "max()"))
            or "quantile(" in ref.split(".")[-1]
        ):
            continue
        ds = files[name]
        stored = np.concatenate(
            [
                np.asarray(v.values, float).ravel()
                for v in ds.variables.values()
                if v.dtype.kind in "biuf"
            ]
        )
        value = _at(resolved, path)
        drawn = (
            np.asarray(value, dtype=float).ravel()
            if np.asarray(value).dtype.kind in "biuf"
            else None
        )
        if drawn is None:
            continue
        drawn = drawn[np.isfinite(drawn)]
        assert np.isin(drawn, stored).all(), (
            f"{recipe.name} {path} = {ref} draws values not in {name}"
        )


def _at(tree, path):
    for part in re.findall(r"[^.\[\]]+|\[\d+\]", path):
        tree = tree[int(part[1:-1])] if part.startswith("[") else tree[part]
    return tree


def test_png_with_provenance_and_qa(tmp_path, plot_fn, capsys):
    grid = write_zarr(make_gridded(n_time=2), tmp_path / "grid.zarr")
    out = tmp_path / "map.png"
    spec = "{data: [{type: 'heatmap', uid: 'R', x: '@G.lon', y: '@G.lat', z: '@G.precip.isel(time=0)'}]}"
    run_skill(plot_fn, "-i", f"{grid}=G", "--spec", spec, "-o", str(out))
    text = capsys.readouterr().out
    assert "plot hash:" in text and "data: not null (R 12/12 finite)" in text
    entry = load_figure_history(out)[-1]
    assert entry["skill"] == "plot"
    # Provenance keeps the evaluated figure, which replays as --spec by itself.
    assert entry["args"]["spec"]["data"][0]["z"] == "@G.precip.isel(time=0)"
    # Recorded under the flags' own names, with the path relative to the working
    # directory, so the command replays from there.
    assert entry["args"]["input"] == [f"{os.path.relpath(grid)}=G"]


def test_constants_are_recorded_as_passed(tmp_path, plot_fn):
    grid = write_zarr(make_gridded(n_time=1), tmp_path / "grid.zarr")
    out = tmp_path / "c.png"
    spec = "{data: [{type: 'heatmap', x: '@G.lon', y: '@G.lat', z: '@G.precip.isel(time=0)', zmax: '@const.top'}]}"
    run_skill(plot_fn, "-i", f"{grid}=G", "--const", "top=5", "--spec", spec, "-o", str(out))
    assert load_figure_history(out)[-1]["args"]["const"] == ["top=5"]


def test_a_station_with_no_position_is_left_out_of_the_bbox(tmp_path, plot_fn, capsys):
    stations = make_point_obs()
    lat = stations["latitude"].values.copy()
    lat[0] = np.nan
    stations = stations.assign_coords(latitude=(stations["latitude"].dims, lat))
    path = write_zarr(stations, tmp_path / "stn.zarr")
    run_skill(plot_fn, "-i", f"{path}=S", "--spec", "std.extVar('inputs').S.bbox", "--dump-spec")
    bbox = json.loads(capsys.readouterr().out)
    assert all(np.isfinite(v) for v in bbox.values() if isinstance(v, float))


def test_every_output_is_checked_before_any_is_written(tmp_path, plot_fn, capsys):
    grid = write_zarr(make_gridded(n_time=1), tmp_path / "grid.zarr")
    spec = "{data: [{type: 'heatmap', x: '@G.lon', y: '@G.lat', z: '@G.precip.isel(time=0)'}]}"
    png, txt = tmp_path / "a.png", tmp_path / "a.txt"
    with pytest.raises(SystemExit):
        run_skill(plot_fn, "-i", f"{grid}=G", "--spec", spec, "-o", str(png), "-o", str(txt))
    assert "must end in .png" in capsys.readouterr().err and not png.exists()


def test_gif_frame_traces_must_name_base_traces(tmp_path, plot_fn, capsys):
    grid = write_zarr(make_gridded(n_time=2), tmp_path / "grid.zarr")
    spec = (
        "{data: [{type: 'heatmap', x: '@G.lon', y: '@G.lat', z: '@G.precip.isel(time=0)'}],"
        " frames: [{name: 'a', traces: [3], data: [{type: 'heatmap', z: '@G.precip.isel(time=1)'}]}]}"
    )
    with pytest.raises(SystemExit):
        run_skill(plot_fn, "-i", f"{grid}=G", "--spec", spec, "-o", str(tmp_path / "a.gif"))
    assert "frames[0]: traces [3] must name one of data[0]" in capsys.readouterr().err


def test_html_with_an_empty_base_map_layer(tmp_path, plot_fn, capsys, monkeypatch):
    # A layer with no lines in the window (no coast in a landlocked box) is an empty array.
    from weather_skills_plotting import geo

    monkeypatch.setattr(geo, "load_ne_layer", lambda *args, **kwargs: [])
    spec = "local ws = import 'ws.libsonnet'; local b = {n: 4, w: 30, s: -1, e: 35}; {data: ws.basemap(b), layout: ws.map(b)}"
    out = tmp_path / "empty.html"
    run_skill(plot_fn, "--spec", spec, "-o", str(out))
    assert "Plotly.newPlot" in out.read_text() and "plot hash:" in capsys.readouterr().out


def test_html_pdf_svg_outputs(tmp_path, plot_fn, capsys):
    grid = write_zarr(make_gridded(n_time=1), tmp_path / "grid.zarr")
    spec = "{data: [{type: 'heatmap', x: '@G.lon', y: '@G.lat', z: '@G.precip.isel(time=0)'}]}"
    html, pdf, svg = tmp_path / "m.html", tmp_path / "m.pdf", tmp_path / "m.svg"
    run_skill(
        plot_fn, "-i", f"{grid}=G", "--spec", spec, "-o", str(html), "-o", str(pdf), "-o", str(svg)
    )
    assert "Plotly.newPlot" in html.read_text() and "weather_skills_history" in html.read_text()
    assert pdf.read_bytes().startswith(b"%PDF") and "<svg" in svg.read_text()
    hashes = [line for line in capsys.readouterr().out.splitlines() if "plot hash:" in line]
    # HTML, PDF and SVG are hashed from the figure JSON; PDF and SVG draw the same figure.
    assert len(hashes) == 3 and hashes[1] == hashes[2]


def test_dump_spec_prints_evaluated_jsonnet(tmp_path, plot_fn, capsys):
    grid = write_zarr(make_gridded(n_time=2), tmp_path / "grid.zarr")
    spec = (
        "local v = '@G.precip'; local t = std.extVar('inputs').G.coords.time; "
        "{data: [{type: 'heatmap', z: v + '.isel(time=1)'}], layout: {title: {text: t[1] + ' ' + std.extVar('const').who}}}"
    )
    run_skill(
        plot_fn, "-i", f"{grid}=G", "--spec", spec, "--const", "who=CHIRPS", "--dump-spec", "-"
    )
    dumped = json.loads(capsys.readouterr().out)
    assert dumped["data"][0]["z"] == "@G.precip.isel(time=1)"
    assert dumped["layout"]["title"]["text"] == "2026-01-02 CHIRPS"


def test_inputs_metadata(tmp_path, plot_fn, capsys):
    fc = write_zarr(make_forecast(n_step=2, members=3), tmp_path / "fc.zarr")
    spec = "local i = std.extVar('inputs').F; {layout: {meta: i}}"
    run_skill(plot_fn, "-i", f"{fc}=F", "--spec", spec, "--dump-spec", "-")
    meta = json.loads(capsys.readouterr().out)["layout"]["meta"]
    assert meta["dims"] == {"number": 3, "step": 2, "latitude": 2, "longitude": 2}
    assert meta["vars"]["precip"]["dims"] == ["number", "step", "latitude", "longitude"]
    assert meta["coords"]["time"] == "2026-01-01" and meta["coords"]["number"] == [0, 1, 2]
    assert meta["bbox"] == {"n": 2.0, "w": 10.0, "s": 1.0, "e": 11.0}


def test_a_spec_without_a_template_gets_the_house_one(tmp_path, plot_fn, capsys):
    run_skill(plot_fn, "--spec", "{data: [], layout: {}}", "--dump-spec", "-")
    assert "colorway" in json.loads(capsys.readouterr().out)["layout"]["template"]["layout"]
    run_skill(plot_fn, "--spec", "{data: [], layout: {template: {}}}", "--dump-spec", "-")
    assert json.loads(capsys.readouterr().out)["layout"]["template"] == {}


def test_gif_needs_frames(tmp_path, plot_fn, capsys):
    grid = write_zarr(make_gridded(n_time=1), tmp_path / "grid.zarr")
    with pytest.raises(SystemExit):
        run_skill(plot_fn, "-i", f"{grid}=G", "--spec", "{data: []}", "-o", str(tmp_path / "a.gif"))
    assert "a GIF plays the figure's frames" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("argv", "match"),
    [
        (["-i", "x.zarr"], "-i expects PATH=NAME"),
        (["-i", "x.zarr=IMERG Final"], "-i expects PATH=NAME"),
        (["-i", "x.zarr=NE"], "is built in"),
        (["-i", "x.zarr=GB"], "is built in"),
    ],
)
def test_bad_input_name(plot_fn, capsys, argv, match):
    with pytest.raises(SystemExit):
        run_skill(plot_fn, *argv, "--spec", "{}", "-o", "x.png")
    assert match in capsys.readouterr().err


@pytest.mark.parametrize(
    ("spec", "match"),
    [
        # Jsonnet syntax errors come back from the evaluator.
        ("{data: [", "--spec:"),
        # Plotly keys are checked by Plotly, naming the trace.
        ("{data: [{type: 'scatter', align: 'x'}]}", "a scatter has no property 'align'"),
        # A scatter's colour-scale keys go under marker.
        ("{data: [{type: 'scatter', showscale: false}]}", "On a scatter it goes under marker"),
        ("{data: [{type: 'heatmap', z: '@G.precip'}]}", "needs 2-D"),
        ("{data: [{type: 'heatmap', z: '@G.prcp.isel(time=0)'}]}", "data variables: precip"),
        ("{data: [{type: 'heatmap', z: '@X.precip'}]}", "'X' is not an input; inputs are G"),
        ("{layout: {title: {text: std.extVar('inputs').X.attrs.name}}}", "pass it with -i FILE=X"),
        (
            "{data: [{type: 'scatter', x: '@G.time', y: [1, 2, 3]}]}",
            "data[0].y: data key needs an @binding",
        ),
        ("{data: [{type: 'scatter', y: std.range(0, 5)}]}", "at most 5 values per key"),
        ("{data: [{type: 'box', y: '@G.precip', x0: 'Day 1'}]}", "generates a value"),
    ],
)
def test_spec_errors(tmp_path, plot_fn, capsys, spec, match):
    grid = write_zarr(make_gridded(n_time=2), tmp_path / "grid.zarr")
    with pytest.raises(SystemExit):
        run_skill(plot_fn, "-i", f"{grid}=G", "--spec", spec, "-o", str(tmp_path / "x.png"))
    assert match in capsys.readouterr().err


def test_an_unknown_key_alone_gets_no_binding_reminder(tmp_path, plot_fn, capsys):
    grid = write_zarr(make_gridded(n_time=2), tmp_path / "grid.zarr")
    spec = "{data: [{type: 'heatmap', z: '@G.precip', line: {}}]}"
    with pytest.raises(SystemExit):
        run_skill(plot_fn, "-i", f"{grid}=G", "--spec", spec, "-o", str(tmp_path / "x.png"))
    err = capsys.readouterr().err
    assert "a heatmap has no property 'line'" in err and "Data comes from the inputs" not in err


def test_literal_at_sign(tmp_path, plot_fn, capsys):
    spec = "{layout: {annotations: [{text: '@@rhiza', xref: 'paper', yref: 'paper', x: 0, y: 1}]}}"
    run_skill(plot_fn, "--spec", spec, "--dump-spec", "-")
    assert json.loads(capsys.readouterr().out)["layout"]["annotations"][0]["text"] == "@@rhiza"
    out = tmp_path / "t.html"
    run_skill(plot_fn, "--spec", spec, "-o", str(out))
    assert '"@rhiza"' in out.read_text()


def test_constants_bind_on_a_data_axis(tmp_path, plot_fn, capsys):
    grid = write_zarr(make_gridded(n_time=2), tmp_path / "grid.zarr")
    spec = (
        "{data: [{type: 'scatter', x: '@G.time', y: '@G.precip.isel(lat=0, lon=0)'}],"
        " layout: {shapes: [{type: 'line', x0: '@const.day', x1: '@const.day', yref: 'paper',"
        " y0: 0, y1: 1}], yaxis: {range: [0, '@const.top']}}}"
    )
    out = tmp_path / "c.html"
    run_skill(
        plot_fn,
        "-i",
        f"{grid}=G",
        "--const",
        "day=2026-01-02",
        "--const",
        "top=5",
        "--spec",
        spec,
        "-o",
        str(out),
    )
    assert '"x0":"2026-01-02"' in out.read_text().replace(" ", "")
    with pytest.raises(SystemExit):
        run_skill(plot_fn, "-i", f"{grid}=G", "--spec", spec, "-o", str(tmp_path / "d.html"))
    assert "no constant 'day'; pass it with --const day=VALUE" in capsys.readouterr().err


def test_shapes_and_annotations_take_typed_in_positions(tmp_path, plot_fn, capsys):
    grid = write_zarr(make_gridded(n_time=1), tmp_path / "grid.zarr")
    spec = (
        "{data: [{type: 'heatmap', x: '@G.lon', y: '@G.lat', z: '@G.precip.isel(time=0)'}],"
        " layout: {shapes: [{type: 'line', x0: 36, x1: 36, yref: 'paper', y0: 0, y1: 1}],"
        " annotations: [{x: 36, y: 1.02, yref: 'paper', text: 'Lake', showarrow: false}]}}"
    )
    out = tmp_path / "s.html"
    run_skill(plot_fn, "-i", f"{grid}=G", "--spec", spec, "-o", str(out))
    assert '"x0":36' in out.read_text().replace(" ", "")


def test_still_image_refuses_more_than_100_panels(tmp_path, plot_fn, capsys):
    spec = "{layout: {['xaxis%d' % i]: {} for i in std.range(2, 102)}}"
    with pytest.raises(SystemExit):
        run_skill(plot_fn, "--spec", spec, "-o", str(tmp_path / "x.png"))
    assert "too many for one image" in capsys.readouterr().err


def test_recipes_by_name_and_listing(tmp_path, plot_fn, capsys):
    run_skill(plot_fn, "--recipe")
    listing = capsys.readouterr().out
    assert "animation " in listing and "map " in listing
    run_skill(plot_fn, "--recipe", "map")
    assert "ws.basemap(bbox)" in capsys.readouterr().out
    grid = write_zarr(make_gridded(n_time=1), tmp_path / "grid.zarr")
    # A bare name, and a path whose ${CLAUDE_SKILL_DIR} was never expanded, both find the recipe.
    for spec in ("map", "${CLAUDE_SKILL_DIR}/recipes/map.jsonnet"):
        run_skill(plot_fn, "-i", f"{grid}=OBS", "--spec", spec, "--dump-spec", "-")
        assert "@REGION.x" in capsys.readouterr().out


def test_importstr_reads_a_recipe(plot_fn, capsys):
    run_skill(plot_fn, "--spec", "{text: importstr 'map.jsonnet'}", "--dump-spec", "-")
    assert "ws.basemap(bbox)" in json.loads(capsys.readouterr().out)["text"]


def test_gif_size_is_reported_and_large_ones_warn(tmp_path, plot_fn, capsys, monkeypatch):
    from weather_skills_plotting import export

    grid = write_zarr(make_gridded(n_time=2), tmp_path / "grid.zarr")
    spec = (
        "local f(k) = {type: 'heatmap', x: '@G.lon', y: '@G.lat', z: '@G.precip.isel(time=%d)' % k};"
        "{data: [f(0)], frames: [{name: 'a', data: [f(0)]}, {name: 'b', data: [f(1)]}]}"
    )
    monkeypatch.setattr(export, "LARGE_GIF_BYTES", 10)
    run_skill(plot_fn, "-i", f"{grid}=G", "--spec", spec, "-o", str(tmp_path / "a.gif"))
    err = capsys.readouterr().err
    assert "gif: 2 frames" in err and "show images only up to" in err
    assert "HTML will be large" not in err and "animation:" not in err


def test_dpi_sets_pixel_density(tmp_path, plot_fn):
    from PIL import Image

    grid = write_zarr(make_gridded(n_time=1), tmp_path / "grid.zarr")
    spec = "{data: [{type: 'heatmap', x: '@G.lon', y: '@G.lat', z: '@G.precip.isel(time=0)'}], layout: {width: 300, height: 300}}"
    run_skill(plot_fn, "-i", f"{grid}=G", "--spec", spec, "-o", str(tmp_path / "a.png"))
    run_skill(
        plot_fn, "-i", f"{grid}=G", "--spec", spec, "--dpi", "192", "-o", str(tmp_path / "b.png")
    )
    assert Image.open(tmp_path / "a.png").size == (300, 300)
    assert Image.open(tmp_path / "b.png").size == (600, 600)


def test_imports_from_the_working_folder(tmp_path, plot_fn, capsys, monkeypatch):
    (tmp_path / "mine.libsonnet").write_text("{title: 'From my file'}")
    monkeypatch.chdir(tmp_path)
    run_skill(
        plot_fn,
        "--spec",
        "{layout: {title: {text: (import 'mine.libsonnet').title}}}",
        "--dump-spec",
        "-",
    )
    assert json.loads(capsys.readouterr().out)["layout"]["title"]["text"] == "From my file"
