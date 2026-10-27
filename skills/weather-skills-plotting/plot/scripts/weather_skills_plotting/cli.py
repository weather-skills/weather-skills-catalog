"""Shared skill flow: skeleton + ``--spec`` → dump, or compile and export."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from weather_skills_core.errors import UsageError

from weather_skills_plotting.spec import (
    SPEC_VERSION,
    dump_spec,
    dump_spec_dest,
    merge_spec,
    validate,
)

LARGE_HTML_MB = 50
LARGE_PNG_MP = 40  # megapixels: a PNG/JPG above this gets a warning


def input_path(ds) -> str | None:
    from weather_skills_core.decorator import INPUT_PATH_ATTR

    return None if ds is None else ds.attrs.get(INPUT_PATH_ATTR)


_NAME = re.compile(r"[A-Za-z0-9_]+")


def split_name(value: str, flag: str) -> tuple[str, str | None]:
    """``PATH=NAME`` → ``(PATH, NAME)``; a bare ``PATH`` has no name."""
    path, sep, name = str(value).rpartition("=")
    if not sep:
        return str(value), None
    if not path or not _NAME.fullmatch(name):
        raise argparse.ArgumentTypeError(
            f"{flag} expects PATH or PATH=NAME, NAME made of letters, digits and _; got {value!r}. "
            "NAME is the trace's uid and its default legend / panel text. Other text takes any "
            "characters: "
            'legend text is the trace\'s "name" in --spec data (matched by uid), '
            'panel title text is layout.annotations [{"name": "panel-title-N", "text": ...}]'
        )
    return path, name


class InputArg:
    """``-i PATH[=NAME]``. The decorator opens the Zarr (``.ds``)."""

    def __init__(self, path: str, name: str | None):
        self.path, self.name, self.ds = path, name, None

    def zarr_paths(self):
        return [Path(self.path)]

    def __str__(self):
        # Provenance records str(); keep the flag's own form.
        return f"{self.path}={self.name}" if self.name else self.path


def parse_input(value):
    return InputArg(*split_name(value, "-i"))


def input_ids(names) -> list[str]:
    """The id of each input in order: its NAME, else its position (``"0"``, ``"1"``, …)."""
    ids = [name or str(i) for i, name in enumerate(names)]
    for key in ids:
        if ids.count(key) > 1:
            raise UsageError(f"two inputs have the id {key!r}; give each a distinct PATH=NAME")
    return ids


def skeleton(skill: str, datasets: dict, data: list, layout: dict | None = None) -> dict:
    """The spec a skill builds from its files, before ``--spec`` is merged on."""
    inputs = {key: str(input_path(ds)) for key, ds in datasets.items() if input_path(ds)}
    meta = {"version": SPEC_VERSION, "skill": skill}
    if inputs:
        meta["inputs"] = inputs
    return {"data": data, "layout": {**(layout or {}), "meta": meta}}


def spec_datasets(spec_arg, cli: dict) -> dict:
    """Datasets named on the command line, else those listed in ``layout.meta.inputs``."""
    if cli:
        return cli
    return spec_arg.opened() if spec_arg is not None else {}


def run(
    skill_spec: dict,
    user_spec,
    datasets: dict,
    output,
    dump,
    *,
    theme_file=None,
    scale=None,
    animate=False,
):
    """Merge, then either dump the spec or draw it. Returns the output path(s) or ``None``.

    ``output`` is one path, or a list (repeated ``-o``): the same figure is written to each.

    ``animate`` turns the figure's map panels into frames of one animated map
    (``plot --animate``); the output must then be HTML or GIF.
    """
    from weather_skills_plotting import SpecArg, compile, export
    from weather_skills_plotting.themes import load_theme

    user = user_spec.data if isinstance(user_spec, SpecArg) else (user_spec or {})
    merged = merge_spec(skill_spec, user)
    theme = load_theme(theme_file)
    validate(merged, theme)
    dest = dump_spec_dest(dump)
    if dest is not None:
        # A band trace's other parts are traces of their own: list their uids to style.
        have = {t.get("uid") for t in merged.get("data") or []}
        for trace in list(merged.get("data") or []):
            if (trace.get("meta") or {}).get("band") is not None:
                parts = ["band"] + ([] if trace["meta"].get("members") is False else ["members"])
                merged["data"] += [
                    {"uid": f"{trace.get('uid')}-{p}"}
                    for p in parts
                    if f"{trace.get('uid')}-{p}" not in have
                ]
        dump_spec(merged, dest)
        return None
    if output is None:
        raise UsageError("--output is required unless --dump-spec is set")
    outputs = [Path(p) for p in (output if isinstance(output, list) else [output])]
    suffixes = {path.suffix.lower() for path in outputs}
    for path in outputs:
        if animate and path.suffix.lower() not in (".html", ".htm", ".gif"):
            raise UsageError(
                f"output {path.name!r} must end in .html or .gif: an animation plays in a "
                "browser or as a GIF. For a still image of one time, drop --animate"
            )
        if not animate and path.suffix.lower() == ".gif":
            raise UsageError(
                f"output {path.name!r}: a GIF plays an animation; add --animate, or write a "
                "still map to .png"
            )
    fig = panels = compile(merged, datasets, theme=theme)
    layout_meta = (merged.get("layout") or {}).get("meta") or {}
    animation = layout_meta.get("animation") or {}
    gif = None
    if animate:
        from weather_skills_plotting.animate import animate_figure, estimate_html_mb

        # The skeleton adds only layout.meta, so the merged layout is the user's own.
        user_layout = merged.get("layout") or {}
        if ".gif" in suffixes:
            # A GIF plays by itself: no play / pause or slider.
            gif = animate_figure(fig, user_layout=user_layout, animation=animation, controls=False)
        fig = animate_figure(fig, user_layout=user_layout, animation=animation)
        size = estimate_html_mb(fig)
        print(
            f"animation: {len(fig.frames)} frames, {size:.1f} MB of figure data",
            file=sys.stderr,
        )
        if size > LARGE_HTML_MB and suffixes & {".html", ".htm"}:
            warn(
                f"the HTML will be large (~{size:.0f} MB) and slow to open; select fewer frames "
                "(meta.source.isel / sel) or a smaller layout.meta.geo.bbox"
            )
    export_meta = layout_meta.get("export") or {}
    scale = scale or export_meta.get("scale") or 1
    width, height = fig.layout.width or 700, fig.layout.height or 500
    if any(path.suffix.lower() not in (".html", ".htm") for path in outputs):
        if min(width, height) < 300:
            warn(
                f"a {width:.0f}x{height:.0f} px canvas is too small for titles, ticks and the "
                "colorbar to fit; leave layout width/height unset or use 300 px or more "
                "(layout.meta.export.scale changes pixel density, not the layout)"
            )
        if width * height * scale**2 > LARGE_PNG_MP * 1e6:
            warn(
                f"export.scale {scale:g} writes a {width * scale:.0f}x{height * scale:.0f} px "
                f"image ({width * height * scale**2 / 1e6:.0f} MP); 2-4 is plenty for print"
            )
    written = []
    for path in outputs:
        if path.suffix.lower() == ".gif":
            from weather_skills_plotting.animate import DEFAULT_DURATION_MS, write_gif
            from weather_skills_plotting.qa import report_figure

            duration = animation.get("duration", DEFAULT_DURATION_MS)
            written.append(write_gif(gif, path, duration=duration, scale=scale))
            # The panel figure holds every frame's data for the finite counts.
            report_figure(path, datasets, fig=panels)
        else:
            written.append(export(fig, path, datasets=datasets, scale=scale))
    return written if isinstance(output, list) else written[0]


def warn(message: str) -> None:
    print(f"Warning: {message}", file=sys.stderr)
