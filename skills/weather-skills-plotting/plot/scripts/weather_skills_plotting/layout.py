"""Plotly templates and map-panel helpers.

Charts keep Plotly's layout. Maps size the canvas from the map's lon/lat
aspect, so equal-degree maps fill it, and place every panel with an explicit
axis domain. Plotly still grows the margins to fit tick labels, titles and
colorbars.
"""

from __future__ import annotations

import copy
import math
import re

import numpy as np
from weather_skills_core.errors import UsageError

from weather_skills_plotting.palettes import parse_palette

DEFAULT_TEMPLATE = "weather_skills"


def register_templates(theme: dict) -> None:
    """``weather_skills`` and ``colorblind``: Plotly's ``seaborn`` with the theme's template.

    ``colorblind`` takes the theme's ``colorblind`` palette as its colorway, even when
    the template sets one, since picking it is an accessibility choice.
    """
    import plotly.graph_objects as go
    import plotly.io as pio

    for name in ("weather_skills", "colorblind"):
        base = copy.deepcopy(pio.templates["seaborn"])
        try:
            base.update(go.layout.Template(theme["template"]))
        except ValueError as exc:
            from weather_skills_plotting.spec import plotly_error

            raise plotly_error(exc, "theme file template") from None
        if name == "colorblind":
            base.layout.colorway = parse_palette(
                theme["palettes"]["colorblind"], loc="palettes.colorblind", registry={}
            )["colors"]
        pio.templates[name] = base


def grid_shape(n, rows=None, columns=None):
    """``(rows, columns)`` for ``n`` panels: a near-square grid unless the user fixes a side."""
    for value, name in ((rows, "rows"), (columns, "columns")):
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int) or value < 1
        ):
            raise UsageError(f"layout.grid.{name} must be an integer >= 1; got {value!r}")
    if rows is None and columns is None:
        columns = math.ceil(math.sqrt(max(n, 1)))
    if rows is not None and columns is not None:
        if rows * columns < n:
            raise UsageError(
                f"layout.grid {rows} rows × {columns} columns holds {rows * columns} panels, "
                f"but the figure has {n}"
            )
        return rows, columns
    if columns is not None:
        return math.ceil(n / columns) or 1, columns
    return rows, math.ceil(n / rows) or 1


def choose_grid(n: int, aspect: float, rows=None, columns=None) -> tuple[int, int]:
    """``(rows, columns)`` for ``n`` map panels of lon/lat ``aspect``.

    Picks the grid whose overall shape is closest to a 1.6:1 landscape page,
    with few empty cells and never a whole empty row. A side the user fixed wins.
    """
    if rows is not None or columns is not None:
        return grid_shape(n, rows, columns)
    best = None
    for cols in range(1, min(n, 6) + 1):
        r = math.ceil(n / cols)
        empty = r * cols - n
        if empty >= cols:
            continue
        cost = abs(math.log(cols * aspect / r / 1.6)) + 0.35 * empty
        if best is None or cost <= best[0]:
            best = (cost, r, cols)
    return best[1], best[2]


# Largest map area, in pixels, for one panel alone and per panel in a grid.
MAP_BOX_SINGLE = (760, 560)
MAP_BOX_PANEL = (340, 340)  # area per grid panel: width x height of a square map
PANEL_MAX_H = 420  # tallest grid panel, for maps taller than wide
MAX_PLOT_WIDTH = 1400  # panels shrink so the panel area fits this width
# Room around map panels, in multiples of the font size. This only sizes the
# canvas and the gaps between panels: Plotly grows the outer margins itself
# (autoexpand) when tick labels, titles or colorbars need more.
TICK_ROOM = 4  # y tick labels left of a map, such as "-10°"
LINE = 2  # one line of text: x tick labels, a panel title, the figure title
COLORBAR_ROOM = 8  # a colorbar with its title
SEAL_CLEAR = 0.88  # horizontal colorbars end left of the provenance seal (plot-width fraction)
SEAL_CLEAR_Y = 0.06  # vertical colorbars start above it (plot-height fraction)
WIDE_MAP = 2.5  # a map wider than this (lon / lat span) gets its colorbar below


