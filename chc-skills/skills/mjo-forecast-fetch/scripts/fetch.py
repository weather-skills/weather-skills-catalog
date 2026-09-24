# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "weather-skills-core @ git+https://github.com/rhiza-research/weather-skills-core@main",
# ]
# ///
"""Fetch the latest CPC CLIVAR MJO Wheeler–Hendon phase-space forecast PNG."""

from __future__ import annotations

import argparse
import sys
import urllib.error
import urllib.request
from pathlib import Path

from weather_skills_core import DataError, UsageError, weather_skill

# Auto-populated by the version-bump CI workflow. Do not edit manually.
_SKILL_VERSION = "0.0.3"

# Latest diagrams are served at fixed filenames under this directory (updated in place).
# Relative paths on the CLIVAR page resolve here:
#   https://www.cpc.ncep.noaa.gov/products/precip/CWlink/MJO/CLIVAR/clivar_wh.shtml
#   → ../../../mjo/img/<file>.png
IMG_BASE = "https://www.cpc.ncep.noaa.gov/products/precip/mjo/img"
_HTTP_TIMEOUT = 60

# Friendly model id → optional raw / bias-corrected filenames.
# Not every CPC product publishes both variants.
MODELS: dict[str, dict[str, str]] = {
    "gefs": {"raw": "GEFS.png", "bc": "GEFS_BC.png"},
    "gefs-extended": {"raw": "GMON.png"},
    "cfs": {"bc": "NCFS.png"},
    "cmc": {"raw": "CANM.png"},
    "jma": {"raw": "JMAN.png"},
    "ecmwf": {"raw": "ECMF.png", "bc": "ECMF_BC.png"},
    "ecmwf-extended-range": {"raw": "EMON.png", "bc": "EMON_BC.png"},
    "bom": {"raw": "BOMM.png", "bc": "BOMM_BC.png"},
}

MODEL_CHOICES = tuple(MODELS)


def _available_variants(model: str) -> list[str]:
    variants = MODELS[model]
    out = []
    if "raw" in variants:
        out.append("raw (--no-bias-corrected)")
    if "bc" in variants:
        out.append("bias-corrected (--bias-corrected)")
    return out


def _image_url(model: str, bias_corrected: bool) -> str:
    """Resolve CPC image URL for model + bias preference.

    ``bias_corrected=True`` (CLI default): prefer BC; if CPC only publishes
    raw for this model, use raw. ``False``: require a raw diagram.
    """
    if model not in MODELS:
        raise UsageError(
            f"unknown --model {model!r}; choose one of: {', '.join(MODEL_CHOICES)}"
        )
    variants = MODELS[model]
    if bias_corrected:
        filename = variants.get("bc") or variants.get("raw")
    else:
        filename = variants.get("raw")
        if filename is None:
            raise UsageError(
                f"--model {model!r} has no raw diagram on CPC "
                f"(only bias-corrected). Pass --bias-corrected, or choose a "
                f"model with raw: {', '.join(_available_variants(model))}."
            )
    assert filename is not None
    return f"{IMG_BASE}/{filename}"


def _download(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "chc-skills/mjo-forecast-fetch"})
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
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise DataError(
            f"downloaded {url!r} is not a PNG (content-type={content_type!r}, size={len(data)})."
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)


@weather_skill(name="mjo-forecast-fetch", version=_SKILL_VERSION)
@weather_skill.argument(
    "--model",
    required=True,
    choices=list(MODEL_CHOICES),
    help=(
        "Forecast model: gefs, gefs-extended, cfs, cmc, jma, ecmwf, "
        "ecmwf-extended-range, or bom."
    ),
)
@weather_skill.argument(
    "--bias-corrected",
    action=argparse.BooleanOptionalAction,
    default=True,
    help=(
        "Prefer the CPC bias-corrected diagram (default on; falls back to raw "
        "when CPC does not publish BC for that model). Pass --no-bias-corrected "
        "for the raw diagram."
    ),
)
def fetch(model, bias_corrected, output, **kwargs):
    """Fetch the latest CPC CLIVAR MJO phase-space forecast PNG for one model.

    Downloads a pre-rendered Wheeler–Hendon diagram from the static CPC image
    URLs (latest forecast only; filenames are overwritten upstream daily / 2×
    weekly). Writes the PNG to ``--output``. No Zarr input.
    """
    url = _image_url(model, bool(bias_corrected))
    variants = MODELS[model]
    if bias_corrected and "bc" not in variants and "raw" in variants:
        print(
            f"Note: --model {model} has no bias-corrected diagram on CPC; using raw.",
            file=sys.stderr,
        )
    output = Path(output)
    print(f"Fetching {url}", file=sys.stderr)
    _download(url, output)
    return output


if __name__ == "__main__":
    fetch()
