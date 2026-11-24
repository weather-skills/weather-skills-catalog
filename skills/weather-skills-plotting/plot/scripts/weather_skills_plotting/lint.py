"""Check that a Plotly figure carries data only as @bindings.

    from weather_skills_plotting.lint import UnboundDataError, check_bindings

    check_bindings(figure)  # a dict or JSON text; raises UnboundDataError

The spec styles a figure in Jsonnet and binds its data with @references that
bind.resolve fills in from the inputs. plot runs this on the rendered figure,
before binding: it raises if anything that can put data on the chart holds
something other than bindings.

- data keys (``x``, ``z``, ``dimensions[].values``, ...): a binding, or a list
  made only of bindings. A scatter trace with no binding anywhere is a mark placed
  by hand (a line between two places, a pin) and may type in up to MARK_POINTS values
  per key. Any other trace type encodes values (heatmap cells, bar heights), so
  without a binding it fails however few values it holds.
- per-point keys (``marker.color``, ``text``, ...): one value is styling, but
  a list must be bindings.
- generator keys (``x0``/``dx``, ``base``, error-bar ``value``, ...): scalars
  that make up a value for every point. Error bars that would fall back to
  Plotly's default ±10 also fail.
- images and selections on a data axis: their coordinates. Shapes and annotations
  are marks placed by hand (a line at a date, a label at a place), so their
  coordinates may be typed in.
- menu and slider ``args``: may not set data keys or coordinates to literals,
  or carry inline frames.
- unknown keys, since only keys Plotly defines can be checked.

It checks bindings only; validate the filled figure with plotly afterwards.

Which keys hold data comes from the attribute definitions plotly.py generates from
the plotly.js schema (``plotly/validators/_validators.json``), sorted per leaf key:

- ``always``: a data array (plotly.js ``valType: data_array``), e.g. ``x``,
  ``z``, ``dimensions[].values``. Always one value per point/cell.
- ``array_settings``: also typed ``data_array``, but a list of axis ticks,
  categories or formats rather than plotted values (hand-picked below).
- ``may``: a scalar setting that also accepts a per-point array (plotly.js
  ``arrayOk: true``), e.g. ``marker.color``, ``text``. Data only when the value
  given is an array.
- ``schema_silent``: keys the schema types as plain values that still carry
  data in practice (hand-picked below, with reasons).

The same key can sort differently by trace type (``text`` is a data array on
``heatmap`` but ``arrayOk`` on ``scatter``), so ``any_trace`` lists such keys
under ``may``: look up ``traces[type]`` when the type is known.

Paths are relative to a trace object (``data[i]``) or to ``layout``. ``[]``
marks an array of objects (``dimensions[].values``) and ``*`` a numbered
subplot key (``xaxis*`` = ``xaxis``, ``xaxis2``, ...).

Regenerate the listing for review after a Plotly upgrade:
``python -m weather_skills_plotting.lint [out.json]``
"""

import functools
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import plotly
import plotly.graph_objects as go

VALIDATORS = Path(plotly.__file__).parent / "validators" / "_validators.json"

COMPOUND = {"CompoundValidator", "TitleValidator"}
CATEGORIES = ("always", "array_settings", "may", "schema_silent")

# data_array keys that list axis ticks, categories or formats, not plotted values.
ARRAY_SETTING_NAMES = {"tickvals", "ticktext", "categoryarray"}
ARRAY_SETTING_PATHS = {
    "hiddenlabels",  # layout: pie/funnelarea labels hidden from the legend
    "map.layers[].line.dash",  # layout: dash pattern of a map layer's lines
    "columnorder",  # table: display order of columns
    "cells.format",  # table: d3 format string per column
    "header.format",
}

# Typed as strings, indices or free-form values, so neither data_array nor
# arrayOk flags them, but they carry data rather than styling.
SCHEMA_SILENT = {
    "geojson": "GeoJSON object (or URL) holding the shapes being plotted",
    "selectedpoints": "array of point indices selected in this trace",
    "source": "image trace: the image itself as a data URI",
    "node.groups": "sankey: arrays of node indices grouped together",
    "images[].source": "layout: an image as a data URI or URL",
    "map.layers[].source": "layout: GeoJSON or tile source drawn as a map layer",
}


