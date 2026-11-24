"""Export: Plotly's own checks on the bound figure, then the file and its QA lines.

Every image prints a ``plot hash`` and whether the plotted data is finite.
"""

from __future__ import annotations

import base64
import hashlib
import re
import sys
import tempfile
from pathlib import Path

import numpy as np
from weather_skills_core.errors import UsageError

from weather_skills_plotting.render import palette_names

# Plotly keys a scatter takes under ``marker``, not at the top of the trace.
MARKER_KEYS = frozenset({"showscale", "colorscale", "colorbar", "cmin", "cmax", "reversescale"})


def validate(fig: dict):
    """A ``go.Figure`` from the resolved spec; Plotly's own checks, naming the trace."""
    import plotly.graph_objects as go

    for i, trace in enumerate(fig.get("data") or []):
        if not isinstance(trace, dict):
            raise UsageError(f"data[{i}] must be an object (a Plotly trace)")
        try:
            go.Figure(data=[trace])
        except ValueError as exc:
            raise UsageError(f"data[{i}]: {_explain(exc)}") from None
    try:
        return go.Figure(fig)
    except ValueError as exc:
        raise UsageError(f"--spec: {_explain(exc)}") from None


def key_hint(kind: str, key: str) -> str:
    """What to do about ``key``, which a ``kind`` trace (or layout object) doesn't have."""
    import plotly.graph_objects as go

    if key in ("animation", "animation_frame", "animation_group"):
        return (
            " animation_frame / animation_group are Plotly Express arguments, not figure "
            "keys; animate with frames (--recipe animation)."
        )
    if kind in ("scatter", "scattergl") and key in MARKER_KEYS | {"zmin", "zmax", "zmid"}:
        under = {"zmin": "cmin", "zmax": "cmax", "zmid": "cmid"}.get(key, key)
        return f' On a {kind} it goes under marker: {{"marker": {{"{under}": …}}}}.'
    if kind == "heatmap" and key in ("line", "marker", "mode", "text"):
        return " Outlines, markers and labels are scatter traces of their own."
    if kind == "bar" and key == "mode":
        return " Remove it: bars have no mode."
    trace_class = getattr(go, kind.capitalize(), None) or next(
        (getattr(go, n) for n in dir(go) if n.lower() == kind), None
    )
    names = sorted(getattr(trace_class(), "_valid_props", [])) if trace_class else []
    return f" Its properties: {', '.join(names)}." if names else ""


def _explain(exc) -> str:
    """Plotly's error in one or two lines, with what to do for the usual mix-ups.

    Plotly's own "Did you mean" guesses are dropped: they are often wrong
    (zmin → line, mode → base).
    """

    text = str(exc).strip()
    reference = "\nKeys are standard Plotly: https://plotly.com/python/reference/"
    bad_key = re.search(r"object of type plotly\.graph_objs\.([\w.]+): '(\w+)'", text)
    if bad_key:
        kind, key = bad_key.group(1).split(".")[-1].lower(), bad_key.group(2)
        return f"a {kind} has no property {key!r}.{key_hint(kind, key)}" + reference
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    message = " ".join(lines[:2])
    if re.search(r"for the '[xy]' property", text) and "Received value: '" in text:
        message += (
            " x and y take arrays; one value (a scalar coordinate such as a forecast's init "
            "time, or a single picked position) is not an axis. For a forecast, "
            "relabel its leads as valid times with a data skill first."
        )
    if "'colorscale' property" in text:
        message += (
            f" A colorscale is a Plotly name (Viridis, RdBu, YlGnBu, …) or a list of "
            f"[position, color]; a class palette goes through ws.classBar / ws.stepped "
            f"(ws.palettes has {', '.join(palette_names())})."
        )
    return message + reference


OUTPUT_SUFFIXES = (".png", ".jpg", ".jpeg", ".pdf", ".svg", ".html", ".htm", ".gif")

# A GIF frame lasts this long (ms) unless the figure's play button sets a duration.
DEFAULT_DURATION_MS = 500

# Many chat clients and slide tools show images only up to about this size inline.
LARGE_GIF_BYTES = 8 * 1024 * 1024


