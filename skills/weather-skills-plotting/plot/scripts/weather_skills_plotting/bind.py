"""Bind: fill each ``@`` reference in the rendered spec with data from an input.

A string that starts with ``@`` is a reference; ``@@`` starts a literal ``@``.
The text after ``@`` is a Python expression, parsed with :mod:`ast` and walked
against the constants below. Nothing is ``eval``'d.

A reference starts with a name: an input (``-i PATH=NAME``), ``NE`` (Natural
Earth lines, ``geo.NE_LAYERS``), ``GB`` (geoBoundaries admin lines, ``admin1`` /
``admin2``) or ``const`` (the ``--const`` values). Then come
attributes (a variable, a coordinate, ``.dt.<DT_FIELDS>``, ``.attrs.<name>``)
and calls to ``METHODS``; ``lat`` / ``lon`` / ``time`` stand for the input's
own names for those dims (``_ALIASES``).

Every method selects values, reshapes them, labels them, or gives a display
limit, so every value drawn is a value stored in an input; calculations belong
in data skills. Two kinds of reference are checked against the key they fill:
a display limit only on ``LIMIT_KEYS``, and arithmetic, the one calculation
allowed (a marker size in pixels), only on ``SCALED_KEYS``.
"""

from __future__ import annotations

import ast
import sys

import numpy as np
from weather_skills_core.cf import cf_dim
from weather_skills_core.errors import UsageError

from weather_skills_plotting import geo

# The methods a reference may call. Any other method is refused.
METHODS = (
    # Select: by position, by value, the entries with a value, a Natural Earth layer's box.
    "isel",
    "sel",
    "dropna",
    "clip",
    # Reshape: drop size-1 dims, relabel longitudes, set dim order, one value per cell.
    "squeeze",
    "lon360",
    "lon180",
    "transpose",
    "flat",
    # Label: time as text, after .dt.
    "strftime",
    # Display limit: one number from the whole array, only on LIMIT_KEYS.
    "min",
    "max",
    "quantile",
)

# Keys a display limit may fill: a reference ending in .min(), .max() or .quantile(q)
# goes only where its JSON path ends in one of these (or an item of range / tickvals).
LIMIT_KEYS = frozenset({"zmin", "zmax", "zmid", "cmin", "cmax", "cmid", "range", "tickvals"})

# Keys that are sizes in pixels, the only ones whose reference may take arithmetic: data
# + - * / a number or @const ('@W.wind_speed.flat() * 3' is 3 px per unit, as Plotly's
# sizeref scales marker.size). It sets how big a marker is on screen, not what is drawn.
SCALED_KEYS = ("marker.size", "marker.standoff", "marker.line.width", "textfont.size")

# The operators that arithmetic may use.
ARITHMETIC = {ast.Add: np.add, ast.Sub: np.subtract, ast.Mult: np.multiply, ast.Div: np.divide}

# Fields of .dt: parts of a time, and .days of a lead time.
DT_FIELDS = ("year", "month", "day", "hour", "dayofyear", "days")

# Short dim names a reference may use for any input; cf_dim finds the input's own name
# (lat for latitude, and so on).
_ALIASES = {"lat": "latitude", "lon": "longitude", "time": "time"}


class GeoLines:
    """Shapes from a GeoJSON input or a clipped Natural Earth layer, drawn as ``.x`` / ``.y``."""

    def __init__(self, geoms):
        self.geoms = geoms


class NaturalEarth:
    """The built-in ``NE`` input: ``NE.<layer>_<scale>.clip(n, w, s, e)``."""


class NELayer:
    """``@NE.<layer>_<scale>`` before its ``.clip(n, w, s, e)``: which layer to load."""

    def __init__(self, layer, scale):
        self.layer, self.scale = layer, scale


class GeoBoundaries:
    """The built-in ``GB`` input: ``GB.admin1.clip(n, w, s, e)`` or ``GB.admin2``."""