def load_validators():
    """Return ``{parent class path: {validator key: spec}}``."""
    by_parent = defaultdict(dict)
    for key, spec in json.loads(VALIDATORS.read_text()).items():
        by_parent[spec["params"]["parent_name"]][key] = spec
    return by_parent


def trace_types(by_parent):
    return sorted(
        key
        for key, spec in by_parent[""].items()
        if spec["superclass"] == "CompoundValidator" and key != "layout"
    )


def item_class(spec):
    """Class path of one item in a CompoundArrayValidator (``dimensions`` -> ``parcoords.dimension``)."""
    params = spec["params"]
    return f"{params['parent_name']}.{params['data_class_str'].lower()}"


def leaf_category(path, spec):
    """Category of a leaf key at ``path`` (relative, ``[]`` for array items), or None for plain settings."""
    if spec["superclass"] == "DataArrayValidator":
        is_setting = path.rpartition(".")[2] in ARRAY_SETTING_NAMES or path in ARRAY_SETTING_PATHS
        return "array_settings" if is_setting else "always"
    if spec["params"].get("array_ok"):
        return "may"
    if path in SCHEMA_SILENT:
        return "schema_silent"
    return None


def walk(by_parent, class_path, prefix, out):
    for key, spec in by_parent[class_path].items():
        kind = spec["superclass"]
        path = prefix + spec["params"]["plotly_name"]
        if kind in COMPOUND:
            walk(by_parent, key, path + ".", out)
        elif kind == "CompoundArrayValidator":
            walk(by_parent, item_class(spec), path + "[].", out)
        elif category := leaf_category(path, spec):
            out[category].append(path)


def classify(by_parent, root, subplot_keys=()):
    out = {k: [] for k in CATEGORIES}
    walk(by_parent, root, "", out)

    def star(path):
        head, dot, rest = path.partition(".")
        return f"{head}*{dot}{rest}" if head in subplot_keys else path

    return {k: sorted(star(p) for p in v) for k, v in out.items()}


# A reference: a string that starts with one @ ('@@' starts literal text).
BINDING = re.compile(r"^@[^@]", re.S)
AXIS_REF = re.compile(r"^[xy]\d*$")  # a data axis; 'paper' and 'x domain' are not
SUBPLOT_KEY = re.compile(rf"^({'|'.join(go.Layout._subplotid_prop_names)})\d+$")

# Scalars from which Plotly makes up coordinates or offsets for every point.
GENERATORS = {
    "x0",
    "dx",
    "y0",
    "dy",
    "a0",
    "da",
    "b0",
    "db",
    "r0",
    "dr",
    "theta0",
    "dtheta",
    "base",
    "delta.reference",
    "gauge.threshold.value",
    *(f"error_{axis}.{k}" for axis in "xyz" for k in ("value", "valueminus")),
}

# Most values a key of a trace with no bindings (a mark placed by hand) may type in.
MARK_POINTS = 5

# Layout collections drawn in data coordinates when anchored to an axis: (x keys, y keys).
# Shapes and annotations are left out: a line or label placed by hand may sit at a typed-in
# position.
ANCHORED = {
    "selections": ({"x0", "x1", "path"}, {"y0", "y1", "path"}),
    "images": ({"x", "sizex"}, {"y", "sizey"}),
}
MENU_ARGS = {
    "updatemenus[].buttons[].args",
    "updatemenus[].buttons[].args2",
    "sliders[].steps[].args",
}
FRAME_KEYS = {"data", "layout", "name", "group", "traces", "baseframe"}


class UnboundDataError(ValueError):
    """Raised when part of a figure that can hold data holds something other than @bindings."""

    def __init__(self, problems):
        self.problems = problems  # [(path, message), ...]
        listing = "\n".join(f"  {path}: {message}" for path, message in problems)
        super().__init__(f"figure has {len(problems)} binding problem(s):\n{listing}")


def check_bindings(figure, pattern=BINDING):
    """Raise UnboundDataError unless every data-carrying key in ``figure`` holds @bindings.

    ``figure`` is a Plotly figure as a dict or JSON text; ``pattern`` is the regex a
    binding must match. Every problem is collected before raising, so one error lists
    them all.
    """
    if isinstance(figure, (str, bytes)):
        figure = json.loads(figure)
    problems = _Checker(pattern).check(figure)
    if problems:
        raise UnboundDataError(problems)


