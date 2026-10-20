# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "weather-skills-core>=0.1.1",
# ]
# ///
"""Print a KMSA or CHC plot theme, or one CHC or KMSA rainfall colormap, as JSON."""

import json
import sys
from pathlib import Path

from weather_skills_core import Dataset, UsageError, weather_skill
from weather_skills_core.units import parse_aggregation_period

# Auto-populated by the version-bump CI workflow. Do not edit manually.
_SKILL_VERSION = "0.0.2"

DATA = Path(__file__).resolve().parent.parent / "data"
THEME = DATA / "chc_theme.json"
COLORMAPS = json.loads(THEME.read_text(encoding="utf-8"))["colormaps"]
THEMES = {"kmsa": DATA / "kmsa_theme.json", "chc": DATA / "chc_classic_theme.json"}
# CHC families whose window (daily / week / month / season) --window-days or --for picks.
FAMILIES = ("chc_precip", "chc_precip_anom")


@weather_skill(name="chc-plot-theme", version=_SKILL_VERSION, output=False)
@weather_skill.argument(
    "--theme",
    choices=list(THEMES),
    help="Print this plot theme as JSON; save it to a file for plot --theme-file.",
)
@weather_skill.argument(
    "--colormap",
    choices=[*COLORMAPS, *FAMILIES],
    help=(
        "Colormap to print as JSON for a trace's meta.palette, e.g. kmsa or chc_precip_week. "
        "Pass the family chc_precip or chc_precip_anom with --window-days or --for to have "
        "the window picked for you."
    ),
)
@weather_skill.argument(
    "--window-days",
    type=float,
    help="Length of the totals window in days (e.g. 10 for a dekad, 15 for a SubC 15d outlook).",
)
@weather_skill.argument(
    "--for",
    type=Dataset("spatial"),
    dest="for_ds",
    help="Zarr to plot; the window is read from its aggregation_period attr.",
)
def chc_plot_theme(theme=None, colormap=None, window_days=None, for_ds=None, **kwargs):
    """Print a KMSA or CHC plot theme, or one CHC or KMSA rainfall colormap, as JSON."""
    if (theme is None) == (colormap is None):
        raise UsageError("pass exactly one of --theme or --colormap")
    if theme is not None:
        if window_days is not None or for_ds is not None:
            raise UsageError("--window-days / --for go with --colormap, not --theme.")
        sys.stdout.write(THEMES[theme].read_text(encoding="utf-8"))
        return

    if for_ds is not None and window_days is None:
        periods = {
            da.attrs["aggregation_period"]
            for da in for_ds.data_vars.values()
            if "aggregation_period" in da.attrs
        }
        if len(periods) != 1:
            raise UsageError(
                f"--for needs exactly one aggregation_period on the data variables, found "
                f"{sorted(periods) or 'none'}; pass --window-days N instead."
            )
        window_days = float(parse_aggregation_period(periods.pop()).to("day").magnitude)

    if colormap in FAMILIES:
        if window_days is None:
            raise UsageError(f"--colormap {colormap} needs --window-days N or --for PATH.zarr.")
        if window_days < 2:
            window = "daily"
        elif window_days <= 10:
            window = "week"
        elif window_days < 40:
            window = "month"
        else:
            window = "season"
        colormap = f"{colormap}_{window}"
        print(f"Using {colormap} for a {window_days:g}-day window", file=sys.stderr)
    elif window_days is not None:
        raise UsageError(
            "--window-days / --for pick the window only for --colormap chc_precip or chc_precip_anom."
        )

    print(json.dumps({"name": colormap, **COLORMAPS[colormap]}, separators=(",", ":")))


if __name__ == "__main__":
    chc_plot_theme()
