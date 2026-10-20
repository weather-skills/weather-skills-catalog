# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "weather-skills-core>=0.1.1",
# ]
# ///
"""Download an NCICS MJO or equatorial-wave map or Hovmöller diagram as a PNG."""

from __future__ import annotations

import argparse
import re
import sys
import urllib.error
import urllib.request
from datetime import UTC, date, datetime
from pathlib import Path

from weather_skills_core import DataError, UsageError, weather_skill

# Auto-populated by the version-bump CI workflow. Do not edit manually.
_SKILL_VERSION = "0.0.2"

# Live figures are overwritten in place under /pub/mjo/v2/{map,hov}/.
# Dated snapshots live under /pub/mjo/archive/YYYY/YYYY-MM-DD/v2/{map,hov}/
# (v2 PNGs from mid-2017; typically weekly). Source:
#   https://ncics.org/mjo
IMG_BASE = "https://ncics.org/pub/mjo/v2"
ARCHIVE_BASE = "https://ncics.org/pub/mjo/archive"
PAGE_URL = "https://ncics.org/mjo"
_HTTP_TIMEOUT = 60
_USER_AGENT = "chc-skills/ncics-mjo-png"
_DATE_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

PRODUCTS = ("map", "hovmoller")
ALGORITHMS = ("cfs", "orig", "notc", "sst")
WAVES = ("all", "tc", "sum", "low", "mjo", "er", "kelvin", "mtd")
REGIONS = ("africa", "global", "west", "east", "pacific", "atlantic", "indonesia", "asia")
LATITUDES = ("wide", "eqtr", "north", "south", "north2")
DAYS = (1, 2, 3, 5, 7, 10)
LEVELS = (850, 200)

# NCICS form families. Level is appended only for uwnd/vwnd/chi/psi.
NO_LEVEL_VARS = ("olr", "uShear", "shear", "pwat")
LEVEL_FAMILIES = ("uwnd", "vwnd", "chi", "psi")
COMPLETE_VARS = NO_LEVEL_VARS + tuple(f"{fam}{lev}" for fam in LEVEL_FAMILIES for lev in LEVELS)

_PRODUCT_ALIASES = {
    "map": "map",
    "maps": "map",
    "hovmoller": "hovmoller",
    "hovmuller": "hovmoller",
    "hovmollers": "hovmoller",
    "hovmullers": "hovmoller",
    "hov": "hovmoller",
}
_ALGORITHM_ALIASES = {
    "cfs": "cfs",
    "cfs-forecasts": "cfs",
    "orig": "orig",
    "original": "orig",
    "notc": "notc",
    "no-tc": "notc",
    "no-tcs": "notc",
    "tcs-removed": "notc",
    "sst": "sst",
    "cfs-sst": "sst",
    "cfs-with-ssts": "sst",
}
_WAVE_ALIASES = {
    "all": "all",
    "tc": "tc",
    "tropical-cyclones": "tc",
    "sum": "sum",
    "sum-of-modes": "sum",
    "low": "low",
    "low-frequency": "low",
    "mjo": "mjo",
    "er": "er",
    "equatorial-rossby": "er",
    "rossby": "er",
    "kelvin": "kelvin",
    "mtd": "mtd",
    "mrg": "mtd",
    "mrg-td": "mtd",
    "td": "mtd",
}
_REGION_ALIASES = {
    "africa": "africa",
    "global": "global",
    "west": "west",
    "western-hemisphere": "west",
    "west-hemisphere": "west",
    "east": "east",
    "eastern-hemisphere": "east",
    "east-hemisphere": "east",
    "pacific": "pacific",
    "pacific-ocean": "pacific",
    "atlantic": "atlantic",
    "south-atlantic": "atlantic",
    "indonesia": "indonesia",
    "asia": "asia",
}
_LATITUDE_ALIASES = {
    "wide": "wide",
    "tropics": "wide",
    "tropical": "wide",
    "15s-15n": "wide",
    "eqtr": "eqtr",
    "equator": "eqtr",
    "equatorial": "eqtr",
    "5s-5n": "eqtr",
    "north": "north",
    "5n-15n": "north",
    "north2": "north2",
    "10n-20n": "north2",
    "south": "south",
    "15s-5s": "south",
}
_VARIABLE_ALIASES = {
    "olr": "olr",
    "outgoing-longwave-radiation": "olr",
    "uwnd": "uwnd",
    "zonal-wind": "uwnd",
    "u": "uwnd",
    "vwnd": "vwnd",
    "meridional-wind": "vwnd",
    "v": "vwnd",
    "chi": "chi",
    "velocity-potential": "chi",
    "psi": "psi",
    "streamfunction": "psi",
    "stream-function": "psi",
    "ushear": "uShear",
    "u-shear": "uShear",
    "zonal-shear": "uShear",
    "shear": "shear",
    "shear-magnitude": "shear",
    "pwat": "pwat",
    "precipitable-water": "pwat",
    "uwnd850": "uwnd850",
    "u850": "uwnd850",
    "uwnd200": "uwnd200",
    "u200": "uwnd200",
    "vwnd850": "vwnd850",
    "v850": "vwnd850",
    "vwnd200": "vwnd200",
    "v200": "vwnd200",
    "chi850": "chi850",
    "chi200": "chi200",
    "psi850": "psi850",
    "psi200": "psi200",
}