@dataclass(frozen=True)
class _Schema:
    children: dict  # class path -> {key name: (validator key, spec)}
    trace_types: frozenset
    trace_data: frozenset  # trace paths whose values must be bindings
    trace_may: frozenset  # trace paths whose lists must be bindings


@functools.cache
def _schema():
    by_parent = load_validators()
    types = frozenset(trace_types(by_parent))
    sets = [classify(by_parent, t) for t in types]
    trace_data = frozenset(p for s in sets for p in s["always"] + s["schema_silent"]) | GENERATORS
    return _Schema(
        children={
            parent: {spec["params"]["plotly_name"]: (key, spec) for key, spec in specs.items()}
            for parent, specs in by_parent.items()
        },
        trace_types=types,
        trace_data=trace_data,
        trace_may=frozenset(p for s in sets for p in s["may"]) - trace_data,
    )


def leaves(value):
    if isinstance(value, list):
        for item in value:
            yield from leaves(item)
    else:
        yield value


def has_dict(value):
    return isinstance(value, dict) or (isinstance(value, list) and any(map(has_dict, value)))


def flatten(value, prefix=""):
    """Dotted (key, value) pairs of nested dicts, e.g. a restyle payload."""
    if isinstance(value, dict):
        for k, v in value.items():
            yield from flatten(v, f"{prefix}.{k}" if prefix else k)
    elif isinstance(value, list) and has_dict(value):
        for v in value:
            yield from flatten(v, prefix)
    elif prefix:
        yield prefix, value


def on_axis(ref):
    """Whether an xref/yref places coordinates on a data axis (Plotly's default when unset)."""
    if ref is None:
        return True
    if isinstance(ref, list):
        return any(on_axis(r) for r in ref)
    return isinstance(ref, str) and bool(AXIS_REF.match(ref))


