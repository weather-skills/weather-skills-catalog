---
name: africa-itf
description: "Download the latest NOAA/CPC Africa Intertropical Front (ITF) figure, which CPC also labels ITCZ, as a PNG: the continental position map (current vs mean) or the west or east latitude-by-dekad time series. Use when a briefing or answer needs CPC's published ITF / ITCZ position figure for the most recent dekad."
license: MIT
compatibility: Requires Python 3.12 and uv. Fetches over HTTPS from www.cpc.ncep.noaa.gov; no credentials required.
allowed-tools: Bash(uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py *)
metadata:
  catalog-group: figure
  availability:
    shape: latest
    policy: none
    lag_days: 0
    note: CPC overwrites fixed image URLs with the latest dekadal ITF analysis (typically Apr–Oct, a few days after each dekad ends)
---

# africa-itf

Downloads CPC's latest Africa ITF figure and writes it as a PNG.

## Usage

```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py [--location africa|west-africa|east-africa] -o OUT.png
```

### Arguments
- `--location` — which figure. Default: `africa`.
  - `africa` — continental map of the current ITF position against its mean.
  - `west-africa`, `east-africa` — line chart of the ITF's mean latitude by dekad since April, averaged over a fixed longitude band (east: 20E–35E).
- `--output`, `-o` — PNG output path.

### Output
A PNG with `weather_skills_history` in its metadata. stderr names the analysed dekad read from the CPC page (e.g. `Analysed dekad: September 21-30`); if the page text cannot be read, stderr warns and the date is only on the figure.

### Notes
- Only the latest dekad is available; CPC overwrites the same image files each analysis.
- The `east-africa` band lies west of Kenya (34–42E). For Kenya or the Horn of Africa, use the `africa` map.
- The output is an image only; there is no gridded data.

### Example
```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py -o itf_africa.png
```
Writes the latest continental ITF position map.

## Details

Source page: https://www.cpc.ncep.noaa.gov/products/international/itf/itcz.shtml

| `--location` | CPC page label | Source image |
| --- | --- | --- |
| `africa` | Mean vs Current | `itcz.jpg` (JPEG) |
| `west-africa` | West Region | `west.gif` (PNG bytes) |
| `east-africa` | East Region | `east.gif` (PNG bytes) |

The skill converts every source to PNG.
