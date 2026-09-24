---
name: mjo-forecast-fetch
description: Fetch the latest CPC CLIVAR Madden–Julian Oscillation (MJO) Wheeler–Hendon phase-space forecast PNG for GEFS, GEFS-extended, CFS, CMC, JMA, ECMWF, ECMWF extended-range, or BoM ACCESS-S1 (bias-corrected preferred by default). Use when a task needs the published MJO RMM diagram image from https://www.cpc.ncep.noaa.gov/products/precip/CWlink/MJO/CLIVAR/clivar_wh.shtml; does not produce gridded data.
license: MIT
compatibility: Requires Python 3.12 and uv. Fetches over HTTPS from www.cpc.ncep.noaa.gov; no credentials required.
allowed-tools: Bash(uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py *)
metadata:
  catalog-group: figure
  availability:
    shape: latest
    policy: none
    lag_days: 0
    note: CPC overwrites fixed image URLs with the latest forecast (most systems daily; ECMWF extended-range 2× weekly)
---

# mjo-forecast-fetch

Downloads the **latest** CPC CLIVAR dynamical-model MJO Wheeler–Hendon
phase-space diagram PNG and writes it to `--output`. There is no Zarr input —
this skill only produces a figure.

Source page:

https://www.cpc.ncep.noaa.gov/products/precip/CWlink/MJO/CLIVAR/clivar_wh.shtml

Image URLs are **static** (CPC updates the same filenames in place):

```
https://www.cpc.ncep.noaa.gov/products/precip/mjo/img/<FILE>.png
```

## Models

Not every system publishes both a raw and a bias-corrected diagram. Cells marked
**—** mean CPC does not provide that variant.

| `--model` | Bias-corrected (`--bias-corrected`) | Raw (`--no-bias-corrected`) | CPC heading |
| --- | --- | --- | --- |
| `gefs` | `GEFS_BC.png` | `GEFS.png` | NCPB / NCPE — NCEP GEFSv12 (daily) |
| `gefs-extended` | — (falls back to raw) | `GMON.png` | GMON — NCEP GEFSv12 Extended (daily, day-1 init) |
| `cfs` | `NCFS.png` | — | NCFS — NCEP CFSv2 bias-corrected (daily) |
| `cmc` | — (falls back to raw) | `CANM.png` | CMET — Canadian ensemble (daily, day-1 init) |
| `jma` | — (falls back to raw) | `JMAN.png` | JMAN — JMA GSM ensemble (daily) |
| `ecmwf` | `ECMF_BC.png` | `ECMF.png` | ECMM / ECMF — ECMWF ensemble (daily) |
| `ecmwf-extended-range` | `EMON_BC.png` | `EMON.png` | EMOM / EMON — ECMWF extended-range (2× weekly) |
| `bom` | `BOMM_BC.png` | `BOMM.png` | BOMM / BOMA — BoM ACCESS-S1 (daily, day-2 init) |

`--bias-corrected` (default **on**) prefers the bias-corrected file when CPC
publishes one; for models with only a raw diagram it uses that raw file.
`--no-bias-corrected` requires a raw diagram (errors for `cfs`, which is
BC-only on CPC).

## When to use

- Need the published MJO RMM / phase-space forecast diagram for a CLIVAR model.
- Prefer the official CPC figure rather than recomputing RMM indices.

## When not to use

- You need analyzable gridded OLR / u850 / u200 fields — this skill only
  fetches PNGs.
- You need a historical init date — only the latest overwritten image is
  available at these URLs.

## Usage

```
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py \
    --model gefs|gefs-extended|cfs|cmc|jma|ecmwf|ecmwf-extended-range|bom \
    [--bias-corrected | --no-bias-corrected] \
    -o <out.png>
```

### Arguments

- `--model` — required. One of the ids in the table above.
- `--bias-corrected` / `--no-bias-corrected` — boolean flag (default:
  bias-corrected on). See Models for which variants exist per system.
- `--output`, `-o` — PNG output path.

### Output

A PNG at `--output`. The decorator stamps `weather_skills_history` into the PNG
metadata.

## Examples

```bash
# Latest GEFS bias-corrected MJO diagram (default)
uv run skills/mjo-forecast-fetch/scripts/fetch.py --model gefs -o /tmp/mjo_gefs.png

# GEFS extended (raw only on CPC)
uv run skills/mjo-forecast-fetch/scripts/fetch.py --model gefs-extended -o /tmp/mjo_gmon.png

# JMA / BoM
uv run skills/mjo-forecast-fetch/scripts/fetch.py --model jma -o /tmp/mjo_jma.png
uv run skills/mjo-forecast-fetch/scripts/fetch.py --model bom -o /tmp/mjo_bom.png

# ECMWF raw (not bias-corrected)
uv run skills/mjo-forecast-fetch/scripts/fetch.py \
  --model ecmwf --no-bias-corrected -o /tmp/mjo_ecmwf_raw.png
```
