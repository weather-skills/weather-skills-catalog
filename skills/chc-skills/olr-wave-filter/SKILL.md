---
name: olr-wave-filter
description: "Filter an ensemble OLR forecast into convectively coupled equatorial waves: MJO, Kelvin, equatorial Rossby (ER), mixed Rossby-gravity / tropical depression (MRG/TD) and a low-frequency band, for every member. Uses the padded wavenumber-frequency method of Janiga et al. (2018) with a two-year ERA5 lead-in from the public africa-forecasting-data bucket. Use when a task needs tropical wave forecasts, Hovmöllers, or local wave phase/amplitude (feed the output to plot-wave-hovmoller or plot-wave-phase)."
license: MIT
compatibility: Requires Python 3.12 and uv. Reads the public GCS bucket africa-forecasting-data (HTTPS), dynamical.org, and (with --bias-correct) Planette's public S3 store; no credentials required.
allowed-tools: Bash(uv run ${CLAUDE_SKILL_DIR}/scripts/olr_wave_filter.py *)
metadata:
  catalog-group: transforms
---

# olr-wave-filter

Turns a daily ensemble OLR forecast into wave-filtered OLR anomalies, observed lead-in included.

## Usage

```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/olr_wave_filter.py -i OLR_FORECAST.zarr [-v VAR] [--waves W ...] \
  [--lat-max 15] [--leadin-days 730] [--window-before 90] [--bias-correct] [--obs-source URL|DIR] -o OUT.zarr
```

### Arguments
- `--input`, `-i` — one daily forecast at **every longitude** on the 1.5° grid. Fetch it with `dynamical-fetch --dataset ecmwf-ifs-ens-forecast-46-day-daily-1-5-degree --date D --bbox 30/-180/-30/180 -v net_long_wave_radiation_flux_top_of_atmosphere`. Steps must be consecutive days from step 0 (day 1).
- `--variable`, `-v` — OLR variable. Default: the first of `olr`, `net_long_wave_radiation_flux_top_of_atmosphere`, `ttr`, `top_net_thermal_radiation`, `mtnlwrf`. Units must be `W m-2`. A net (negative-upward) flux is sign-flipped to OLR.
- `--waves` — `low`, `mjo`, `kelvin`, `er`, `mrg_td`; repeat for several. Default: all five.
- `--lat-max` — keep `|lat| <=` this many degrees. Default: `15`. Must be at least 5.
- `--leadin-days` — ERA5 days padded in front of the forecast. Default: `730`. Must be at least 365 + `--window-before`.
- `--window-before` — observed days kept in the output before the init. Default: `90`.
- `--bias-correct` — subtract a lead-dependent model bias. It is estimated from Planette ECMWF reforecasts (2006-2024, near the init's calendar day), with ERA5 as a control variate and smoothing over leads.
- `--obs-source` — where the ERA5 archive (`era5_olr_<YYYY>.nc`) lives: a base URL or a local folder. Default: `https://storage.googleapis.com/africa-forecasting-data/era5`.
- `--output`, `-o` — output Zarr.

| Wave | Zonal wavenumber (+ east) | Period (days) | Extra limit |
|---|---|---|---|
| `low` | -10..10 | 100..1461 | no high-pass |
| `mjo` | 0..9 | 20..100 | |
| `kelvin` | 1..14 | 2.5..20 | Kelvin dispersion, h = 8-90 m |
| `er` | -10..-1 | 10..100 | n=1 ER dispersion, h = 8-90 m |
| `mrg_td` | -20..0 | 2.5..10 | |

### Output
The time axis runs from `--window-before` days before the init to the last forecast day. A boolean `is_forecast(time)` marks the forecast days, and the attr `forecast_reference_time` holds the init. Observed days are the same in every member.

| Variable | Dims | Meaning |
|---|---|---|
| `olr_anomaly` | number, time, lat, lon | unfiltered anomaly from the ERA5 2006-2022 annual cycle, in `W m-2` |
| `olr_<wave>` | number, time, lat, lon | wave-filtered anomaly in `W m-2`. Attrs give the band and the all-longitude ERA5 std of A and B (`amplitude_std_a`, `amplitude_std_b`). |
| `olr_<wave>_obs_only` | time, lat, lon | filtered from observations then zeros: the no-forecast baseline |
| `olr_<wave>_local_std_a`, `_local_std_b` | lon | seasonal ERA5 std of the 5S-5N filtered anomaly A and of its time derivative B |
| `olr_<wave>_clim_amp_p90`, `_p99` | phase, lon | seasonal ERA5 90th and 99th percentiles of the local amplitude in each phase |
| `olr_<wave>_clim_amp_p90_all` | lon | seasonal ERA5 90th percentile of the local amplitude |

### Notes
- Steps:
  1. Anomalies are taken from a mean + 4-harmonic ERA5 annual cycle.
  2. Each band's running-mean high-pass is applied.
  3. The first lead-in year is tapered to zero.
  4. The series is zero-padded to 1461 days.
  5. A 2-D FFT over (time, longitude) is masked to the band.

  The method is linear, so the mean of `olr_<wave>` over `number` is the filtered ensemble mean.
- Days not yet in ERA5 (ERA5T runs about 5 days behind, and the bucket archive is topped up daily) are filled with the ensemble-mean lead-1 forecasts from dynamical.org. The attr `era5t_gap_days` gives how many. At most 15 days are filled. For a later init the run exits 1 and names the newest usable init date.
- The forecast must be on the 1.5° grid of the ERA5 archive. Otherwise the run exits 2 and points at `coarsen`.
- Performance:
  - The first run downloads about 550 MB of ERA5 year files into `~/.cache/chc-skills/era5/`. Later runs check the files and reuse them.
  - Filtering 101 members takes about 5 minutes.
  - At `--lat-max 15` the output is about 1.7 GB in memory and on disk.
  - `--bias-correct` adds a Planette read of a few minutes.
- Errors:
  - A missing archive year or a lead-in hole exits 1 and names the dates.
  - A forecast with NaNs (still publishing) exits 1.
  - A regional bbox, non-daily steps or a non-flux unit exits 2.

### Example
First fetch the forecast with the `dynamical-fetch` skill: `--dataset ecmwf-ifs-ens-forecast-46-day-daily-1-5-degree --date 2026-10-10 --bbox 30/-180/-30/180 -v net_long_wave_radiation_flux_top_of_atmosphere -o olr_2026-10-10.zarr`. Then:
```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/olr_wave_filter.py -i olr_2026-10-10.zarr --bias-correct -o waves_2026-10-10.zarr
```
This writes every member's MJO, Kelvin, ER, MRG/TD and low-frequency filtered OLR, 15S-15N, from 90 days before 2026-10-10 to 46 days after.
