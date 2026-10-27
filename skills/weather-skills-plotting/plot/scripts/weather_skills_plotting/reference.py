"""The spec reference printed after the flags in ``--help``.

Every key outside ``meta`` is standard Plotly and documented at
https://plotly.com/python/reference/. This page covers the ``meta`` keys
(generated from the validator's key lists) and recipes for common edits.
"""

from __future__ import annotations

import argparse
import json

from weather_skills_plotting import spec
from weather_skills_plotting.themes import DEFAULT_THEME_FILE

_BINDS = {
    "field": "lat/lon grid → z of a heatmap or contour (default for those types); panels the time/step dim",
    "speed": "wind speed from u/v → heatmap or contour (pair with an arrows trace for a quiver map)",
    "arrows": "u/v wind arrows on a map → scatter lines; meta.arrows.step / .scale",
    "points": "station_id / point_id values → colored scatter markers on a map",
    "geojson": "GeoJSON boundary → scatter lines (meta.source.geojson); repeats on every panel",
    "series": "1-D along time or step → scatter / bar (default for those types)",
    "xy": "one variable against another from the same input → scatter markers, or bars with "
    'type "bar" (set by meta.x / meta.y)',
    "samples": "values at a point per step → box (all samples) or scatter (the mean)",
    "windrose": "u/v pooled into a polar wind rose → barpolar (default for that type)",
}

_META = {
    "bind": "how the trace's arrays are filled; see BINDS",
    "source": "the data: {input, variable, isel, sel, u, v, geojson, mask_geojson, point}",
    "x / y": "variable names for a scatter of one against the other, a point per shared "
    "position (merge puts variables from two files in one)",
    "facet": "dim to panel (default: step or time when present); false for none",
    "along": "series: dim drawn as one line per value (e.g. number for spaghetti)",
    "along_color": "same (default; one color, one legend entry) or cycle",
    "band": "series with along: percentile pair such as [10, 90] ([0, 100] = min–max); draws "
    "the mean (this uid), the shading (uid-band) and member lines (uid-members), each "
    "styled by its own data entry",
    "members": "series with band: false hides the member lines (band and mean only)",
    "align": "series: dayofyear overlays years on one seasonal axis",
    "palette": "class palette name (default_precip, default_precip_anom, ppt_week, spi, …, or "
    "one a theme adds: see THEMES), "
    "color list, or {colors, bounds, under, over}: bounds are class edges, one color per "
    "class (len(bounds) - 1), under / over color values outside the edges (or pack them "
    "first and last in colors); replaces the automatic precipitation palette",
    "arrows": "arrows only: {step: an arrow on every Nth cell, scale: arrow length in map "
    "degrees per unit of speed (default: the 95th-percentile speed spans ~one arrow spacing)}",
}

_SOURCE = {
    "input": "input id: NAME from -i PATH=NAME, else the position 0, 1, … (obs, forecast1 … for --verify grids; forecast, mclimate for --mediogram)",
    "variable": "data variable to draw; required when the file holds more than one",
    "isel": '0-based positions, e.g. {"number": 0, "step": [0, 1]}; -1 is the last. One '
    "integer picks one value (one map); a list keeps the dim (one panel each); a range is "
    '{"start": 0, "stop": 12, "step": 3} (stop excluded)',
    "sel": 'labels to select (nearest for numbers and dates), e.g. {"time": "2026-09-21"}',
    "u / v": "wind component variables (default: found by CF standard_name eastward_wind / "
    "northward_wind, or names such as u10/v10, u/v)",
    "geojson": "geojson bind: path to the boundary file",
    "mask_geojson": "blank cells outside this polygon for this trace only",
    "point": 'samples and windrose binds: {"lat": …, "lon": …}, nearest grid cell',
}

