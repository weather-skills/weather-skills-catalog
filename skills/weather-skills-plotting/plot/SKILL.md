---
name: plot
description: "Draw weather-skills Zarrs as maps (heatmap, contour, stations, wind arrows), time series, xy scatter plots, wind roses, forecast verification grids (obs, lead-week forecasts and their hits / bias / MAE maps), ECMWF-style mediograms (forecast vs model-climate ensembles at a point), or animated HTML maps through time, saved as PNG, JPG, or interactive HTML. Titles, colors, panels, and layout are set with a standard Plotly figure JSON."
license: MIT
compatibility: Requires Python 3.12 and uv. PNG/JPG export needs Chrome (installed, or `plotly_get_chrome -y`). Self-contained; the plotting library ships in scripts/.
allowed-tools: Bash(uv run ${CLAUDE_SKILL_DIR}/scripts/plot.py *)
metadata:
  version: "1.0.1"
  catalog-group: figure
---

# plot

Draws one or more Zarrs as a figure. Name the files on the command line; set titles, colors, and layout in `--spec`.

## Usage

```bash
# One map (or one panel per time / step)
uv run ${CLAUDE_SKILL_DIR}/scripts/plot.py -i rain.zarr -o rain.png
# Maps side by side, each on its own grid
uv run ${CLAUDE_SKILL_DIR}/scripts/plot.py -i chirps.zarr=OBS -i ecmwf.zarr=FC -o compare.png
# Area means or stations as lines on one time axis
uv run ${CLAUDE_SKILL_DIR}/scripts/plot.py -i chirps_area.zarr -i imerg_area.zarr -o rain.png
# Layers on one map, bottom first
uv run ${CLAUDE_SKILL_DIR}/scripts/plot.py --layer heatmap:rain.zarr --layer scatter:stations.zarr --layer outline:kenya.geojson -o map.png
# One variable against another
uv run ${CLAUDE_SKILL_DIR}/scripts/plot.py -i iod_rain.zarr -o iod_vs_rain.png --spec '{"data": [{"uid": "0", "meta": {"x": "dmi", "y": "precip"}}]}'
# Verification grid: obs, then each lead's forecast and verify map
uv run ${CLAUDE_SKILL_DIR}/scripts/plot.py --obs chirps_week.zarr --forecast ecmwf_w1.zarr --verify verify_w1.zarr --forecast ecmwf_w2.zarr --verify verify_w2.zarr -o verify.png
# Mediogram: forecast ensemble vs model climate at one point
uv run ${CLAUDE_SKILL_DIR}/scripts/plot.py --mediogram -i forecast.zarr -i mclimate.zarr -o mediogram.png --spec '{"layout": {"meta": {"geo": {"point": {"lat": -1.3, "lon": 36.8}}}}}'
# Animation through time, as interactive HTML
uv run ${CLAUDE_SKILL_DIR}/scripts/plot.py --animate -i chirps_daily.zarr -o chirps.html
```

The plot type follows the data: a lat/lon grid is a colored map, stations are colored markers, 1-D series are lines on one time axis. `meta.x` / `meta.y` scatter two variables. `--obs`/`--forecast`/`--verify`, `--mediogram` and `--animate` pick the three special figures (sections below); use one at a time.

### Arguments
- `--input`, `-i` — Zarr to draw, as `PATH` or `PATH=NAME`; repeat for side-by-side maps or more lines.
- `--layer` — `KIND:PATH` or `KIND:PATH=NAME` to stack on one map; repeat. Kinds: `heatmap`, `contour` (over a heatmap: labeled black lines, every 4 hPa for sea-level pressure; `"contours": {"coloring": "fill"}` fills it), `scatter` (stations), `quiver` (wind u/v), `outline` and `mask` (GeoJSON). `outline` and `mask` layers also combine with `-i` inputs (`-i grid.zarr --layer outline:counties.geojson`); `-i` itself takes Zarr paths only.
- `--obs`, `--forecast`, `--verify` — verification grid: the observation for one week, then each lead's forecast (repeat, week-1 first) and its verify-skill output (once per `--forecast`, same order). See **Verification grid**.
- `--mediogram` — forecast vs model climate at one point; pass `-i` twice (forecast, then m-climate). See **Mediogram**.
- `--animate` — one `-i` grid animated through its times or steps; `-o` must end in `.html`. See **Animation**.
- `--spec` — Plotly figure JSON, inline or a file path.
- `--theme-file` — house style: Plotly `template`, `palettes`, per-field `defaults`, base-map `overlays` (see Colors).
- `--dump-spec` — print (`-`) or save (a path) the spec, without drawing.
- `--output`, `-o` — `.png`, `.jpg`, or `.html`.