class GBLayer:
    """``@GB.admin<level>`` before its ``.clip(n, w, s, e)``: which level to load."""

    def __init__(self, level):
        self.level = level


class _Accessor:
    """``.dt`` or ``.attrs`` of a DataArray, waiting for its field."""

    def __init__(self, kind, da):
        self.kind, self.da = kind, da


def _dim(da, name, ref):
    """The name of the dim of ``da`` that ``name`` means.

    That is ``name`` itself, or for ``lat`` / ``lon`` / ``time`` the input's own CF
    latitude / longitude / time dim. A UsageError lists the dims if there is none.
    """
    if name in da.dims:
        return name
    if name in _ALIASES:
        dim = cf_dim(da, _ALIASES[name])
        if dim in da.dims:
            return dim
    raise UsageError(f"{ref}: no dim {name!r}; its dims are {list(da.dims)}")


def _literal(node, ref):
    """A method argument as a Python value.

    Only constants are allowed: numbers (negative too), strings, None, lists or
    tuples of them, and ``slice(...)``. Anything else (a name, a call, arithmetic)
    is a UsageError.
    """
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        value = _literal(node.operand, ref)
        if isinstance(value, (int, float)):
            return -value
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_literal(n, ref) for n in node.elts]
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "slice"
        and not node.keywords
    ):
        return slice(*[_literal(n, ref) for n in node.args])
    raise UsageError(
        f"{ref}: arguments must be numbers, strings, lists of them or slice(...); "
        f"got {ast.unparse(node)!r}"
    )


def _refuse(node, ref):
    """Raise the UsageError for a part of a reference that isn't allowed.

    Arithmetic outside a pixel size has its own message; any other syntax
    (a subscript, a comprehension, a bare call) gets a generic one.
    """
    if isinstance(node, ast.BinOp):
        raise UsageError(
            f"{ref}: arithmetic ({ast.unparse(node)!r}) is only for a size in pixels: + - * / "
            f"of data by a number or @const, on {', '.join(SCALED_KEYS)}. For a calculated field, "
            "compute it with a data skill first and plot the result"
        )
    raise UsageError(
        f"{ref}: {type(node).__name__} is not allowed in a reference; references select, "
        "reshape and label stored values (see the reference table in SKILL.md)"
    )