_LAYOUT_META = {
    "inputs": "{id: path} — opened when no file flag is passed (written by --dump-spec)",
    "geo.bbox": "[N, W, S, E] or the N/W/S/E string resolve-region prints; map window, subsets every input. Named places: run resolve-region",
    "geo.mask_geojson": "blank every map cell outside this polygon (does not draw it)",
    "geo.point": '{"lat": …, "lon": …} for samples (--mediogram) and wind-rose traces; nearest cell',
    "overlays": "true, false, or {coastline|borders|disputed|lakes|rivers|admin1: false or a scatter "
    'style such as {"line": {"width": 2}}}',
    "export.scale": 'PNG/JPG/GIF pixel multiplier (default 1), e.g. {"export": {"scale": 2}}',
    "(dotted names)": 'are nested keys: geo.bbox is {"geo": {"bbox": […]}}',
    "animation": "--animate only: {duration: ms per frame (500), transition: ms between "
    "frames (0)}",
    "version / skill": "leave as dumped",
}

_RECIPES = [
    ("Title and font size", '{"layout": {"title": {"text": "Week 1"}, "font": {"size": 20}}}'),
    (
        "Two-line title (subtitle, or <br> in text)",
        '{"layout": {"title": {"text": "ECMWF rainfall", "subtitle": {"text": "Week 1: 16–22 Oct"}}}}',
    ),
    ("Contour instead of heatmap", '{"data": [{"uid": "0", "type": "contour"}]}'),
    (
        "MSLP chart: --layer heatmap:anom.zarr --layer contour:msl.zarr=MSLP --layer "
        "scatter:highs.zarr=HIGHS --layer scatter:lows.zarr=LOWS (from local-extrema --radius 7, a "
        "Tropical Tidbits-style set of centres; 15 or more misses most); black isobars every 4 hPa, "
        "H / L with the central value (the default for local-extrema points)",
        '{"data": [{"uid": "MSLP", "contours": {"start": 900, "end": 1080, "size": 4}}]}',
    ),
    (
        "Tropical Tidbits MSLP & precip (--theme-file tropical_tidbits; precip: aggregate-temporal "
        "--period '6 hour' --time-dim step, select the step 6 h before the MSLP lead, unit-convert "
        "to mm h-1): --layer heatmap:precip.zarr=PRECIP --layer contour:msl.zarr=MSLP --layer "
        "scatter:highs.zarr=HIGHS --layer scatter:lows.zarr=LOWS; the theme puts the value above H / L",
        '{"data": [{"uid": "PRECIP", "meta": {"palette": "tt_precip_rate"}}, {"uid": "MSLP", '
        '"contours": {"coloring": "none", "showlabels": true, "start": 900, "end": 1080, '
        '"size": 2}}]}',
    ),
    (
        "Tropical Tidbits MSLP & 10 m wind (--theme-file tropical_tidbits colors the speed): "
        "--layer quiver:uv.zarr=WIND (speed WIND, arrows WIND-arrows) --layer contour:msl.zarr=MSLP",
        '{"data": [{"uid": "WIND-arrows", "meta": {"arrows": {"step": 12}}}, '
        '{"uid": "MSLP", "contours": {"coloring": "none", '
        '"showlabels": true, "start": 900, "end": 1080, "size": 2}}]}',
    ),
    (
        "Tropical Tidbits total precip (aggregate-temporal, then convert-to-totals; "
        "--theme-file tropical_tidbits makes tt_precip_total the precip default): -i total.zarr",
        '{"layout": {"title": {"text": "Total precipitation, 0–48 h"}}}',
    ),
    (
        "Tropical Tidbits MSLP & precipitable water (--theme-file tropical_tidbits): "
        "--layer heatmap:pwat.zarr=PWAT --layer contour:msl.zarr=MSLP",
        '{"data": [{"uid": "PWAT", "meta": {"palette": "tt_pwat"}}, {"uid": "MSLP", "contours": '
        '{"coloring": "none", "showlabels": true, "start": 900, "end": 1080, "size": 2}}]}',
    ),
    (
        "Tropical Tidbits 500 mb height & MSLP (--theme-file tropical_tidbits; select vertical "
        "500, unit-convert to dam): --layer contour:z500.zarr=Z500 --layer contour:msl.zarr=MSLP",
        '{"data": [{"uid": "Z500", "meta": {"palette": "tt_z500"}, "line": {"width": 0}}, '
        '{"uid": "MSLP", "contours": {"coloring": "none", "showlabels": true, "start": 900, '
        '"end": 1080, "size": 4}, "line": {"color": "white"}}]}',
    ),
    (
        "Color limits and colorscale (continuous)",
        '{"data": [{"uid": "0", "zmin": 0, "zmax": 50, "colorscale": "YlGnBu"}]}',
    ),
    ("Class palette", '{"data": [{"uid": "0", "meta": {"palette": "ppt_month"}}]}'),
    (
        "Your own class palette (6 classes on 7 edges, plus a color above 100)",
        '{"data": [{"uid": "0", "meta": {"palette": {"colors": ["#ffffff", "#d1ffbe", '
        '"#55ff00", "#73dfff", "#00a9e6", "#ffaa00"], "bounds": [0, 1, 10, 20, 50, 70, 100], '
        '"over": "#ff5500"}}}]}',
    ),
    ("Latest time only", '{"data": [{"uid": "0", "meta": {"source": {"isel": {"time": -1}}}}]}'),
    (
        "First two forecast weeks as two panels",
        '{"data": [{"uid": "0", "meta": {"source": {"isel": {"step": [0, 1]}}}}]}',
    ),
    (
        "First 4 ensemble members, 2x2",
        '{"data": [{"uid": "0", "meta": {"facet": "number", "source": {"isel": '
        '{"number": [0, 1, 2, 3]}}}}], "layout": {"grid": {"rows": 2, "columns": 2}}}',
    ),
    (
        "One day of a station file drawn over a map (--layer heatmap:… --layer scatter:…)",
        '{"data": [{"uid": "1", "marker": {"size": 14}, "meta": {"source": {"sel": '
        '{"time": "2026-10-01"}}}}]}',
    ),
    (
        "Colorbar label and ticks",
        '{"layout": {"coloraxis": {"colorbar": {"title": {"text": "Rain [mm]"}, "len": 0.5}}}}',
    ),
    (
        "Grid shape and gaps (map gaps are fractions of a panel)",
        '{"layout": {"grid": {"rows": 2, "columns": 3, "xgap": 0.3, "ygap": 0.2}}}',
    ),
    (
        "Colorbar under the maps (placed for you; leave x / y unset)",
        '{"layout": {"coloraxis": {"colorbar": {"orientation": "h"}}}}',
    ),
    (
        "Rename panel 2 (panel titles are numbered from 1)",
        '{"layout": {"annotations": [{"name": "panel-title-2", "text": "ECMWF"}]}}',
    ),
    ("Map window", '{"layout": {"meta": {"geo": {"bbox": [5, 33.5, -5, 42]}}}}'),
    ("2x pixels in PNG/JPG", '{"layout": {"meta": {"export": {"scale": 2}}}}'),
    ("Base-map layers", '{"layout": {"meta": {"overlays": {"rivers": false, "admin1": true}}}}'),
    (
        "Separate colorbars for side-by-side maps (same quantity shares one by default)",
        '{"data": [{"uid": "1", "coloraxis": "coloraxis2"}]}',
    ),
    (
        "The variable to draw from a multi-variable file",
        '{"data": [{"uid": "1", "meta": {"source": {"variable": "precip"}}}]}',
    ),
    (
        "Boundary outline",
        '{"data": [{"uid": "kenya", "type": "scatter", "meta": {"bind": "geojson", '
        '"source": {"geojson": "kenya.geojson"}}, "line": {"width": 3}}]}',
    ),
    (
        "City markers (a plain Plotly trace; repeats on every map panel)",
        '{"data": [{"uid": "cities", "type": "scatter", "mode": "markers+text", "x": [36.82], '
        '"y": [-1.29], "text": ["Nairobi"], "textposition": "top right"}]}',
    ),
    (
        "Box outline on the map",
        '{"data": [{"uid": "box", "type": "scatter", "mode": "lines", "x": [34, 42, 42, 34, 34], '
        '"y": [-5, -5, 5, 5, -5], "line": {"color": "red"}}]}',
    ),
    (
        "Wind quiver map (or --layer quiver:PATH), an arrow every 3rd cell",
        '{"data": [{"uid": "0", "meta": {"bind": "speed"}}, {"uid": "arrows", "type": "scatter", '
        '"meta": {"bind": "arrows", "source": {"input": "0"}, "arrows": {"step": 3}}}]}',
    ),
    ("Wind rose", '{"data": [{"uid": "0", "type": "barpolar"}]}'),
    (
        "Area-mean time series of a grid (summarize-dim --dim latitude --dim longitude "
        "--method mean --lat-weighted first)",
        '{"data": [{"uid": "0", "type": "scatter", "meta": {"bind": "series"}}]}',
    ),
    (
        "One variable against another (e.g. a merge --by-year output)",
        '{"data": [{"uid": "0", "meta": {"x": "dmi", "y": "precip"}}]}',
    ),
    (
        "Ensemble spaghetti with a 10–90% band",
        '{"data": [{"uid": "0", "meta": {"along": "number", "band": [10, 90]}}]}',
    ),
    (
        "Band parts styled apart: bold red mean (uid), faint grey members (uid-members), "
        "named shading (uid-band)",
        '{"data": [{"uid": "0", "line": {"color": "red", "width": 4}, "meta": {"along": '
        '"number", "band": [0, 100]}}, {"uid": "0-members", "line": {"color": "grey"}, '
        '"opacity": 0.3}, {"uid": "0-band", "name": "Ensemble range"}]}',
    ),
    (
        "Bars instead of lines; stacked",
        '{"data": [{"uid": "0", "type": "bar"}], "layout": {"barmode": "stack"}}',
    ),
    (
        "Second series on a right-hand axis",
        '{"data": [{"uid": "1", "yaxis": "y2"}], "layout": {"yaxis2": {"overlaying": "y", "side": "right"}}}',
    ),
    (
        "One stacked panel per series",
        '{"data": [{"uid": "1", "xaxis": "x2", "yaxis": "y2"}], '
        '"layout": {"grid": {"rows": 2, "columns": 1, "pattern": "coupled"}}}',
    ),
    (
        "Text label on panel 1",
        '{"layout": {"annotations": [{"text": "Onset", "xref": "x domain", "yref": "y domain", '
        '"x": 0.5, "y": 0.03, "showarrow": false, "bgcolor": "white"}]}}',
    ),
    (
        "Shaded period on a time axis",
        '{"layout": {"shapes": [{"type": "rect", "xref": "x", "yref": "y domain", '
        '"x0": "2026-10-01", "x1": "2026-10-10", "y0": 0, "y1": 1, "fillcolor": "grey", '
        '"opacity": 0.2, "line": {"width": 0}}]}}',
    ),
    (
        "Verification grid (--obs OBS --forecast W1 --forecast W2 --verify V1 --verify V2): title "
        "(replaces the default week title; include the week yourself)",
        '{"layout": {"title": {"text": "IMERG vs ENS, 3–9 Aug"}}}',
    ),
    (
        "Verification grid: rename the obs panel; fix the verify row range (coloraxis2)",
        '{"layout": {"annotations": [{"name": "panel-title-1", "text": "CHIRPS"}], '
        '"coloraxis2": {"cmin": -20, "cmax": 20}}}',
    ),
    (
        "Mediogram (--mediogram -i FORECAST -i MCLIMATE) at a point, leads 0, 2, 4",
        '{"layout": {"meta": {"geo": {"point": {"lat": -1.29, "lon": 36.82}}}}, "data": ['
        '{"uid": "forecast", "meta": {"source": {"isel": {"step": [0, 2, 4]}}}}, '
        '{"uid": "mclimate", "meta": {"source": {"isel": {"step": [0, 2, 4]}}}}, '
        '{"uid": "forecast-mean", "meta": {"source": {"isel": {"step": [0, 2, 4]}}}}]}',
    ),
    (
        "Animation (--animate -i GRID -o out.html): slower frames, first 12 steps",
        '{"layout": {"meta": {"animation": {"duration": 900}}}, "data": [{"uid": "0", '
        '"meta": {"source": {"isel": {"step": {"start": 0, "stop": 12}}}}}]}',
    ),
]


