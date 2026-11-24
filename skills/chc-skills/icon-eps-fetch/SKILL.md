---
name: icon-eps-fetch
description: "Fetch the DWD ICON-EPS global ensemble forecast (40 members, out to 7.5 days), regridded from ICON's triangular grid onto a regular lat/lon grid, as a weather-skills Zarr: precipitation, 2 m temperature and dewpoint, humidity, 10 m winds and gusts, cloud cover, surface pressure. Use for a one-week ensemble forecast to set beside ECMWF, GEFS or AIFS. Reads the gs://africa-forecasting-data archive first (past 00 UTC runs over Africa, so older forecasts are available), else downloads the run from DWD open data, which keeps only the last ~24 h. No credentials needed."
license: MIT
compatibility: Requires Python 3.12 and uv. Requires the eccodes library for cfgrib (bundled on Windows; `eccodeslib` is installed on macOS/Linux). Reads the public gs://africa-forecasting-data bucket anonymously (gcsfs), or downloads global GRIB2 files over HTTPS from opendata.dwd.de; no credentials required.
allowed-tools: Bash(uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py *)
metadata:
  catalog-group: fetchers
  variables: "precip t2m d2m r2 u10 v10 fg10 tcc sp"
---

# icon-eps-fetch

Writes one ICON-EPS run as a weather-skills standard dataset Zarr, every member on a regular lat/lon grid. The run is read from the public `gs://africa-forecasting-data` archive when the archive holds the whole request; otherwise it is downloaded from https://opendata.dwd.de/weather/nwp/icon-eps/grib/ and regridded from the triangular grid. The archive is what makes runs older than DWD's ~24 h window available.

## Usage

```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py [--date YYYY-MM-DD] [--source auto|archive|dwd] [--run 0|6|12|18] [--bbox N/W/S/E] [-v VAR ...] -o OUT.zarr
# Newest init date of the run on DWD (no download); add --source archive for the newest archived run
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py --probe-latest
```

