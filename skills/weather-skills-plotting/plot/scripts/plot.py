# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "weather-skills-core>=0.2.0",
#   "cf-xarray>=0.11",
#   "cftime>=1.6",
#   "numpy>=2.5",
#   "xarray>=2026.7",
#   "zarr>=3.2",
#   "pint-xarray>=0.6",
#   "cartopy",
#   "shapely>=2.1.2",
#   "plotly>=7.1",
#   "kaleido>=1.5",
# ]
# ///
# The weather_skills_plotting library sits next to this script (scripts/weather_skills_plotting),
# so the skill folder is self-contained: Python finds it on the script's own path.
"""Draw weather-skills Zarrs as maps, time series, scatter plots, wind roses, verification grids, mediograms, or animations."""

import argparse
from pathlib import Path

from weather_skills_core import Dataset, UsageError, weather_skill
from weather_skills_core.cf import cf_dim

from weather_skills_plotting import parse_spec_arg
from weather_skills_plotting.cli import (
    input_ids,
    parse_input,
    run,
    skeleton,
    spec_datasets,
    split_name,
)
from weather_skills_plotting.geodata import point_dim
from weather_skills_plotting.mediogram import build_mediogram_spec
from weather_skills_plotting.reference import install_spec_help
from weather_skills_plotting.series import (
    MAX_INPUTS,
    trace_label,
    warn_disjoint_dates,
    warn_mixed_units,
)
from weather_skills_plotting.spec import DUMP_SPEC_HELP, SPEC_ARGUMENT_HELP, merge_spec
from weather_skills_plotting.verify import build_verify_spec

# Auto-populated by the version-bump CI workflow. Do not edit manually.
_SKILL_VERSION = "1.0.3"

LAYER_KINDS = ("heatmap", "contour", "scatter", "quiver", "outline", "mask")
_ZARR_LAYERS = frozenset({"heatmap", "contour", "scatter", "quiver"})


class LayerArg:
    """``--layer KIND:PATH[=NAME]``. The decorator opens Zarr layers (``.ds``)."""

    def __init__(self, kind: str, path: str, name: str | None):
        self.kind, self.path, self.name, self.ds = kind, path, name, None

    def zarr_paths(self):
        return [Path(self.path)] if self.kind in _ZARR_LAYERS else []

    def __str__(self):
        # Provenance records str(); keep the flag's own form.
        return f"{self.kind}:{self.path}" + (f"={self.name}" if self.name else "")


def parse_layer(value):
    kind, sep, path = str(value).partition(":")
    kind = kind.strip().lower()
    if not sep or not path.strip() or kind not in LAYER_KINDS:
        raise argparse.ArgumentTypeError(
            f"--layer expects KIND:PATH[=NAME] with KIND one of {', '.join(LAYER_KINDS)}; "
            f"got {value!r}"
        )
    return LayerArg(kind, *split_name(path.strip(), "--layer"))


def _default_trace(uid: str, ds, user=None, *, xaxis=None) -> dict:
    """Heatmap for a lat/lon grid, station markers for points, else a line. A ``user`` trace
    naming ``meta.x`` / ``meta.y`` is an xy plot, so its stations are not a map."""
    source = {"input": uid}
    xy = bool({"x", "y"} & set((user or {}).get("meta") or {}))
    lat, lon = cf_dim(ds, "latitude"), cf_dim(ds, "longitude")
    pdim = point_dim(ds)
    tdim = next((d for d in ("time", "step") if d in ds.dims and ds.sizes[d] > 1), None)
    if pdim is not None and ds.sizes[pdim] == 1 and tdim:
        # One station over time is a time series, not one dot on a map.
        trace = {"type": "scatter", "meta": {"source": {**source, "isel": {pdim: 0}}}}
    elif pdim is not None and not xy:
        trace = {"type": "scatter", "meta": {"bind": "points", "source": source}}
    elif lat in ds.dims and lon in ds.dims:
        trace = {"type": "heatmap", "meta": {"source": source}}
    else:
        trace = {"type": "scatter", "meta": {"source": source}}
    trace["uid"] = uid
    if xaxis:
        trace.update(xaxis=xaxis, yaxis="y" + xaxis[1:])
    return trace


