# CHC Skills

Climate Hazards Center (CHC) skills for SubC multi-model-ensemble forecasts,
Indian Ocean Dipole (IOD) analysis, CPC MJO forecast diagrams, and Africa ITF
position figures. Built on
[`weather-skills-core`](https://github.com/rhiza-research/weather-skills-core)
(pinned to `main`).

## Skills

| Skill | Role |
| --- | --- |
| [`subc-mme-fetch`](skills/subc-mme-fetch/) | Fetch and stitch SubC global MME mean + anomaly NetCDFs (7/15/30-day leads) into one forecast Zarr |
| [`iod-mode-index`](skills/iod-mode-index/) | Dipole Mode Index (West − East) plus west/east box means from a pre-computed temperature anomaly |
| [`mjo-forecast-fetch`](skills/mjo-forecast-fetch/) | Fetch the latest CPC CLIVAR MJO Wheeler–Hendon phase-space PNG (GEFS / ECMWF / ECMWF extended-range) |
| [`africa-itf`](skills/africa-itf/) | Fetch the latest NOAA/CPC Africa ITF / ITCZ position PNG for one region (`--location africa\|west-africa\|east-africa`; CPC uses both terms) |

## Quick start

```bash
uv sync --group dev
uv run pytest

# SubC MME for one init
uv run skills/subc-mme-fetch/scripts/fetch.py --date 2025-12-01 -o /tmp/subc.zarr

# IOD from a temperature-anomaly field (forecast or observations)
uv run skills/iod-mode-index/scripts/iod.py \
  -i /tmp/subc.zarr -v ts_anomaly -o /tmp/iod.zarr

# Latest CPC MJO phase-space diagram (GEFS, bias-corrected by default)
uv run skills/mjo-forecast-fetch/scripts/fetch.py --model gefs -o /tmp/mjo.png

# Latest CPC Africa ITF position map (default --location africa)
uv run skills/africa-itf/scripts/fetch.py -o /tmp/itf_africa.png
```

If anomalies are not already in the input, compute them first (climatology +
`difference` from forecasting-skills), then run `iod-mode-index`. To **view**
SST or precip over the Indian Ocean without computing the index, plot those
variables over an Indian Ocean bbox and optionally overlay the west/east
dipole boxes (see `iod-mode-index` SKILL.md).

## Install as a Claude plugin

```bash
./install_agent.sh
# or:
claude plugin marketplace add rhiza-research/chc-skills
claude plugin install rhiza-chc@chc-skills
```

## Layout

Same packaging model as
[`forecasting-skills`](https://github.com/rhiza-research/forecasting-skills):
canonical skills under `skills/<name>/`, Claude plugin via `.claude-plugin/` +
`agents/`, CLI via `skills-runner`.

See [CONTRIBUTING.md](CONTRIBUTING.md).
