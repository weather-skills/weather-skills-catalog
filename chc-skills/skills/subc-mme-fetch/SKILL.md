---
name: subc-mme-fetch
description: Fetch one CHC SubC multi-model-ensemble (MME) outlook — 7d, 15d, or 30d — for selected climate variables (pr/tas/tasmax/tasmin/tdps/ts mean + anomaly) from the public global archive into a weather-skills forecast Zarr. Use when a task needs a single SubC MME outlook map for clipping, comparison, or plotting; use --probe-latest with a variable to find the latest published init.
license: MIT
compatibility: Requires Python 3.12 and uv. Fetches over HTTPS from the public GCS mirror gs://sheerwater-public-datalake/chc-mirror (SubC layout matches data.chc.ucsb.edu); no credentials required.
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

Downloads **one** CHC SubC **global** multi-model-ensemble outlook for one
initialization date and writes a weather-skills forecast envelope Zarr.

Outlooks are separate forecasts — pick exactly one with `--outlook`:

| `--outlook` | Archive folder | Window |
| --- | --- | --- |
| `7d` | `07_day` | init → init+7d |
| `15d` | `15_day` | init → init+15d |
| `30d` | `30_day` | init → init+30d |

Source layout (GCS mirror of the CHC SubC archive):

```
gs://sheerwater-public-datalake/chc-mirror/experimental/SubC/{07_day|15_day|30_day}/global/archive/
  mme_mean_{var}_{7d|15d|30d}_{YYYYMMDD}.nc
  mme_anom_{var}_{7d|15d|30d}_{YYYYMMDD}.nc
```

HTTPS equivalent:

```
https://storage.googleapis.com/sheerwater-public-datalake/chc-mirror/experimental/SubC/...
```

Each NetCDF is a single 2D window field. This skill fetches mean + anomaly for
the selected climate variables at **one** outlook only (not all three leads).

## When to use

- Need a SubC MME mean and/or anomaly map for one outlook length on the global
  1° grid.
- Downstream clipping, aggregation, comparison, or plotting.

Prefer other fetchers when you need daily steps, per-model members, or a
non-SubC source.

## Usage

```
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py --date YYYY-MM-DD --outlook 7d|15d|30d \
    [--bbox N/W/S/E] [-v VAR ...] [--workers N] -o <path.zarr>

uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py --outlook 7d|15d|30d \
    --probe-latest [VAR]
```

### Arguments

- `--date` — archive init date `YYYY-MM-DD` (required unless probing). Matches
  the archive filename date / `window_start`.
- `--outlook` — required. One of `7d`, `15d`, `30d`. Selects which SubC
  outlook forecast to fetch (three separate products; one per call).
- `--bbox` — optional spatial subset `N/W/S/E`.
- `--variable`, `-v` — climate variables (repeatable). Allowed: `pr`, `tas`,
  `tasmax`, `tasmin`, `tdps`, `ts`. Default: all six. For each selected
  variable both mean and anomaly are fetched.
- `--workers` — concurrent remote opens (default 4).
- `--output`, `-o` — output Zarr path (not needed with `--probe-latest`).
- `--probe-latest [VAR]` — print the latest available init `YYYY-MM-DD` for
  this `--outlook` on stdout and exit. **Pass a climate variable** (e.g.
  `--probe-latest ts`) unless you truly need the latest date common to
  **all** six variables — probing every variable is slower and stricter.
  Optional `VAR` must be one of the climate variables above. Does not
  download fields.

### Output

Classic single-lead forecast envelope:

- scalar `time` — outlook **valid date** = `--date` (archive init) **+**
  outlook days (e.g. `--date 2025-12-01 --outlook 7d` → `time` is
  `2025-12-08`)
- `step` — one value, the outlook length (`7`, `15`, or `30` days as
  `timedelta64`)
- archive init kept as attrs `initialization_date` and
  `outlook_valid_date` (same as `time`)
- `latitude`, `longitude` — 1° grid (or bbox subset)
- data variables: bare name for the MME mean (`pr`, `tas`, …) and
  `{var}_anomaly` for the anomaly (`pr_anomaly`, `tas_anomaly`, …)

Precipitation means are **window sums** in `mm` (not rates). Temperature-family
fields are window means (source units typically `K`; converted to standard
display units when classified).

Stamped with `weather_skills_source=chc-subc-mme` and `outlook=7d|15d|30d`.

### Provenance

The decorator stamps `weather_skills_history` on write. Inspect with the
`provenance` skill when available.

## Examples

```bash
# Latest init for the 7-day ts outlook (prefer a variable on probe-latest)
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py --outlook 7d --probe-latest ts

# Fetch that 7-day outlook for ts + pr
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py --date 2025-12-01 --outlook 7d \
    --bbox 20/30/-20/120 -v ts -v pr -o /tmp/subc_7d.zarr

# Separate 30-day outlook fetch
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py --date 2025-12-01 --outlook 30d \
    -v ts -o /tmp/subc_30d.zarr
```