def _as_drawn(ds, trace):
    """The input as the spec's own variable / isel / sel / along leave it: the default fits
    what is drawn (one line per station with ``along``, not a station map)."""
    meta = (trace or {}).get("meta") or {}
    source = meta.get("source") or {}
    if source.get("variable") in ds.data_vars:
        ds = ds[[source["variable"]]]
    picks = {**(source.get("isel") or {}), **(source.get("sel") or {})}
    if meta.get("along"):
        picks[meta["along"]] = 0
    for dim, pick in picks.items():
        # One value drops the dim; which value does not matter for the shape.
        if dim in ds.dims and not isinstance(pick, (list, dict)):
            ds = ds.isel({dim: 0})
    return ds


def _layer_traces(layers):
    data, datasets, mask = [], {}, None
    for uid, layer in zip(input_ids([x.name for x in layers]), layers, strict=True):
        if layer.kind == "mask":
            mask = layer.path
            continue
        if layer.kind == "outline":
            data.append(
                {
                    "uid": uid,
                    "type": "scatter",
                    "meta": {"bind": "geojson", "source": {"geojson": layer.path}},
                }
            )
            continue
        datasets[uid] = layer.ds
        source = {"input": uid}
        if layer.kind in ("heatmap", "contour"):
            data.append({"uid": uid, "type": layer.kind, "meta": {"source": source}})
        elif layer.kind == "scatter":
            data.append(
                {"uid": uid, "type": "scatter", "meta": {"bind": "points", "source": source}}
            )
        else:
            data.append(
                {"uid": uid, "type": "heatmap", "meta": {"bind": "speed", "source": source}}
            )
            data.append(
                {
                    "uid": f"{uid}-arrows",
                    "type": "scatter",
                    "meta": {"bind": "arrows", "source": source},
                }
            )
    return data, datasets, mask


