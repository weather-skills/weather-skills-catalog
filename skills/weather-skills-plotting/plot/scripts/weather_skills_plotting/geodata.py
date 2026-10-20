"""Lon/lat helpers: Natural Earth overlays, spatial subsets, map extents."""

from __future__ import annotations

import json
import sys
from importlib.resources import files

import numpy as np
from weather_skills_core.cf import cf_dim
from weather_skills_core.errors import UsageError
from weather_skills_core.standard_utils import ensure_normalized_longitude, lat_slice

# Natural Earth scale vs map span (max of the lon/lat extent in degrees).
# Admin-1 (states / provinces / counties) is only readable on country-scale
# views; a multi-country or basin map would be a thicket of province lines.
ADMIN1_MAX_SPAN_DEG = 20.0


HIRES_MAX_SPAN_DEG = 45.0


MIDRES_MAX_SPAN_DEG = 90.0


def extent_span_deg(extent):
    lon_min, lon_max, lat_min, lat_max = extent
    return max(abs(lon_max - lon_min), abs(lat_max - lat_min))


def boundary_layers(extent):
    """Natural Earth scale and whether to overlay admin-1 for this view."""
    span = extent_span_deg(extent)
    if span > MIDRES_MAX_SPAN_DEG:
        return {"scale": "110m", "admin1": False}
    if span > HIRES_MAX_SPAN_DEG:
        return {"scale": "50m", "admin1": False}
    return {"scale": "10m", "admin1": span <= ADMIN1_MAX_SPAN_DEG}


def extent_clip_geom(extent):
    """Shapely clip geometry for ``lon_min,lon_max,lat_min,lat_max``.

    Antimeridian views store a continuous unwrapped lon (e.g. 170..190) which
    is split back into ``[-180, 180]`` pieces for Natural Earth intersection.
    """
    from shapely.geometry import box

    lon_min, lon_max, lat_min, lat_max = extent
    if lon_max > 180.0:
        return box(lon_min, lat_min, 180.0, lat_max).union(
            box(-180.0, lat_min, lon_max - 360.0, lat_max)
        )
    if lon_min > lon_max:
        return box(lon_min, lat_min, 180.0, lat_max).union(box(-180.0, lat_min, lon_max, lat_max))
    return box(lon_min, lat_min, lon_max, lat_max)


def unwrap_geoms(geoms, lon_min):
    """Shift western-hemisphere pieces so they match an unwrapped lon axis."""
    import numpy as np
    import shapely

    def shift(coords):
        out = np.asarray(coords).copy()
        out[:, 0] = np.where(out[:, 0] < lon_min, out[:, 0] + 360.0, out[:, 0])
        return out

    return [shapely.transform(g, shift) for g in geoms if g is not None and not g.is_empty]


def clip_ne_geoms(resolution, category, name, clip_geom):
    """Natural Earth geometries intersecting ``clip_geom`` (eager download)."""
    import cartopy.io.shapereader as shpreader

    path = shpreader.natural_earth(resolution=resolution, category=category, name=name)
    geoms = []
    # The bbox skips shapes outside the view without parsing them.
    for geom in shpreader.Reader(path, bbox=clip_geom.bounds).geometries():
        if geom is None or geom.is_empty:
            continue
        try:
            if not geom.intersects(clip_geom):
                continue
            clipped = geom.intersection(clip_geom)
        except Exception:  # noqa: BLE001
            clipped = geom
        if clipped is None or clipped.is_empty:
            continue
        if clipped.geom_type == "GeometryCollection":
            geoms.extend(g for g in clipped.geoms if g is not None and not g.is_empty)
        else:
            geoms.append(clipped)
    return geoms