def _method(base, name, args, kwargs, ref):
    """Return ``base.<name>(*args, **kwargs)`` for one of METHODS, checking its arguments.

    ``base`` is what the reference holds so far: a DataArray, a Natural Earth
    layer (which only takes ``.clip``), or a ``.dt`` accessor (only ``.strftime``).
    A method outside METHODS, wrong arguments, or a selection that finds nothing
    (the error then says what each picked dim holds) is a UsageError.
    """
    if name not in METHODS:
        raise UsageError(
            f"{ref}: .{name}() is not allowed; references select, reshape and label stored "
            f"values with {', '.join(METHODS)}. For a calculated field, compute it with a data skill first and plot the result"
        )
    if isinstance(base, NELayer):
        if name != "clip" or len(args) != 4 or set(kwargs) - {"view"}:
            raise UsageError(
                f"{ref}: a Natural Earth layer takes .clip(n, w, s, e), or "
                ".clip(n, w, s, e, view='KEN') for a country's own border"
            )
        n, w, s, e = (float(v) for v in args)
        return GeoLines(
            geo.load_ne_layer(base.layer, base.scale, n, w, s, e, view=kwargs.get("view"))
        )
    if isinstance(base, GBLayer):
        if name != "clip" or len(args) != 4 or kwargs:
            raise UsageError(f"{ref}: a geoBoundaries layer takes .clip(n, w, s, e)")
        n, w, s, e = (float(v) for v in args)
        return GeoLines(geo.load_admin_lines(base.level, n, w, s, e))
    if isinstance(base, _Accessor):
        if base.kind == "dt" and name == "strftime" and len(args) == 1 and not kwargs:
            try:
                return base.da.dt.strftime(args[0])
            except (AttributeError, TypeError):
                raise UsageError(f"{ref}: .dt.strftime() needs dates, not lead times") from None
        raise UsageError(f"{ref}: .{base.kind}.{name}() is not allowed")
    if not hasattr(base, "dims"):
        raise UsageError(f"{ref}: .{name}() needs a variable or coordinate before it")
    da = base
    if name in ("isel", "sel"):
        if args:
            raise UsageError(f"{ref}: .{name}() takes dim=value pairs, e.g. .{name}(time=0)")
        method = kwargs.pop("method", None)
        if name == "isel" and method is not None:
            raise UsageError(f"{ref}: .isel() takes no method")
        if method not in (None, "nearest"):
            raise UsageError(f"{ref}: .sel() method must be 'nearest'")
        picks = {_dim(da, k, ref): v for k, v in kwargs.items()}
        try:
            return getattr(da, name)(picks, method=method) if method else getattr(da, name)(picks)
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            # Say what each picked dim holds, so the right value or position is plain.
            held = []
            for dim in picks:
                values = da[dim].values
                if values.size == 0:
                    held.append(f"{dim} has no values left")
                    continue
                first, last = (_iso(v.item() if hasattr(v, "item") else v) for v in values[[0, -1]])
                held.append(f"{dim} has {values.size} values, {first} to {last}")
            raise UsageError(
                f"{ref}: nothing at that {'position' if name == 'isel' else 'value'} "
                f"({exc}); {'; '.join(held)}"
            ) from None
    if name == "dropna":
        if len(args) != 1 or kwargs:
            raise UsageError(f"{ref}: .dropna(dim) takes one dim, e.g. .dropna('station_id')")
        return da.dropna(_dim(da, args[0], ref))
    if name in ("lon360", "lon180"):
        # Longitudes as 0..360 or -180..180, in order, so a field that crosses the dateline
        # (lon360) or the prime meridian (lon180) draws as one piece.
        lon = _dim(da, "lon", ref) if "lon" not in da.coords else "lon"
        wrap = (lambda v: v % 360) if name == "lon360" else (lambda v: (v + 180) % 360 - 180)
        if da.name == lon:
            da = da.copy(data=wrap(np.asarray(da.values)))
        da = da.assign_coords({lon: wrap(da[lon])})
        return da.sortby(lon) if lon in da.dims else da
    if name == "squeeze":
        return da.squeeze()
    if name == "transpose":
        order = [_dim(da, d, ref) for d in args]
        extra = [d for d in da.dims if d not in order]
        if extra:
            raise UsageError(
                f"{ref}: dims {extra} are left besides {order}; pick a value along them with "
                ".isel(dim=…) / .sel(dim=…) before .transpose()"
            )
        return da.transpose(*order)
    if name == "flat":
        # A 2-D selection as one value per cell (a lat/lon grid row by row), and with a dim
        # named, that dim's coordinate for each value: grid cells as points, or members
        # with their lead for one box per lead.
        if da.ndim != 2:
            raise UsageError(f"{ref}: .flat() needs two dims; dims are {list(da.dims)}")
        lat, lon = cf_dim(da, "latitude"), cf_dim(da, "longitude")
        grid = da.transpose(lat, lon) if set(da.dims) == {lat, lon} else da
        if not args:
            return np.asarray(grid.values).ravel()
        which = _dim(da, args[0], ref)
        values = np.asarray(grid[which].broadcast_like(grid).transpose(*grid.dims).values).ravel()
        if values.dtype.kind == "m":
            # A lead time, in days.
            values = values / np.timedelta64(1, "D")
        return values
    if name in ("min", "max", "quantile"):
        if kwargs or (name != "quantile" and args) or (name == "quantile" and len(args) != 1):
            raise UsageError(
                f"{ref}: .{name}() works on the whole array only (a display limit); "
                "for an average over a dim, compute it with a data skill first and plot the result"
            )
        values = np.asarray(da.values, dtype=float)
        if values.size == 0:
            raise UsageError(f"{ref}: the selection is empty, so it has no {name}")
        if not np.isfinite(values).any():
            raise UsageError(f"{ref}: every value is NaN")
        if name == "quantile":
            return float(np.nanquantile(values, float(args[0])))
        return float(np.nanmin(values) if name == "min" else np.nanmax(values))
    raise UsageError(f"{ref}: .{name}() is not allowed here")


