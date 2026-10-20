---
name: mjo-forecast-fetch
description: "Download the latest CPC CLIVAR Madden–Julian Oscillation (MJO) Wheeler–Hendon phase-space forecast diagram as a PNG, for one of GEFS, GEFS extended, CFSv2, CMC, JMA, ECMWF, ECMWF extended range, or BoM ACCESS-S1. Use when a briefing or answer needs a model's published MJO RMM forecast plot."
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

Downloads one model's latest MJO phase-space forecast diagram from CPC and writes it as a PNG.

## Usage

```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py --model MODEL [--bias-corrected | --no-bias-corrected] -o OUT.png
```

### Arguments
- `--model` — forecast system. Required.
  - `gefs` — NCEP GEFSv12, 16 days, daily.
  - `gefs-extended` — GEFSv12 extended, 35 days, daily.
  - `cfs` — NCEP CFSv2, daily.
  - `cmc` — Canadian ensemble, daily.
  - `jma` — JMA GSM ensemble, daily.
  - `ecmwf` — ECMWF ensemble, 15 days, daily. Suits the next two weeks.
  - `ecmwf-extended-range` — ECMWF 46-day run, twice weekly. Suits a week 2–4 sub-seasonal briefing.
  - `bom` — BoM ACCESS-S1, daily.
- `--bias-corrected`, `--no-bias-corrected` — use CPC's bias-corrected diagram or the raw one. Default: `--bias-corrected`, which uses the raw diagram (with a stderr note) for `gefs-extended`, `cmc`, and `jma`, where CPC publishes only raw. `--no-bias-corrected` exits 2 for `cfs`, which is bias-corrected only.
- `--output`, `-o` — PNG output path.

### Output
A PNG with `weather_skills_history` in its metadata. stderr names the model, the variant (`bias-corrected` or `raw`), and the URL.

### Notes
- Only the latest forecast is available; CPC overwrites the same files. The init date is printed on the figure.
- The output is an image only; there are no RMM values or gridded fields.

### Example
```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py --model ecmwf-extended-range -o mjo_ecmwf_er.png
```
Writes the latest bias-corrected ECMWF extended-range MJO phase diagram.

## Details

Source page: https://www.cpc.ncep.noaa.gov/products/precip/CWlink/MJO/CLIVAR/clivar_wh.shtml. Images are `https://www.cpc.ncep.noaa.gov/products/precip/mjo/img/<FILE>`:

| `--model` | Bias-corrected | Raw |
|---|---|---|
| `gefs` | `GEFS_BC.png` | `GEFS.png` |
| `gefs-extended` | — | `GMON.png` |
| `cfs` | `NCFS.png` | — |
| `cmc` | — | `CANM.png` |
| `jma` | — | `JMAN.png` |
| `ecmwf` | `ECMF_BC.png` | `ECMF.png` |
| `ecmwf-extended-range` | `EMON_BC.png` | `EMON.png` |
| `bom` | `BOMM_BC.png` | `BOMM.png` |
