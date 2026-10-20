# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "weather-skills-core>=0.1.1",
#   "numpy",
#   "pillow>=10",
#   "xarray",
# ]
# ///
"""Download Bureau of Meteorology IOD and ENSO graphs, index values, or ACCESS-S outlooks."""

from __future__ import annotations

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
_SKILL_VERSION = "0.0.2"

IMG_HOST = "https://www.bom.gov.au/clim_data"
OBS_PAGE = "https://www.bom.gov.au/climate/enso/"
FORECAST_PAGE = "https://www.bom.gov.au/climate/ocean/outlooks/?index=iod#tabs=Graphs"
FORECAST_ARCHIVE = "https://www.bom.gov.au/climate/ocean/outlooks/archive"
ARCHIVE_INDEX_URL = f"{FORECAST_ARCHIVE}/archive_index.json"
_HTTP_TIMEOUT = 60

# BoM switches Niño outlook plumes to Relative Niño filenames on this issue date.
RNINO_START = date(2025, 7, 1)

# BoM Akamai returns 403 for library-style User-Agents (e.g. "chc-skills/…").
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

# Observation graphs overwritten in place (product IDCK000072 / IDCKGSM000).
INDEX_FILES: dict[str, tuple[str, str]] = {
    "iod": ("IDCK000072", "iod1.png"),
    "enso": ("IDCK000072", "rnino_3.4.png"),
    "nino3.4": ("IDCK000072", "nino3_4.png"),
    "relative-nino3.4": ("IDCK000072", "rnino_3.4.png"),
    "soi": ("IDCKGSM000", "soi30.png"),
}

# Matching clim_data text series (week-start, week-end, value).
INDEX_DATA_FILES: dict[str, tuple[str, str]] = {
    "iod": ("IDCK000072", "iod_1.txt"),
    "enso": ("IDCK000072", "rnino_3.4.txt"),
    "nino3.4": ("IDCK000072", "nino_3.4.txt"),
    "relative-nino3.4": ("IDCK000072", "rnino_3.4.txt"),
    "soi": ("IDCKGSM000", "soi.txt"),
}

# ACCESS-S plume / JSON region token (prefix ``r`` added for operational Niño).
FORECAST_REGIONS: dict[str, str] = {
    "iod": "iod",
    "enso": "nino34",
    "nino3.4": "nino34",
    "relative-nino3.4": "nino34",
}

# Output variable + CF-ish metadata for the index series.
INDEX_VARS: dict[str, tuple[str, str, str]] = {
    "iod": (
        "iod_mode_index",
        "degree_Celsius",
        "Indian Ocean Dipole Mode Index (west minus east SST anomaly)",
    ),
    "enso": (
        "relative_nino34",
        "degree_Celsius",
        "Relative Niño3.4 sea surface temperature anomaly",
    ),
    "relative-nino3.4": (
        "relative_nino34",
        "degree_Celsius",
        "Relative Niño3.4 sea surface temperature anomaly",
    ),
    "nino3.4": (
        "nino34",
        "degree_Celsius",
        "Traditional Niño3.4 sea surface temperature anomaly",
    ),
    "soi": ("soi", "1", "30-day Troup Southern Oscillation Index"),
}

INDEX_CHOICES = tuple(INDEX_FILES)
PRODUCT_CHOICES = ("observation", "forecast")
FORMAT_CHOICES = ("figure", "data")
_FIGURE_SUFFIXES = {".png", ".jpg", ".jpeg"}


def _browser_headers(referer: str, accept: str) -> dict[str, str]:
    return {
        "User-Agent": _USER_AGENT,
        "Referer": referer,
        "Accept": accept,
    }


def _open(url: str, *, referer: str, accept: str):
    req = urllib.request.Request(url, headers=_browser_headers(referer, accept))
    try:
        return urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT)
    except urllib.error.HTTPError as exc:
        raise DataError(f"download failed for {url!r}: HTTP {exc.code} {exc.reason}") from None
    except urllib.error.URLError as exc:
        raise DataError(f"download failed for {url!r}: {exc.reason}") from None