def export(fig, output, *, scale=1):
    """Write ``output`` (.png, .jpg, .pdf, .svg, .html or .gif) and print QA lines for images.

    HTML embeds plotly.js so the file works offline. A GIF plays the figure's frames.
    """
    output = Path(output)
    suffix = output.suffix.lower()
    output.parent.mkdir(parents=True, exist_ok=True)
    import plotly.io as pio

    spec = _float32(fig.to_plotly_json())
    if suffix in (".html", ".htm"):
        # An animation opens paused on its first frame; the viewer presses Play.
        pio.write_html(
            spec,
            output,
            include_plotlyjs=True,
            full_html=True,
            auto_play=False,
            config={
                "responsive": True,
                "scrollZoom": True,
                "showSendToCloud": False,
                "displaylogo": False,
            },
        )
        # An HTML page has no pixels to hash; hash the figure JSON it embeds. Plotly's
        # encoder, not json.dumps: an empty array stays a numpy array in the spec.
        digest = hashlib.sha256(pio.to_json(spec, validate=False).encode()).hexdigest()
        report_figure(output, fig, digest=digest)
        return output
    if suffix == ".gif":
        write_gif(spec, output, scale=scale)
        size = output.stat().st_size
        print(f"gif: {len(spec['frames'])} frames, {size / 1e6:.1f} MB", file=sys.stderr)
        if size > LARGE_GIF_BYTES:
            print(
                f"Warning: {output.name} is {size / 1e6:.1f} MB; many chat clients and slide "
                f"tools show images only up to about {LARGE_GIF_BYTES / 1e6:.0f} MB inline. Use "
                "fewer frames, a smaller width / height, sparser markers or a smaller window",
                file=sys.stderr,
            )
        report_figure(output, fig)
        return output
    pio.write_image(spec, output, scale=scale, validate=False)
    digest = None
    if suffix in (".pdf", ".svg"):
        # A vector file's bytes carry a creation time; hash the figure JSON instead.
        digest = hashlib.sha256(pio.to_json(spec, validate=False).encode()).hexdigest()
    report_figure(output, fig, digest=digest)
    return output


def write_gif(spec: dict, output, *, scale=1):
    """Write a figure's frames as a looping GIF.

    Each frame is drawn as a PNG by kaleido, then Pillow joins them on one
    shared 256-colour palette. A frame lasts as long as the play button's
    ``frame.duration`` (``ws.player``), else DEFAULT_DURATION_MS.
    """
    import plotly.io as pio
    from PIL import Image

    duration = DEFAULT_DURATION_MS
    for menu in spec["layout"].get("updatemenus") or []:
        for button in menu.get("buttons") or []:
            args = button.get("args") or []
            if len(args) > 1 and isinstance(args[1], dict) and "frame" in args[1]:
                duration = args[1]["frame"].get("duration") or duration
                break
    layout = {k: v for k, v in spec["layout"].items() if k not in ("updatemenus", "sliders")}
    data = list(spec["data"])
    stills = []
    for n, frame in enumerate(spec["frames"]):
        # As the play button does: each frame is merged key by key into the figure the
        # frame before left, its traces into the base traces it names, else by position.
        frame_data = frame.get("data") or []
        targets = frame.get("traces") or list(range(len(frame_data)))
        if len(targets) != len(frame_data) or not all(0 <= i < len(data) for i in targets):
            raise UsageError(
                f"frames[{n}]: traces {list(targets)} must name one of data[0]…data[{len(data) - 1}] "
                f"for each of its {len(frame_data)} trace(s)"
            )
        for i, trace in zip(targets, frame_data, strict=True):
            data[i] = _merge(data[i], trace)
        layout = _merge(layout, frame.get("layout") or {})
        stills.append({"data": list(data), "layout": layout})
    with tempfile.TemporaryDirectory() as tmp:
        paths = [Path(tmp) / f"{i}.png" for i in range(len(stills))]
        # kaleido draws at 700 x 500 unless each image is given the layout's own size.
        widths = [still["layout"].get("width") or 700 for still in stills]
        heights = [still["layout"].get("height") or 500 for still in stills]
        pio.write_images(stills, paths, scale=scale, width=widths, height=heights, validate=False)
        images = [Image.open(p).convert("RGB") for p in paths]
    # One palette picked from all frames stacked, so a colorbar bands the same in every frame.
    w, h = images[0].size
    stack = Image.new("RGB", (w, h * len(images)))
    for i, im in enumerate(images):
        stack.paste(im, (0, h * i))
    palette = stack.quantize(256, dither=Image.Dither.NONE)
    images = [im.quantize(palette=palette, dither=Image.Dither.NONE) for im in images]
    images[0].save(output, save_all=True, append_images=images[1:], duration=duration, loop=0)
    return output


