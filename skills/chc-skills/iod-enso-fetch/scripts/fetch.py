# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "weather-skills-core>=0.4.0",
#   "numpy",
#   "pillow>=10",
#   "xarray",
# ]
# ///
"""Download BoM and NOAA CPC IOD / ENSO index values or graphs, or BoM ACCESS-S outlooks."""

from __future__ import annotations

import io
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, date, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

import numpy as np
import xarray as xr
from weather_skills_core import DataError, UsageError, weather_skill
from weather_skills_core.cf import stamp_cf_coords
from weather_skills_core.units import stamp_data_interval

# Auto-populated by the version-bump CI workflow. Do not edit manually.
_SKILL_VERSION = "0.0.4"

BOM = "https://www.bom.gov.au/clim_data"
CPC = "https://www.cpc.ncep.noaa.gov/data/indices"
OBS_PAGE = "https://www.bom.gov.au/climate/enso/"
CPC_PAGE = f"{CPC}/"
FORECAST_PAGE = "https://www.bom.gov.au/climate/ocean/outlooks/?index=iod#tabs=Graphs"
FORECAST_ARCHIVE = "https://www.bom.gov.au/climate/ocean/outlooks/archive"
ARCHIVE_INDEX_URL = f"{FORECAST_ARCHIVE}/archive_index.json"
_HTTP_TIMEOUT = 60

# BoM Akamai returns 403 for library-style User-Agents (e.g. "chc-skills/…").
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

_RNINO = "Relative Niño3.4 sea surface temperature anomaly"
# --index: (graph URL, or None for CPC text-only series; text series URL; variable; units;
# long name). BoM overwrites its files in place; each holds the full history.
INDICES: dict[str, tuple[str | None, str, str, str, str]] = {
    "iod": (
        f"{BOM}/IDCK000072/iod1.png",
        f"{BOM}/IDCK000072/iod_1.txt",
        "iod_mode_index",
        "degree_Celsius",
        "Indian Ocean Dipole Mode Index (west minus east SST anomaly)",
    ),
    "enso": (
        f"{BOM}/IDCK000072/rnino_3.4.png",
        f"{BOM}/IDCK000072/rnino_3.4.txt",
        "relative_nino34",
        "degree_Celsius",
        _RNINO,
    ),
    "relative-nino3.4": (
        f"{BOM}/IDCK000072/rnino_3.4.png",
        f"{BOM}/IDCK000072/rnino_3.4.txt",
        "relative_nino34",
        "degree_Celsius",
        _RNINO,
    ),
    "nino3.4": (
        f"{BOM}/IDCK000072/nino3_4.png",
        f"{BOM}/IDCK000072/nino_3.4.txt",
        "nino34",
        "degree_Celsius",
        "Traditional Niño3.4 sea surface temperature anomaly",
    ),
    "soi": (
        f"{BOM}/IDCKGSM000/soi30.png",
        f"{BOM}/IDCKGSM000/soi.txt",
        "soi",
        "1",
        "30-day Troup Southern Oscillation Index",
    ),
    "roni": (
        None,
        f"{CPC}/RONI.ascii.txt",
        "roni",
        "degree_Celsius",
        "Relative Oceanic Niño Index (3-month running relative Niño3.4 anomaly)",
    ),
    "cpc-relative-nino3.4-monthly": (
        None,
        f"{CPC}/Rnino34.ascii.txt",
        "relative_nino34",
        "degree_Celsius",
        f"NOAA CPC monthly {_RNINO}",
    ),
    "cpc-relative-nino3.4-weekly": (
        None,
        f"{CPC}/rel_wksst9120.txt",
        "relative_nino34",
        "degree_Celsius",
        f"NOAA CPC weekly {_RNINO}",
    ),
}
MONTH = np.timedelta64(1, "M")
RONI_SEASONS = ("DJF", "JFM", "FMA", "MAM", "AMJ", "MJJ", "JJA", "JAS", "ASO", "SON", "OND", "NDJ")

# ACCESS-S plume / JSON region token (prefix ``r`` added for operational Niño).
FORECAST_REGIONS = {
    "iod": "iod",
    "enso": "nino34",
    "nino3.4": "nino34",
    "relative-nino3.4": "nino34",
}
# BoM switches Niño outlook plumes to Relative Niño filenames on this issue date.
RNINO_START = date(2025, 7, 1)