### Customizing with `--spec`
`--spec` changes the defaults. It takes [Plotly figure](https://plotly.com/python/reference/) JSON, `{"data": [...], "layout": {...}}`; write only the parts you change.

Each input file is one entry in `data`. Name it on the command line (`-i ecmwf.zarr=FC`) and put that name in `"uid"`. Unnamed files are `"0"`, `"1"`, … in order.

| To | `--spec` |
| --- | --- |
| Set the title | `{"layout": {"title": {"text": "Week 1"}}}` |
| Add a second title line | `{"layout": {"title": {"text": "ECMWF rainfall", "subtitle": {"text": "Week 1: 16–22 Oct"}}}}` (maps, time series, every plot; a `<br>` in `text` is an error: only `subtitle` gets room above the plot) |
| Title a panel | `{"data": [{"uid": "OBS", "name": "CHIRPS"}]}` |
| Pick one step | `{"data": [{"uid": "FC", "meta": {"source": {"isel": {"step": 0}}}}]}` |
| Set the color range | `{"data": [{"uid": "0", "zmin": 0, "zmax": 100}]}` |
| Set the map window | `{"layout": {"meta": {"geo": {"bbox": [5.5, 33.5, -5, 42.5]}}}}` |
| Draw contours | `{"data": [{"uid": "0", "type": "contour"}]}` |

`isel` counts from 0 (`-1` is the last); `sel` picks by label. `bbox` is `[N, W, S, E]`. `--help` lists every option; `--dump-spec -` shows the spec for your files.

### Output
The figure at `-o`. PNG/JPG also print `data: NULL` when every value drawn is NaN.

### Notes
- A map shows one value per cell: pick one value of any extra dim (`number`, `vertical`) with `isel` / `sel`, panel it with `"meta": {"facet": "number"}`, or average it first with summarize-dim.
- Plot draws the values in the file. A precip rate stays a rate (mm/day); for period totals (mm) run aggregate-temporal, then convert-to-totals.

### Example
```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/plot.py -i chirps_week.zarr=OBS -i ecmwf_week.zarr=FC -o obs_vs_fc.png --spec '{
  "data": [{"uid": "OBS", "name": "CHIRPS"}, {"uid": "FC", "name": "ECMWF", "meta": {"source": {"isel": {"step": 0}}}}],
  "layout": {"title": {"text": "Week 1 rainfall"}, "meta": {"geo": {"bbox": [5.5, 33.5, -5, 42.5]}}}}'
```
CHIRPS and ECMWF week-1 rainfall side by side over Kenya, each on its own grid, with one shared colorbar.

## Details

### Panels and layers
- A file with several times or steps gets one panel each, titled with its date. `layout.grid.rows` / `columns` set the grid; `xgap` / `ygap` the spacing, as a fraction of a panel.
- Panels showing the same quantity share one colorbar. To split one off: `{"data": [{"uid": "FC", "coloraxis": "coloraxis2"}]}`.
- Panel titles are annotations named `panel-title-1`, `panel-title-2`, …; rename one with `{"layout": {"annotations": [{"name": "panel-title-2", "text": "ECMWF"}]}}`.
- Maps use plain x/y axes in degrees (x = longitude). Add points or lines as `scatter` traces with `x` / `y`, and boxes as `layout.shapes` with `xref: "x"`, `yref: "y"`.
- An `outline` layer frames the map unless `geo.bbox` is set, and hides the built-in admin-1 lines. Use it to draw official boundaries.
- The canvas fits the maps (up to 760×560 px for one map, 340×340 px per panel in a grid). `layout.width` / `height` fix it.

### Lines over time
- A series runs along `time`, or for a forecast along its valid date (init time + `step`). Every other dim must go: pick one value with `isel` / `sel`, draw one line per value with `"along": "number"`, or average it first with summarize-dim (for an area mean, `--dim latitude --dim longitude --method mean --lat-weighted`).
- Each 1-D file is one line on one shared axis (up to 26 files). The legend names it after its file, or its station id; `"name"` renames it. With two or more lines the legend sits in rows above the plot; one line has none.
- Settings under `meta` on the first line (`along`, `band`, `members`, `align`, `source.variable`) apply to every line that does not set its own. `along` passes only to files that have that dim, so a climatology next to an ensemble stays one line. Set a key to `null` on a later line to drop it.
- `"along": "number"` draws one line per member under one legend entry; `"along_color": "cycle"` gives each its own color and entry. `"band": [10, 90]` shades that percentile range and draws the mean bold; `"members": false` hides the members. With `"type": "bar"`, `along` gives one bar series per value.
- Two variables of one file are two `-i` of the same path, each with its own `"meta": {"source": {"variable": …}}`.
- One panel per line: `{"layout": {"grid": {"rows": 2, "columns": 1, "pattern": "coupled"}}}`. A right-hand axis: `{"data": [{"uid": "1", "yaxis": "y2"}], "layout": {"yaxis2": {"overlaying": "y", "side": "right"}}}`.
- Lines on one axis with different units, or with no dates in common, print a warning and still draw.
- `"align": "dayofyear"` on the first line puts every year on one calendar, labelled `1 Oct`, `15 Nov`, … (29 Feb joins 28 Feb). Pass one file per year, each a single season; a season across New Year (DJF) puts Jan–Feb at the left.
- The y-axis label is the variable's `long_name` and units. A rainfall axis does not go below zero.

### Colors
- Precip totals use the mm class palette `default_precip`; anomalies use `default_precip_anom`. The range follows `aggregation_period`: under 2 days 0–50 mm, up to 10 days (a dekad) 0–200, under 40 days 0–400, longer 0–1000 (anomalies ±50 / ±200 / ±300 / ±500).
- Set a palette with `"meta": {"palette": NAME}`: `default_precip`, `default_precip_anom`, `ppt_daily` … `ppt_season`, `ppt_anom_*`, `spi`, or your own `{colors, bounds}`.
- Temperatures use `RdYlBu_r`; fields spanning zero use `RdBu_r`; flag fields (verify hits) get one color per flag.
- `{"layout": {"coloraxis": {"colorbar": {"orientation": "h"}}}}` puts the colorbar under the maps.
- The standard look is one theme file, `scripts/weather_skills_plotting/themes/default.json` in this skill; `themes/README.md` beside it explains each key. A `--theme-file` is JSON/TOML with any of its four keys, layered on top of it — copy `default.json` to start one:
  - `template`: a Plotly template (fonts, colorway, backgrounds).
  - `palettes`: `{name: {colors, bounds}}` for `meta.palette`; a name like a built-in one replaces it.
  - `defaults`: what a field gets when the spec names no palette, per kind: `precip`, `precip_anomaly`, `precip_poa`, `spi`, `wind_speed`, `diverging`, `temperature`, `sequential`. A value is a palette (name, color list or `{colors, bounds}`) or a Plotly colorscale name.
  - `overlays`: line styles per base-map layer, e.g. `{"borders": {"line": {"color": "#333333"}}}`.
- Without `--theme-file`, `$WEATHER_SKILLS_PLOT_THEME` or `~/.config/weather-skills/plot.json|toml` is used if present.

### Verification grid
`--obs OBS --forecast W1 --verify V1 --forecast W2 --verify V2 …` draws, for one verifying week, the observation and one column per forecast lead on one color scale, with each lead's verify map (hits, bias, or MAE from the verify skill) underneath and the metric named under the observation. One `--forecast` gives one row: observation, forecast, verify map.
- Prepare: `--obs` and each `--forecast` hold one time, the verifying week (select it first; a forecast with a `step` axis needs step-to-time before that). Obs and forecasts share one grid: coarsen the obs onto the forecast with `--reference-grid`. An ensemble forecast is drawn as its mean: run summarize-dim `--dim number --method mean` first. For rainfall, run aggregate-temporal, then convert-to-totals, before verify. All `--verify` files use one metric.
- Traces: `"obs"`, `"forecast1"` … `"forecastN"`, `"verify1"` … `"verifyN"`; column titles `panel-title-1` (obs), `panel-title-2` (week 1), …; the metric label `row-label-metric`. Obs and forecasts share `coloraxis`, verify maps `coloraxis2` (`{"layout": {"coloraxis2": {"cmin": -20, "cmax": 20}}}`). A forecast variable named differently: `{"data": [{"uid": "forecast1", "meta": {"source": {"variable": "tp"}}}]}`.
- Colors: hits are disagree / below / hit, with the event threshold in the colorbar title; bias is centred on zero to the 98th percentile of |bias| (rainfall brown = too dry, green = too wet); MAE runs white to warm. Each lead's score summary goes to stderr.
- The week's dates (`5–11 Oct '26`) join the title from any input that still has a `time` coordinate; when none has one, put the week in the title.

### Mediogram
`--mediogram -i FORECAST -i MCLIMATE` draws, at `layout.meta.geo.point` (required), a blue box of the forecast ensemble next to a red box of the model climate for each lead, with a black line through the forecast mean. Each file uses its grid cell nearest the point; up to 15 leads both files have.
- Inputs: both need ensemble members (`number`), leads (`step`) and latitude/longitude. The m-climate is the same system's reforecasts of past years for the same leads (individual members; a mean/std climatology is not one). No fetcher here supplies reforecasts; a past init of the same system is a rough stand-in, so say so in the title. For rainfall totals, run aggregate-temporal then convert-to-totals on both first.
- Traces: `"forecast"`, `"mclimate"` (box) and `"forecast-mean"` (line). Pick leads with the same `meta.source.isel` `{"step": [0, 2, 4]}` on all three; set `meta.source.variable` per trace when the files name it differently. A title keeps the grid cell and init date appended.
- Boxes span the 25th–75th percentile with the median; whiskers reach 1.5 × the box height. With the forecast's init `time` the x-axis shows valid dates ("Week starting" for weekly totals).

### Animation
`--animate -i GRID -o out.html` turns one gridded Zarr into a self-contained interactive map (plotly.js embedded, works offline): one frame per time or step, play / pause and a slider, every frame on one color scale and titled with its time. Each frame is the map `plot` draws for that time.
- Every dim other than latitude, longitude and the animated one must go (`isel` / `sel`, or summarize-dim); `{"data": [{"uid": "0", "meta": {"facet": "number"}}]}` animates another dim. Station and 1-D data are refused. It takes one input: to animate precip under isobars, `merge` the two fields into one Zarr and add a trace reading the other variable.
- Speed: `{"layout": {"meta": {"animation": {"duration": 300}}}}` (ms per frame, default 500; `transition` ms between frames). Your own `layout.updatemenus` / `sliders` replace the default controls.
- Size grows with frames × cells; stderr gives the frame count and data size and warns above 50 MB. Pick fewer frames (`{"step": {"start": 0, "stop": 12}}`) or a smaller `bbox`.

### Provenance
The file's history (`weather_skills_history`) is embedded in the PNG metadata or HTML `<meta>` tag; read it with the `provenance` skill. A PNG/JPG with a complete history carries a small red seal in the bottom-right corner.

### More examples
```bash
# Stations over IMERG, inside a county outline
uv run ${CLAUDE_SKILL_DIR}/scripts/plot.py -o nairobi.png \
  --layer heatmap:imerg.zarr --layer scatter:tahmo.zarr=STATIONS --layer outline:nairobi.geojson \
  --spec '{"data": [{"uid": "STATIONS", "marker": {"size": 14}}]}'
# MSLP chart: anomaly shading, labeled black isobars every 4 hPa, and H / L with the
# central pressure (highs / lows from local-extrema; --radius 7 marks a Tropical Tidbits-style
# set of centres, 15 or more misses most of them). For a forecast, pick the same step
# on every layer with "meta": {"source": {"isel": {"step": N}}}.
uv run ${CLAUDE_SKILL_DIR}/scripts/plot.py -o mslp.png \
  --layer heatmap:msl_anom.zarr --layer contour:msl.zarr=MSLP \
  --layer scatter:highs.zarr=HIGHS --layer scatter:lows.zarr=LOWS --spec '{"data": [
  {"uid": "HIGHS", "mode": "text", "texttemplate": "<b>H</b><br>%{customdata:.0f}", "textfont": {"color": "#1f4fbf", "shadow": "0 0 3px white"}},
  {"uid": "LOWS", "mode": "text", "texttemplate": "<b>L</b><br>%{customdata:.0f}", "textfont": {"color": "#c8102e", "shadow": "0 0 3px white"}}]}'