def _bundled_country_geoms(clip_geom):
    """Country outlines from the bundled Natural Earth extract (no cartopy)."""
    import shapely
    from shapely.geometry import shape

    raw = json.loads(files("weather_skills_core.data").joinpath("countries.geojson").read_text())
    geoms = []
    for feat in raw.get("features") or []:
        geom = feat.get("geometry")
        if not geom:
            continue
        try:
            poly = shape(geom)
            if not poly.intersects(clip_geom):
                continue
            geoms.append(poly.intersection(clip_geom))
        except Exception:  # noqa: BLE001
            continue
    return [shapely.boundary(g) for g in geoms if g is not None and not g.is_empty]


# Base-map layers ``layout.meta.overlays`` can switch on or off, in draw order (later on top).
OVERLAY_NAMES = ("admin1", "rivers", "lakes", "borders", "coastline")


def plain(da):
    if getattr(da.pint, "units", None) is not None:
        return da.pint.dequantify()
    return da


def step_dim(da):
    for cand in ("step", "time", "valid_time"):
        if cand in da.dims:
            return cand
    cf = cf_dim(da, "time")
    return cf if cf and cf in da.dims else None


def pad_cell_extent(lat_vals, lon_vals):
    lat_vals = np.asarray(lat_vals, dtype=float)
    lon_vals = np.asarray(lon_vals, dtype=float)
    dlat = float(np.nanmean(np.abs(np.diff(lat_vals)))) if lat_vals.size > 1 else 0.5
    dlon = float(np.nanmean(np.abs(np.diff(lon_vals)))) if lon_vals.size > 1 else 0.5
    lon_min = float(np.nanmin(lon_vals)) - dlon / 2
    lon_max = float(np.nanmax(lon_vals)) + dlon / 2
    lat_min = float(np.nanmin(lat_vals)) - dlat / 2
    lat_max = float(np.nanmax(lat_vals)) + dlat / 2
    if lon_max - lon_min >= 360.0 - 1e-6:
        lon_min, lon_max = -180.0, 180.0
    lat_min = max(lat_min, -90.0)
    lat_max = min(lat_max, 90.0)
    return [lon_min, lon_max, lat_min, lat_max]


def dateline_bbox(datasets):
    """``(N, W, S, E)`` with ``W > E`` for a grid that crosses the dateline, else None.

    A regional fetch across 180° comes back on -180..180 with a gap in the middle
    (e.g. 150E..80W is -180..-80 plus 150..180); drawn as is it spans the globe.
    """
    for ds in datasets:
        lat_dim, lon_dim = cf_dim(ds, "latitude"), cf_dim(ds, "longitude")
        if lat_dim not in ds.dims or lon_dim not in ds.dims or ds.sizes[lon_dim] < 3:
            continue
        lon = np.sort(((np.asarray(ds[lon_dim].values, dtype=float) + 180.0) % 360.0) - 180.0)
        gaps = np.diff(lon)
        step = float(np.median(gaps))
        widest = int(np.argmax(gaps))
        touches = lon[0] - step <= -180.0 + 1e-6 and lon[-1] + 2 * step >= 180.0 - 1e-6
        if not touches or gaps[widest] <= 3 * step:
            continue
        lat = np.asarray(ds[lat_dim].values, dtype=float)
        half = step / 2
        return (
            float(lat.max()) + half,
            float(lon[widest + 1]) - half,
            float(lat.min()) - half,
            float(lon[widest]) + half,
        )
    return None


def subset_spatial(da, lat_dim, lon_dim, bbox_nwse, region_polygon, extent_vals):
    if bbox_nwse is None and region_polygon is None:
        return da, extent_vals
    import xarray as xr

    da = ensure_normalized_longitude(da, lon_dim)
    if bbox_nwse is not None:
        r_n, r_w, r_s, r_e = bbox_nwse
        da = da.sel({lat_dim: lat_slice(da[lat_dim].values, r_n, r_s)})
        if r_w > r_e:
            da = da.where((da[lon_dim] >= r_w) | (da[lon_dim] <= r_e), drop=True)
        else:
            da = da.sel({lon_dim: slice(r_w, r_e)})
    if region_polygon is not None:
        import shapely

        lon_grid, lat_grid = np.meshgrid(da[lon_dim].values, da[lat_dim].values)
        mask = shapely.contains_xy(region_polygon, lon_grid, lat_grid)
        da = da.where(xr.DataArray(mask, dims=(lat_dim, lon_dim)))
    if bbox_nwse is not None:
        r_n, r_w, r_s, r_e = bbox_nwse
        if r_w > r_e:
            shifted = ((da[lon_dim] - r_w) % 360.0) + r_w
            da = da.assign_coords({lon_dim: shifted}).sortby(lon_dim)
        if extent_vals is None:
            if r_w > r_e:
                extent_vals = [float(r_w), float(r_e) + 360.0, float(r_s), float(r_n)]
            else:
                extent_vals = [float(r_w), float(r_e), float(r_s), float(r_n)]
    return da, extent_vals


