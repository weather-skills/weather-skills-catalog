# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "weather-skills-core>=0.1.1",
#   "pillow>=10",
# ]
# ///
"""Download the latest NOAA/CPC Africa Intertropical Front (ITF) figure as a PNG."""

from __future__ import annotations

import io
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image
from weather_skills_core import DataError, UsageError, weather_skill

# Auto-populated by the version-bump CI workflow. Do not edit manually.
_SKILL_VERSION = "0.0.4"

# Source page (images updated in place each dekadal analysis season):
#   https://www.cpc.ncep.noaa.gov/products/international/itf/itcz.shtml
IMG_BASE = "https://www.cpc.ncep.noaa.gov/products/international/itf"
PAGE_URL = f"{IMG_BASE}/itcz.shtml"
_HTTP_TIMEOUT = 60

# Location id → CPC static path (page labels: Mean vs Current / West / East).
# Note: west.gif and east.gif are served with PNG magic bytes despite the .gif suffix.
LOCATION_FILES: dict[str, str] = {
    "africa": "itcz.jpg",
    "east-africa": "east.gif",
    "west-africa": "west.gif",
}

LOCATION_CHOICES = tuple(LOCATION_FILES)


def _image_url(location: str) -> str:
    if location not in LOCATION_FILES:
        raise UsageError(
            f"unknown --location {location!r}; choose one of: {', '.join(LOCATION_CHOICES)}"
        )
    return f"{IMG_BASE}/{LOCATION_FILES[location]}"


def _download_as_png(url: str, dest: Path) -> None:
    """Download a CPC figure and write it as a PNG (sources may be JPEG or PNG)."""
    req = urllib.request.Request(url, headers={"User-Agent": "chc-skills/africa-itf"})
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

    try:
        with Image.open(io.BytesIO(data)) as img:
            img.load()
            if img.mode not in ("RGB", "RGBA", "L", "LA", "P"):
                img = img.convert("RGB")
            elif img.mode == "P" and "transparency" in img.info:
                img = img.convert("RGBA")
            dest.parent.mkdir(parents=True, exist_ok=True)
            img.save(dest, format="PNG")
    except Exception as exc:  # noqa: BLE001 - Pillow raises many types for a bad image
        raise DataError(
            f"downloaded {url!r} is not a usable image "
            f"(content-type={content_type!r}, size={len(data)}): {exc}"
        ) from None

    written = dest.read_bytes()
    if written[:8] != b"\x89PNG\r\n\x1a\n":
        raise DataError(f"failed to write a PNG at {dest}")


@weather_skill(name="africa-itf", version=_SKILL_VERSION)
@weather_skill.argument(
    "--location",
    default="africa",
    choices=list(LOCATION_CHOICES),
    help=(
        "africa (default): continental ITF position map. west-africa / east-africa: "
        "latitude-vs-dekad time series of the ITF averaged over a fixed longitude band "
        "(east = 20E-35E, which excludes Kenya), not maps."
    ),
)
def fetch(location, output, **kwargs):
    """Download the latest NOAA/CPC Africa Intertropical Front (ITF) figure as a PNG."""
    # Downloads a pre-rendered dekadal ITF figure (continental map, or a west/east
    # latitude time series) from the static CPC URLs on
    # https://www.cpc.ncep.noaa.gov/products/international/itf/itcz.shtml
    # (filenames updated in place). Converts JPEG/GIF-labeled sources to PNG.
    # No Zarr input — figure-only skill. Prints the analysed dekad on stderr.
    url = _image_url(location)
    output = Path(output)
    print(f"Fetching {location}: {url}", file=sys.stderr)
    _download_as_png(url, output)

    # The images are overwritten in place and carry no machine-readable date; the
    # page text names the analysed dekad ("From September 21-30, the ITF ...").
    match = None
    try:
        req = urllib.request.Request(PAGE_URL, headers={"User-Agent": "chc-skills/africa-itf"})
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            page = resp.read().decode("utf-8", errors="replace")
        match = re.search(r"From ([A-Z][a-z]+ \d{1,2}\s*[-\u2013]\s*\d{1,2}),", page)
    except (urllib.error.URLError, OSError):
        pass
    if match:
        print(f"Analysed dekad: {match.group(1)} (latest CPC analysis)", file=sys.stderr)
    else:
        print(
            f"warning: could not read the analysed dekad from {PAGE_URL}; "
            "check the date printed on the figure.",
            file=sys.stderr,
        )
    return output


if __name__ == "__main__":
    fetch()
