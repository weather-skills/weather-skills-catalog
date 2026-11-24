---
name: plot-wave-hovmoller
description: "Render a time-longitude (Hovmöller) PNG of filtered OLR from olr-wave-filter. Styles: the NCICS-monitor look (MJO, Kelvin, ER and low-frequency contours over the OLR anomaly), wave-amplitude contours, multi-wave contour panels, or ensemble agreement on wave activity. Use when a briefing needs a tropical wave Hovmöller from a fresh forecast, with a region such as Kenya boxed."
license: MIT
compatibility: Requires Python 3.12 and uv.
allowed-tools: Bash(uv run ${CLAUDE_SKILL_DIR}/scripts/plot_wave_hovmoller.py *)
metadata:
  catalog-group: figure
---

# plot-wave-hovmoller

Draws one PNG from an `olr-wave-filter` Zarr: ERA5 above the "Begin forecast" line, the forecast below it.

## Usage

```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/plot_wave_hovmoller.py -i WAVES.zarr [--style ncics|amplitude|contours|probability] \
  [--member mean|control|N ...] [--waves W ...] [--lat-band 5] [--weeks-before 12] [--weeks-after N] \
  [--threshold T] [--highlight-lon 33/42] [--highlight-label Kenya] [--title TEXT] -o OUT.png
```

### Arguments
- `--input`, `-i` — `olr-wave-filter` output.
- `--style` — default `ncics`.
  - `ncics`: the NCICS monitor look. Brown/green 16 W m-2 shading of the unfiltered anomaly, with one ±16 W m-2 contour per wave (solid = enhanced convection, dashed = suppressed).
  - `amplitude`: the same shading, with contours of the Janiga wave amplitude at ±`--threshold`.
  - `contours`: wave pairs (MJO + Kelvin, ER + MRG/TD) contoured every 5 or 10 W m-2 over RdBu shading.
  - `probability`: one panel per wave. It shows the share of members with amplitude > `--threshold` in the enhanced or suppressed half (50/66/80% contours), plus the observed amplitude > 2 line.
- `--member` — `mean`, `control` or a member number; one panel each. Repeat for several. Default: `mean` and `control`. Ignored by `probability`, which uses every member.
- `--waves` — `low`, `mjo`, `kelvin`, `er`, `mrg_td`; repeat for several. Default: `kelvin er mjo low` for `ncics`/`amplitude`, `mjo kelvin er mrg_td` for `contours`, `kelvin er mjo` for `probability`. A requested wave missing from the input exits 2.
- `--lat-band` — average over `|lat| <=` this. Default: `5`, or `10` for `contours`. Amplitudes are scaled by 5S-5N ERA5 statistics.
- `--weeks-before` — observed weeks shown. Default: `12`.
- `--weeks-after` — forecast weeks shown. Default: the whole forecast. NCICS shows `4`.
- `--threshold` — wave amplitude threshold. Default: `2` for `amplitude`, `1` for `probability`.
- `--highlight-lon` — `W/E` longitudes boxed on every panel, or `none`. Default: `33/42` (Kenya).
- `--highlight-label` — text over the box. Default: `Kenya`.
- `--title` — figure title. Default: automatic.
- `--output`, `-o` — PNG path.

### Notes
- Longitudes run 0 to 360 (Africa on the left, the date line in the middle).
- Plotting reads only the selected latitude band. `probability` reads every member's band mean, which takes a few seconds per wave.

### Example
```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/plot_wave_hovmoller.py -i waves_2026-10-10.zarr --style ncics --weeks-after 4 -o hov_ncics.png
uv run ${CLAUDE_SKILL_DIR}/scripts/plot_wave_hovmoller.py -i waves_2026-10-10.zarr --style probability -o hov_probability.png
```
The first command draws ensemble-mean and control panels in the NCICS style: 12 weeks of ERA5 and 4 forecast weeks. The second shows ensemble agreement on Kelvin, ER and MJO activity.