def slice_bbox_mask(da, lat_dim, lon_dim, bbox, polygon, label):
    """Slice a lat/lon field to ``bbox`` / polygon; error if the grid is empty."""
    if bbox is None and polygon is None:
        return da
    da, _ = subset_spatial(da, lat_dim, lon_dim, bbox, polygon, None)
    if polygon is not None:
        import shapely

        lon_grid, lat_grid = np.meshgrid(da[lon_dim].values, da[lat_dim].values)
        if not bool(shapely.contains_xy(polygon, lon_grid, lat_grid).any()):
            print(
                f"Warning: geo.mask_geojson polygon does not intersect {label}; "
                "its panels will be entirely empty.",
                file=sys.stderr,
            )
    if da.sizes.get(lat_dim, 0) == 0 or da.sizes.get(lon_dim, 0) == 0:
        raise UsageError(
            f"selection produced an empty grid on {label} "
            "(no cells remain after geo.bbox / geo.mask_geojson); nothing to plot."
        )
    return da


def extent_from_da(da, lat_dim, lon_dim, bbox=None):
    """Lon/lat extent from ``bbox`` (NWSE) or half-cell padding around ``da``."""
    if bbox is not None:
        r_n, r_w, r_s, r_e = bbox
        if r_w > r_e:
            return [float(r_w), float(r_e) + 360.0, float(r_s), float(r_n)]
        return [float(r_w), float(r_e), float(r_s), float(r_n)]
    return pad_cell_extent(da[lat_dim].values, da[lon_dim].values)


def is_cftime_axis(values):
    arr = np.asarray(values)
    return (
        getattr(arr.dtype, "kind", None) == "O"
        and arr.size > 0
        and hasattr(arr.flat[0], "calendar")
    )


def axis_kind(values):
    kind = getattr(np.asarray(values).dtype, "kind", None)
    if kind == "M":
        return "datetime"
    if kind == "m":
        return "timedelta"
    if is_cftime_axis(values):
        return "datetime"
    return None


def subset_points(da, bbox_nwse, region_polygon):
    """Filter station / point samples to ``--bbox`` / ``--mask-geojson``."""
    import numpy as np
    import xarray as xr

    lat_name = cf_dim(da, "latitude")
    lon_name = cf_dim(da, "longitude")
    if lat_name is None or lon_name is None:
        raise UsageError(
            f"geo.bbox / geo.mask_geojson need latitude/longitude coordinates; got dims {list(da.dims)}"
        )
    lat = np.asarray(da[lat_name].values)
    lon = np.asarray(da[lon_name].values)
    keep = np.ones(np.broadcast(lat, lon).shape, dtype=bool)
    lat_b, lon_b = np.broadcast_arrays(lat, lon)
    if bbox_nwse is not None:
        r_n, r_w, r_s, r_e = bbox_nwse
        keep &= (lat_b >= r_s) & (lat_b <= r_n)
        if r_w > r_e:
            keep &= (lon_b >= r_w) | (lon_b <= r_e)
        else:
            keep &= (lon_b >= r_w) & (lon_b <= r_e)
    if region_polygon is not None:
        import shapely

        keep &= shapely.contains_xy(region_polygon, lon_b, lat_b)
        if not bool(keep.any()):
            print(
                "Warning: geo.mask_geojson polygon does not intersect the points; "
                "the rose will be empty.",
                file=sys.stderr,
            )
    keep_da = xr.DataArray(keep, dims=da[lat_name].dims)
    return da.where(keep_da, drop=True)