def layout_value(user_layout: dict, path: str):
    """``layout.<path>`` (``"font.size"``) from the spec, else from its template, else None.

    The template is the spec's ``layout.template``, a registered name or a dict.
    """
    import plotly.io as pio

    template = user_layout.get("template", DEFAULT_TEMPLATE)
    if isinstance(template, str):
        template = pio.templates[template] if template in pio.templates else {}
        template = template.to_plotly_json() if template else {}
    for obj in (user_layout, (template or {}).get("layout") or {}):
        for part in path.split("."):
            obj = obj.get(part) if isinstance(obj, dict) else None
        if obj is not None:
            return obj
    return None


def title_lines(title) -> int:
    """Lines the figure title takes: one per ``<br>``-separated line, plus a ``subtitle``."""
    if not isinstance(title, dict):
        title = {"text": title}
    text = str(title.get("text") or "")
    if not text:
        return 0
    lines = len(re.split(r"<br\s*/?>", text, flags=re.IGNORECASE))
    return lines + (1 if (title.get("subtitle") or {}).get("text") else 0)


def map_layout(
    rows, cols, aspect, *, titled, panel_titles, right=False, below=False, below_lines=0, user=None
) -> tuple[dict, list[tuple[float, float, float, float]]]:
    """Canvas, margins and per-panel ``(x0, x1, y0, y1)`` domains for a map grid.

    ``titled`` is how many lines the figure title takes (``title_lines``).

    ``right`` / ``below`` say a colorbar stands there; ``below_lines`` is more
    room under the maps, in lines of text. ``user`` is the spec layout: its
    ``width`` / ``height`` fix the canvas (maps shrink to fit), and
    ``grid.xgap`` / ``grid.ygap`` are gaps as a fraction of a panel.
    """
    user = user or {}
    grid = user.get("grid") or {}
    em = float(layout_value(user, "font.size") or 12)
    if rows * cols == 1:
        box_w, box_h = MAP_BOX_SINGLE
        w = min(box_w, box_h * aspect)
    else:
        # Same area per panel whatever its shape: a Pacific strip gets width, not a sliver.
        box_w, box_h = MAP_BOX_PANEL
        w = min(MAP_BOX_SINGLE[0], (box_w * box_h * aspect) ** 0.5, PANEL_MAX_H * aspect)
    h = w / aspect
    title_lines = LINE if panel_titles else 0
    ml = TICK_ROOM * em
    mr = (COLORBAR_ROOM if right else 1) * em
    # A subtitle takes a line plus a little air above the panel titles.
    mt = (LINE * int(titled) + (1 if titled > 1 else 0) + title_lines + 1) * em
    mb = (LINE + (COLORBAR_ROOM if below else 0) + below_lines) * em

    def gaps(w, h):
        gx = grid["xgap"] * w if grid.get("xgap") is not None else (TICK_ROOM + 1) * em
        gy = grid["ygap"] * h if grid.get("ygap") is not None else (LINE + title_lines + 1) * em
        return gx, gy

    gx, gy = gaps(w, h)
    avail = MAX_PLOT_WIDTH - (cols - 1) * gx
    if cols * w > avail:
        w = avail / cols
        h = w / aspect
    width, height = user.get("width"), user.get("height")
    if width is not None:
        w = (width - ml - mr - (cols - 1) * gx) / cols
        h = w / aspect
    if height is not None:
        h_fit = (height - mt - mb - (rows - 1) * gy) / rows
        if width is None or h_fit < h:
            h = h_fit
            w = h * aspect
    if w < 20 or h < 20:
        raise UsageError(
            f"layout width/height {width}x{height} leaves no room for {rows}x{cols} map panels"
        )
    gx, gy = gaps(w, h)
    width = width if width is not None else ml + mr + cols * w + (cols - 1) * gx
    height = height if height is not None else mt + mb + rows * h + (rows - 1) * gy
    pw, ph = width - ml - mr, height - mt - mb
    # Centre the block when a fixed canvas leaves spare room.
    ox = (pw - (cols * w + (cols - 1) * gx)) / 2
    oy = (ph - (rows * h + (rows - 1) * gy)) / 2
    domains = []
    for p in range(rows * cols):
        r, c = divmod(p, cols)
        x0 = (ox + c * (w + gx)) / pw
        top = 1 - (oy + r * (h + gy)) / ph
        domains.append(tuple(min(max(v, 0.0), 1.0) for v in (x0, x0 + w / pw, top - h / ph, top)))
    layout = {
        "width": round(width),
        "height": round(height),
        "margin": {"l": round(ml), "r": round(mr), "t": round(mt), "b": round(mb)},
    }
    return layout, domains


