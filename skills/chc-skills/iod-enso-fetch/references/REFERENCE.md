# iod-enso-fetch sources

## Indices

BoM page: https://www.bom.gov.au/climate/enso/ · NOAA CPC page: https://www.cpc.ncep.noaa.gov/data/indices/

| `--index` | Agency | Graph | Text series |
| --- | --- | --- | --- |
| `iod` | BoM | `https://www.bom.gov.au/clim_data/IDCK000072/iod1.png` | `https://www.bom.gov.au/clim_data/IDCK000072/iod_1.txt` |
| `enso`, `relative-nino3.4` | BoM | `https://www.bom.gov.au/clim_data/IDCK000072/rnino_3.4.png` | `https://www.bom.gov.au/clim_data/IDCK000072/rnino_3.4.txt` |
| `nino3.4` | BoM | `https://www.bom.gov.au/clim_data/IDCK000072/nino3_4.png` | `https://www.bom.gov.au/clim_data/IDCK000072/nino_3.4.txt` |
| `soi` | BoM | `https://www.bom.gov.au/clim_data/IDCKGSM000/soi30.png` | `https://www.bom.gov.au/clim_data/IDCKGSM000/soi.txt` |
| `roni` | NOAA CPC | none | `https://www.cpc.ncep.noaa.gov/data/indices/RONI.ascii.txt` |
| `cpc-relative-nino3.4-monthly` | NOAA CPC | none | `https://www.cpc.ncep.noaa.gov/data/indices/Rnino34.ascii.txt` |
| `cpc-relative-nino3.4-weekly` | NOAA CPC | none | `https://www.cpc.ncep.noaa.gov/data/indices/rel_wksst9120.txt` |

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

