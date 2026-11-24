---
name: plot-rmm-phase
description: "Render a Wheeler-Hendon RMM phase-space diagram PNG of an MJO forecast from mjo-rmm-index output. It shows ensemble-member dots on chosen forecast days, the ensemble-mean track with weekly markers, the recent observed track, and BoM's RMM for comparison. Use when a briefing needs a freshly computed MJO phase diagram rather than the published CPC figure."
license: MIT
compatibility: Requires Python 3.12 and uv.
allowed-tools: Bash(uv run ${CLAUDE_SKILL_DIR}/scripts/plot_rmm_phase.py *)
metadata:
  catalog-group: figure
---

# plot-rmm-phase

Draws the RMM1/RMM2 phase diagram of an `mjo-rmm-index` Zarr.

## Usage

```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/plot_rmm_phase.py -i RMM.zarr [--member-days 1,5,10,15,20] [--obs-days N] \
  [--no-bom] [--title TEXT] -o OUT.png
```

### Arguments
- `--input`, `-i` — `mjo-rmm-index` output.
- `--member-days` — comma-separated forecast days whose members are drawn as dots; day 1 is the init day. Default: `1,5,10,15,20`. Days outside the forecast exit 2.
- `--obs-days` — observed days drawn before the init. Default: all in the input (40 from `mjo-rmm-index`).
- `--no-bom` — leave out the dashed BoM track.
- `--title` — figure title. Default: automatic.
- `--output`, `-o` — PNG path.

### Notes
- Inside the unit circle the MJO is weak.
- Phases run anticlockwise from 1 (West. Hem. and Africa) through 2-3 (Indian Ocean), 4-5 (Maritime Continent) and 6-7 (western Pacific) to 8.
- The axis limit grows past 3 when members reach further out.

### Example
```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/plot_rmm_phase.py -i rmm_2026-10-10.zarr -o mjo_phase_2026-10-10.png
```
Draws the 2026-10-10 forecast with members on days 1, 5, 10, 15 and 20, and the last 40 observed days.
