---
name: ncics-mjo-png
description: "Download an NCICS tropical-monitoring figure as a PNG: wave-filtered latitude-longitude maps or longitude-time Hovmöller diagrams of OLR, winds, velocity potential, streamfunction, shear, or precipitable water, showing the MJO, Kelvin, Rossby, and other equatorial waves. Use when a briefing or answer needs the published NCICS MJO / equatorial-wave figure, current or from a past weekly snapshot (from mid-2017)."
license: MIT
compatibility: Requires Python 3.12 and uv. Fetches over HTTPS from ncics.org; no credentials required.
allowed-tools: Bash(uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py *)
metadata:
  catalog-group: figure
  availability:
    shape: latest
    policy: none
    lag_days: 0
    note: Omit --date for the live overwritten image (typically daily). --date reads /pub/mjo/archive/YYYY/YYYY-MM-DD/v2/ (typically weekly snapshots; v2 PNGs from mid-2017).
---

# ncics-mjo-png

Downloads one NCICS MJO / equatorial-wave map or Hovmöller from https://ncics.org/mjo and writes it as a PNG.

## Usage

```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py [--product map|hovmoller] [-v VAR] [--level 850|200] [--algorithm ALG] \
  [--wave WAVE] [--region REGION] [--days N] [--latitude BAND] [--date YYYY-MM-DD] -o OUT.png
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py --probe-latest
```

### Arguments
- `--product` — `map` (observed and forecast lat/lon panels) or `hovmoller` (longitude-time; also `hov`). Default: `map`.
- `--variable`, `-v` — `olr`, `uwnd`, `vwnd`, `chi` (velocity potential), `psi` (streamfunction), `ushear` (zonal shear), `shear` (shear magnitude), `pwat` (precipitable water), or a full token such as `uwnd850` / `chi200`. Default: `olr`.
- `--level` — hPa for `uwnd`, `vwnd`, `chi`, `psi`: `850` or `200`. Default: `200`.
- `--algorithm` — `cfs` (with CFS forecasts), `orig` (Wheeler–Weickmann), `notc` (tropical cyclones removed), or `sst` (CFS with SST anomaly shading; Hovmöller only). Default: `cfs`.
- `--wave` — map overlay: `all`, `tc`, `sum`, `low`, `mjo`, `er` (equatorial Rossby), `kelvin`, `mtd` (MRG / TD). Default: `all`. Maps only.
- `--region` — map region: `africa`, `global`, `west` / `east` (hemispheres), `pacific`, `atlantic` (South Atlantic), `indonesia`, `asia`. Default: `africa`. Maps only.
- `--days` — map averaging window: `1`, `2`, `3`, `5`, `7`, `10`. Default: `7`. Maps only.
- `--latitude` — Hovmöller band: `wide` (15S–15N, also `tropics`), `eqtr` (5S–5N), `north` (5N–15N), `north2` (10N–20N), `south` (15S–5S). Default: `wide`. Hovmöllers only.
- `--date` — archive snapshot; uses the latest snapshot on or before it (stderr names it). Default: the live figure.
- `--probe-latest` — print the latest archive snapshot date and exit, no `-o`. Takes no value.
- `--output`, `-o` — PNG output path.

### Output
A PNG with `weather_skills_history` in its metadata. stderr prints the URL fetched.

### Notes
- Options that do not apply to the chosen product are ignored.
- Archive snapshots are typically weekly and start mid-2017; an earlier `--date` exits 1.
- The output is an image only; there is no gridded data.

### Example
```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py --product hovmoller -v uwnd --level 850 --latitude eqtr -o u850_hov.png
```
Writes the live 5S–5N 850 hPa zonal-wind Hovmöller with CFS forecasts.

## Details

URL patterns:
```
live:    https://ncics.org/pub/mjo/v2/map/{var}.{algorithm}.{wave}.{region}.{days}.png
         https://ncics.org/pub/mjo/v2/hov/{var}.{algorithm}.{latitude}.png
archive: https://ncics.org/pub/mjo/archive/{YYYY}/{YYYY-MM-DD}/v2/{map|hov}/{file}.png
```
The default figure is https://ncics.org/pub/mjo/v2/map/olr.cfs.all.africa.7.png.

More examples:
```bash
# Global 10-day 200 hPa velocity potential, MJO overlay
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py -v chi --region global --days 10 --wave mjo -o chi200_global.png
# Africa OLR map from the 2024-12-30 snapshot (or the one before it)
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py --date 2024-12-30 -o olr_africa_20241230.png
```
