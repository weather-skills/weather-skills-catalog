---
name: chc-plot-theme
description: "Print the Kenya Meteorological Department (KMSA) or classic CHC plot theme as JSON to save for plot --theme-file, or a CHC or KMSA rainfall colormap as a JSON object for a plot trace's meta.palette, choosing the CHC window (daily, week, month, season) from a window length or a Zarr's aggregation period. Use when a rainfall map should look like a KMSA Kenya rainfall map or a classic CHC map, or use the CHC precipitation classes."
license: MIT
compatibility: Requires Python 3.12 and uv.
allowed-tools: Bash(uv run ${CLAUDE_SKILL_DIR}/scripts/theme.py *)
metadata:
  catalog-group: agent-tooling
---

# chc-plot-theme

Prints a KMSA or CHC plot theme as JSON, or one rainfall colormap as a single line of JSON (`name`, `colors`, `bounds`), for the weather-skills `plot` skills.

## Usage

```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/theme.py --theme kmsa|chc > THEME.json
uv run ${CLAUDE_SKILL_DIR}/scripts/theme.py --colormap NAME [--window-days N | --for PATH.zarr]
```

### Arguments
- `--theme` — `kmsa` (KMSA rainfall classes and Kenya-map styling) or `chc` (classic CHC classes): print that theme as JSON. Save it to a `.json` file in your working directory and pass that file to the plotting skills' `--theme-file`. Exactly one of `--theme` and `--colormap` is required.
- `--colormap` — `kmsa`, `chc_precip_daily` / `_week` / `_month` / `_season`, `chc_precip_anom_*` (same windows), or the family `chc_precip` / `chc_precip_anom` with `--window-days` or `--for` to pick the window. Classes are listed under Details.
- `--window-days` — window length in days; only with a family name: under 2 → `daily`, up to 10 (a week or dekad) → `week`, under 40 → `month`, longer → `season`. stderr names the choice.
- `--for` — Zarr you will plot, only with a family name; the window comes from its data variables' `aggregation_period`. Exits 2 unless exactly one period is found. `--window-days` wins when both are given.

### Output
`--theme`: the theme JSON on stdout. `--colormap`: one JSON object on stdout.

### Notes
- The theme and colormaps are for period totals or anomalies in mm, so aggregate and convert to totals before plotting.
- With `--theme-file`, rainfall fields get the theme's classes with no spec changes. For daily or few-day totals under `--theme chc`, the CHC window classes fit better: put `--colormap chc_precip --for ZARR` in that trace's `meta.palette`.
- With `--theme kmsa`, rainfall totals get the KMSA classes. The file also names the palette `kmsa`, so `"meta": {"palette": "kmsa"}` puts it on one trace.
- Put the whole `--colormap` object, not its name, in a trace's `meta.palette`; `plot` has no `kmsa` or `chc_precip_*` palette of its own.
- The lowest KMSA class and the near-zero CHC anomaly class are white, like masked cells; say so in a caption when the map is masked.
- Neither carries dates; put the period in the title.

### Example
KMSA-style Kenya map with county lines and the Tana basin outline:
```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/theme.py --theme kmsa > kmsa_theme.json
plot -i totals_kenya.zarr --layer outline:kenya_counties.json --layer outline:tana.json -o kenya_kmsa.png \
  --theme-file kmsa_theme.json \
  --spec '{"layout": {"title": {"text": "Rainfall 21–30 Sep 2026"}},
           "data": [{"uid": "outline1", "line": {"color": "#4d4d4d", "width": 0.8}},
                    {"uid": "outline2", "line": {"width": 1.6}}]}'
```
Draws dekad totals in KMSA classes, with thin grey county lines and a heavier basin outline; outline layers are `outline1`, `outline2`, … in `--layer` order. The GeoJSON comes from resolve-chc-region.

## Details

| Theme | Draws |
|---|---|
| `kmsa` | Rainfall totals in the KMSA classes < 1, 2 - 10, 11 - 20, 21 - 50, 51 - 70, 71 - 100, > 100 mm (white, pale green, green, light blue, blue, orange, red-orange), one labelled legend box per class; Arial text; black national borders, dark grey county lines. Other fields keep the standard colors. |
| `chc` | Classic CHC map classes, fixed whatever the period: totals in `ppt_total` (0, 2, 5, 10, 25, 50, 75, 100, 150, 200, 300, 500, 750, 1000, 1500, 2500 mm), anomalies in the full CHC anomaly classes (±10 … ±500 mm), percent of normal in `ppt_poa`, SPI in `spi`. The standard look instead crops the CHC hues to a window picked from `aggregation_period`. Other fields keep the standard colors. |

| Colormap | Classes |
|---|---|
| `kmsa` | KMSA rainfall-map classes, edges 1, 10, 20, 50, 70, 100 mm, white under and red-orange over |
| `chc_precip_daily` / `_week` / `_month` / `_season` | CHC totals, white/beige at the low end through greens and blues, top edge 50 / 200 / 400 / 1000 mm |
| `chc_precip_anom_daily` / `_week` / `_month` / `_season` | CHC anomalies, brown/red (dry) through white (−10 to +10 mm) to green, blue, purple (wet), ±50 / ±200 / ±300 / ±500 mm |

The example's GeoJSON: `resolve-chc-region Kenya --counties-geojson kenya_counties.json` and `resolve-chc-region "Tana basin" --geojson tana.json`. An outline layer turns off the Natural Earth admin-1 lines, so they do not double the county lines.

For a CHC window picked from the data:
```bash
PAL=$(uv run ${CLAUDE_SKILL_DIR}/scripts/theme.py --colormap chc_precip_anom --window-days 15)
plot -i anom.zarr -o anom.png --spec "{\"data\": [{\"type\": \"heatmap\", \"meta\": {\"palette\": $PAL}}]}"
```
gives `chc_precip_anom_month` for a 15-day outlook.

The CHC classes are the same palettes as `plot`'s built-in `default_precip` / `default_precip_anom`; this skill lets you choose the window yourself (e.g. a dekad on the week classes) or use the KMSA classes.

`data/kmsa_theme.json` is the KMSA plot theme: a Plotly `template` (fonts), `palettes.kmsa` (one color and legend label per class), `defaults.precip` and `overlays` (base-map line styles). `data/chc_classic_theme.json` is the classic CHC plot theme: `palettes` with every class palette it draws (`ppt_total`, `ppt_anom_season`, `ppt_poa`, `spi`; colors are one per class, or packed as under color, classes, over color) and `defaults` naming them for each rainfall field kind. The palettes are written out in full under `plot`'s built-in names, so the theme keeps the classic CHC classes if `plot`'s own palettes change. `data/chc_theme.json` holds one `colormaps` entry per name: packed `colors` (under color, one per class, over color) and class `bounds`. KMSA colors are sampled from a published Kenya Meteorological Department rainfall map and match the standard ArcGIS swatches. CHC colors are the weather-skills-plotting nested precipitation palettes (`ppt_*` / `ppt_anom_*`).
