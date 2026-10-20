---
name: resolve-chc-region
description: "Resolve a bundled CHC region to an N/W/S/E bbox and optionally its boundary GeoJSON: the Tana River basin, Kenya's official KNSDI national outline and 47 county boundaries, the Kenya OND briefing box (central and eastern Kenya), and East Africa cut off at 15°S. Use whenever a task names the Tana River basin or catchment, needs Kenya's boundary or county lines on a map, or names the Kenya OND region or a CHC East Africa view."
license: MIT
compatibility: Requires Python 3.12 and uv.
allowed-tools: Bash(uv run ${CLAUDE_SKILL_DIR}/scripts/resolve.py *)
metadata:
  catalog-group: agent-tooling
---

# resolve-chc-region

Prints a region's bbox and writes its polygon, in the same form as weather-skills `resolve-region`.

## Usage

```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/resolve.py NAME [--geojson PATH] [--counties-geojson PATH]
```

### Arguments
- `NAME` — region name or alias, case-insensitive:

  | Region | Aliases | bbox `N/W/S/E` |
  |---|---|---|
  | `Tana River basin` | `Tana basin`, `Tana River catchment`, `Tana catchment` | `0.48/36.5972/-3.0402/41.5605` |
  | `Kenya` (KNSDI) | `KEN`, `Kenya KNSDI`, `Kenya counties` | `5.4141/33.9098/-4.7053/41.906` |
  | `Kenya OND region` (5°S–5°N, 36.5–42°E) | `Kenya OND`, `Kenya OND box`, `Kenya OND briefing box`, `OND Kenya`, `Central-Eastern Kenya`, `Central and Eastern Kenya`, `CE Kenya` | `5.0/36.5/-5.0/42.0` |
  | `East Africa` (south to 15°S) | — | `17.9983/21.9035/-15.0/51.1339` |
- `--geojson` — write the boundary as a one-feature `FeatureCollection`, for clipping or a plot mask or outline.
- `--counties-geojson` — Kenya only: write the 47 county polygons as one `FeatureCollection` (properties `county_index`, `source`; no county names), for a plot outline layer.

### Output
- stdout: `N/W/S/E` in decimal degrees.
- stderr: `Wrote boundary polygon: PATH` / `Wrote county boundaries: PATH`.
- `--geojson`: a Polygon (Kenya and East Africa: MultiPolygon) with properties `iso3`, `name`, `region_name`, `level` (`basin`, `country`, `custom`, or `region`), `country`, `source`, `bbox`, and `area_km2` for the basin and Kenya.

### Notes
- Bare `Tana River` exits 2: it could be the basin or Tana River County. For the county, use `resolve-region kenya-tana_river`; ask the user when unclear.
- Other names exit 2. Countries, single counties, multi-country regions, and landmarks go to `resolve-region`. Do not send a basin name there: it has no basins and its Nominatim fallback returns an unrelated one.
- The Tana outline (~126,000 km²) is larger than HydroBASINS (~95,250 km²), so basin totals differ from other basin products; say which outline you used.
- This `East Africa` stops at 15°S; `resolve-region "Eastern Africa"` is the full UN grouping down to Madagascar and Zimbabwe.

### Example
```bash
BBOX=$(uv run ${CLAUDE_SKILL_DIR}/scripts/resolve.py "Tana River basin" --geojson tana.json)
```
Sets `BBOX` to `0.48/36.5972/-3.0402/41.5605` and writes the basin polygon, ready to fetch over the bbox and clip with `tana.json`.

## Details

Each region is `data/<key>.geojson`, a one-feature FeatureCollection whose properties are what `--geojson` writes; the script adds only `bbox`. To add a region, add its file and list its names in `REGIONS` in `scripts/resolve.py`.

- **Tana River basin** (`tana_river_basin.geojson`): converted from a supplied `Tana Basin.shp` (WGS84) that came with no metadata, so the delineating agency is not recorded; `source` says "User-supplied". It reaches ~41.56°E along the Kenya–Somalia border. Rounded to 4 decimals (~11 m).
- **Kenya** (`kenya.geojson`, `kenya_counties.geojson`): from the Kenya National Spatial Data Infrastructure (KNSDI) `Kenya_Counties_KNSDI.shp`, which has no attribute table. Coverage-simplified at 0.001° (~100 m) so neighbouring counties still meet, rounded to 4 decimals. The national outline is the union of the counties without the sliver holes between them. `area_km2` (~591,500) is from the unsimplified source.
- **Kenya OND region** (`kenya_ond_region.geojson`): the plain rectangle used as the analog and verification box in CHC Kenya OND briefings.
- **East Africa** (`east_africa.geojson`): the Natural Earth 110m Eastern Africa subregion clipped at 15°S, a Greater Horn view without southern Madagascar, Mozambique, Malawi, Zambia, and Zimbabwe.