def _block(title: str, rows: dict) -> str:
    width = max(len(k) for k in rows) + 2
    lines = [title]
    lines += [f"  {k.ljust(width)}{v}" for k, v in rows.items()]
    return "\n".join(lines)


def spec_reference(recipes=None) -> str:
    """Plain-text reference appended to ``--help``; ``recipes`` defaults to plot's."""
    # One line per theme shipped beside default.json: its palettes and the field kinds it recolors.
    shipped = []
    for path in sorted(DEFAULT_THEME_FILE.parent.iterdir(), key=lambda p: p.name):
        if path.name.endswith(".json") and path.name != "default.json":
            theme = json.loads(path.read_text())
            defaults = ", ".join(f"{k} -> {v}" for k, v in theme.get("defaults", {}).items())
            shipped += [
                f"  {path.name.removesuffix('.json')}: palettes "
                + ", ".join(theme.get("palettes", {}))
                + (f"; defaults {defaults}" if defaults else "")
            ]
    parts = [
        "PLOT SPEC (--spec)",
        '  A standard Plotly figure: {"data": [traces], "layout": {…}}. Every key is Plotly\'s',
        "  (https://plotly.com/python/reference/) except meta, which holds the dataset bindings.",
        "  --spec is merged onto the figure the command builds from its files: data[] by uid",
        "  (else position; a new uid adds a trace), layout.annotations / shapes by name, and",
        "  everything else deep-merged. Run with --dump-spec - to see that figure, edit, pass back.",
        "  Output by -o suffix: .png, .jpg, .html (interactive, works offline), or .gif",
        "  (--animate only).",
        "",
        "  Panels: map traces on the same xaxis stack as layers; traces on x, x2, … sit side by",
        "  side, each on its own lat/lon grid (do not coarsen to plot). One trace with a time or",
        "  step dim gets one panel per value. Panel titles are annotations named panel-title-N.",
        "",
        _block("BINDS (data[].meta.bind)", _BINDS),
        "",
        _block("data[].meta", {**{k: _META[k] for k in _META}}),
        "  (keys: " + ", ".join(sorted(spec.TRACE_META_KEYS)) + ")",
        "",
        _block("data[].meta.source", _SOURCE),
        "",
        _block("layout.meta", _LAYOUT_META),
        "",
        "THEMES (--theme-file PATH, or a shipped theme by name, e.g. --theme-file tropical_tidbits)",
        "  Shipped themes (their palettes exist only with that --theme-file):",
        *shipped,
        "  JSON or TOML with any of five sections, layered on the standard look:",
        "  template  Plotly template (fonts, colorway, backgrounds)",
        "  palettes  name -> {colors, bounds[, under, over, labels]} or a color list; adds to the",
        "            standard ppt_daily / ppt_week / ppt_month / ppt_season, ppt_anom_* (cropped",
        "            from ppt_master / ppt_anom_master), ppt_total, ppt_poa, ppt_spp, spi, rocket,",
        "            hits, bias, precip_bias, mae, wind_rose, colorblind (same name replaces one)",
        "  defaults  field kind -> palette or Plotly colorscale: precip, precip_anomaly, precip_poa,",
        "            spi, wind_speed, diverging, sea_level_pressure, temperature, sequential",
        "  styles    drawn element -> Plotly settings: maps (no-data background), classed_colorbar,",
        "            isobars, highs, lows, map_marks, boundary, arrows, arrow_key_box, members,",
        "            band_members",
        "  overlays  coastline / borders / disputed / lakes / rivers / admin1 -> Plotly scatter style",
        "            (admin1 also styles a many-polygon outline layer, e.g. county lines)",
        "  --dump-spec shows your spec before draw-time defaults (palette, limits) are chosen;",
        "  render once to see those.",
        "",
        "RECIPES (each is a complete --spec)",
    ]
    for title, example in _RECIPES if recipes is None else recipes:
        parts += [f"  {title}:", f"    {example}"]
    return "\n".join(parts)


def install_spec_help(skill, recipes=None) -> None:
    """Append the spec reference to a skill's ``--help``, with that skill's recipes."""
    parser = skill.parser
    parser.formatter_class = argparse.RawDescriptionHelpFormatter
    parser.epilog = f"{spec_reference(recipes)}\n\n{parser.epilog or ''}".rstrip()