def _image_url(index: str) -> str:
    if index not in INDEX_FILES:
        raise UsageError(f"unknown --index {index!r}; choose one of: {', '.join(INDEX_CHOICES)}")
    product, filename = INDEX_FILES[index]
    return f"{IMG_HOST}/{product}/{filename}"


def _obs_data_url(index: str) -> str:
    if index not in INDEX_DATA_FILES:
        raise UsageError(f"unknown --index {index!r}; choose one of: {', '.join(INDEX_CHOICES)}")
    product, filename = INDEX_DATA_FILES[index]
    return f"{IMG_HOST}/{product}/{filename}"


def _forecast_prefix(index: str, issue: date) -> str:
    if index == "relative-nino3.4":
        if issue < RNINO_START:
            raise UsageError(
                f"--index relative-nino3.4 starts {RNINO_START.isoformat()} "
                f"(BoM Relative Niño switch); pass --index nino3.4 or a later --date."
            )
        return "r"
    if index in {"enso"} and issue >= RNINO_START:
        return "r"
    return ""


def _forecast_stem(index: str, issue: date) -> str:
    if index not in FORECAST_REGIONS:
        raise UsageError(
            f"--product forecast has no plume for --index {index!r}; "
            f"choose iod, enso, nino3.4, or relative-nino3.4."
        )
    region = FORECAST_REGIONS[index]
    prefix = _forecast_prefix(index, issue)
    ymd = issue.strftime("%Y%m%d")
    return f"{FORECAST_ARCHIVE}/{ymd}/plumes/sstOutlooks.{prefix}{region}"


def _forecast_url(index: str, issue: date) -> str:
    return f"{_forecast_stem(index, issue)}.hr.png"


def _forecast_data_url(index: str, issue: date) -> str:
    return f"{_forecast_stem(index, issue)}.json"


def _request_url(url: str) -> str:
    """Append a cache-buster query, matching the BoM pages."""
    return f"{url}?{int(time.time())}"


def _parse_archive_dates(payload: dict) -> list[date]:
    try:
        rows = payload["archive_index"]["data"]["index"]
    except (KeyError, TypeError) as exc:
        raise DataError(
            "BoM outlook archive_index.json is missing archive_index.data.index"
        ) from exc
    dates: list[date] = []
    for row in rows:
        raw = row.get("init_date") if isinstance(row, dict) else None
        if not raw:
            continue
        dates.append(date.fromisoformat(str(raw)))
    if not dates:
        raise DataError("BoM outlook archive_index.json listed no init dates.")
    return sorted(dates)


def _archive_dates() -> list[date]:
    req_url = _request_url(ARCHIVE_INDEX_URL)
    with _open(req_url, referer=FORECAST_PAGE, accept="application/json,text/plain,*/*") as resp:
        payload = json.load(resp)
    return _parse_archive_dates(payload)


def _resolve_issue_date(when: date | None, dates: list[date]) -> date:
    if not dates:
        raise DataError("BoM outlook archive listed no ACCESS-S issue dates.")
    if when is None:
        return dates[-1]
    candidates = [d for d in dates if d <= when]
    if not candidates:
        raise UsageError(
            f"--date {when.isoformat()} is before the ACCESS-S outlook archive "
            f"(first issue {dates[0].isoformat()})."
        )
    return candidates[-1]


def _observation_mtime(url: str) -> date | None:
    req = urllib.request.Request(
        url,
        method="HEAD",
        headers=_browser_headers(OBS_PAGE, "*/*"),
    )
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            raw = resp.headers.get("Last-Modified")
    except (urllib.error.HTTPError, urllib.error.URLError):
        return None
    if not raw:
        return None
    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).date()


def _read_url(url: str, *, referer: str, accept: str) -> tuple[bytes, str]:
    req_url = _request_url(url)
    with _open(req_url, referer=referer, accept=accept) as resp:
        data = resp.read()
        content_type = resp.headers.get("Content-Type", "")
    if not data:
        raise DataError(f"download of {url!r} returned an empty body.")
    return data, content_type


