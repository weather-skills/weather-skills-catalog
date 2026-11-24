"""Map lines: Natural Earth layers (the built-in ``NE`` input), geoBoundaries admin
lines (the built-in ``GB`` input) and GeoJSON inputs.

Each loader returns shapely geometries; ``geoms_to_xy`` turns them into the
NaN-separated x / y arrays a Plotly line trace draws.
"""

from __future__ import annotations

import functools
import json
import sys
from importlib.resources import files

import numpy as np
from weather_skills_core.errors import UsageError

# Natural Earth layers for @NE.<layer>_<scale>: name → (category, shapefile name,
# agreed-boundary filter). ``ocean`` is polygons, the sea ws.map fills as a path shape.
# ``borders`` is agreed international boundaries; ``disputed``
# is every other land line (disputed, indefinite, line of control).
NE_LAYERS = {
    "rivers": ("physical", "rivers_lake_centerlines", None),
    "lakes": ("physical", "lakes", None),
    "borders": ("cultural", "admin_0_boundary_lines_land", True),
    "disputed": ("cultural", "admin_0_boundary_lines_land", False),
    "coastline": ("physical", "coastline", None),
    "ocean": ("physical", "ocean", None),
}


def window_parts(w, e):
    """``[(west, east, shift)]``: a lon window in Natural Earth's [-180, 180] longitudes.

    A window past 180° (170 to 200, or w > e as 170 to -160) is split at the
    dateline; its far part is clipped at -180..-160 and moved by ``shift`` (360°)
    to sit beside the near part on the map's continuous axis.
    """
    if w > e:
        e += 360.0
    parts = []
    for shift in (0.0, 360.0, -360.0):
        west, east = max(w - shift, -180.0), min(e - shift, 180.0)
        if west < east:
            parts.append((west, east, shift))
    return parts


# How far (degrees) from a view country's own border Natural Earth's lines are dropped:
# its 10m lines and core's 50m country polygons differ by up to a few hundredths.
VIEW_GAP = 0.05


@functools.cache
def view_country(iso3: str):
    """``(polygon, land border)`` of a country as core's region lookup draws it.

    The outline is the one resolve-region writes (geoBoundaries, or core's coarser
    Natural Earth 50m outline when that can't be reached), so a resolve-region outline
    drawn on top lines up. The land border is the part of its edge within 0.25° of a
    neighbour in core's bundled countries: coasts are left out, borders through
    lakes kept. A code that isn't three capital letters is a UsageError.
    """
    import shapely
    from shapely.geometry import shape
    from weather_skills_core.region import lookup_region

    if not (len(iso3) == 3 and iso3.isalpha() and iso3.isupper()):
        raise UsageError(f"view={iso3!r}: pass a country's ISO 3166 alpha-3 code, e.g. view='KEN'")
    country = shape(lookup_region(iso3)["geometry"])
    raw = json.loads(files("weather_skills_core.data").joinpath("countries.geojson").read_text())
    neighbours = shapely.union_all(
        [
            shape(f["geometry"])
            for f in raw.get("features") or []
            if f.get("geometry") and f["properties"].get("iso3") != iso3
        ]
    ).intersection(country.buffer(0.5))
    return country, country.boundary.intersection(neighbours.buffer(0.25))


@functools.cache
def load_ne_layer(layer: str, scale: str, n, w, s, e, view=None):
    """A Natural Earth layer's shapes clipped to the bbox (downloaded once by cartopy).

    If borders or coastline can't be fetched, the country outlines bundled with
    core stand in for both, with a warning; other layers are left out.

    ``view`` (an ISO3 code such as ``'KEN'``) draws that country's border its own
    way: the ``borders`` and ``disputed`` lines on or inside it are dropped, and
    ``borders`` draws its land border from core's countries instead.

    Results are cached: every panel's ``.x`` and ``.y`` of one clip share one load.
    """
    import shapely
    from shapely.geometry import box, shape

    category, name, agreed = NE_LAYERS[layer]
    if view is not None and layer in ("borders", "disputed"):
        country, own_border = view_country(view)
        near_country = country.buffer(VIEW_GAP)
    else:
        view = None
    geoms = []
    for west, east, shift in window_parts(w, e):
        clip = box(west, s, east, n)
        try:
            import cartopy.io.shapereader as shpreader

            path = shpreader.natural_earth(resolution=scale, category=category, name=name)
            records = list(shpreader.Reader(path, bbox=clip.bounds).records())
        except Exception as exc:  # noqa: BLE001 — offline, or cartopy missing
            if layer not in ("borders", "coastline"):
                print(f"Warning: NE.{layer} unavailable ({exc}); not drawn.", file=sys.stderr)
                return []
            print(
                f"Warning: NE.{layer} unavailable ({exc}); drawing the bundled country outlines.",
                file=sys.stderr,
            )
            raw = files("weather_skills_core.data").joinpath("countries.geojson").read_text()
            parts = []
            for feat in json.loads(raw).get("features") or []:
                geom = shape(feat["geometry"]) if feat.get("geometry") else None
                if geom is None or not geom.intersects(clip):
                    continue
                clipped = geom.intersection(clip)
                for part in getattr(clipped, "geoms", [clipped]):
                    if part.geom_type in ("Polygon", "MultiPolygon") and not part.is_empty:
                        parts.append(shapely.boundary(part))
        else:
            parts = []
            for rec in records:
                if agreed is not None:
                    is_agreed = str(rec.attributes.get("FEATURECLA", "")).startswith(
                        "International boundary"
                    )
                    if is_agreed != agreed:
                        continue
                geom = rec.geometry
                if geom is None or geom.is_empty or not geom.intersects(clip):
                    continue
                clipped = geom.intersection(clip)
                if not clipped.is_empty:
                    parts.extend(getattr(clipped, "geoms", [clipped]))
        if view is not None:
            # Natural Earth's lines near the view country give way to its own border.
            parts = [p.difference(near_country) for p in parts]
            if layer == "borders":
                parts.append(own_border.intersection(clip))
            parts = [
                g
                for p in parts
                for g in getattr(p, "geoms", [p])
                if not g.is_empty and g.geom_type in ("LineString", "LinearRing")
            ]
        geoms += [shapely.transform(g, lambda xy, d=shift: xy + [d, 0.0]) for g in parts]
    return geoms


