# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "weather-skills-core>=0.3.2",
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
#   "gojsonnet>=0.22",
#   "pillow>=11",
# ]
# ///
# The weather_skills_plotting library sits next to this script (scripts/weather_skills_plotting),
# so the skill folder is self-contained: Python finds it on the script's own path.
"""Draw weather-skills Zarrs with a Jsonnet spec that evaluates to a Plotly figure."""

import argparse
import json
import re
import sys
from pathlib import Path

from weather_skills_core import UsageError, weather_skill
from weather_skills_core.units import dequantify_dataset

from weather_skills_plotting.bind import GeoLines, resolve
from weather_skills_plotting.export import OUTPUT_SUFFIXES, export, key_hint, validate
from weather_skills_plotting.geo import load_geojson
from weather_skills_plotting.lint import MARK_POINTS, UnboundDataError, check_bindings
from weather_skills_plotting.render import (
    SpecArg,
    evaluate,
    house_template,
    input_metadata,
    recipe_text,
)

# Auto-populated by the version-bump CI workflow. Do not edit manually.
_SKILL_VERSION = "2.0.5"

# A still image with more panels than this is refused: Chrome takes ~7 s to render
# 200 small maps and never finishes 1,826. Frames have no limit.
MAX_PANELS = 100
LARGE_HTML_MB = 50

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class InputArg:
    """``-i PATH=NAME``. The decorator opens a Zarr (``.ds``); a GeoJSON is read here."""

    def __init__(self, path: str, name: str):
        self.path, self.name, self.ds = path, name, None

    def is_geojson(self) -> bool:
        return Path(self.path).suffix.lower() in (".geojson", ".json")

    def zarr_paths(self):
        return [] if self.is_geojson() else [Path(self.path)]

    def __str__(self):
        return f"{self.path}={self.name}"


def parse_input(value):
    path, sep, name = str(value).rpartition("=")
    if not sep or not path or not _NAME.fullmatch(name):
        raise argparse.ArgumentTypeError(
            f"-i expects PATH=NAME, NAME a letter or _ then letters, digits and _ (OBS, "
            f"fc_week1); got {value!r}. NAME is how the spec refers to the file ('@NAME.var'); "
            'readable legend text goes in the trace\'s "name"'
        )
    if name in ("NE", "GB", "const"):
        raise argparse.ArgumentTypeError(
            f"-i NAME {name!r} is built in ('NE' is Natural Earth, 'GB' the geoBoundaries "
            "admin lines, 'const' the --const values)"
        )
    return InputArg(path, name)


def parse_const(value):
    """``--const NAME=VALUE``, kept as the text given so provenance replays it as passed."""
    key, sep, _ = str(value).partition("=")
    if not sep or not _NAME.fullmatch(key):
        raise argparse.ArgumentTypeError(f"--const expects NAME=VALUE; got {value!r}")
    return str(value)