def _download(url: str, dest: Path, *, referer: str = OBS_PAGE) -> None:
    data, content_type = _read_url(url, referer=referer, accept="image/png,image/*;q=0.8,*/*;q=0.5")
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise DataError(
            f"downloaded {url!r} is not a PNG (content-type={content_type!r}, size={len(data)})."
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)


def _expand_palette_png(path: Path) -> None:
    """Rewrite 8-bit palette PNGs as RGB before provenance stamping.

    BoM IOD / ENSO graphs are 256-color palettes. Opening one, compositing the
    official mark, and converting back to ``P`` requantizes nearby fills — the
    pastel positive (pink) and negative (blue) bands become one purple. RGB
    survives that stamp path without merging colors.
    """
    from PIL import Image

    with Image.open(path) as img:
        if img.mode not in {"P", "PA"}:
            return
        rgb = img.convert("RGBA").convert("RGB")
    rgb.save(path)


def _parse_ymd(raw: str) -> date:
    try:
        return datetime.strptime(raw, "%Y%m%d").replace(tzinfo=UTC).date()
    except ValueError as exc:
        raise DataError(f"unrecognized BoM date {raw!r} (expected YYYYMMDD).") from exc


def _parse_float(raw: str) -> float:
    token = raw.strip()
    if token.lower() in {"nan", ""}:
        return float("nan")
    try:
        return float(token)
    except ValueError as exc:
        raise DataError(f"unrecognized BoM numeric value {raw!r}.") from exc


def _parse_obs_text(text: str) -> tuple[np.ndarray, np.ndarray]:
    """Parse ``start,end,value`` clim_data rows into week-end times and values."""
    if text.lstrip().startswith("<"):
        raise DataError("BoM index text download returned HTML, not CSV values.")
    times: list[np.datetime64] = []
    values: list[float] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        row = line.strip()
        if not row or row.startswith("#"):
            continue
        parts = [p.strip() for p in row.split(",")]
        if len(parts) < 3:
            raise DataError(f"BoM index text line {line_no} is not start,end,value: {row!r}")
        end = _parse_ymd(parts[1])
        times.append(np.datetime64(end.isoformat(), "ns"))
        values.append(_parse_float(parts[2]))
    if not times:
        raise DataError("BoM index text listed no values.")
    return np.asarray(times), np.asarray(values, dtype=np.float64)


def _obs_interval(index: str) -> str:
    return "1 day" if index == "soi" else "7 day"


def _obs_dataset(index: str, text: str) -> xr.Dataset:
    name, units, long_name = INDEX_VARS[index]
    times, values = _parse_obs_text(text)
    ds = xr.Dataset({name: ("time", values)}, coords={"time": times})
    ds[name].attrs.update(long_name=long_name, units=units)
    if index != "soi":
        ds[name].attrs["standard_name"] = "sea_surface_temperature_anomaly"
    stamp_cf_coords(ds)
    if index == "soi":
        ds[name].attrs["comment"] = "30-day rolling Troup SOI; time is the window end date."
    else:
        ds[name].attrs["comment"] = "Weekly index; time is the week-ending date."
    ds.attrs.update(Conventions="CF-1.13", weather_skills_source="bom")
    return stamp_data_interval(ds, period=_obs_interval(index))


def _parse_month_label(label: str) -> date:
    try:
        return datetime.strptime(label.strip(), "%b %Y").replace(tzinfo=UTC).date().replace(day=1)
    except ValueError as exc:
        raise DataError(f"unrecognized BoM month label {label!r} (expected 'Mon YYYY').") from exc


def _frequency_bucket(label: str) -> str:
    key = label.lower().replace("\u2212", "-").strip()
    if key.startswith("below"):
        return "below"
    if key.startswith("above"):
        return "above"
    if key.startswith("neutral"):
        return "neutral"
    raise DataError(f"unrecognized ACCESS-S frequency bucket {label!r}.")


