"""Plotly figures from weather-skills standard dataset Zarrs.

The spec is a standard Plotly figure; weather-skills bindings live in each
trace's ``meta`` and in ``layout.meta``.

=====================  ========================================================
Module                 Role
=====================  ========================================================
:mod:`.spec`           Load, merge (by uid / name), validate, dump the spec
:mod:`.bind`           Fill traces from datasets (``meta.bind`` modes)
:mod:`.figure`         Panels, color axes, overlays → ``go.Figure``
:mod:`.layout`         Templates, template lookups and the map panel grid
:mod:`.themes`         ``themes/default.json`` and ``--theme-file`` loading
:mod:`.palettes`       Class palettes, the precip windows, Plotly colorscales
:mod:`.kinds`          What a field is (precip, anomaly, SPI, temperature …)
:mod:`.geodata`        Natural Earth overlays, spatial subsets, extents
:mod:`.labels`         Figure text: dates, panel titles, product, variable, units
:mod:`.wind`           u/v detection, arrows, wind-rose histograms, xy pairing
:mod:`.qa`             Pixel hash + finite-data report printed by ``export()``
=====================  ========================================================
"""

from __future__ import annotations

from pathlib import Path

from weather_skills_plotting.spec import SPEC_VERSION, dump_spec, load_spec, merge_spec, validate

__all__ = [
    "SPEC_VERSION",
    "SpecArg",
    "compile",
    "dump_spec",
    "export",
    "load_spec",
    "merge_spec",
    "validate",
]

OUTPUT_SUFFIXES = (".png", ".jpg", ".jpeg", ".html", ".htm")


class SpecArg:
    """A parsed ``--spec``. The skill decorator opens the Zarrs it lists.

    ``layout.meta.inputs`` maps input ids to paths; those are opened only
    when the command line names no dataset files.
    """

    def __init__(self, data: dict):
        self.data = data
        self.ds = None
        self.datasets = None

    def to_dict(self) -> dict:
        """What provenance records for ``--spec``: the spec itself, not an object repr."""
        return self.data

    def input_paths(self) -> dict:
        return dict(((self.data.get("layout") or {}).get("meta") or {}).get("inputs") or {})

    def zarr_paths(self):
        return [Path(p) for p in self.input_paths().values()]

    def opened(self) -> dict:
        """``{input id: Dataset}`` for the paths in ``layout.meta.inputs``."""
        opened = list(self.datasets or ([self.ds] if self.ds is not None else []))
        return dict(zip(self.input_paths(), opened, strict=False))


def parse_spec_arg(value):
    """Argparse converter for ``--spec`` → :class:`SpecArg`."""
    from weather_skills_plotting.spec import parse_plot_spec

    return SpecArg(parse_plot_spec(value))


def compile(spec, datasets, *, theme=None):
    """Compile a spec against ``{input id: Dataset}`` into a Plotly figure."""
    from weather_skills_plotting.figure import compile_figure

    if isinstance(spec, SpecArg):
        spec = spec.data
    return compile_figure(spec, datasets, theme=theme)


def export(fig, output, *, datasets=None, scale=None):
    """Write ``output`` (.png, .jpg or .html) and print the QA lines for images.

    Images print a pixel ``plot hash`` and whether the plotted data is all-NaN.
    HTML embeds plotly.js so the file works offline.
    """
    from weather_skills_core.errors import UsageError

    output = Path(output)
    suffix = output.suffix.lower()
    if suffix not in OUTPUT_SUFFIXES:
        raise UsageError(
            f"output {output.name!r} must end in .png, .jpg or .html (got {suffix or 'no suffix'})"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    if suffix in (".html", ".htm"):
        import plotly.io as pio

        # An animation opens paused on its first frame; the viewer presses Play.
        # The scroll wheel zooms, which Plotly leaves off by default.
        pio.write_html(
            _float32(fig.to_plotly_json()),
            output,
            include_plotlyjs=True,
            full_html=True,
            auto_play=False,
            # No Plotly Cloud upload or Plotly logo link.
            config={
                "responsive": True,
                "scrollZoom": True,
                "showSendToCloud": False,
                "displaylogo": False,
            },
        )
        return output
    import plotly.io as pio

    # An image never shows hover, so the heatmaps' customdata (a full copy of
    # each field) stays out of what Chrome has to parse. Other traces keep it:
    # a texttemplate may print it.
    image = _float32(fig.to_plotly_json())
    for trace in image["data"]:
        if trace.get("type") in ("heatmap", "contour"):
            trace.pop("customdata", None)
    pio.write_image(image, output, scale=scale, validate=False)
    from weather_skills_plotting.qa import report_figure

    report_figure(output, datasets, fig=fig)
    return output


def _float32(obj):
    """Copy of a figure dict with its float64 arrays stored as float32.

    Plotly writes each array as base64 ``{"dtype": "f8", "bdata": ...}``. The
    HTML embeds every one; 32-bit floats halve its size and keep ~7
    significant digits, finer than any plotted value or hover label needs.
    """
    import base64

    import numpy as np

    if isinstance(obj, dict) and obj.get("dtype") == "f8" and "bdata" in obj:
        values = np.frombuffer(base64.b64decode(obj["bdata"]), dtype="<f8").astype("<f4")
        return {**obj, "dtype": "f4", "bdata": base64.b64encode(values.tobytes()).decode()}
    if isinstance(obj, dict):
        return {k: _float32(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_float32(v) for v in obj]
    return obj