@weather_skill(name="plot", version=_SKILL_VERSION)
@weather_skill.argument(
    "-i",
    "--input",
    dest="input",
    type=parse_input,
    action="append",
    help="An input as PATH=NAME: a Zarr, or a GeoJSON (.geojson / .json) of outlines. Repeat. "
    "The spec refers to it as '@NAME…'; its metadata is std.extVar('inputs').NAME.",
)
@weather_skill.argument(
    "--spec",
    type=SpecArg,
    help="Jsonnet (a .jsonnet / .json file, a recipe name such as map, or the text itself) that "
    "evaluates to a Plotly "
    "figure {data, layout, frames}. A string starting with @ names data: '@OBS.precip', "
    "'@OBS.precip.isel(time=0)', '@OBS.precip.max()' (scales only). import 'ws.libsonnet' "
    "for map axes, grids, base maps, palettes and an animation player.",
)
@weather_skill.argument(
    "--const",
    type=parse_const,
    action="append",
    help="NAME=VALUE, a constant: '@const.NAME' in a trace's data or a shape / annotation "
    "position (a threshold line at --const nino=0.5, a date), std.extVar('const').NAME in text "
    "such as a title. Numbers and text; repeat. A trace's data is an input or one of these, "
    "never typed into the spec.",
)
@weather_skill.argument(
    "--dpi",
    type=float,
    default=96,
    help="Pixel density of PNG, JPG and GIF output, in dots per inch. The layout is in CSS "
    "pixels at 96 dpi (the default); 192 doubles the pixels at the same layout, 300 is print.",
)
@weather_skill.argument(
    "--recipe",
    nargs="?",
    const="",
    probe=True,
    help="List the recipes (no NAME), or print recipe NAME's Jsonnet to copy and edit; draws nothing.",
)
@weather_skill.argument(
    "--dump-spec",
    nargs="?",
    const="-",
    probe=True,
    help="Print (-) or save (a path) the evaluated spec, @ references unresolved, and draw nothing.",
)
def plot(input, output, spec, const, dpi, recipe, dump_spec, **kwargs):
    """Draw weather-skills Zarrs with a Jsonnet spec that evaluates to a Plotly figure."""
    if recipe is not None:
        print(recipe_text(recipe))
        return None
    if spec is None:
        raise UsageError(
            "--spec is required: a .jsonnet file, a recipe name (--recipe lists them) or Jsonnet text"
        )
    datasets = {}
    for x in input or []:
        if x.name in datasets:
            raise UsageError(f"two inputs are named {x.name!r}; give each its own -i PATH=NAME")
        if x.is_geojson():
            if not Path(x.path).is_file():
                raise UsageError(f"input not found: {x.path}")
            datasets[x.name] = load_geojson(x.path)
        else:
            # Plain arrays, with units in CF form (g kg-1, mm day-1).
            datasets[x.name] = dequantify_dataset(x.ds)
    meta = {name: input_metadata(value) for name, value in datasets.items()}
    consts = {}
    for item in const or []:
        key, _, text = item.partition("=")
        try:
            consts[key] = float(text)
        except ValueError:
            consts[key] = text
    figure = evaluate(spec, meta, consts)
    # A spec with no template gets the house one, not Plotly's grey-blue default.
    figure.setdefault("layout", {}).setdefault("template", house_template())
    spec.evaluated = figure
    if dump_spec is not None:
        text = json.dumps(figure, indent=2, ensure_ascii=False)
        if dump_spec == "-":
            print(text)
        else:
            Path(dump_spec).write_text(text + "\n")
        return None
    if output is None:
        raise UsageError("--output is required unless --dump-spec is set")
    outputs = output if isinstance(output, list) else [output]
    # Lint: every key that can put data on the chart holds an @ reference, not typed-in values.
    try:
        check_bindings(figure)
    except UnboundDataError as exc:
        lines = []
        unbound = False
        for path, message in exc.problems:
            unknown = re.match(r"unknown key for ([\w.]+)", message)
            if unknown:
                # A key Plotly doesn't have: say what to do instead.
                kind, key = unknown.group(1).split(".")[-1], path.rsplit(".", 1)[-1]
                message = f"a {kind} has no property {key!r}.{key_hint(kind, key)}"
            unbound = unbound or "@binding" in message
            lines.append(f"  {path}: {message}")
        # How data gets in, only when a value is missing its @ binding.
        footer = (
            "\nData comes from the inputs: -i FILE=NAME, then '@NAME.var' in the key. A line or "
            "point at fixed coordinates is a layout shape (name and showlegend: true give it a "
            f"legend entry), or a scatter trace with no @ references at all, up to {MARK_POINTS} values "
            "per key. A "
            "constant in a trace that draws data is --const NAME=VALUE, then '@const.NAME'"
            if unbound
            else ""
        )
        raise UsageError(
            f"the spec has {len(lines)} problem(s) before binding:\n" + "\n".join(lines) + footer
        ) from None
    # Bind: GeoJSON inputs resolve as lines ('@KENYA.x' / '@KENYA.y'), constants as '@const.NAME'.
    sources = {
        name: GeoLines(value) if isinstance(value, list) else value
        for name, value in datasets.items()
    }
    sources["const"] = consts
    resolved, _ = resolve(figure, sources)
    fig = validate(resolved)
    n_panels = len(list(fig.select_xaxes()))
    stills = [p for p in outputs if Path(p).suffix.lower() not in (".html", ".htm", ".gif")]
    if stills and n_panels > MAX_PANELS:
        raise UsageError(
            f"{n_panels} panels is too many for one image; the limit is {MAX_PANELS}. Pick "
            "fewer, or play them as frames in .html / .gif"
        )
    if fig.frames and any(Path(p).suffix.lower() in (".html", ".htm") for p in outputs):
        size = len(fig.to_json()) / 1e6
        print(f"animation: {len(fig.frames)} frames, {size:.1f} MB of figure data", file=sys.stderr)
        if size > LARGE_HTML_MB:
            print(
                f"Warning: the HTML will be large (~{size:.0f} MB); use fewer frames or a "
                "smaller window",
                file=sys.stderr,
            )
    # Every output is checked before any is written.
    for path in outputs:
        suffix = Path(path).suffix.lower()
        if suffix not in OUTPUT_SUFFIXES:
            raise UsageError(
                f"output {Path(path).name!r} must end in .png, .jpg, .pdf, .svg, .html or .gif "
                f"(got {suffix or 'no suffix'})"
            )
        if suffix == ".gif" and not fig.frames:
            raise UsageError(
                f"output {Path(path).name!r}: a GIF plays the figure's frames, and this spec has "
                "none; add frames, or write a still image to .png"
            )
    if not dpi > 0:
        raise UsageError(f"--dpi must be above 0, got {dpi}")
    # Plotly lays out in CSS pixels, 96 to the inch; kaleido's scale multiplies them.
    written = [export(fig, path, scale=dpi / 96) for path in outputs]
    return written if isinstance(output, list) else written[0]


if __name__ == "__main__":
    plot()
