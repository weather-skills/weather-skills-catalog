---
name: subc-mme-fetch
description: "Fetch one CHC SubC multi-model-ensemble outlook (7-, 15-, or 30-day) for an init date as a weather-skills forecast Zarr: the ensemble mean and anomaly of precipitation, near-surface air, max, min, dewpoint, or surface temperature on a global 1° grid, optionally cut to a bbox. Use when a task needs a CHC SubC outlook map or its anomalies for one window."
license: MIT
compatibility: Requires Python 3.12 and uv. Fetches the public GCS mirror first (gs://sheerwater-public-datalake/chc-mirror), then falls back to data.chc.ucsb.edu. No credentials required.
allowed-tools: Bash(uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py *)
metadata:
  catalog-group: fetchers
  availability:
    shape: date
    policy: none
    lag_days: 0
    note: CHC SubC experimental global MME archive; coverage depends on published init dates per outlook
---

# subc-mme-fetch

Downloads the mean and anomaly fields of one SubC MME outlook and writes them as a single-lead forecast Zarr.

## Usage

```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py --date YYYY-MM-DD --outlook 7d|15d|30d [--bbox N/W/S/E] [-v VAR ...] [--workers N] -o OUT.zarr
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py --outlook 7d|15d|30d --probe-latest [VAR]
```

### Arguments
- `--date` — init date, as in the archive filename. Required unless probing.
- `--outlook` — window length: `7d`, `15d`, or `30d`. Each is a separate forecast; one per call. Required.
- `--bbox` — `N/W/S/E`. Keeps every 1° cell that overlaps the box, including cells whose centre is just outside. Default: global.
- `--variable`, `-v` — `pr` (precipitation), `tas` (near-surface air temperature), `tasmax`, `tasmin`, `tdps` (dewpoint), `ts` (surface temperature); repeat for several. Default: all six.
- `--workers` — concurrent downloads, at least 1. Default: 4.
- `--probe-latest` — print the latest init for this `--outlook` (or `none`) and exit, no `-o`. With `VAR`, checks that variable only; without, the latest init common to all six (slower). stderr gives the init's age and warns when its window has already ended.
- `--output`, `-o` — output Zarr.

### Output
- Dims `(step, latitude, longitude)`, `step = 0 days`, scalar `time` = init. The window runs from the init for `aggregation_period` (`7 day`, `15 day`, `30 day`), e.g. `--date 2025-12-01 --outlook 7d` covers 2025-12-01 to 2025-12-08.
- Variables: the ensemble mean under the bare name (`pr`, `ts`, …) and `{var}_anomaly`.
- `pr` and `pr_anomaly` are window totals in `mm` (`cell_methods: time: sum`). Temperatures are window means (`time: mean`): `tas`, `tasmax`, `tasmin` in `degree_Celsius`, `ts` and `tdps` in `K`; every temperature anomaly is a difference in `delta_degC`.
- Attrs: `outlook`, `initialization_date`, `outlook_valid_date` (window end), `weather_skills_source = chc-subc-mme`.

### Notes
- SubC publishes irregularly: the latest init can be weeks old with its window already over. Probe first and check the window covers the period you need.
- `pr` is already a total; do not convert it to totals again.
- To mask to a country or basin, clip with its polygon after fetching.

### Example
```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py --date 2025-12-01 --outlook 7d --bbox 5.5/33.5/-5/42.5 -v pr -o subc_7d_kenya.zarr
```
Writes the 7-day precipitation total and anomaly from the 2025-12-01 init over Kenya on 1° cells.

## Details

Archive layout and the mirror / origin fallback: [references/REFERENCE.md](references/REFERENCE.md).