def evaluate(text: str, inputs: dict):
    """Evaluate one reference, e.g. ``'@OBS.precip.isel(time=0)'``, against ``inputs``.

    ``inputs`` maps each input name to a Dataset or GeoLines, and ``const`` to the
    --const values. Returns ``(value, last)``: the value (a DataArray, array, number
    or text, not yet JSON) and what the reference ends in (``'arithmetic'``, the
    last method's name, or None), which ``resolve`` checks against the key it fills.
    """
    ref = text
    try:
        tree = ast.parse(text[1:], mode="eval")
    except SyntaxError as exc:
        raise UsageError(
            f"{ref}: not a valid reference ({exc.msg}). A string that starts with @ is a "
            'reference; start literal text with @@ ("@@home" draws @home)'
        ) from None

    def walk(node):
        """The value of one node of the parsed reference."""
        if isinstance(node, ast.Name):
            if node.id == "NE":
                return NaturalEarth()
            if node.id == "GB":
                return GeoBoundaries()
            if node.id == "const":
                return inputs.get("const", {})
            if node.id not in inputs:
                raise UsageError(
                    f"{ref}: {node.id!r} is not an input; inputs are "
                    f"{', '.join(inputs) or 'none'} (name each with -i PATH=NAME)"
                )
            return inputs[node.id]
        if isinstance(node, ast.Attribute):
            base = walk(node.value)
            return _attribute(base, node.attr, ref)
        if isinstance(node, ast.Call):
            # Name the start of the chain before its methods: @open("x").read() is not an input.
            root = node
            while isinstance(root, (ast.Attribute, ast.Call)):
                root = root.func if isinstance(root, ast.Call) else root.value
            if not (isinstance(root, ast.Name) and root.id in {*inputs, "NE", "GB", "const"}):
                start = repr(root.id) if isinstance(root, ast.Name) else ast.unparse(root)
                raise UsageError(
                    f"{ref}: a reference starts with an input name, not {start}; inputs are "
                    f"{', '.join(inputs) or 'none'} (name each with -i PATH=NAME)"
                )
            if not isinstance(node.func, ast.Attribute):
                _refuse(node, ref)
            refused = [
                n.func.attr
                for n in ast.walk(node)
                if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and n.func.attr not in METHODS
            ]
            if refused:
                # The innermost one is the step to do upstream (.rolling(...) in .rolling().mean()).
                _method(None, refused[-1], [], {}, ref)
            base = walk(node.func.value)
            args = [_literal(a, ref) for a in node.args]
            kwargs = {}
            for kw in node.keywords:
                if kw.arg is None:
                    _refuse(kw.value, ref)
                kwargs[kw.arg] = _literal(kw.value, ref)
            return _method(base, node.func.attr, args, kwargs, ref)
        if isinstance(node, ast.BinOp) and type(node.op) in ARITHMETIC:
            # A pixel size: data on exactly one side, a number (or a const) on the other, so
            # a size never combines two fields.
            if _has_data(node.left) == _has_data(node.right):
                _refuse(node, ref)
            data = []
            for side in (node.left, node.right):
                if _has_data(side):
                    value = walk(side)
                    data.append(
                        np.asarray(value.values if hasattr(value, "dims") else value, float)
                    )
                    continue
                # A number: a literal, or @const.NAME.
                if isinstance(side, ast.Attribute) and getattr(side.value, "id", None) == "const":
                    number = _attribute(inputs.get("const", {}), side.attr, ref)
                else:
                    number = _literal(side, ref)
                if not isinstance(number, (int, float)) or isinstance(number, bool):
                    raise UsageError(f"{ref}: {ast.unparse(side)!r} is not a number")
                data.append(np.asarray(float(number)))
            if isinstance(node.op, ast.Div) and not np.all(data[1]):
                raise UsageError(f"{ref}: division by zero")
            return ARITHMETIC[type(node.op)](*data)
        _refuse(node, ref)

    # What the reference ends in: arithmetic, or a method (max in @A.b.max()).
    body = tree.body
    if isinstance(body, ast.BinOp):
        last = "arithmetic"
        # A pixel size scales stored values; a display limit (.max()) is not one.
        reduced = next(
            (
                n.func.attr
                for n in ast.walk(body)
                if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and n.func.attr in ("min", "max", "quantile")
            ),
            None,
        )
        if reduced:
            raise UsageError(
                f"{ref}: .{reduced}() is a display limit, set on a scale key as it is; "
                "arithmetic is for a size in pixels from stored values"
            )
    else:
        last = (
            body.func.attr
            if isinstance(body, ast.Call) and isinstance(body.func, ast.Attribute)
            else None
        )
        nested = next((n for n in ast.walk(body) if isinstance(n, ast.BinOp)), None)
        if nested is not None:
            _refuse(nested, ref)
    return walk(body), last