def _fold(value: str) -> str:
    return value.strip().lower().replace("ö", "o").replace("ø", "o")


def _alias(value: str, table: dict[str, str], kind: str, allowed: tuple[str, ...]) -> str:
    key = _fold(str(value))
    resolved = table.get(key)
    if resolved is None:
        raise UsageError(f"unknown {kind} {value!r}; choose one of: {', '.join(allowed)}")
    return resolved


def _arg_alias(table: dict[str, str], kind: str, allowed: tuple[str, ...]):
    def parse(value: str) -> str:
        try:
            return _alias(value, table, kind, allowed)
        except UsageError as exc:
            raise argparse.ArgumentTypeError(str(exc)) from None

    return parse


def _filename_variable(variable: str, level: int) -> str:
    """Resolve a form family or complete token into the NCICS filename stem."""
    token = _VARIABLE_ALIASES.get(_fold(variable))
    if token is None:
        raise UsageError(
            f"unknown --variable {variable!r}; choose one of: "
            f"{', '.join(NO_LEVEL_VARS + LEVEL_FAMILIES)} "
            f"(add --level 850|200 for uwnd/vwnd/chi/psi), or a complete token "
            f"such as uwnd850, chi200."
        )
    if token in COMPLETE_VARS:
        return token
    if token in LEVEL_FAMILIES:
        if level not in LEVELS:
            raise UsageError(f"unknown --level {level!r}; choose one of: 850, 200")
        return f"{token}{level}"
    raise UsageError(f"unknown --variable {variable!r}")


def _figure_relpath(
    product: str,
    variable: str,
    algorithm: str,
    wave: str,
    region: str,
    days: int,
    latitude: str,
    level: int,
) -> str:
    """Return ``map/...png`` or ``hov/...png`` relative to the v2 tree."""
    product = _alias(product, _PRODUCT_ALIASES, "--product", PRODUCTS)
    algorithm = _alias(algorithm, _ALGORITHM_ALIASES, "--algorithm", ALGORITHMS)
    stem = _filename_variable(variable, level)
    if product == "map":
        if algorithm == "sst":
            raise UsageError(
                "--algorithm sst (CFS with SSTs) is Hovmöller-only; "
                "pass --product hovmoller, or choose --algorithm cfs|orig|notc."
            )
        wave = _alias(wave, _WAVE_ALIASES, "--wave", WAVES)
        region = _alias(region, _REGION_ALIASES, "--region", REGIONS)
        if int(days) not in DAYS:
            raise UsageError(f"unknown --days {days!r}; choose one of: {', '.join(map(str, DAYS))}")
        return f"map/{stem}.{algorithm}.{wave}.{region}.{int(days)}.png"
    latitude = _alias(latitude, _LATITUDE_ALIASES, "--latitude", LATITUDES)
    return f"hov/{stem}.{algorithm}.{latitude}.png"


