---
name: plot-wave-phase
description: "Render a local equatorial-wave phase PNG at one longitude from olr-wave-filter output: weekly or daily phase roses (a wind rose of the wave phase across ensemble members). Use when a briefing asks whether a Kelvin wave, ER wave or the MJO will bring wet or dry phases to a place such as Kenya."
license: MIT
compatibility: Requires Python 3.12 and uv.
allowed-tools: Bash(uv run ${CLAUDE_SKILL_DIR}/scripts/plot_wave_phase.py *)
metadata:
  catalog-group: figure
---

# plot-wave-phase

Draws the local phase of one filtered wave at one longitude, for every ensemble member.

## Usage

```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/plot_wave_phase.py -i WAVES.zarr [--wave kelvin|er|mjo|mrg_td|low] [--lon 37.5] \
  [--kind rose|rose-daily] [--days 20] [--label NAME] [--title TEXT] -o OUT.png
```

### Arguments
- `--input`, `-i` — `olr-wave-filter` output containing the wave.
- `--wave` — wave band. Default: `kelvin`.
- `--lon` — longitude in degrees east; the nearest grid column is used. Default: `37.5` (Kenya).
- `--kind` — default `rose`.
  - `rose`: one rose per forecast week, 6 weeks. Each bar is the share of members × days in a phase, stacked by amplitude class (1-1.5, 1.5-2, 2-3, > 3). The share with amplitude < 1 is printed as "weak".
  - `rose-daily`: one rose per forecast day. Suited to Kelvin waves, which pass in a few days.
- `--days` — number of daily roses for `rose-daily`. Default: `20`.
- `--label` — place name in the title, e.g. `Kenya`. Default: only the longitude.
- `--title` — figure title. Default: automatic.
- `--output`, `-o` — PNG path.

### Notes
- A is the 5S-5N filtered OLR anomaly at the point and B its time derivative.
- Phases run clockwise: P1 dry (A > 0, suppressed convection), P3 rising, P5 wet, P7 decaying. A passing wave turns clockwise through them. Amplitude 1 equals one seasonal ERA5 standard deviation.
- The phase scaling uses the `olr_<wave>_local_std_*` variables written by `olr-wave-filter`. A wave the input lacks exits 2.

### Example
```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/plot_wave_phase.py -i waves_2026-10-10.zarr --wave kelvin --kind rose --label Kenya -o kelvin_rose.png
uv run ${CLAUDE_SKILL_DIR}/scripts/plot_wave_phase.py -i waves_2026-10-10.zarr --wave kelvin --kind rose-daily --days 10 --label Kenya -o kelvin_rose_daily.png
```
The first command draws weekly Kelvin-wave phase roses at 37.5E for the 2026-10-10 forecast. The second draws one rose per day for the first 10 forecast days.