### Arguments
- `--date` — init date. Default: the newest init of `--run` on DWD (with `--source archive`, the newest archived run). DWD keeps only ~24 h of runs; older 00 UTC runs over Africa come from the archive.
- `--source` — `auto` (default): use the archive when it holds the whole request, else DWD. `archive`: archive only, for past runs; a request the archive cannot serve exits instead of downloading. `dwd`: DWD open data only, never the archive.
- `--run` — init hour UTC: `0`, `6`, `12` or `18`. Default `0`. 00/12 runs reach 180 h, 06/18 runs 120 h.
- `--bbox` — `N/W/S/E` decimal degrees. Omit for the globe. DWD serves global files only, so a bbox saves memory, not download time.
- `--variable`, `-v` — one field per flag; repeat for several. Default `precip`. `precip`, `t2m`, `d2m`, `r2` (2 m relative humidity), `u10`, `v10`, `fg10` (DWD `vmax_10m`, maximum 10 m gust), `tcc` (total cloud cover), `sp`. `tp` and DWD folder names (`tot_prec`, `t_2m`, `td_2m`, `relhum_2m`, `u_10m`, `v_10m`, `vmax_10m`, `clct`, `ps`) are accepted. An unknown name exits 2 and lists `Available (most used first):`.
- `--max-lead` — last lead to keep (pandas timedelta: `168h`, `7D`). Default `168h`, one week.
- `--step-interval` — keep the published leads that are multiples of this (pandas timedelta). Default `24h`. Use `6h` for a sub-daily cycle of instantaneous fields.
- `--target-resolution` — output grid spacing in degrees. Default `0.25` (the native grid is ~20 km).
- `--regrid` — `linear` (default; barycentric over the triangles between cell centres) or `nearest` (each output point takes the nearest cell's value).
- `--workers` — parallel downloads. Default `4`; lower it if DWD throttles.
- `--probe-latest [IDENT]` — print the newest init date of `--run` on DWD on stdout and exit (with `--source archive`, the newest archived 00 UTC run of the first `-v`); stderr names the run. `IDENT` is ignored.
- `--output`, `-o` — output Zarr, overwritten.

### Output
- Dims `(number, step, latitude, longitude)`: 40 members numbered 1–40 (no control), latitude ascending, longitude `[-180, 180)`. Scalar `time` is the init.
- `precip` is a rate in `mm day-1` over `[step, next step)`, labelled at the interval start (`step=0` is 0–24 h with the default interval). The last lead has no interval, so it is dropped, for every field when `precip` is in the request.
- `t2m`, `d2m` are `degree_Celsius`; `sp` is `hPa`; `r2`, `tcc` are `%`; winds `m s-1`.
- `data_interval` is stamped for even leads; uneven leads (6-hourly to 120 h, then 12-hourly) get `step_bounds`. `weather_skills_source = dwd-icon-eps:<HH>/<YYYYMMDDHH>` from either source; `retrieved_from` names the archive store(s) or the DWD folder the run was read from. `license = CC BY 4.0; source: Deutscher Wetterdienst`.

### Archive
- `gs://africa-forecasting-data/data/<YYYY-MM-DD>/ICON_eps_<dwd folder>_<YYYY-MM-DD>.zarr`, written daily by the ECMWF-S2S4AFRICA pipeline from DWD open data: 00 UTC runs only, linear onto 0.25° over Africa (N/W/S/E `22.5/-21/-34.5/55.5`), 24-hourly `tot_prec` (stored as `tp`, written as `precip`) and 6-hourly instantaneous fields. It holds `tot_prec` daily from 2026-10-28; other variables are there only on dates the pipeline downloaded them.
- `auto` reads the archive only when all of these hold: `--run 0`, `--regrid linear`, `--target-resolution 0.25`, a `--bbox` inside the archive box, a store for every requested variable on that date, and every requested lead that DWD publishes present in the store (so `-v precip --step-interval 6h` goes to DWD). Otherwise it downloads from DWD, and stderr says why the archive was not used.
- An archived run gives the same output as the same request downloaded from DWD. A `--bbox` edge off the 0.25° lattice snaps inward to the archive points.
- Reading the archive takes seconds per variable, against the ~400 MB of global files a DWD week of `precip` downloads.

### Notes
- ICON-EPS is published only on ICON's triangular (icosahedral, R02B07) grid: 1,310,720 cells whose centres come from the run's `clat`/`clon` files. The skill regrids every field so the output works with every gridded skill. Linear interpolation smooths slightly and does not exactly conserve totals; `--regrid nearest` keeps native values. Output points outside the triangulation (the poles) take the nearest cell.
- Leads thin out with lead time: hourly to 48 h, 3-hourly to 72 h, 6-hourly to 120 h, then 12-hourly. Requested leads the server lacks are skipped and named on stderr.
- Each lead of each field is one global file, ~30–60 MB compressed. A week of `precip` at the default interval is 8 files (~400 MB); `--step-interval 6h` to 168 h is 25 files per field.
- Credit DWD when publishing figures or data (CC BY 4.0).

### Errors
- Exit 1 — the init is neither on DWD (older than ~24 h, or not yet published) nor readable from the archive; the message gives DWD's newest run and why the archive could not serve it (for an old date: pass a `--bbox` inside Africa with `--run 0`, or the date was never archived). With `--source archive`: no store for that date or variable, or the store lacks requested leads. A run still uploading has too few leads for the request. A transport failure after retries, from DWD (retry later or lower `--workers`) or from the bucket (retry later or use `--source dwd`).
- Exit 2 — unknown `--variable`, a malformed `--max-lead` / `--step-interval`, a non-positive `--target-resolution`, or a `--bbox` that holds no output points. With `--source archive`: `--run` other than 0, `--regrid nearest`, a `--target-resolution` other than 0.25, or no `--bbox` or one outside the archive box.

### Example
```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py --bbox 5.5/33.5/-5/42.5 -o icon_kenya.zarr
```
Writes daily ICON-EPS precip rates for the coming week over Kenya from the newest 00 UTC run, all 40 members at 0.25°.

```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/fetch.py --date 2026-10-30 --source archive --bbox 5.5/33.5/-5/42.5 -o icon_kenya_20261030.zarr
```
Writes the same week of forecasts from the archived 2026-10-30 00 UTC run.

## Details

**Weekly totals and maps.** The output is a standard forecast Zarr, so the usual chain applies: `aggregate-temporal --period "7 day"`, then `convert-to-totals`, then `summarize-dim --dim number --method mean` (or `exceedance-probability` on the totals with members kept), then `plot` or `plot-compare-forecasts`. For a KMSA-style Kenya map use `chc-plot-theme --theme kmsa` and the outline from `resolve-chc-region`.