def _image_url(
    product: str,
    variable: str,
    algorithm: str,
    wave: str,
    region: str,
    days: int,
    latitude: str,
    level: int,
    snapshot: date | None = None,
) -> str:
    rel = _figure_relpath(product, variable, algorithm, wave, region, days, latitude, level)
    if snapshot is None:
        return f"{IMG_BASE}/{rel}"
    iso = snapshot.isoformat()
    return f"{ARCHIVE_BASE}/{iso[:4]}/{iso}/v2/{rel}"


def _get_text(url: str) -> str | None:
    """Return response text, or None on HTTP 404."""
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            return resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise DataError(f"listing failed for {url!r}: HTTP {exc.code} {exc.reason}") from None
    except urllib.error.URLError as exc:
        raise DataError(f"listing failed for {url!r}: {exc.reason}") from None


def _list_year_dates(year: int) -> list[date]:
    """Archive snapshot dates listed under ``/pub/mjo/archive/{year}/``."""
    html = _get_text(f"{ARCHIVE_BASE}/{year}/")
    if html is None:
        return []
    found: list[date] = []
    for href in re.findall(r"""href=["']([^"']+)["']""", html, flags=re.IGNORECASE):
        name = href.rstrip("/").split("/")[-1]
        if _DATE_DIR_RE.fullmatch(name):
            found.append(date.fromisoformat(name))
    return sorted(set(found))


def _latest_archive_date() -> date:
    today = datetime.now(UTC).date()
    for year in range(today.year, today.year - 6, -1):
        dates = _list_year_dates(year)
        if dates:
            return max(dates)
    raise DataError(
        f"no YYYY-MM-DD folders found under {ARCHIVE_BASE}/; the NCICS MJO archive "
        "may be empty or unreachable."
    )


def _resolve_archive_date(requested: date) -> date:
    """Pick the archive snapshot on ``requested``, or the latest one on or before it."""
    candidates: list[date] = []
    for year in (requested.year, requested.year - 1):
        candidates.extend(_list_year_dates(year))
    on_or_before = [d for d in candidates if d <= requested]
    if not on_or_before:
        raise DataError(
            f"no NCICS MJO archive snapshot on or before {requested.isoformat()}. "
            f"v2 PNG archives start around mid-2017 and are typically weekly. "
            f"Browse {ARCHIVE_BASE}/ or pass --probe-latest."
        )
    return max(on_or_before)