def _has_data(node):
    """Whether an operand names an input (anything but a number or @const)."""
    names = [n.id for n in ast.walk(node) if isinstance(n, ast.Name)]
    return any(name != "const" for name in names)


def _attribute(base, attr, ref):
    """Return ``base.<attr>``, for the kinds of ``base`` a reference can reach.

    A --const value, a Natural Earth layer by name, a GeoJSON input's ``.x`` / ``.y``,
    a ``.dt`` or ``.attrs`` field, or a variable or coordinate of an input or of a
    selection (``lat`` / ``lon`` / ``time`` by alias). Names starting with ``_`` and
    anything else are a UsageError.
    """
    if attr.startswith("_"):
        raise UsageError(f"{ref}: {attr!r} is not allowed")
    if isinstance(base, dict):
        # @const.NAME: a number or text passed with --const NAME=VALUE.
        if attr not in base:
            raise UsageError(
                f"{ref}: no constant {attr!r}; pass it with --const {attr}=VALUE "
                f"(given: {', '.join(base) or 'none'})"
            )
        return base[attr]
    if isinstance(base, NaturalEarth):
        layer, _, scale = attr.rpartition("_")
        if layer not in geo.NE_LAYERS or scale not in ("10m", "50m", "110m"):
            raise UsageError(
                f"{ref}: NE has {', '.join(geo.NE_LAYERS)}, each as NAME_10m, NAME_50m or "
                "NAME_110m, e.g. @NE.coastline_50m.clip(n, w, s, e).x; admin lines are "
                "@GB.admin1 / @GB.admin2"
            )
        return NELayer(layer, scale)
    if isinstance(base, GeoBoundaries):
        if attr not in ("admin1", "admin2"):
            raise UsageError(f"{ref}: GB has admin1 and admin2, e.g. @GB.admin1.clip(n, w, s, e).x")
        return GBLayer(int(attr[-1]))
    if isinstance(base, GBLayer):
        raise UsageError(f"{ref}: clip a geoBoundaries layer first: .clip(n, w, s, e).{attr}")
    if isinstance(base, NELayer):
        raise UsageError(f"{ref}: clip a Natural Earth layer first: .clip(n, w, s, e).{attr}")
    if isinstance(base, GeoLines):
        if attr == "path":
            # A filled shape's SVG path (the sea under the data), not points to draw.
            return geo.geoms_to_path(base.geoms)
        if attr not in ("x", "y", "lon", "lat"):
            raise UsageError(
                f"{ref}: a GeoJSON input has .x (or .lon) and .y (or .lat), and .path "
                "for a filled path shape"
            )
        return geo.geoms_to_xy(base.geoms)[0 if attr in ("x", "lon") else 1]
    if isinstance(base, _Accessor):
        if base.kind == "attrs":
            if attr not in base.da.attrs:
                raise UsageError(f"{ref}: no attribute {attr!r}; has {list(base.da.attrs)}")
            return str(base.da.attrs[attr])
        if attr not in DT_FIELDS:
            raise UsageError(f"{ref}: .dt has {', '.join(DT_FIELDS)} and .strftime(format)")
        try:
            return getattr(base.da.dt, attr)
        except (AttributeError, TypeError):
            kind = "a lead time" if base.da.dtype.kind == "m" else "a date"
            fields = "days" if kind == "a lead time" else "year, month, day, hour, dayofyear"
            raise UsageError(
                f"{ref}: .dt.{attr} is not a field of {kind}; it has {fields}"
            ) from None
    if hasattr(base, "data_vars"):
        name = attr
        if attr in _ALIASES and attr not in base.variables:
            name = cf_dim(base, _ALIASES[attr])
        if name is None or name not in base.variables:
            raise UsageError(
                f"{ref}: no variable or coordinate {attr!r}; data variables: "
                f"{', '.join(map(str, base.data_vars))}; coordinates: "
                f"{', '.join(map(str, base.coords))}"
            )
        return base[name]
    if hasattr(base, "dims"):
        if attr in ("dt", "attrs"):
            if attr == "dt" and base.dtype.kind not in "mM" and base.dtype != object:
                raise UsageError(f"{ref}: .dt needs a time or timedelta coordinate")
            return _Accessor(attr, base)
        # A coordinate of what is left after a selection: the lon / lat of the
        # points .dropna() kept, the valid times of the steps picked.
        name = attr
        if attr in _ALIASES and attr not in base.coords:
            name = cf_dim(base, _ALIASES[attr])
        if name in base.coords:
            return base[name]
        raise UsageError(
            f"{ref}: {attr!r} is neither .dt, .attrs nor a coordinate of this selection "
            f"(coordinates: {', '.join(map(str, base.coords))})"
        )
    raise UsageError(f"{ref}: {attr!r} is not allowed here")


