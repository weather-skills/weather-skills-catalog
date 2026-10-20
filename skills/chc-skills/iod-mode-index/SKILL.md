---
name: iod-mode-index
description: "Compute the Indian Ocean Dipole Mode Index (DMI) from a gridded temperature-anomaly Zarr, forecast or observed: the latitude-weighted mean anomaly over the western box minus the eastern box, plus each box mean. Use when a task needs an IOD index computed from model or observation fields, for every lead, time, or ensemble member in the input."
license: MIT
compatibility: Requires Python 3.12 and uv.
allowed-tools: Bash(uv run ${CLAUDE_SKILL_DIR}/scripts/iod.py *)
metadata:
  catalog-group: transforms
---

# iod-mode-index

Reduces a temperature-anomaly grid to the Dipole Mode Index and the two box means.

## Usage

```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/iod.py -i IN.zarr -v ANOMALY_VAR -o OUT.zarr
```

### Arguments
- `--input`, `-i` — gridded Zarr covering both boxes (bbox `15/45/-15/115` or wider).
- `--variable`, `-v` — the temperature anomaly variable, e.g. `ts_anomaly` or `sst_anomaly`. An unknown name exits 2 and lists the variables.
- `--output`, `-o` — output Zarr.

| Box | Longitude | Latitude | `N/W/S/E` |
|---|---|---|---|
| West | 50°E – 70°E | 10°S – 10°N | `10/50/-10/70` |
| East | 90°E – 110°E | 10°S – 0° | `0/90/-10/110` |

### Output
Latitude and longitude are reduced away; `step`, `time`, `number`, and other dims and non-spatial coords are kept. Other input variables are dropped.

| Variable | Meaning |
|---|---|
| `iod_mode_index` | west minus east |
| `west_indian_ocean_average_anomaly` | latitude-weighted mean over the west box |
| `east_indian_ocean_average_anomaly` | latitude-weighted mean over the east box |

Units are the input's (`degree_Celsius` if it has none): a `K` anomaly stays `K`.

### Notes
- The variable must already be an anomaly; the skill does not subtract a climatology. For raw SST, subtract a climatological mean first (field − climatology).
- To compare with BoM's DMI in `degree_Celsius`, convert a `K` input's units first.
- DMI values usually lie between −4 and +4; plot with a fixed symmetric range so leads and inits are comparable.

### Example
```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/iod.py -i subc_ts_30d.zarr -v ts_anomaly -o iod_subc_30d.zarr
```
Writes the DMI and box means in `K` for a CHC SubC 30-day surface-temperature outlook fetched over `15/45/-15/115`.
