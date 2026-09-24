---
name: iod-mode-index
description: Compute the Indian Ocean Dipole Mode Index (DMI) from a pre-computed temperature-anomaly field on any spatial gridded dataset (forecast or observations) — lat-weighted averages over the west and east IOD boxes, then west minus east, plus the two box means as separate variables. Use when a task needs the IOD index in degree C; do not use when you only want maps of SST or precip over the Indian Ocean.
license: MIT
compatibility: Requires Python 3.12 and uv.
allowed-tools: Bash(uv run ${CLAUDE_SKILL_DIR}/scripts/iod.py *)
metadata:
  catalog-group: transforms
---

# iod-mode-index

Computes the classic Indian Ocean **Dipole Mode Index (DMI)** from a
**pre-computed** temperature anomaly field:

\[
\mathrm{IOD} = \overline{T'}_{\mathrm{west}} - \overline{T'}_{\mathrm{east}}
\]

Works on forecasts (e.g. SubC MME `ts_anomaly` vs `step`) and observations /
analyses (anomaly vs `time`). Spatial dims are required (`Dataset("spatial")`);
other dims are preserved.

## Dipole boxes

| Region | Longitude | Latitude |
| --- | --- | --- |
| West Indian Ocean | 50°E – 70°E | 10°S – 10°N |
| East Indian Ocean | 90°E – 110°E | 10°S – 0° (equator) |

Bbox form `N/W/S/E`: west `10/50/-10/70`, east `0/90/-10/110`.

## When to use

- You already have (or will compute) a temperature **anomaly** field and need
  the scalar / lead-wise IOD index plus the two regional averages.

## When not to use / general guidance

1. **Anomalies must be pre-computed.** This skill does not build climatologies
   or subtract them. The `--variable` you pass must already be an anomaly
   (e.g. `ts_anomaly`, `sst_anomaly`).

2. **If the input lacks anomalies:** build a climatology for the same variable,
   then use the **`difference`** skill (field − climo). Optionally **`rename`**
   the result to `*_anomaly`, then run `iod-mode-index`. The same recipe works
   for forecasts and observations.

3. **If you only want to view IOD-relevant fields** (surface temperature,
   precipitation, etc.) over the Indian Ocean, you may **not** need this skill.
   Plot those variables over an Indian Ocean bounding box, and optionally
   overlay the west and east dipole zones as boxes on the figure using the
   coordinates in the table above.

## Usage

```
uv run ${CLAUDE_SKILL_DIR}/scripts/iod.py -i <input.zarr> -v <anomaly_var> -o <path.zarr>
```

### Arguments

- `--input`, `-i` — spatial gridded Zarr (forecast or observations).
- `--variable`, `-v` — name of the pre-computed temperature anomaly field.
- `--output`, `-o` — output Zarr path.

### Output

Dataset with three variables (units inherited from the anomaly field;
documented as degree C when the input is in °C/K-converted):

| Variable | Meaning |
| --- | --- |
| `iod_mode_index` | West − East Dipole Mode Index |
| `west_indian_ocean_average_anomaly` | Lat-weighted mean over the west box |
| `east_indian_ocean_average_anomaly` | Lat-weighted mean over the east box |

Lat/lon are collapsed; `step`, `time`, ensemble `number`, and other non-spatial
dims are kept.

## Plotting guidance

Typical DMI values fall roughly in **−4 to +4** (°C / K anomaly difference).
When plotting `iod_mode_index` (mediogram, timeseries, or heatmap of related
anomaly maps), prefer a fixed, symmetric color/axis range around that span
(e.g. `vmin=-4`, `vmax=4`) rather than autoscaling to the data min/max — that
keeps weak and strong events comparable across leads and inits.

## Example: SubC MME surface-temperature anomaly

```bash
uv run skills/subc-mme-fetch/scripts/fetch.py --date 2025-12-01 -v ts -o /tmp/subc_ts.zarr
uv run ${CLAUDE_SKILL_DIR}/scripts/iod.py -i /tmp/subc_ts.zarr -v ts_anomaly -o /tmp/iod.zarr
```

## Example: observations without anomalies

```bash
# Pseudocode pipeline with forecasting-skills helpers:
# 1) fetch SST observations → sst.zarr
# 2) reduce over a climatology window → sst_climo.zarr
# 3) difference sst.zarr − sst_climo.zarr → sst_anom.zarr (rename if needed)
uv run ${CLAUDE_SKILL_DIR}/scripts/iod.py -i /tmp/sst_anom.zarr -v sst_anomaly -o /tmp/iod_obs.zarr
```