def to_json_value(value, path: str, ref: str):
    """The JSON value Plotly takes at ``path`` for a resolved reference.

    Its shape is checked against the key: ``z`` is 2-D (a lat/lon grid as rows of
    latitude), ``customdata`` 1-D or 2-D, anything else 1-D or one value, after
    size-1 dims are dropped. Times become ISO text (a midnight drops its clock),
    numbers a float array, anything else a list. Lead times, and a whole input or
    layer with no variable named, are a UsageError.
    """
    if isinstance(value, (str, int, float)):
        return value
    if isinstance(
        value, (GeoLines, NELayer, NaturalEarth, GBLayer, GeoBoundaries, _Accessor)
    ) or hasattr(value, "data_vars"):
        raise UsageError(f"{path} = {ref}: name a variable or coordinate to draw")
    key = path.rsplit(".", 1)[-1].split("[")[0]
    if hasattr(value, "dims"):
        da = value
        allowed = 2 if key in ("z", "customdata") else 1
        # A dim of length 1 (the one time of a period total) holds no choice: drop such
        # dims while there are more than the attribute takes.
        for dim in [d for d in da.dims if da.sizes[d] == 1]:
            if da.ndim > allowed:
                da = da.squeeze(dim)
        if key == "z":
            lat, lon = cf_dim(da, "latitude"), cf_dim(da, "longitude")
            if da.ndim == 2 and set(da.dims) == {lat, lon}:
                da = da.transpose(lat, lon)
        if key == "z" and da.ndim < 2:
            raise UsageError(
                f"{path} = {ref} has dims {list(da.dims)}; z needs a 2-D grid, so keep two dims "
                "(lat and lon, or e.g. vertical and lon) and pick only the others"
            )
        if da.ndim > allowed:
            want = "2-D (a grid)" if allowed == 2 else "1-D or a single value"
            raise UsageError(
                f"{path} = {ref} has dims {list(da.dims)}; {key} needs {want}. "
                "Pick a position along the other dims with .isel(dim=…) / .sel(dim=…), "
                "draw one trace per value (a Jsonnet loop), or reduce the dim with a data skill first"
            )
        values = np.asarray(da.values)
    else:
        values = np.asarray(value)
    if values.dtype.kind == "m":
        raise UsageError(
            f"{path} = {ref}: these are lead times; to plot by valid date, relabel the leads as "
            "valid times with a data skill first, or use .dt.days for lead days"
        )
    if values.dtype.kind == "M":
        # ISO times; a time that is midnight drops its clock part (2026-10-01).
        text = np.datetime_as_string(values, unit="s")
        values = np.vectorize(lambda v: v[:10] if v.endswith("T00:00:00") else v, otypes=[object])(
            text
        )
    elif values.dtype == object:
        values = np.vectorize(_iso, otypes=[object])(values)
    if values.ndim == 0:
        item = values.item()
        return float(item) if isinstance(item, (np.floating, float)) else item
    if values.dtype.kind in "biuf":
        return values.astype(float)
    return values.tolist()


