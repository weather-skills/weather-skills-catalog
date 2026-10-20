"""Turn a faceted map figure into one animated panel.

``compile_figure`` draws one panel per facet value (``time`` / ``step`` by
default). Animation keeps panel 1 as the visible figure and makes every panel a
Plotly frame: the frame carries only the traces that differ from panel 1 (the
field, stations, arrows), not the overlays or plain traces that repeat on every
panel, plus that panel's title. Play / pause buttons and a slider are added
unless the user's layout already sets ``updatemenus`` / ``sliders``.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re

from weather_skills_core.errors import UsageError

from weather_skills_plotting.layout import map_layout, title_lines

DEFAULT_DURATION_MS = 500
DEFAULT_TRANSITION_MS = 0

_AXIS_RE = re.compile(r"^[xy]axis(\d+)$")
_PANEL_UID_RE = re.compile(r"^(.*)-p(\d+)(-.*)?$")


def _panel_of(trace: dict) -> int:
    """0-based panel index from the trace's ``xaxis`` (``x`` → 0, ``x3`` → 2)."""
    raw = trace.get("xaxis") or "x"
    return int(raw[1:] or 1) - 1


def _base_uid(uid: str) -> str:
    """``a-p3`` → ``a``; ``a-p3-1`` → ``a-1``; ``overlay-coastline-3`` → ``overlay-coastline``."""
    m = _PANEL_UID_RE.match(uid or "")
    if m:
        return m.group(1) + (m.group(3) or "")
    if uid and uid.startswith("overlay-"):
        return uid.rsplit("-", 1)[0]
    return uid


def _content(trace: dict) -> str:
    """Trace JSON without its identity and axes, to spot traces that repeat on every panel."""
    body = {k: v for k, v in trace.items() if k not in ("uid", "xaxis", "yaxis")}
    # str() of a large numpy array shows only its corners; compare every value.
    return json.dumps(body, sort_keys=True, default=_array_digest)


def _array_digest(value) -> str:
    if hasattr(value, "tobytes"):
        return f"{value.dtype}{value.shape}{hashlib.sha1(value.tobytes()).hexdigest()}"
    return str(value)


def _to_panel_one(trace: dict) -> dict:
    out = copy.deepcopy(trace)
    out["xaxis"], out["yaxis"] = "x", "y"
    return out


def _panel_title_text(annotations: list, p: int):
    name = f"panel-title-{p + 1}"
    return next((a.get("text") for a in annotations if a.get("name") == name), None)


