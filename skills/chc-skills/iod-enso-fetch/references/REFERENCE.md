# iod-enso-fetch sources

## Observations

Page: https://www.bom.gov.au/climate/enso/

| `--index` | Graph | Text series |
| --- | --- | --- |
| `iod` | `https://www.bom.gov.au/clim_data/IDCK000072/iod1.png` | `IDCK000072/iod_1.txt` |
| `enso`, `relative-nino3.4` | `https://www.bom.gov.au/clim_data/IDCK000072/rnino_3.4.png` | `IDCK000072/rnino_3.4.txt` |
| `nino3.4` | `https://www.bom.gov.au/clim_data/IDCK000072/nino3_4.png` | `IDCK000072/nino_3.4.txt` |
| `soi` | `https://www.bom.gov.au/clim_data/IDCKGSM000/soi30.png` | `IDCKGSM000/soi.txt` |

Text rows are `week_start,week_end,value` (`YYYYMMDD`). SOI is a 30-day rolling window updated daily. The Zarr `time` is the `week_end` column.

## ACCESS-S forecasts

Page: https://www.bom.gov.au/climate/ocean/outlooks/?index=iod#tabs=Graphs

Issue dates: `https://www.bom.gov.au/climate/ocean/outlooks/archive/archive_index.json`

```
https://www.bom.gov.au/climate/ocean/outlooks/archive/YYYYMMDD/plumes/sstOutlooks.<stem>.hr.png
https://www.bom.gov.au/climate/ocean/outlooks/archive/YYYYMMDD/plumes/sstOutlooks.<stem>.json
```

| `--index` | Stem |
| --- | --- |
| `iod` | `iod` |
| `enso` | `rnino34` from 2025-07-01; `nino34` before |
| `relative-nino3.4` | `rnino34` (2025-07-01 onward) |
| `nino3.4` | `nino34` |

The JSON's `data.mean` gives the monthly ensemble mean keyed by `Mon YYYY`; `data.frequency` gives the percent of members below, neutral, and above the event threshold.