def _parse_forecast_json(payload: dict) -> tuple[np.ndarray, np.ndarray, dict[str, np.ndarray]]:
    try:
        block = payload["data"]
        mean = block["mean"]
        frequency = block["frequency"]
    except (KeyError, TypeError) as exc:
        raise DataError("ACCESS-S outlook JSON is missing data.mean / data.frequency.") from exc
    if not isinstance(mean, dict) or not mean:
        raise DataError("ACCESS-S outlook JSON listed no monthly mean values.")
    labels = list(mean)
    times = np.array(
        [np.datetime64(_parse_month_label(label).isoformat(), "ns") for label in labels]
    )
    means = np.array([_parse_float(str(mean[label])) for label in labels], dtype=np.float64)
    buckets = {"below": [], "neutral": [], "above": []}
    for label in labels:
        row = frequency.get(label) if isinstance(frequency, dict) else None
        if not isinstance(row, dict):
            raise DataError(f"ACCESS-S outlook JSON is missing frequency for {label!r}.")
        found: dict[str, float] = {}
        for raw_key, raw_val in row.items():
            found[_frequency_bucket(str(raw_key))] = _parse_float(str(raw_val))
        for bucket, values in buckets.items():
            if bucket not in found:
                raise DataError(
                    f"ACCESS-S outlook JSON frequency for {label!r} is missing {bucket}."
                )
            values.append(found[bucket])
    freqs = {name: np.asarray(vals, dtype=np.float64) for name, vals in buckets.items()}
    return times, means, freqs


def _next_month(day: date) -> date:
    if day.month == 12:
        return date(day.year + 1, 1, 1)
    return date(day.year, day.month + 1, 1)


def _month_time_bounds(times: np.ndarray) -> np.ndarray:
    """CF ``(time, 2)`` bounds: first of month through first of next month."""
    bounds = []
    for raw in times:
        start = date.fromisoformat(str(np.datetime64(raw, "D")))
        end = _next_month(start)
        bounds.append(
            [
                np.datetime64(start.isoformat(), "ns"),
                np.datetime64(end.isoformat(), "ns"),
            ]
        )
    return np.asarray(bounds, dtype="datetime64[ns]")


def _forecast_dataset(index: str, issue: date, payload: dict) -> xr.Dataset:
    # Before the Relative Niño switch, --index enso fetches the traditional Niño3.4 plume.
    if index == "enso" and _forecast_prefix(index, issue) == "":
        index = "nino3.4"
    name, units, long_name = INDEX_VARS[index]
    times, means, freqs = _parse_forecast_json(payload)
    ds = xr.Dataset(
        {
            name: ("time", means),
            "prob_below": ("time", freqs["below"]),
            "prob_neutral": ("time", freqs["neutral"]),
            "prob_above": ("time", freqs["above"]),
        },
        coords={
            "time": times,
            "init_time": np.datetime64(issue.isoformat(), "ns"),
            "time_bounds": (("time", "nv"), _month_time_bounds(times)),
        },
    )
    ds[name].attrs.update(
        long_name=f"ACCESS-S ensemble-mean {long_name}",
        units=units,
        comment="Monthly ensemble mean from the BoM outlooks JSON, not the 99-member plume.",
    )
    if index != "soi":
        ds[name].attrs["standard_name"] = "sea_surface_temperature_anomaly"
    stamp_cf_coords(ds)
    for var, phrase in (
        ("prob_below", "below the event threshold"),
        ("prob_neutral", "in the neutral range"),
        ("prob_above", "above the event threshold"),
    ):
        ds[var].attrs.update(
            long_name=f"ACCESS-S ensemble percent {phrase}",
            units="percent",
        )
    ds["init_time"].attrs.update(
        standard_name="forecast_reference_time",
        long_name="ACCESS-S outlook issue date",
    )
    ds["time"].attrs["bounds"] = "time_bounds"
    ds.attrs.update(Conventions="CF-1.13", weather_skills_source="bom")
    # Month lengths vary; CF time_bounds already describe the cells. Do not
    # infer a scalar data_interval (fails on a single-month table).
    return ds


def _resolve_output_format(output_format: str | None, output) -> str:
    suffix = Path(output).suffix.lower() if output is not None else ""
    if output_format is None:
        return "data" if suffix == ".zarr" else "figure"
    if output_format == "data" and suffix in _FIGURE_SUFFIXES:
        raise UsageError("--format data writes a Zarr; pass -o with a .zarr suffix.")
    if output_format == "figure" and suffix == ".zarr":
        raise UsageError("--format figure writes a PNG; pass -o with a .png suffix.")
    return output_format


