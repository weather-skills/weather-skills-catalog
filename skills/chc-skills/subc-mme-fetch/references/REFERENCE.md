# SubC MME global archive

Primary source: public GCS mirror of the CHC SubC tree (faster/more reliable
than the origin server):

`gs://sheerwater-public-datalake/chc-mirror/experimental/SubC/`

HTTPS:

`https://storage.googleapis.com/sheerwater-public-datalake/chc-mirror/experimental/SubC/`

Paths mirror `data.chc.ucsb.edu/experimental/SubC/` exactly.

## Outlook folders

| `--outlook` | Folder | Filename lead tag | Lead (days) |
| --- | --- | --- | --- |
| `7d` | `07_day` | `7d` | 7 |
| `15d` | `15_day` | `15d` | 15 |
| `30d` | `30_day` | `30d` | 30 |

Each file is a single field for that outlook: a mean (temps) or sum (precip)
over the window from the init date through that lead (`window_start` /
`window_end` / `window_days` in the NetCDF attrs). The skill fetches **one**
outlook per call. Envelope `time` is the outlook valid date (init + lead
days); `step` is the outlook length; archive init is `initialization_date`.

## Global archive paths

```
{lead_folder}/global/archive/mme_{mean|anom}_{var}_{lead_tag}_{YYYYMMDD}.nc
```

Example object key:

`chc-mirror/experimental/SubC/07_day/global/archive/mme_mean_pr_7d_20251201.nc`

## Variables

`pr`, `tas`, `tasmax`, `tasmin`, `tdps`, `ts`

Each file holds one data variable named `mme_mean` or `mme_anom` on dims
`(Y, X)` with 1° coordinates. Global attrs include `window_start`,
`window_end`, `window_days`, and `operation` (`sum` for precip, `mean` for
temperatures).

Variable acronyms: `../variable_acronyms.txt` on the SubC root (origin site).

## Probe-latest

The GCS JSON API lists objects under
`chc-mirror/experimental/SubC/{lead_folder}/global/archive/` with prefix
`mme_mean_{var}_{lead_tag}_`. Prefer `--probe-latest <var>` so only one
variable's dates are scanned; omitting `VAR` requires a date present for all
six variables.