# How far (degrees) an admin edge may sit from its country's outline and still count
# as that outline: geoBoundaries' simplified units leave slivers between neighbours.
ADMIN_GAP = 0.005


@functools.cache
def load_admin_lines(level: int, n, w, s, e):
    """geoBoundaries admin-``level`` lines (1 or 2) inside each country, clipped to the bbox.

    The units come from core's region lookup (the source resolve-region, clip-region
    and label-group use), one download per country the box touches. Only the lines
    between a country's units are kept: its outline is the borders and coastline
    layers' to draw. If the units can't be fetched, a warning is printed and nothing
    is drawn.
    """
    import shapely
    from shapely.geometry import box, shape
    from weather_skills_core.region import level_features

    geoms = []
    for west, east, shift in window_parts(w, e):
        try:
            features = level_features(f"admin_{level}", (n, west, s, east))
        except Exception as exc:  # noqa: BLE001 — offline, or geoBoundaries down
            print(f"Warning: GB.admin{level} unavailable ({exc}); not drawn.", file=sys.stderr)
            return []
        by_country = {}
        for feat in features:
            by_country.setdefault(feat["properties"]["iso3"], []).append(shape(feat["geometry"]))
        clip = box(west, s, east, n)
        for units in by_country.values():
            outline = shapely.union_all(units).boundary.buffer(ADMIN_GAP)
            inner = shapely.union_all([u.boundary for u in units]).difference(outline)
            inner = inner.intersection(clip)
            if not inner.is_empty:
                geoms.append(shapely.transform(inner, lambda xy, d=shift: xy + [d, 0.0]))
    return geoms


def load_geojson(path):
    """Every geometry in a GeoJSON file, as shapely shapes.

    Reads a FeatureCollection, a Feature or a bare geometry; features without a
    geometry are skipped. An unreadable file is a UsageError.
    """
    from shapely.geometry import shape

    try:
        raw = json.loads(open(path).read())
    except (OSError, ValueError) as exc:
        raise UsageError(f"GeoJSON input {path}: {exc}") from None
    if raw.get("type") == "FeatureCollection":
        items = [f.get("geometry") for f in raw.get("features") or []]
    elif raw.get("type") == "Feature":
        items = [raw.get("geometry")]
    else:
        items = [raw]
    return [shape(g) for g in items if g]


def geoms_to_path(geoms):
    """An SVG path tracing every polygon in ``geoms``, for a filled Plotly path shape.

    Each polygon's outer ring winds one way and its holes the other, so a nonzero
    fill leaves the holes (islands in the sea) empty.
    """
    import shapely
    from shapely.geometry.polygon import orient

    parts = []
    for geom in geoms:
        for part in shapely.get_parts(shapely.get_parts(geom)):
            if part.geom_type != "Polygon" or part.is_empty:
                continue
            part = orient(part, 1.0)
            for ring in [part.exterior, *part.interiors]:
                xy = " L".join(f"{x:.4f},{y:.4f}" for x, y in ring.coords)
                parts.append(f"M{xy}Z")
    return "".join(parts)


def geoms_to_xy(geoms):
    """NaN-separated x / y arrays tracing every line and polygon ring in ``geoms``."""
    import shapely

    xs, ys = [], []
    # get_parts splits multi-part shapes and collections into single shapes.
    for geom in geoms:
        for part in shapely.get_parts(shapely.get_parts(geom)):
            if part.geom_type == "Polygon":
                rings = [part.exterior, *part.interiors]
            elif part.geom_type in ("LineString", "LinearRing"):
                rings = [part]
            else:
                continue
            for ring in rings:
                x, y = ring.xy
                xs += [*x, np.nan]
                ys += [*y, np.nan]
    return np.array(xs, dtype=float), np.array(ys, dtype=float)