@weather_skill(name="plot", version=_SKILL_VERSION)
@weather_skill.argument(
    "-i",
    "--input",
    dest="inputs",
    type=parse_input,
    action="append",
    help="Input Zarr as PATH or PATH=NAME. Repeat for side-by-side maps, one panel per file on "
    "its own grid. In --spec the input is uid NAME, else its position: 0, 1, …. Exclusive with "
    "data layers.",
)
@weather_skill.argument(
    "--layer",
    action="append",
    type=parse_layer,
    help="KIND:PATH or KIND:PATH=NAME layer drawn on one shared map; repeat to stack, bottom "
    "first. Kinds: heatmap, contour, scatter (stations), quiver (u/v), outline (GeoJSON), mask "
    "(GeoJSON). In --spec the layer is uid NAME, else its position: 0, 1, ….",
)
@weather_skill.argument(
    "--obs",
    type=Dataset("spatial"),
    help="Verification grid: observation Zarr for the verifying week (input id obs).",
)
@weather_skill.argument(
    "--forecast",
    type=Dataset("spatial"),
    action="append",
    help="Verification grid: forecast Zarr for that week at one lead; repeat, week-1 first "
    "(input ids forecast1, forecast2, …).",
)
@weather_skill.argument(
    "--verify",
    type=Dataset("any"),
    action="append",
    help="Verification grid: verify Zarr from the verify skill, once per --forecast, same order.",
)
@weather_skill.argument(
    "--mediogram",
    action="store_true",
    help="Mediogram at one point: -i the forecast Zarr, then -i the m-climate Zarr "
    "(input ids forecast, mclimate); set layout.meta.geo.point in --spec.",
)
@weather_skill.argument(
    "--animate",
    action="store_true",
    help="Play the map's times or steps as frames of one animated map instead of side-by-side "
    "panels; takes the same -i / --layer inputs as a still map. -o must end in .html or .gif.",
)
@weather_skill.argument("--spec", type=parse_spec_arg, help=SPEC_ARGUMENT_HELP)
@weather_skill.argument(
    "--theme-file",
    help="Theme JSON/TOML, or a shipped theme name (tropical_tidbits), layered on the "
    "standard look: {template, palettes, defaults, styles, overlays}.",
)
@weather_skill.argument("--dump-spec", nargs="?", const="-", probe=True, help=DUMP_SPEC_HELP)
def plot(
    inputs,
    output,
    layer,
    obs,
    forecast,
    verify,
    mediogram,
    animate,
    spec,
    theme_file,
    dump_spec,
    **kwargs,
):
    """Draw weather-skills Zarrs as maps, time series, scatter plots, wind roses, verification grids, mediograms, or animations."""
    inputs = list(inputs or [])
    files = [x.ds for x in inputs]
    layers = list(layer or [])
    grid = obs is not None or bool(forecast) or bool(verify)
    if grid + bool(mediogram) + bool(animate) > 1:
        raise UsageError("pick one of --obs/--forecast/--verify, --mediogram, or --animate")
    # A dumped spec replays its own mode: an animation spec animates again.
    replayed = ((spec.data.get("layout") or {}).get("meta") or {}).get("skill") if spec else None
    animate = animate or (replayed == "plot --animate" and not files and not layers)
    if grid:
        if inputs or layers:
            raise UsageError("--obs/--forecast/--verify make a verification grid; drop -i/--layer")
        merged, datasets = build_verify_spec(obs, forecast, verify, spec)
        return run(merged, None, datasets, output, dump_spec, theme_file=theme_file)
    if mediogram:
        if layers:
            raise UsageError("--mediogram takes two -i inputs (forecast, m-climate), not --layer")
        merged, datasets = build_mediogram_spec(files, spec)
        return run(merged, None, datasets, output, dump_spec, theme_file=theme_file)
    if replayed in ("plot --verify", "plot --mediogram") and not files and not layers:
        # The dumped spec already holds every trace and its layout.
        return run(spec.data, None, spec.opened(), output, dump_spec, theme_file=theme_file)
    # Boundaries and masks go on -i maps too: an outline on every panel.
    shapes = [x for x in layers if x.kind in ("outline", "mask")] if files else []
    if shapes and len(shapes) == len(layers):
        layers = []
    if files and layers:
        raise UsageError(
            "pass -i/--input or --layer, not both (-i takes only outline: and mask: layers)"
        )
    if animate and len(files) > 1:
        raise UsageError(
            "--animate plays one map: stack the inputs with --layer (heatmap:, contour:, "
            "scatter:, quiver:, outline:, mask:) instead of several -i"
        )
    layout = {}
    series = False
    if layers:
        data, datasets, mask = _layer_traces(layers)
        datasets = spec_datasets(spec, datasets)
        if mask:
            layout["meta"] = {"geo": {"mask_geojson": mask}}
    else:
        cli = dict(zip(input_ids([x.name for x in inputs]), files, strict=True))
        datasets = spec_datasets(spec, cli)
        user = {t.get("uid"): t for t in (spec.data.get("data") or [])} if spec else {}
        drawn = {key: _as_drawn(d, user.get(key)) for key, d in datasets.items()}
        data = [_default_trace(key, d, user.get(key)) for key, d in drawn.items()]
        # 1-D series share one x/y axis, one legend entry per file.
        series = bool(data) and all(
            t["type"] == "scatter" and "bind" not in t["meta"] for t in data
        )
        if series:
            if len(data) > MAX_INPUTS:
                raise UsageError(
                    f"--input may be passed at most {MAX_INPUTS} times for series; got "
                    f'{len(data)}. For ensemble members use meta.along "number" on one input'
                )
            for i, (key, d) in enumerate(datasets.items()):
                data[i]["name"] = trace_label(d, i, key)
        elif cli:
            data = [
                _default_trace(key, d, user.get(key), xaxis=None if i == 0 else f"x{i + 1}")
                for i, (key, d) in enumerate(drawn.items())
            ]
        else:
            data = []
        for j, shape in enumerate(shapes):
            if shape.kind == "mask":
                layout["meta"] = {"geo": {"mask_geojson": shape.path}}
                continue
            for i in range(len(datasets)):
                outline = {
                    "uid": f"outline{j + 1}" + (f"-{i + 1}" if i else ""),
                    "type": "scatter",
                    "meta": {"bind": "geojson", "source": {"geojson": shape.path}},
                }
                if i:
                    outline.update(xaxis=f"x{i + 1}", yaxis=f"y{i + 1}")
                data.append(outline)
    if not datasets and not any((t.get("meta") or {}).get("bind") == "geojson" for t in data):
        if spec is None or not (spec.data.get("data")):
            raise UsageError(
                "pass -i/--input, --layer, or a --spec with data and layout.meta.inputs"
            )
    base = skeleton("plot --animate" if animate else "plot", datasets, data)
    if layout.get("meta"):
        base["layout"]["meta"].update(layout["meta"])
    merged = merge_spec(base, spec.data if spec is not None else {})
    if series:
        warn_mixed_units(merged, datasets)
        warn_disjoint_dates(merged, datasets)
    return run(merged, None, datasets, output, dump_spec, theme_file=theme_file, animate=animate)


install_spec_help(plot)

if __name__ == "__main__":
    plot()