def _download(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            data = resp.read()
            content_type = resp.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        hint = ""
        if exc.code == 404 and "/archive/" in url:
            hint = (
                " That snapshot may predate v2 PNGs (mid-2017), omit a later-added "
                "region/day option, or not be a published archive date. Browse "
                f"{ARCHIVE_BASE}/ or pass --probe-latest."
            )
        raise DataError(
            f"download failed for {url!r}: HTTP {exc.code} {exc.reason}.{hint}"
        ) from None
    except urllib.error.URLError as exc:
        raise DataError(f"download failed for {url!r}: {exc.reason}") from None

    if not data:
        raise DataError(f"download of {url!r} returned an empty body.")
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise DataError(
            f"downloaded {url!r} is not a PNG (content-type={content_type!r}, size={len(data)})."
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)


@weather_skill(name="ncics-mjo-png", version=_SKILL_VERSION)
@weather_skill.argument("--date")
@weather_skill.argument(
    "--probe-latest",
    nargs="?",
    const="",
    metavar="IDENT",
    probe=True,
    help=(
        "Print the latest archive snapshot YYYY-MM-DD on stdout and exit. "
        "Does not download. Takes no value."
    ),
)
@weather_skill.argument(
    "--product",
    default="map",
    type=_arg_alias(_PRODUCT_ALIASES, "--product", PRODUCTS),
    choices=list(PRODUCTS),
    help="Figure type: map (default, Africa 7-day panels) or hovmoller (longitude-time).",
)
@weather_skill.argument(
    "--variable",
    "-v",
    default="olr",
    help=(
        "Field: olr (default), uwnd, vwnd, chi, psi, ushear, shear, pwat. "
        "Pressure-level families take --level (default 200). Complete tokens "
        "(uwnd850, chi200, …) are also accepted."
    ),
)
@weather_skill.argument(
    "--level",
    type=int,
    default=200,
    choices=list(LEVELS),
    help="Pressure level hPa for uwnd/vwnd/chi/psi (default 200). Ignored otherwise.",
)
@weather_skill.argument(
    "--algorithm",
    default="cfs",
    type=_arg_alias(_ALGORITHM_ALIASES, "--algorithm", ALGORITHMS),
    choices=list(ALGORITHMS),
    help="NCICS algorithm: cfs (default), orig, notc, or sst (Hovmöller-only).",
)
@weather_skill.argument(
    "--wave",
    default="all",
    type=_arg_alias(_WAVE_ALIASES, "--wave", WAVES),
    choices=list(WAVES),
    help="Map wave overlay: all (default), tc, sum, low, mjo, er, kelvin, mtd. Maps only.",
)
@weather_skill.argument(
    "--region",
    default="africa",
    type=_arg_alias(_REGION_ALIASES, "--region", REGIONS),
    choices=list(REGIONS),
    help=(
        "Map region: africa (default), global, west, east, pacific, atlantic, "
        "indonesia, asia. Maps only."
    ),
)
@weather_skill.argument(
    "--latitude",
    default="wide",
    type=_arg_alias(_LATITUDE_ALIASES, "--latitude", LATITUDES),
    choices=list(LATITUDES),
    help=(
        "Hovmöller latitude band: wide/tropics 15S–15N (default), eqtr 5S–5N, "
        "north 5N–15N, north2 10N–20N, south 15S–5S. Hovmöllers only."
    ),
)
@weather_skill.argument(
    "--days",
    type=int,
    default=7,
    choices=list(DAYS),
    help="Map averaging window in days (default 7). Maps only.",
)
def fetch(
    date,
    product,
    variable,
    level,
    algorithm,
    wave,
    region,
    latitude,
    days,
    output,
    probe_latest,
    **kwargs,
):
    """Download an NCICS MJO or equatorial-wave map or Hovmöller diagram as a PNG."""
    # Without ``--date``, downloads the live figure from the static NCICS URLs
    # (https://ncics.org/mjo; filenames updated in place daily). With ``--date``,
    # reads the snapshot archive
    # (``/pub/mjo/archive/YYYY/YYYY-MM-DD/v2/``), snapping to the latest
    # published date on or before the request. Default is the Africa 7-day OLR
    # CFS map. Pass ``--product hovmoller`` for the longitude-time diagram
    # (default tropics 15S–15N). No Zarr input.
    if probe_latest is not None:
        if probe_latest:
            raise UsageError(
                f"--probe-latest takes no value (got {probe_latest!r}); "
                "it always reports the latest archive snapshot."
            )
        print(_latest_archive_date().isoformat())
        return
    snapshot = None
    if date is not None:
        snapshot = _resolve_archive_date(date)
        if snapshot != date:
            print(
                f"No snapshot for {date.isoformat()}; using archive {snapshot.isoformat()}",
                file=sys.stderr,
            )
        else:
            print(f"Resolved archive date: {snapshot.isoformat()}", file=sys.stderr)
    url = _image_url(
        product, variable, algorithm, wave, region, days, latitude, level, snapshot=snapshot
    )
    output = Path(output)
    print(f"Fetching {url}", file=sys.stderr)
    _download(url, output)
    return output


if __name__ == "__main__":
    fetch()