_FIGURE_SUFFIXES = {".png", ".jpg", ".jpeg"}
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _read_url(url: str, *, referer: str, accept: str) -> tuple[bytes, str]:
    """GET with browser headers and a cache-buster query, as the BoM pages do."""
    headers = {"User-Agent": _USER_AGENT, "Referer": referer, "Accept": accept}
    req = urllib.request.Request(f"{url}?{int(time.time())}", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            data = resp.read()
            content_type = resp.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        raise DataError(f"download failed for {url!r}: HTTP {exc.code} {exc.reason}") from None
    except urllib.error.URLError as exc:
        raise DataError(f"download failed for {url!r}: {exc.reason}") from None
    if not data:
        raise DataError(f"download of {url!r} returned an empty body.")
    return data, content_type


def _archive_dates() -> list[date]:
    """Sorted ACCESS-S outlook issue dates from the BoM archive index."""
    raw, _ = _read_url(
        ARCHIVE_INDEX_URL, referer=FORECAST_PAGE, accept="application/json,text/plain,*/*"
    )
    try:
        rows = json.loads(raw)["archive_index"]["data"]["index"]
        dates = sorted(
            date.fromisoformat(str(row["init_date"]))
            for row in rows
            if isinstance(row, dict) and row.get("init_date")
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise DataError(f"BoM outlook archive_index.json is malformed: {exc!r}") from exc
    if not dates:
        raise DataError("BoM outlook archive_index.json listed no init dates.")
    return dates


def _obs_dataset(index: str, text: str) -> xr.Dataset:
    """Parse an observation text series into a ``(time)`` dataset."""
    if text.lstrip().startswith("<"):
        raise DataError(f"--index {index} download returned HTML, not the text series.")
    times, values, bounds = [], [], []
    for line_no, line in enumerate(text.splitlines(), start=1):
        parts = line.replace(",", " ").split()
        # Data rows start with a season name (RONI) or a digit; skip titles and headers.
        if not parts or not (parts[0] in RONI_SEASONS or parts[0][0].isdigit()):
            continue
        try:
            if index == "roni":  # SEAS YR ANOM; DJF of year Y runs Dec Y-1 to Feb Y
                start = (
                    np.datetime64(f"{parts[1]}-01", "M")
                    + (RONI_SEASONS.index(parts[0]) - 1) * MONTH
                )
                times.append(start + MONTH)
                bounds.append((start, start + 3 * MONTH))
                value = parts[2]
            elif index == "cpc-relative-nino3.4-monthly":  # YR MTH ANOM
                start = np.datetime64(f"{int(parts[0]):04d}-{int(parts[1]):02d}", "M")
                times.append(start)
                bounds.append((start, start + MONTH))
                value = parts[2]
            elif index == "cpc-relative-nino3.4-weekly":  # DDMONYYYY n12 n3 n34 n4
                week = datetime.strptime(parts[0], "%d%b%Y").replace(tzinfo=UTC)
                times.append(np.datetime64(week.date().isoformat()))
                value = parts[3]
            else:  # BoM: week_start,week_end,value (YYYYMMDD); keep week_end
                end = parts[1]
                times.append(np.datetime64(f"{end[:4]}-{end[4:6]}-{end[6:8]}"))
                value = parts[2]
            values.append(float(value))
        except (ValueError, IndexError) as exc:
            raise DataError(f"--index {index} line {line_no} is not a data row: {line!r}") from exc
    if not times:
        raise DataError(f"--index {index} text series listed no values.")

    graph, _, name, units, long_name = INDICES[index]
    data = np.asarray(values, dtype=np.float64)
    data[data <= -99] = np.nan  # CPC marks missing as -99.9
    ds = xr.Dataset({name: ("time", data)}, coords={"time": np.asarray(times, "datetime64[ns]")})
    ds[name].attrs.update(long_name=long_name, units=units)
    if index != "soi":
        ds[name].attrs["standard_name"] = "sea_surface_temperature_anomaly"
    ds[name].attrs["comment"] = {
        "soi": "30-day rolling Troup SOI; time is the window end date.",
        "roni": "Overlapping 3-month seasons; time is the middle month, time_bounds the season.",
        "cpc-relative-nino3.4-monthly": "Monthly ERSSTv5 index; time is the first of the month.",
        "cpc-relative-nino3.4-weekly": "Weekly OISST index; time is the week's centre date.",
    }.get(index, "Weekly index; time is the week-ending date.")
    stamp_cf_coords(ds)
    ds.attrs.update(Conventions="CF-1.13", weather_skills_source="bom" if graph else "noaa-cpc")
    if bounds:
        # Months vary in length and RONI seasons overlap: time_bounds, not data_interval.
        ds = ds.assign_coords(time_bounds=(("time", "nv"), np.asarray(bounds, "datetime64[ns]")))
        ds["time"].attrs["bounds"] = "time_bounds"
        return ds
    return stamp_data_interval(ds, period="1 day" if index == "soi" else "7 day")


def _forecast_dataset(index: str, issue: date, payload: dict) -> xr.Dataset:
    """ACCESS-S monthly ensemble mean and below / neutral / above percentages."""
    months, means = [], []
    probs = {"below": [], "neutral": [], "above": []}
    try:
        frequency = payload["data"]["frequency"]
        for label, mean in payload["data"]["mean"].items():
            month = datetime.strptime(label.strip(), "%b %Y").replace(tzinfo=UTC)
            months.append(np.datetime64(f"{month:%Y-%m}", "M"))
            means.append(float(mean))
            # Keys look like "below −0.4", "neutral", "above 0.4".
            row = {key.split()[0].lower(): float(v) for key, v in frequency[label].items()}
            for bucket, column in probs.items():
                column.append(row[bucket])
    except (KeyError, TypeError, ValueError, AttributeError, IndexError) as exc:
        raise DataError(f"ACCESS-S outlook JSON is malformed: {exc!r}") from exc
    if not months:
        raise DataError("ACCESS-S outlook JSON listed no monthly mean values.")

    _, _, name, units, long_name = INDICES[index]
    months = np.asarray(months)
    ds = xr.Dataset(
        {name: ("time", np.asarray(means, dtype=np.float64))}
        | {
            f"prob_{bucket}": ("time", np.asarray(col, dtype=np.float64))
            for bucket, col in probs.items()
        },
        coords={
            "time": months.astype("datetime64[ns]"),
            "init_time": np.datetime64(issue.isoformat(), "ns"),
            "time_bounds": (
                ("time", "nv"),
                np.stack([months, months + MONTH], axis=1).astype("datetime64[ns]"),
            ),
        },
    )
    ds[name].attrs.update(
        long_name=f"ACCESS-S ensemble-mean {long_name}",
        units=units,
        standard_name="sea_surface_temperature_anomaly",
        comment="Monthly ensemble mean from the BoM outlooks JSON, not the 99-member plume.",
    )
    stamp_cf_coords(ds)
    for bucket, phrase in (
        ("below", "below the event threshold"),
        ("neutral", "in the neutral range"),
        ("above", "above the event threshold"),
    ):
        ds[f"prob_{bucket}"].attrs.update(
            long_name=f"ACCESS-S ensemble percent {phrase}", units="percent"
        )
    ds["init_time"].attrs.update(
        standard_name="forecast_reference_time", long_name="ACCESS-S outlook issue date"
    )
    # Month lengths vary; CF time_bounds describe the cells, so no scalar data_interval.
    ds["time"].attrs["bounds"] = "time_bounds"
    ds.attrs.update(Conventions="CF-1.13", weather_skills_source="bom")
    return ds


@weather_skill(name="iod-enso-fetch", version=_SKILL_VERSION)
@weather_skill.argument(
    "--index",
    required=True,
    choices=list(INDICES),
    help=(
        "Index: iod (weekly DMI), enso / relative-nino3.4 "
        "(operational Relative Niño3.4), nino3.4 (traditional), or soi "
        "(30-day SOI; observation only), or roni (NOAA CPC 3-month Relative "
        "Oceanic Niño Index from 1950), cpc-relative-nino3.4-monthly (NOAA CPC "
        "monthly relative Niño3.4 from 1949-12), or cpc-relative-nino3.4-weekly "
        "(NOAA CPC weekly relative Niño3.4 from 1981). CPC indices are Zarr data only."
    ),
)
@weather_skill.argument(
    "--product",
    default="observation",
    choices=["observation", "forecast"],
    help=(
        "observation (default): current monitoring timeseries. "
        "forecast: ACCESS-S plume or monthly outlook table from the archive."
    ),
)
@weather_skill.argument(
    "--format",
    dest="output_format",
    choices=["figure", "data"],
    help=(
        "figure (PNG graph or plume) or data (Zarr of official index values). "
        "Default: data when -o ends in .zarr, otherwise figure."
    ),
)
@weather_skill.argument(
    "--date",
    help=(
        "Forecast issue date YYYY-MM-DD. Snaps to the latest ACCESS-S archive "
        "issue on or before this day. Omit for the latest issue. Observation "
        "products have no dated archive."
    ),
)
@weather_skill.argument(
    "--probe-latest",
    nargs="?",
    const="",
    probe=True,
    help=(
        "Print the latest available YYYY-MM-DD on stdout and exit; takes no value. "
        "Forecast: latest ACCESS-S issue. Observation: Last-Modified of the "
        "current graph or text series, or none."
    ),
)
def fetch(index, product, date, output, output_format, probe_latest, **kwargs):
    """Download BoM and NOAA CPC IOD / ENSO index values or graphs, or BoM ACCESS-S outlooks."""
    if probe_latest:
        raise UsageError(
            f"--probe-latest takes no value (got {probe_latest!r}); "
            "choose the series with --index and --product."
        )
    graph, text_url, *_ = INDICES[index]

    suffix = Path(output).suffix.lower() if output is not None else ""
    kind = output_format or ("data" if suffix == ".zarr" else "figure")
    if kind == "data" and suffix in _FIGURE_SUFFIXES:
        raise UsageError("--format data writes a Zarr; pass -o with a .zarr suffix.")
    if kind == "figure" and suffix == ".zarr":
        raise UsageError("--format figure writes a PNG; pass -o with a .png suffix.")
    if graph is None:
        if product == "forecast" or output_format == "figure" or suffix not in {"", ".zarr"}:
            raise UsageError(
                f"--index {index} is NOAA observation data only; "
                "pass --product observation and -o OUT.zarr."
            )
        kind = "data"

    if product == "forecast":
        if index not in FORECAST_REGIONS:
            raise UsageError(
                f"--product forecast has no plume for --index {index!r}; "
                "choose iod, enso, nino3.4, or relative-nino3.4."
            )
        dates = _archive_dates()
        earlier = [d for d in dates if date is None or d <= date]
        if not earlier:
            raise UsageError(
                f"--date {date.isoformat()} is before the ACCESS-S outlook archive "
                f"(first issue {dates[0].isoformat()})."
            )
        issue = earlier[-1]
        if probe_latest is not None:
            print(issue.isoformat())
            return
        if date is not None and issue != date:
            print(
                f"Note: no ACCESS-S issue on {date.isoformat()}; using {issue.isoformat()}.",
                file=sys.stderr,
            )
        if index == "relative-nino3.4" and issue < RNINO_START:
            raise UsageError(
                f"--index relative-nino3.4 starts {RNINO_START.isoformat()} "
                "(BoM Relative Niño switch); pass --index nino3.4 or a later --date."
            )
        relative = index in {"enso", "relative-nino3.4"} and issue >= RNINO_START
        stem = (
            f"{FORECAST_ARCHIVE}/{issue:%Y%m%d}/plumes/"
            f"sstOutlooks.{'r' if relative else ''}{FORECAST_REGIONS[index]}"
        )
        url = f"{stem}.json" if kind == "data" else f"{stem}.hr.png"
        referer = FORECAST_PAGE
    else:
        if date is not None:
            raise UsageError(
                "Observation products are current-only (the source overwrites the same "
                "file in place). For a dated ACCESS-S forecast pass "
                "--product forecast --date YYYY-MM-DD."
            )
        url = text_url if kind == "data" else graph
        referer = OBS_PAGE if graph else CPC_PAGE
        if probe_latest is not None:
            # The file's Last-Modified day is the latest update.
            headers = {"User-Agent": _USER_AGENT, "Referer": referer, "Accept": "*/*"}
            req = urllib.request.Request(url, method="HEAD", headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
                    modified = parsedate_to_datetime(resp.headers.get("Last-Modified"))
                if modified.tzinfo is None:
                    modified = modified.replace(tzinfo=UTC)
                print(modified.astimezone(UTC).date().isoformat())
            except (urllib.error.URLError, TypeError, ValueError):
                print("none")
            return

    print(f"Fetching {product} {index} {kind}: {url}", file=sys.stderr)
    if kind == "figure":
        accept = "image/png,image/*;q=0.8,*/*;q=0.5"
    elif product == "forecast":
        accept = "application/json,text/plain,*/*"
    else:
        accept = "text/plain,text/*;q=0.8,*/*;q=0.5"
    raw, content_type = _read_url(url, referer=referer, accept=accept)

    if kind == "data":
        try:
            text = raw.decode("utf-8")
            if product == "observation":
                return _obs_dataset(index, text)
            payload = json.loads(text)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DataError(
                f"downloaded {url!r} is not UTF-8 text or JSON "
                f"(content-type={content_type!r}, size={len(raw)})."
            ) from exc
        # Before the Relative Niño switch, --index enso is the traditional Niño3.4 plume.
        return _forecast_dataset(
            "nino3.4" if index == "enso" and not relative else index, issue, payload
        )

    if raw[:8] != _PNG_SIGNATURE:
        raise DataError(
            f"downloaded {url!r} is not a PNG (content-type={content_type!r}, size={len(raw)})."
        )
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    from PIL import Image

    # BoM graphs are 256-colour palettes; provenance stamping would requantize them and
    # merge the pink and blue fills, so save palette PNGs as RGB first.
    with Image.open(io.BytesIO(raw)) as img:
        if img.mode in {"P", "PA"}:
            img.convert("RGBA").convert("RGB").save(output)
        else:
            output.write_bytes(raw)
    return output


if __name__ == "__main__":
    fetch()