def axis_suffix(p: int) -> str:
    """Plotly axis suffix for 0-based panel ``p`` (``""``, ``"2"``, …)."""
    return "" if p == 0 else str(p + 1)


def map_axes(p: int, extent, domain=None) -> tuple[dict, dict]:
    """Lon/lat axes for panel ``p`` over ``extent``, one degree the same length on both."""
    common = {
        "constrain": "domain",
        "showgrid": False,
        "zeroline": False,
        "ticksuffix": "°",
        "ticks": "outside",
        # Tick labels push the figure margin rather than being clipped.
        "automargin": True,
    }
    s = axis_suffix(p)
    xaxis = {"range": [extent[0], extent[1]], **common, "anchor": f"y{s}"}
    if extent[1] > 180:
        # A map across the dateline runs on 0..360: label 200 as -160, like every other map.
        span = extent[1] - extent[0]
        step = next(d for d in (1, 2, 5, 10, 20, 30, 45, 60, 90) if span / d <= 8)
        ticks = np.arange(np.ceil(extent[0] / step) * step, extent[1] + 1e-9, step)
        xaxis.update(
            tickmode="array",
            tickvals=[float(t) for t in ticks],
            ticktext=[f"{180 if t % 360 == 180 else (t + 180) % 360 - 180:g}°" for t in ticks],
        )
    yaxis = {
        "range": [extent[2], extent[3]],
        **common,
        "anchor": f"x{s}",
        "scaleanchor": f"x{s}",
        "scaleratio": 1,
    }
    if domain is not None:
        xaxis["domain"] = [domain[0], domain[1]]
        yaxis["domain"] = [domain[2], domain[3]]
    return xaxis, yaxis


def colorbar_mode(panels_of: dict, cols: int) -> str:
    """Where colorbars go: ``right`` (one bar), ``rows`` / ``columns`` (each bar
    beside the rows or under the columns it colours), else ``below``."""
    if len(panels_of) <= 1:
        return "right"
    for mode, key in (("rows", lambda p: p // cols), ("columns", lambda p: p % cols)):
        seen, ok = set(), True
        for panels in panels_of.values():
            band = sorted({key(p) for p in panels})
            if band != list(range(band[0], band[-1] + 1)) or seen & set(band):
                ok = False
                break
            seen |= set(band)
        if ok:
            return mode
    return "below"


def place_colorbars(mode: str, panels_of: dict, domains) -> dict:
    """Colorbar settings per color axis for ``mode`` (see ``colorbar_mode``)."""
    out = {}
    k = len(panels_of)
    for i, (name, panels) in enumerate(panels_of.items()):
        ds = [domains[p] for p in panels]
        if mode in ("right", "rows"):
            y0, y1 = min(d[2] for d in ds), max(d[3] for d in ds)
            # Clear of the provenance seal in the bottom-right corner.
            y0 = max(y0, SEAL_CLEAR_Y)
            out[name] = {
                "len": y1 - y0,
                "y": (y0 + y1) / 2,
                "yanchor": "middle",
                "thickness": 16,
                # Along the bar: a long title laid flat would eat the map's width.
                "title": {"side": "right"},
            }
            continue
        bar = {
            "orientation": "h",
            "y": 0,
            "yref": "container",
            "yanchor": "bottom",
            "thickness": 14,
            "tickangle": 0,
            # Below the bar: beside it squeezes the bar, above it overlaps it.
            "title": {"side": "bottom"},
        }
        if mode == "columns":
            x0, x1 = min(d[0] for d in ds), max(d[1] for d in ds)
        else:
            x0, x1 = i / k, (i + 1) / k
        # The provenance seal sits in the bottom-right corner: bars and their
        # titles stop short of it.
        x1 = min(x1, SEAL_CLEAR)
        bar.update(len=(x1 - x0) * 0.9, x=(x0 + x1) / 2, xanchor="center")
        out[name] = bar
    return out


def panel_title_annotation(p: int, text: str) -> dict:
    """Title annotation centred above panel ``p`` (named so a spec can override it)."""
    s = axis_suffix(p)
    return {
        "name": f"panel-title-{p + 1}",
        "text": text,
        "xref": f"x{s} domain",
        "yref": f"y{s} domain",
        "x": 0.5,
        "y": 1,
        "xanchor": "center",
        "yanchor": "bottom",
        "showarrow": False,
    }
