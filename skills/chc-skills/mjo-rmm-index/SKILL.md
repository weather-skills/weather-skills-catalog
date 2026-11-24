---
name: mjo-rmm-index
description: "Compute the Wheeler-Hendon RMM (Real-time Multivariate MJO) index, RMM1/RMM2 plus amplitude and phase, for every member of an ensemble forecast. It uses 15S-15N OLR and 850/200 hPa zonal wind, ERA5 EOFs (2006-2022) and a 120-day running mean from the public africa-forecasting-data bucket, with the axes rotated onto BoM's RMM. Use when a task needs an MJO forecast index or a phase diagram (feed the output to plot-rmm-phase)."
license: MIT
compatibility: Requires Python 3.12 and uv. Reads the public GCS bucket africa-forecasting-data (HTTPS), dynamical.org, and (with --bias-correct) Planette's public S3 store; no credentials required.
allowed-tools: Bash(uv run ${CLAUDE_SKILL_DIR}/scripts/mjo_rmm_index.py *)
metadata:
  catalog-group: transforms
---

# mjo-rmm-index

Projects a forecast's OLR and zonal winds onto the RMM EOFs, member by member.

## Usage

```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/mjo_rmm_index.py --olr OLR.zarr --wind U.zarr [-v VAR] [--wind-variable VAR] \
  [--wind-daily-mean] [--obs-days 40] [--bias-correct] [--no-align-bom] [--obs-source URL|DIR] -o OUT.zarr
```

### Arguments
- `--olr` — daily OLR forecast covering 15S-15N at every longitude on the 1.5° grid. Fetch it with `dynamical-fetch --dataset ecmwf-ifs-ens-forecast-46-day-daily-1-5-degree --bbox 15/-180/-15/180 -v net_long_wave_radiation_flux_top_of_atmosphere`.
- `--wind` — zonal wind forecast from the same init, with 850 and 200 hPa on a `vertical` dim. Fetch it from the same dataset with `-v u`.
- `--variable`, `-v` — OLR variable. Default: the first of `olr`, `net_long_wave_radiation_flux_top_of_atmosphere`, `ttr`, `top_net_thermal_radiation`, `mtnlwrf`.
- `--wind-variable` — wind variable. Default: the first of `wind_u`, `u`, `u_component_of_wind`.
- `--wind-daily-mean` — the wind steps are already daily means. By default they are taken as 00 UTC instants from step 0, and each consecutive pair is averaged into a day.
- `--obs-days` — observed days kept before the init. Default: `40`.
- `--bias-correct` — subtract each field's lead-dependent model bias, estimated from Planette ECMWF reforecasts 2006-2024.
- `--no-align-bom` — skip the rotation onto BoM's RMM1/RMM2. RMM1, RMM2 and the phases then do not follow Wheeler and Hendon (2004).
- `--obs-source` — where `era5_olr_<YYYY>.nc`, `era5_u_15S15N_<YYYY>.nc` and `rmm.74toRealtime.txt` live: a base URL or a local folder. Default: `https://storage.googleapis.com/africa-forecasting-data/era5`.
- `--output`, `-o` — output Zarr.

### Output
Time runs from `--obs-days` before the init to the last forecast day. `is_forecast(time)` marks the forecast days, and the attr `forecast_reference_time` holds the init.

| Variable | Dims | Meaning |
|---|---|---|
| `rmm1`, `rmm2` | time, number | per member (observed days are the same in every member) |
| `rmm1_mean`, `rmm2_mean` | time | ensemble mean |
| `amplitude_mean`, `phase_mean` | time | amplitude and phase (1-8) of the ensemble mean |
| `rmm1_bom`, `rmm2_bom` | time | BoM's RMM on the observed days (NaN elsewhere) |

The attrs also record `eof_explained_variance`, `normalisation`, and `bom_correlation` / `bom_rmse` (the EOF-period index against BoM's, typically about 0.95 correlation).

### Notes
- Steps:
  1. Each field is averaged over 15S-15N.
  2. The ERA5 annual cycle is removed, plus the lead bias with `--bias-correct`.
  3. The mean of the previous 120 days is removed. On forecast days that window is the observations followed by the member's own forecast.
  4. Each field is normalised by its overall std and projected on the two leading EOFs of the combined field.
  5. The axes are rotated (orthogonal Procrustes) onto BoM's RMM.
- Phases: 2-3 Indian Ocean, 4-5 Maritime Continent, 6-7 western Pacific, 8-1 Western Hemisphere and Africa.
- Days not yet in ERA5 are filled with ensemble-mean lead-1 forecasts from dynamical.org, at most 15 days. The ERA5 wind archive covers 2006 to 2023-01-10 and from 2026-03-20 on. Inits whose 160-day lead-in falls in that gap exit 1 and name the missing days.
- Performance: the first run downloads about 560 MB of ERA5 year files into `~/.cache/chc-skills/era5/`. The index itself takes under a minute. `--bias-correct` adds about 10-15 minutes of Planette reads (OLR plus u850/u200 for 19 reforecast years).

### Example
```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/mjo_rmm_index.py --olr olr_2026-10-10.zarr --wind u_2026-10-10.zarr -o rmm_2026-10-10.zarr
```
Writes RMM1/RMM2 for all 101 members of the 2026-10-10 forecast, plus the last 40 observed days.