@weather_skill(name="iod-enso-fetch", version=_SKILL_VERSION)
@weather_skill.argument(
    "--index",
    required=True,
    choices=list(INDEX_CHOICES),
    help=(
        "Index: iod (weekly DMI), enso / relative-nino3.4 "
        "(operational Relative Niño3.4), nino3.4 (traditional), or soi "
        "(30-day SOI; observation only)."
    ),
)
@weather_skill.argument(
    "--product",
    default="observation",
    choices=list(PRODUCT_CHOICES),
    help=(
        "observation (default): current monitoring timeseries. "
        "forecast: ACCESS-S plume or monthly outlook table from the archive."
    ),
)
@weather_skill.argument(
    "--format",
    dest="output_format",
    choices=list(FORMAT_CHOICES),
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
    """Download Bureau of Meteorology IOD and ENSO graphs, index values, or ACCESS-S outlooks."""
    # Observation products are the current overwritten clim_data timeseries
    # (https://www.bom.gov.au/climate/enso/). Forecasts are ACCESS-S plumes
    # and monthly mean / category-frequency tables from
    # https://www.bom.gov.au/climate/ocean/outlooks/ (dated archive;
    # omit ``--date`` for the latest issue).
    if product not in PRODUCT_CHOICES:
        raise UsageError(
            f"unknown --product {product!r}; choose one of: {', '.join(PRODUCT_CHOICES)}"
        )

    kind = _resolve_output_format(output_format, output)

    if probe_latest is not None:
        if probe_latest:
            raise UsageError(
                f"--probe-latest takes no value (got {probe_latest!r}); "
                "choose the series with --index and --product."
            )
        if product == "forecast":
            latest = _resolve_issue_date(None, _archive_dates())
            print(latest.isoformat())
        else:
            url = _obs_data_url(index) if kind == "data" else _image_url(index)
            latest = _observation_mtime(url)
            print(latest.isoformat() if latest is not None else "none")
        return

    if product == "observation":
        if date is not None:
            raise UsageError(
                "Observation products are current-only (BoM overwrites the same "
                "file in place). For a dated ACCESS-S forecast pass "
                "--product forecast --date YYYY-MM-DD."
            )
        if kind == "figure":
            url = _image_url(index)
            referer = OBS_PAGE
            print(f"Fetching {product} {index} figure: {url}", file=sys.stderr)
        else:
            url = _obs_data_url(index)
            print(f"Fetching {product} {index} data: {url}", file=sys.stderr)
            text, content_type = _read_url(
                url, referer=OBS_PAGE, accept="text/plain,text/*;q=0.8,*/*;q=0.5"
            )
            try:
                decoded = text.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise DataError(
                    f"downloaded {url!r} is not UTF-8 text "
                    f"(content-type={content_type!r}, size={len(text)})."
                ) from exc
            return _obs_dataset(index, decoded)
    else:
        issue = _resolve_issue_date(date, _archive_dates())
        if date is not None and issue != date:
            print(
                f"Note: no ACCESS-S issue on {date.isoformat()}; using {issue.isoformat()}.",
                file=sys.stderr,
            )
        if kind == "figure":
            url = _forecast_url(index, issue)
            referer = FORECAST_PAGE
            print(
                f"Fetching {product} {index} figure issue {issue.isoformat()}: {url}",
                file=sys.stderr,
            )
        else:
            url = _forecast_data_url(index, issue)
            print(
                f"Fetching {product} {index} data issue {issue.isoformat()}: {url}",
                file=sys.stderr,
            )
            raw, content_type = _read_url(
                url, referer=FORECAST_PAGE, accept="application/json,text/plain,*/*"
            )
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise DataError(
                    f"downloaded {url!r} is not JSON "
                    f"(content-type={content_type!r}, size={len(raw)})."
                ) from exc
            return _forecast_dataset(index, issue, payload)

    output = Path(output)
    _download(url, output, referer=referer)
    _expand_palette_png(output)
    return output


if __name__ == "__main__":
    fetch()