class _Checker:
    def __init__(self, pattern):
        self.pattern = pattern
        self.schema = _schema()
        self.problems = []

    def problem(self, path, message):
        self.problems.append((path, message))

    def require(self, value, path, what):
        bad = next(
            (v for v in leaves(value) if not (isinstance(v, str) and self.pattern.match(v))), None
        )
        if bad is not None or value == []:
            shown = json.dumps(bad, ensure_ascii=False)[:60] if bad is not None else "[]"
            self.problem(path, f"{what} needs an @binding, found {shown}")

    def require_if_list(self, value, path, what):
        if isinstance(value, (list, dict)):
            self.require(value, path, f"{what} given per point")

    # ---- figure structure -------------------------------------------------

    def check(self, fig):
        if not isinstance(fig, dict):
            self.problem("<figure>", "expected a JSON object with data/layout/frames")
            return self.problems
        for key in fig.keys() - {"data", "layout", "frames"}:
            self.problem(key, "unknown top-level key, so it can't be checked for data")
        for i, trace in enumerate(fig.get("data", [])):
            self.check_trace(trace, f"data[{i}]", mark=True)
        if "layout" in fig:
            self.walk(fig["layout"], "layout", "", "layout", "layout")
        for i, frame in enumerate(fig.get("frames", [])):
            self.check_frame(frame, f"frames[{i}]")
        return self.problems

    def check_trace(self, trace, path, trace_type=None, mark=False):
        if not isinstance(trace, dict):
            return self.problem(path, "expected a trace object")
        trace_type = trace_type or trace.get("type", "scatter")
        if trace_type not in self.schema.trace_types:
            return self.problem(f"{path}.type", f"unknown trace type {trace_type!r}")
        # A trace with no binding anywhere is a mark placed by hand; its data keys are
        # checked for size only. Only a figure's own traces can be marks: a frame's trace
        # updates a base trace, and a template holds styles.
        bound = not mark or any(
            isinstance(v, str) and self.pattern.match(v)
            for _, value in flatten(trace)
            for v in leaves(value)
        )
        context = "trace" if bound else "mark"
        if not bound and not trace_type.startswith("scatter"):
            # Only lines, points and text sit at hand-placed positions. A heatmap, bar or
            # pie typed in by hand shows values with no source, however few.
            return self.problem(
                path,
                f"a {trace_type} with no @binding shows values typed in by hand; only a "
                "scatter (a line or points placed by hand) may go without one",
            )
        self.walk({k: v for k, v in trace.items() if k != "type"}, trace_type, "", path, context)
        self.check_error_bars(trace, path)

    def check_frame(self, frame, path):
        if not isinstance(frame, dict):
            return self.problem(path, "expected a frame object")
        for key in frame.keys() - FRAME_KEYS:
            self.problem(f"{path}.{key}", "unknown frame key, so it can't be checked for data")
        for i, trace in enumerate(frame.get("data", [])):
            self.check_trace(trace, f"{path}.data[{i}]")
        if "layout" in frame:
            self.walk(frame["layout"], "layout", "", f"{path}.layout", "layout")

    def check_template(self, template, path):
        # A named Plotly template ('plotly_white', 'plotly_white+presentation') holds only styles.
        if isinstance(template, str):
            import plotly.io as pio

            unknown = [n for n in template.split("+") if n not in pio.templates]
            if unknown:
                self.problem(
                    path, f"unknown template {unknown[0]!r}; Plotly has {', '.join(pio.templates)}"
                )
            return
        if not isinstance(template, dict):
            return self.problem(path, "expected a template object or a Plotly template name")
        for key in template.keys() - {"data", "layout"}:
            self.problem(f"{path}.{key}", "unknown template key, so it can't be checked for data")
        for trace_type, traces in template.get("data", {}).items():
            for i, trace in enumerate(traces):
                self.check_trace(trace, f"{path}.data.{trace_type}[{i}]", trace_type)
        if "layout" in template:
            self.walk(template["layout"], "layout", "", f"{path}.layout", "layout")

    def walk(self, obj, class_path, rel, path, context):
        """Check every key of ``obj`` against the schema class at ``class_path``."""
        if not isinstance(obj, dict):
            return self.problem(path, "expected an object")
        children = self.schema.children.get(class_path, {})
        for name, value in obj.items():
            child_path = f"{path}.{name}"
            match = SUBPLOT_KEY.match(name) if class_path == "layout" else None
            lookup = match[1] if match else name
            if lookup not in children:
                self.problem(
                    child_path, f"unknown key for {class_path}, so it can't be checked for data"
                )
                continue
            key, spec = children[lookup]
            kind, child_rel = spec["superclass"], rel + lookup
            if kind in COMPOUND:
                # *defaults keys (shapedefaults, annotationdefaults) have no class of their
                # own: they take the class of one item (layout.shape).
                child_class = key if key in self.schema.children else item_class(spec)
                self.walk(value, child_class, child_rel + ".", child_path, context)
                # A default applies to every shape / annotation / image, so its coordinates
                # on a data axis are checked like an item's.
                collection = child_rel.removesuffix("defaults") + "s"
                if (
                    context == "layout"
                    and child_rel.endswith("defaults")
                    and collection in ANCHORED
                ):
                    self.check_anchored(value, collection, child_path)
            elif kind == "CompoundArrayValidator":
                if not isinstance(value, list):
                    self.problem(child_path, "expected a list")
                    continue
                for i, item in enumerate(value):
                    self.walk(
                        item,
                        item_class(spec),
                        child_rel + "[].",
                        f"{child_path}[{i}]",
                        context,
                    )
                    if context == "layout" and child_rel in ANCHORED:
                        self.check_anchored(item, child_rel, f"{child_path}[{i}]")
            elif kind == "BaseTemplateValidator":
                self.check_template(value, child_path)
            else:
                self.check_leaf(value, spec, child_rel, child_path, context)

    # ---- the rules ------------------------------------------------------

    def check_leaf(self, value, spec, rel, path, context):
        category = leaf_category(rel, spec)
        if context == "mark":
            if category not in ("always", "schema_silent") and rel not in GENERATORS:
                return
            n = sum(1 for _ in leaves(value)) if isinstance(value, list) else 1
            if not isinstance(value, (list, int, float)) or has_dict(value):
                # An embedded image, a GeoJSON or encoded array is data, not a mark.
                self.require(value, path, "data key")
            elif n > MARK_POINTS:
                self.problem(
                    path,
                    f"a trace typed in by hand holds at most {MARK_POINTS} values per key, found "
                    f"{n}; data to draw needs an @binding",
                )
            return
        if context == "trace" and rel in GENERATORS:
            self.require(value, path, f"'{rel}' generates a value for every point and")
        elif category in ("always", "schema_silent"):
            self.require(value, path, "data key")
        elif category == "may" and (context == "trace" or rel == "meta"):
            self.require_if_list(value, path, f"'{rel}'")
        elif rel in MENU_ARGS:
            self.check_menu_args(value, path)

    def check_error_bars(self, trace, path):
        # Plotly draws ±10 (percent) by default once error bars are visible without data.
        for axis in "xyz":
            bars = trace.get(f"error_{axis}")
            if not isinstance(bars, dict):
                continue
            visible = bars.get("visible", "array" in bars or "value" in bars)
            bar_type = bars.get("type", "data" if "array" in bars else "percent")
            if not visible or bar_type not in ("percent", "constant"):
                continue
            symmetric = bars.get("symmetric", "arrayminus" not in bars and "valueminus" not in bars)
            for k in ["value"] + ([] if symmetric else ["valueminus"]):
                if k not in bars:
                    self.problem(
                        f"{path}.error_{axis}",
                        f"{bar_type} error bars without '{k}' draw Plotly's default 10; "
                        f"give '{k}' an @binding or use type 'data'",
                    )

    def check_anchored(self, item, collection, path):
        if not isinstance(item, dict):
            return
        x_keys, y_keys = ANCHORED[collection]
        coords = (x_keys if on_axis(item.get("xref")) else set()) | (
            y_keys if on_axis(item.get("yref")) else set()
        )
        for k in sorted(coords & item.keys()):
            self.require(item[k], f"{path}.{k}", f"'{k}' is on a data axis and")

    def check_menu_args(self, args, path):
        for key, value in flatten(args):
            norm = re.sub(r"\[\d+\]", "[]", key)
            head = norm.split(".")[0].split("[")[0]
            if head in ("data", "layout", "frames"):
                self.problem(
                    f"{path}.{key}",
                    "args may not carry inline traces or frames; reference frames by name",
                )
            elif norm in self.schema.trace_data:
                self.require(value, f"{path}.{key}", "restyled data key")
            elif norm in self.schema.trace_may:
                # Restyle wraps values per trace, so a per-point array sits one list deeper.
                for item in value if isinstance(value, list) else [value]:
                    self.require_if_list(item, f"{path}.{key}", f"restyled '{norm}'")
            elif head in ANCHORED and (
                norm == head or norm.rpartition(".")[2] in set().union(*ANCHORED[head])
            ):
                self.problem(f"{path}.{key}", f"args may not place {head}; draw them in the layout")