def point_dim(ds):
    for name in ("station_id", "point_id"):
        if name in ds.dims:
            return name
    return None


_NE_LAYERS = {
    "admin1": ("cultural", "admin_1_states_provinces", "10m"),
    "rivers": ("physical", "rivers_lake_centerlines", None),
    "lakes": ("physical", "lakes", None),
    "borders": ("cultural", "admin_0_boundary_lines_land", None),
    "coastline": ("physical", "coastline", None),
}
_OVERLAY_CACHE: dict = {}


def overlay_settings(value) -> dict:
    """``layout.meta.overlays`` → ``{name: False | style dict}`` (True means default style)."""
    if value is None or value is True:
        value = {}
    if value is False:
        return dict.fromkeys(OVERLAY_NAMES, False)
    out = {}
    for name in OVERLAY_NAMES:
        choice = value.get(name)
        out[name] = choice if choice is not None else None
    return out


def overlay_geoms(extent, settings: dict):
    """``[(name, geometries)]`` for the overlays switched on, clipped to ``extent``.

    The Natural Earth scale follows the map span; admin-1 only appears on
    country-scale views unless set explicitly. A layer that cannot be fetched
    warns and is skipped, so the map still renders.
    """
    scale = boundary_layers(extent)
    key = (
        tuple(round(float(v), 4) for v in extent),
        tuple(sorted((k, bool(v) if v is not None else None) for k, v in settings.items())),
    )
    if key in _OVERLAY_CACHE:
        return _OVERLAY_CACHE[key]
    try:
        clip = extent_clip_geom(extent)
    except Exception as exc:  # noqa: BLE001
        print(f"Warning: map overlays unavailable ({exc}); skipping.", file=sys.stderr)
        return []
    out = []
    try:
        import cartopy.io.shapereader  # noqa: F401
    except ImportError:
        if settings.get("borders") is not False:
            geoms = _bundled_country_geoms(clip)
            out = [("borders", unwrap_geoms(geoms, extent[0]) if extent[1] > 180 else geoms)]
        _OVERLAY_CACHE[key] = out
        return out
    for name in OVERLAY_NAMES:
        choice = settings.get(name)
        if choice is False:
            continue
        if name == "admin1" and choice is None and not scale["admin1"]:
            continue
        category, ne_name, res = _NE_LAYERS[name]
        try:
            geoms = clip_ne_geoms(res or scale["scale"], category, ne_name, clip)
        except Exception as exc:  # noqa: BLE001
            print(f"Warning: {name} overlay unavailable ({exc}); skipping.", file=sys.stderr)
            continue
        if extent[1] > 180.0:
            geoms = unwrap_geoms(geoms, extent[0])
        if geoms:
            out.append((name, geoms))
    _OVERLAY_CACHE[key] = out
    return out


def geoms_to_xy(geoms, *, rings_only=False, tolerance=0.0):
    """NaN-separated float32 x/y polylines for shapely lines and polygon rings.

    ``tolerance`` (degrees) drops vertices closer than that to the simplified
    line: Natural Earth 10m carries far more points than a map panel has pixels.
    """
    import numpy as np

    xs, ys = [], []
    for geom in geoms:
        for part in getattr(geom, "geoms", [geom]):
            if part.geom_type == "Polygon":
                rings = [part.exterior] if rings_only else [part.exterior, *part.interiors]
            else:
                rings = [part]
            for ring in rings:
                if tolerance > 0:
                    ring = ring.simplify(tolerance)
                x, y = ring.xy
                xs += [*x, np.nan]
                ys += [*y, np.nan]
    return np.array(xs, dtype="f4"), np.array(ys, dtype="f4")
