---
name: ecmwf-04deg-fetch
description: "Fetch one CHC-standardized ECMWF 0.4 degree daily precipitation ensemble (101 members, 46 lead days, 60S-60N) from the private Sheerwater archive gs://sheerwater-datalake/ecmwf-0_4/forecast/, as a weather-skills forecast Zarr of daily rates. Reads only the chunks inside --bbox. Needs Google Cloud credentials with read access to that bucket. Use for higher-resolution ECMWF sub-seasonal precipitation than the 1.5 degree S2S feed. Licensed ECMWF data: never publish the raw fields."
license: MIT
compatibility: "Requires Python 3.12 and uv. Reads private GCS gs://sheerwater-datalake/ecmwf-0_4/forecast via gcsfs + zarr (h5netcdf for inits not yet converted). Requires Google Cloud credentials: GCLOUD_SERVICE_CREDENTIALS or NEURAL_GCM_SERVICE_CREDENTIALS (the raw service-account key JSON), GOOGLE_APPLICATION_CREDENTIALS (a service-account JSON file path), Application Default Credentials from `gcloud auth application-default login`, or a `gcloud auth login` session with the gcloud CLI on PATH."
allowed-tools: Bash(uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py *)
metadata:
  catalog-group: fetchers
  variables: "tp"
  required-env: "GCLOUD_SERVICE_CREDENTIALS"
---

# ecmwf-04deg-fetch

Opens one archived CHC 0.4 degree forecast, `gs://sheerwater-datalake/ecmwf-0_4/forecast/<YYYY>/pr_IFS-SUBS-0.4_<YYYYMMDD>.zarr` (or the `.daily.nc` CHC file for an init not yet converted), reads the `--bbox` cut-out of every requested lead day, and writes a weather-skills standard dataset Zarr.

## Usage

```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py [--date YYYY-MM-DD] [--bbox N/W/S/E] [--max-lead 46D] [-v tp] -o OUT.zarr
# Newest archived init date (no download)
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py --probe-latest
```

### Arguments
- `--date` — init date. Default: the newest archived init. CHC posts init X at ~21 UTC on day X, and the archive copies it at ~03 UTC on X+1.
- `--bbox` — `N/W/S/E` decimal degrees. Omit for the whole file (60S-60N, every longitude). A bbox crossing the antimeridian (W > E) is supported.
- `--variable`, `-v` — `tp` only (daily precipitation; `pr` and `precip` are accepted). Default `tp`. Anything else exits 2.
- `--max-lead` — keep the lead days whose 24 h interval ends by this lead (pandas timedelta: `14D`, `336h`). Default `46D`, the whole forecast. Must be whole days.
- `--probe-latest [IDENT]` — print the newest archived init date on stdout and exit. `IDENT` is ignored.
- `--output`, `-o` — output Zarr, overwritten.

### Output
- Dims `(number, step, latitude, longitude)`: 101 members numbered 0–100 (0 is the control), latitude ascending (south to north), longitude `[-180, 180)`, 0.4 degree spacing. Scalar `time` is the init (00 UTC, `standard_name: forecast_reference_time`), `valid_time = time + step`.
- `tp` is a rate in `mm day-1`: `step = N days` is the rain of the 24 h starting at init + N days (labelled at the interval start), `data_interval = 1 day`.
- `weather_skills_source = sheerwater-ecmwf-0_4:<YYYYMMDD>`; the `license` attr marks the data as licensed and private.

### Notes
- Each init is a ~2.1 GB zarr store chunked as (101 members, 46 leads, 25 lat, 25 lon) cells. The skill reads only the chunks the bbox touches, concurrently: a Kenya-sized box is 4 chunks (~23 MB) for all 46 lead days. Since every chunk holds all lead days, `--max-lead` does not reduce the download, and a global read downloads the whole store.
- An init with a complete zarr store (its root `zarr.json` is uploaded last) is read from it; otherwise the skill falls back to the archived `.daily.nc` (HDF5 chunks of one lead day each, read one after another over HTTP range requests, several times slower).
- The archive holds one init per CHC init from when archiving began (CHC itself keeps each file only ~2 weeks). Earlier inits are not available.
- This is licensed ECMWF data. Keep the Zarr and any raw-field plots private; derived products (probabilities, anomalies) follow your data agreement.

### Errors
- Exit 1 — no credentials, or access denied (the message lists the four ways to authenticate; nothing secret is printed); the init is not archived (the message gives the archived range); a transport failure while listing or reading (retry later).
- Exit 2 — an unknown `--variable`, a malformed or non-whole-day `--max-lead`, or a `--bbox` holding no grid cells.

### Example
```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py --bbox 5.5/33.5/-5/42.5 --max-lead 14D -o ecmwf04_kenya.zarr
```
Writes the newest init's daily precipitation rates for the first two weeks over Kenya, all 101 members at 0.4 degree.

## Details

**Weekly totals and maps.** The output is a standard forecast Zarr, so the usual chain applies: `aggregate-temporal --period "7 day"`, then `convert-to-totals`, then `summarize-dim --dim number --method mean` (or `exceedance-probability` on the totals with members kept), then `plot` or `plot-compare-forecasts`. For a KMSA-style Kenya map use `chc-plot-theme --theme kmsa` and the outline from `resolve-chc-region`.