def main(out_path):
    by_parent = load_validators()
    traces = {t: classify(by_parent, t) for t in trace_types(by_parent)}
    # layout.template only holds per-trace-type style defaults; skip it.
    by_parent["layout"].pop("layout.template", None)
    layout = classify(by_parent, "layout", go.Layout._subplotid_prop_names)

    union = {k: {p for t in traces.values() for p in t[k]} for k in CATEGORIES}
    mixed = union["always"] & union["may"]
    union["always"] -= mixed
    union = {k: sorted(v) for k, v in union.items()}
    result = {
        "plotly_version": plotly.__version__,
        "notes": {
            "always": "data_array: always per-point data",
            "array_settings": "data_array-typed lists of ticks, categories or formats, not plotted values",
            "may": "arrayOk: data only when the value is an array",
            "any_trace": f"keys that are data_array on some trace types and arrayOk on others are under 'may': {sorted(mixed)}",
            "schema_silent": SCHEMA_SILENT,
            "encoding": "plotly.py may write 'always' arrays as {dtype, bdata, shape} base64 objects instead of lists",
            "nesting": "frames[].data[] and frames[].layout follow the same rules as data[] and layout",
        },
        "any_trace": union,
        "traces": traces,
        "layout": layout,
    }
    Path(out_path).write_text(json.dumps(result, indent=2) + "\n")

    print(f"plotly {plotly.__version__}: {len(traces)} trace types -> {out_path}")
    for k in CATEGORIES:
        per_trace = sum(len(t[k]) for t in traces.values())
        print(
            f"  {k:14} {per_trace:5} trace keys ({len(union[k])} distinct), {len(layout[k])} layout keys"
        )


if __name__ == "__main__":  # python -m weather_skills_plotting.lint [out.json]
    main(sys.argv[1] if len(sys.argv) > 1 else "plotly_data_keys.json")