def _merge(base: dict, update: dict) -> dict:
    """``base`` with ``update`` merged in: nested objects key by key, anything else replaced."""
    merged = dict(base)
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _float32(obj):
    """Copy of a figure dict with its float64 arrays stored as float32.

    Plotly writes each array as base64 ``{"dtype": "f8", "bdata": ...}``; 32-bit
    floats halve an HTML file and keep finer detail than any plot shows.
    """
    if isinstance(obj, dict) and obj.get("dtype") == "f8" and "bdata" in obj:
        values = np.frombuffer(base64.b64decode(obj["bdata"]), dtype="<f8").astype("<f4")
        return {**obj, "dtype": "f4", "bdata": base64.b64encode(values.tobytes()).decode()}
    if isinstance(obj, dict):
        return {k: _float32(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_float32(v) for v in obj]
    return obj


# ---- QA lines


def report_figure(path, fig, *, digest=None):
    """Print ``plot hash`` and ``data:`` lines.

    The hash is ``digest``, else the sha256 of the image's RGB pixels (the first frame of a
    GIF), which provenance metadata does not change. The data line counts finite values per
    trace name in its ``z``, marker colors, ``y`` or ``r``; base-map lines (``ws.basemap``,
    uids ``basemap-…``) are left out.
    """
    if digest is None:
        from PIL import Image

        with Image.open(path) as img:
            digest = hashlib.sha256(img.convert("RGB").tobytes()).hexdigest()
    rows: dict[str, list[int]] = {}
    # A frame's traces count under the base trace they update.
    traces = [(tr.uid or tr.name or f"data[{i}]", tr) for i, tr in enumerate(fig.data)]
    for frame in fig.frames or []:
        targets = frame.traces or range(len(frame.data))
        for k, tr in zip(targets, frame.data, strict=False):
            traces.append((traces[k][0] if k < len(fig.data) else f"data[{k}]", tr))
    for name, tr in traces:
        if str(tr.uid or "").startswith("basemap-"):
            continue
        raw = None
        color = getattr(getattr(tr, "marker", None), "color", None)
        for value in (
            getattr(tr, "z", None),
            None if isinstance(color, str) else color,
            getattr(tr, "y", None),
            getattr(tr, "r", None),
        ):
            if value is not None:
                raw = value
                break
        try:
            values = np.asarray(raw, dtype=float).ravel()
        except (TypeError, ValueError):
            # Names on y (a dot chart): the values are on x.
            try:
                values = np.asarray(getattr(tr, "x", None), dtype=float).ravel()
            except (TypeError, ValueError):
                continue
        if raw is None or values.size == 0:
            continue
        # Traces sharing a name (one per ensemble member) count as one row.
        count = rows.setdefault(name, [0, 0])
        count[0] += int(np.isfinite(values).sum())
        count[1] += values.size
    detail = ", ".join(f"{name} {finite}/{n} finite" for name, (finite, n) in rows.items())
    status = "not null" if any(finite for finite, _ in rows.values()) else "NULL"
    print(f"plot hash: {digest}")
    print(f"data: {status} ({detail or 'no data traces'})")
    if status == "NULL" and rows:
        print("data is all NaN — check the input's values before treating this figure as valid")
    elif rows:
        for name, (finite, n) in rows.items():
            if n and not finite:
                print(f"data: {name} is all NaN and draws nothing")
