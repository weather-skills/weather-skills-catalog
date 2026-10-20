"""Plot themes: the standard look in ``themes/default.json`` and ``--theme-file``.

A theme has five sections (``template``, ``palettes``, ``defaults``,
``styles``, ``overlays``); ``themes/README.md`` explains each. A theme file is layered on
top of ``default.json``, so it lists only what it changes.
"""

from __future__ import annotations

import copy
import json
import os
from importlib.resources import files
from pathlib import Path

from weather_skills_core.errors import UsageError

from weather_skills_plotting.geodata import OVERLAY_NAMES
from weather_skills_plotting.palettes import (
    DEFAULT_PRECIP_PALETTES,
    builtin_palettes,
    parse_palette,
)
from weather_skills_plotting.spec import deep_merge, plotly_error

DEFAULT_THEME_FILE = files("weather_skills_plotting") / "themes" / "default.json"
_DEFAULT_THEME = json.loads(DEFAULT_THEME_FILE.read_text(encoding="utf-8"))

THEME_FILE_KEYS = frozenset({"template", "palettes", "defaults", "styles", "overlays"})
_THEME_ENV = "WEATHER_SKILLS_PLOT_THEME"
_USER_THEME_CANDIDATES = (
    Path.home() / ".config" / "weather-skills" / "plot.json",
    Path.home() / ".config" / "weather-skills" / "plot.toml",
)


def _read_theme_file(path: Path) -> dict:
    """The standard theme with the file at ``path`` layered on top, validated."""
    if not path.is_file():
        shipped = sorted(
            p.name.removesuffix(".json")
            for p in DEFAULT_THEME_FILE.parent.iterdir()
            if p.name.endswith(".json") and p.name != "default.json"
        )
        raise UsageError(
            f"--theme-file {path}: no such file and not a shipped theme "
            f"(shipped: {', '.join(shipped)})"
        )
    text = path.read_text(encoding="utf-8")
    try:
        if path.suffix.lower() == ".toml":
            import tomllib

            data = tomllib.loads(text)
        else:
            data = json.loads(text)
    except ValueError as exc:  # JSONDecodeError and TOMLDecodeError are ValueErrors
        raise UsageError(f"plot theme file {path} is not valid JSON/TOML: {exc}") from None
    if not isinstance(data, dict):
        raise UsageError(f"plot theme file {path} must contain an object")
    unknown = sorted(set(data) - THEME_FILE_KEYS)
    if unknown:
        raise UsageError(
            f"plot theme file {path}: unknown keys {', '.join(unknown)}; "
            f"allowed: {', '.join(sorted(THEME_FILE_KEYS))} "
            "(template is a Plotly template object, palettes maps names to {colors, bounds}, "
            "defaults maps field kinds to a palette or Plotly colorscale, styles maps drawn "
            "elements to Plotly settings, overlays maps base-map layers to Plotly line styles)"
        )
    theme = copy.deepcopy(_DEFAULT_THEME)
    for name, entry in (data.get("palettes") or {}).items():
        parse_palette(entry, loc=f"{path}: palettes.{name}", registry=theme["palettes"])
    theme["palettes"].update(data.get("palettes") or {})
    # Windows crop the masters as the file leaves them; a window the file names wins.
    theme["palettes"] = {**builtin_palettes(theme["palettes"]), **theme["palettes"]}
    for kind, value in (data.get("defaults") or {}).items():
        if kind not in theme["defaults"]:
            raise UsageError(
                f"plot theme file {path}: defaults.{kind} is not a field kind; "
                f"known: {', '.join(theme['defaults'])}"
            )
        _check_default_color(value, theme["palettes"], loc=f"{path}: defaults.{kind}")
    for name, style in (data.get("styles") or {}).items():
        if name not in theme["styles"] or not isinstance(style, dict):
            raise UsageError(
                f"plot theme file {path}: styles.{name} must be one of "
                f"{', '.join(theme['styles'])}, set to an object of Plotly settings"
            )
    for name, style in (data.get("overlays") or {}).items():
        if name not in OVERLAY_NAMES or not isinstance(style, dict):
            raise UsageError(
                f"plot theme file {path}: overlays.{name} must be one of "
                f"{', '.join(OVERLAY_NAMES)}, set to a Plotly line style such as "
                '{"line": {"color": "#333333", "width": 1}}'
            )
        import plotly.graph_objects as go

        try:
            go.Scatter(deep_merge(theme["overlays"][name], style))
        except ValueError as exc:
            msg = str(plotly_error(exc)).removeprefix("plot spec: ")
            raise UsageError(f"plot theme file {path}: overlays.{name}: {msg}") from None
    theme["template"] = deep_merge(theme["template"], data.get("template") or {})
    theme["defaults"].update(data.get("defaults") or {})
    theme["styles"] = deep_merge(theme["styles"], data.get("styles") or {})
    theme["overlays"] = deep_merge(theme["overlays"], data.get("overlays") or {})
    return theme


def _check_default_color(value, palettes: dict, *, loc: str) -> None:
    """A theme default is a palette (name, list or object) or a Plotly colorscale name."""
    if not isinstance(value, str):
        parse_palette(value, loc=loc, registry=palettes)
        return
    known = {k.lower() for k in palettes} | set(DEFAULT_PRECIP_PALETTES)
    if value.lower() in known:
        return
    from plotly.colors import get_colorscale

    try:
        get_colorscale(value)
    except Exception:
        raise UsageError(
            f"{loc} {value!r} is neither a palette ({', '.join(sorted(known))}) "
            "nor a Plotly colorscale name (Viridis, RdBu_r, YlGn, …)"
        ) from None


def default_theme() -> dict:
    """The standard look, read from ``themes/default.json`` (a fresh copy).

    Its palettes gain the precip windows cropped from its masters (``builtin_palettes``).
    """
    theme = copy.deepcopy(_DEFAULT_THEME)
    theme["palettes"] = {**builtin_palettes(theme["palettes"]), **theme["palettes"]}
    return theme


def load_theme(path=None) -> dict:
    """The standard theme with ``--theme-file`` layered on top.

    ``path`` is a JSON/TOML file, or the name of a theme shipped in
    ``themes/`` (``tropical_tidbits``).

    Falls back to ``$WEATHER_SKILLS_PLOT_THEME``, then
    ``~/.config/weather-skills/plot.{json,toml}``. Palettes and defaults
    replace by name; styles and overlay styles merge into the standard ones.
    """
    candidates = [Path(path)] if path is not None else []
    shipped = DEFAULT_THEME_FILE.parent / f"{path}.json"
    if path is not None and not Path(path).exists() and shipped.is_file():
        # A shipped theme by name: --theme-file tropical_tidbits.
        candidates = [Path(str(shipped))]
    if not candidates and os.environ.get(_THEME_ENV):
        candidates = [Path(os.environ[_THEME_ENV])]
    if not candidates:
        candidates = [c for c in _USER_THEME_CANDIDATES if c.is_file()][:1]
    return _read_theme_file(candidates[0]) if candidates else default_theme()