def animate_figure(fig, *, user_layout: dict | None = None, animation: dict | None = None):
    """Return a one-panel ``go.Figure`` whose frames are ``fig``'s panels."""
    import plotly.graph_objects as go

    user_layout = user_layout or {}
    animation = animation or {}
    spec = fig.to_plotly_json()
    data, layout = spec["data"], spec["layout"]
    n = 1 + max((_panel_of(t) for t in data), default=0)
    if n < 2:
        raise UsageError(
            "nothing to animate: the map has a single panel. Animate a dim with at least two "
            "values (data[].meta.facet, default step or time), or drop --animate for one map"
        )

    panels = [[t for t in data if _panel_of(t) == p] for p in range(n)]
    base = [_to_panel_one(t) for t in panels[0]]
    base_keys = [_base_uid(t.get("uid")) for t in panels[0]]
    base_content = [_content(t) for t in panels[0]]

    # Which positions change between panels: compare every panel to panel 1.
    varying = set()
    for p in range(1, n):
        if len(panels[p]) != len(base):
            raise UsageError(
                "cannot animate: panels hold different traces (a layer is missing a time "
                "or step the others have). Select the same times on every layer"
            )
        for j, trace in enumerate(panels[p]):
            if _base_uid(trace.get("uid")) != base_keys[j]:
                raise UsageError("cannot animate: panels draw their traces in different orders")
            if _content(trace) != base_content[j]:
                varying.add(j)
    indices = sorted(varying)

    # Panel 2..N titles sit on x2, x3 … and drop out here; panel 1's becomes the frame label.
    annotations = [a for a in layout.get("annotations") or [] if _annotation_on_panel_one(a)]
    labels = [
        _panel_title_text(layout.get("annotations") or [], p) or f"frame {p + 1}" for p in range(n)
    ]

    def frame_annotations(p):
        out = copy.deepcopy(annotations)
        for a in out:
            if a.get("name") == "panel-title-1":
                a["text"] = labels[p]
        return out

    frames = []
    for p in range(n):
        frames.append(
            {
                "name": str(p),
                "data": [_to_panel_one(panels[p][j]) for j in indices],
                "traces": indices,
                "layout": {"annotations": frame_annotations(p)},
            }
        )

    out_layout = {k: v for k, v in layout.items() if not _AXIS_RE.match(k) and k != "grid"}
    out_layout["annotations"] = frame_annotations(0)
    # Panel 1 was one cell of a grid; as the only panel it takes the whole plot area.
    for key in ("xaxis", "yaxis"):
        out_layout[key] = {**out_layout[key], "domain": [0, 1]}
    bars = [k for k, v in out_layout.items() if k.startswith("coloraxis") and isinstance(v, dict)]
    for key in bars:
        bar = out_layout[key].setdefault("colorbar", {})
        if bar.get("orientation") != "h":
            # A long colorbar title laid flat would eat the narrow map's width.
            bar.update(len=1, y=0.5, yanchor="middle")
            title = bar.setdefault("title", {})
            user_title = ((user_layout.get(key) or {}).get("colorbar") or {}).get("title") or {}
            if "side" not in user_title:
                title["side"] = "right"
    out_layout["shapes"] = [s for s in layout.get("shapes") or [] if _annotation_on_panel_one(s)]
    if not out_layout["shapes"]:
        out_layout.pop("shapes")
    xr_, yr_ = layout["xaxis"]["range"], layout["yaxis"]["range"]
    aspect = (xr_[1] - xr_[0]) / max(yr_[1] - yr_[0], 1e-6)
    horizontal = any((out_layout[k].get("colorbar") or {}).get("orientation") == "h" for k in bars)
    sized, _ = map_layout(
        1,
        1,
        aspect,
        titled=title_lines(out_layout.get("title")),
        panel_titles=True,
        right=bool(bars) and not horizontal,
        below=horizontal,
        # Room under the map for the play button and slider.
        below_lines=6,
        user=user_layout,
    )
    out_layout.update(sized)

    duration = animation.get("duration", DEFAULT_DURATION_MS)
    transition = animation.get("transition", DEFAULT_TRANSITION_MS)
    play_args = {
        "frame": {"duration": duration, "redraw": True},
        "transition": {"duration": transition},
        "fromcurrent": True,
        "mode": "immediate",
    }
    if "updatemenus" not in user_layout:
        out_layout["updatemenus"] = [
            {
                "type": "buttons",
                "direction": "left",
                "showactive": False,
                "x": 0,
                "xanchor": "left",
                "y": 0,
                "yanchor": "top",
                "pad": {"t": 40, "r": 10},
                "buttons": [
                    {"label": "▶ Play", "method": "animate", "args": [None, play_args]},
                    {
                        "label": "❚❚ Pause",
                        "method": "animate",
                        "args": [
                            [None],
                            {"frame": {"duration": 0, "redraw": False}, "mode": "immediate"},
                        ],
                    },
                ],
            }
        ]
    if "sliders" not in user_layout:
        # The frame's label is the title above the map, so the slider shows no text.
        out_layout["sliders"] = [
            {
                "active": 0,
                "x": 0.2,
                "len": 0.8,
                "xanchor": "left",
                "y": 0,
                "yanchor": "top",
                "pad": {"t": 35},
                "currentvalue": {"visible": False},
                "font": {"color": "rgba(0,0,0,0)"},
                "steps": [
                    {
                        "label": labels[p],
                        "method": "animate",
                        "args": [
                            [str(p)],
                            {
                                "frame": {"duration": 0, "redraw": True},
                                "transition": {"duration": 0},
                                "mode": "immediate",
                            },
                        ],
                    }
                    for p in range(n)
                ],
            }
        ]
    for key in ("updatemenus", "sliders"):
        if key in user_layout:
            out_layout[key] = copy.deepcopy(user_layout[key])

    return go.Figure({"data": base, "layout": out_layout, "frames": frames})


def _annotation_on_panel_one(item: dict) -> bool:
    """Annotations / shapes that belong to panel 1 or the whole figure."""
    for key in ("xref", "yref"):
        ref = str(item.get(key) or "")
        axis = ref.split(" ")[0]
        if axis in ("", "paper", "x", "y", "container"):
            continue
        return False
    return True


def estimate_html_mb(fig) -> float:
    """Size of the figure JSON written into the HTML, in MB (plotly.js adds ~4.5 MB on top)."""
    from weather_skills_plotting import _float32

    return len(json.dumps(_float32(fig.to_plotly_json()))) / 1e6


def build_animation_spec(datasets: dict) -> dict:
    """The base spec for ``plot --animate``: one gridded input, one heatmap, a frame per value."""
    from weather_skills_core.cf import cf_dim

    from weather_skills_plotting.cli import skeleton

    if not datasets:
        raise UsageError("pass --animate with -i/--input, or a --spec with layout.meta.inputs")
    if len(datasets) > 1:
        raise UsageError(
            "--animate draws one gridded input; got several. To animate precip under isobars, "
            "merge the two fields into one Zarr and add a trace that reads the other variable"
        )
    key, grid = next(iter(datasets.items()))
    lat, lon = cf_dim(grid, "latitude"), cf_dim(grid, "longitude")
    if lat not in grid.dims or lon not in grid.dims:
        raise UsageError(
            f"--animate needs a latitude/longitude grid; got dims {list(grid.dims)}. "
            "For station or 1-D data drop --animate"
        )
    data = [{"uid": key, "type": "heatmap", "meta": {"source": {"input": key}}}]
    return skeleton("plot --animate", datasets, data)