# Tropical Tidbits look: --theme-file tropical_tidbits adds TT's palettes (tt_precip_rate in
# mm/h, tt_precip_total, tt_pwat, tt_wind, tt_z500 in dam). --help RECIPES has MSLP & precip,
# MSLP & 10 m wind, total precip, MSLP & PWAT, and 500 mb height & MSLP.
uv run ${CLAUDE_SKILL_DIR}/scripts/plot.py -o tt_pwat.png --theme-file tropical_tidbits \
  --layer heatmap:pwat.zarr=PWAT --layer contour:msl.zarr=MSLP --spec '{"data": [
  {"uid": "PWAT", "meta": {"palette": "tt_pwat"}},
  {"uid": "MSLP", "contours": {"coloring": "none", "showlabels": true, "start": 900, "end": 1080, "size": 2}}]}'
# Ensemble members as lines with a 10–90% band
uv run ${CLAUDE_SKILL_DIR}/scripts/plot.py -i ens_area_mean.zarr -o ens.png \
  --spec '{"data": [{"uid": "0", "meta": {"along": "number", "band": [10, 90]}}]}'
# Daily CHIRPS as bars, climatology as a dashed line
uv run ${CLAUDE_SKILL_DIR}/scripts/plot.py -i obs_area.zarr=OBS -i clim_area.zarr=CLIM -o obs_vs_clim.png \
  --spec '{"data": [{"uid": "OBS", "name": "CHIRPS", "type": "bar"}, {"uid": "CLIM", "name": "Climatology", "line": {"dash": "dash"}}]}'
# Three OND seasons on one calendar
uv run ${CLAUDE_SKILL_DIR}/scripts/plot.py -i chirps_ond_2023.zarr -i chirps_ond_2024.zarr -i chirps_ond_2025.zarr -o ond.png \
  --spec '{"data": [{"uid": "0", "meta": {"align": "dayofyear"}}]}'
```