def _iso(item):
    """An object coordinate value (a cftime date, a label) as text, ISO when it is a date."""
    return item.isoformat() if hasattr(item, "isoformat") else str(item)


def resolve(spec, inputs: dict):
    """Replace every ``@`` reference in ``spec`` with its data from ``inputs``.

    Each reference is evaluated, checked against the key it fills (arithmetic only
    on SCALED_KEYS, a display limit only on LIMIT_KEYS) and turned into JSON by
    ``to_json_value``; ``@@`` text loses one ``@``. Then the whole figure is
    checked: a trace's x and y pair up point by point, a lat/lon ``z`` has x and y
    set, and traces sharing a coloraxis in different units print a warning.

    Returns the filled spec and ``{path: reference}`` for every reference.
    """
    found = {}
    grids = []
    # The dim and coordinate values each 1-D reference runs along, to check x / y pair up.
    along = {}
    # The units of what fills each color (z, marker.color), to check shared colorbars.
    color_units = {}

    def walk(node, path):
        """``node`` with its references filled; ``path`` is its JSON path (``data[0].z``)."""
        if isinstance(node, dict):
            return {k: walk(v, f"{path}.{k}" if path else k) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v, f"{path}[{i}]") for i, v in enumerate(node)]
        if not isinstance(node, str) or not node.startswith("@"):
            return node
        if node.startswith("@@"):
            return node[1:]
        value, last = evaluate(node, inputs)
        if last == "arithmetic" and not path.endswith(SCALED_KEYS):
            raise UsageError(
                f"{path} = {node}: arithmetic is only for a size in pixels, on "
                f"{', '.join(SCALED_KEYS)}. For a calculated field, compute it with a data skill first and plot the result"
            )
        keys = [k for k in path.replace("[", ".[").split(".") if k and not k.startswith("[")]
        # Under meta a limit would reach hover text as a computed number.
        is_limit_key = "meta" not in keys and (
            keys[-1] in LIMIT_KEYS or {"range", "tickvals"} & set(keys[-2:])
        )
        if last in ("min", "max", "quantile") and not is_limit_key:
            raise UsageError(
                f"{path} = {node}: .{last}() gives one number, for a scale only "
                f"({', '.join(sorted(LIMIT_KEYS))}); data to draw is stored values"
            )
        found[path] = node
        if hasattr(value, "attrs") and path.endswith((".z", ".marker.color")):
            color_units[path] = str(value.attrs.get("units", ""))
        # A whole input (a Dataset) has no dims of its own; to_json_value names the fix.
        if hasattr(value, "dims") and not hasattr(value, "data_vars") and value.squeeze().ndim == 1:
            dim = value.squeeze().dims[0]
            if dim in value.coords:
                along[path] = (dim, np.asarray(value.squeeze()[dim].values))
        # A frame's trace may set only z; it takes x / y from the trace it updates.
        if path.startswith("data[") and path.endswith(".z") and hasattr(value, "dims"):
            if {cf_dim(value, "latitude"), cf_dim(value, "longitude")} <= set(value.dims):
                grids.append((path[:-2], node))
        return to_json_value(value, path, node)

    resolved = walk(spec, "")
    # x and y pair up point by point; different lengths mean a dim was left in one of them.
    traces = [(f"data[{i}]", t) for i, t in enumerate(resolved.get("data") or [])]
    for f, frame in enumerate(resolved.get("frames") or []):
        traces += [(f"frames[{f}].data[{i}]", t) for i, t in enumerate(frame.get("data") or [])]
    for path, trace in traces:
        # Gridded traces (z) take x and y as the grid's two axes, of their own lengths.
        if not isinstance(trace, dict) or "z" in trace:
            continue
        x, y = trace.get("x"), trace.get("y")
        if (
            hasattr(x, "__len__")
            and hasattr(y, "__len__")
            and not isinstance(x, str)
            and not isinstance(y, str)
        ):
            if np.ndim(x) == 1 and np.ndim(y) == 1 and len(x) != len(y):
                raise UsageError(
                    f"{path}: x has {len(x)} values and y has {len(y)}, so they don't pair up; "
                    f"pick the same positions for both (x = {found.get(path + '.x', 'literal')}, "
                    f"y = {found.get(path + '.y', 'literal')}). A dim left in one of them, such as "
                    "number, needs .isel(...) or one trace per value"
                )
            ax, ay = along.get(path + ".x"), along.get(path + ".y")
            if ax and ay and ax[0] == ay[0] and not np.array_equal(ax[1], ay[1]):
                raise UsageError(
                    f"{path}: x and y run along different {ax[0]} values "
                    f"({found[path + '.x']} vs {found[path + '.y']}), so their points would pair "
                    f"up wrongly; put both variables in one file first (merge), or pick the same "
                    f"{ax[0]} values for both"
                )
    for trace_path, ref in grids:
        trace = _at(resolved, trace_path)
        if "x" not in trace or "y" not in trace:
            name = ref[1:].split(".")[0]
            raise UsageError(
                f"{trace_path}: z = {ref} is a lat/lon grid; set x: '@{name}.lon' and "
                f"y: '@{name}.lat' too, so it sits on degree axes with outlines and the base map"
            )
    # Traces on one coloraxis share one colorbar; different units there mislabel one of them.
    by_axis = {}
    for path, units in color_units.items():
        trace_path = (
            path.rsplit(".z", 1)[0] if path.endswith(".z") else path.rsplit(".marker", 1)[0]
        )
        trace = _at(resolved, trace_path)
        axis = trace.get("coloraxis") or (trace.get("marker") or {}).get("coloraxis")
        if axis and units:
            by_axis.setdefault(axis, {}).setdefault(units, []).append(trace_path)
    for axis, units in by_axis.items():
        if len(units) > 1:
            listed = "; ".join(f"{u} in {', '.join(paths)}" for u, paths in units.items())
            print(
                f"Warning: {axis} colors traces in different units ({listed}); one colorbar "
                "can only label one. Convert them to one unit, or give each its own coloraxis",
                file=sys.stderr,
            )
    return resolved, found


def _at(tree, path):
    """The object at a JSON path such as ``data[0].marker`` in ``tree``."""
    for part in path.replace("[", ".[").split("."):
        if part:
            tree = tree[int(part[1:-1])] if part.startswith("[") else tree[part]
    return tree
