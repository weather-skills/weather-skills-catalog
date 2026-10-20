---
name: analog-years
description: "Print the historical analog years for a date: past years whose large-scale climate state (El Niño / ENSO) resembles the target year. Use when a task needs analog years, \"years like this one\", or the list of years to build an analog-year composite of a season."
license: MIT
compatibility: Requires Python 3.12 and uv.
allowed-tools: Bash(uv run ${CLAUDE_SKILL_DIR}/scripts/analog_years.py *)
metadata:
  catalog-group: agent-tooling
---

# analog-years

Prints the analog years for the year of `--date`, ready to loop over in later fetches.

## Usage

```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/analog_years.py --date YYYY-MM-DD [--emit years|json]
```

### Arguments
- `--date` — absolute date `YYYY-MM-DD`. Only its year is used.
- `--emit` — stdout format: `years` (space-separated) or `json`. Default: `years`.

### Output
- `years`: `1982 1994 1997 2006 2015 2019 2023`
- `json`: `{"date": "2026-09-01", "year": 2026, "years": [1982, 1994, 1997, 2006, 2015, 2019, 2023]}`
- stderr: `year=2026`.

### Notes
- The years come from a fixed table. Only 2026 is in it (an El Niño analog set); any other year exits 2.
- Resolve relative dates ("this year") to an absolute date first.

### Example
```bash
for y in $(uv run ${CLAUDE_SKILL_DIR}/scripts/analog_years.py --date 2026-09-01); do echo "$y"; done
```
Loops over the seven 2026 analog years, e.g. to fetch the same season in each.
