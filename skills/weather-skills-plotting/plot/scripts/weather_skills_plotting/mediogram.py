"""ECMWF-style mediogram: forecast and model-climate ensembles at one place, per lead.

Used by ``plot --mediogram -i FORECAST -i MCLIMATE``.
"""

import numpy as np
from weather_skills_core import UsageError
from weather_skills_core.cf import auto_variable, cf_dim

from weather_skills_plotting.cli import skeleton
from weather_skills_plotting.kinds import is_precip
from weather_skills_plotting.labels import (
    _TRAILING,
    format_plot_date,
    format_step,
    period_axis_title,
    variable_label_for_display,
)
from weather_skills_plotting.spec import merge_spec

MAX_STEPS = 15
# ECMWF's mediogram colours, a little softer than pure cyan and red.
FORECAST_COLOR = "#29b6f6"
MCLIMATE_COLOR = "#e53935"


def _box(uid, name, color, source):
    return {
        "uid": uid,
        "type": "box",
        "name": name,
        "fillcolor": color,
        "line": {"color": "black"},
        "marker": {"color": color, "line": {"color": "black", "width": 1}},
        # Grouped side by side per lead; the forecast mean shares the forecast slot.
        "offsetgroup": uid,
        "meta": {"bind": "samples", "source": source},
    }


def _variable(ds, user, uid):
    trace = next((t for t in user.get("data") or [] if t.get("uid") == uid), {})
    return ((trace.get("meta") or {}).get("source") or {}).get("variable") or auto_variable(ds)


def build_mediogram_spec(ds, spec):
    """The merged mediogram spec and its datasets (forecast, mclimate), for ``cli.run``."""
    files = [d for d in (ds or []) if d is not None]
    if not files and spec is not None:
        opened = spec.opened()
        files = [opened.get("forecast"), opened.get("mclimate")]
    if len(files) != 2 or any(f is None for f in files):
        raise UsageError(
            "pass --mediogram with -i twice (forecast, then m-climate), or a --spec whose layout.meta.inputs "
            "names forecast and mclimate"
        )
    datasets = {"forecast": files[0], "mclimate": files[1]}
    user = spec.data if spec is not None else {}
    point = (((user.get("layout") or {}).get("meta") or {}).get("geo") or {}).get("point")
    if point is None:
        raise UsageError(
            'set the point in --spec: {"layout": {"meta": {"geo": {"point": {"lat": -1.3, "lon": 36.8}}}}}'
        )
    das = {}
    for key, d in datasets.items():
        var = _variable(d, user, key)
        if var not in d:
            raise UsageError(f"{key} has no variable {var!r}; available: {', '.join(d.data_vars)}")
        da = d[var]
        if "number" not in da.dims or "step" not in da.dims:
            raise UsageError(f"{key} input needs 'number' and 'step' dims; got {list(da.dims)}")
        das[key] = da
    n_steps = min(das["forecast"].sizes["step"], das["mclimate"].sizes["step"], MAX_STEPS)
    steps = {"step": list(range(n_steps))}
    fc = {"input": "forecast", "isel": steps}
    data = [
        _box("forecast", "forecast", FORECAST_COLOR, fc),
        _box("mclimate", "m-climate", MCLIMATE_COLOR, {"input": "mclimate", "isel": steps}),
        {
            "uid": "forecast-mean",
            "type": "scatter",
            "name": "forecast mean",
            "line": {"color": "black"},
            "marker": {"color": "black"},
            "offsetgroup": "forecast",
            "meta": {"bind": "samples", "source": fc},
        },
    ]
    da = das["forecast"]
    lat, lon = cf_dim(da, "latitude"), cf_dim(da, "longitude")
    snapped = da.sel({lat: point["lat"], lon: point["lon"]}, method="nearest")
    qty = variable_label_for_display(da, include_units=False)
    where = f"lat={float(snapped[lat]):g}, lon={float(snapped[lon]):g}"
    mc = das["mclimate"]
    mlat, mlon = cf_dim(mc, "latitude"), cf_dim(mc, "longitude")
    msnap = mc.sel({mlat: point["lat"], mlon: point["lon"]}, method="nearest")
    mwhere = f"lat={float(msnap[mlat]):g}, lon={float(msnap[mlon]):g}"
    if mwhere != where:
        # The m-climate sits on its own grid; say which cell it is.
        where += f" (m-climate {mwhere})"
    suffix = ""
    leads = [format_step(v) for v in da["step"].values[:n_steps]]
    xaxis = {"title": {"text": "Forecast step"}}
    init = da["time"].values if "time" in da.coords and da["time"].ndim == 0 else None
    if init is not None and np.asarray(init).dtype.kind == "M":
        # Boxes pair up by lead; the ticks say which day each lead is.
        trailing = _TRAILING.search(str(da.attrs.get("long_name") or ""))
        shift = np.timedelta64(1, "D") if trailing else np.timedelta64(0, "D")
        days = [init + s - shift for s in da["step"].values[:n_steps]]
        xaxis = {
            "title": {"text": period_axis_title(da, "Valid date")},
            "tickvals": leads,
            "ticktext": [format_plot_date(d, year=False) for d in days],
        }
        suffix = f" · init {format_plot_date(init)}"
    # A user title keeps the place and init date, as the verification grid keeps the week.
    user_layout = user.get("layout") or {}
    user_title = user_layout.get("title") or {}
    user_title = user_title if isinstance(user_title, dict) else {"text": user_title}
    if user_title.get("text"):
        title = f"{user_title['text']} · {where}{suffix}"
        # Only the text changes: a subtitle or font in the user's title stays.
        user = {**user, "layout": {**user_layout, "title": {**user_title, "text": title}}}
    else:
        title = f"Mediogram: {qty} at {where}{suffix}"
    layout = {"title": {"text": title}, "boxmode": "group", "scattermode": "group", "xaxis": xaxis}
    if is_precip(da):
        layout["yaxis"] = {"rangemode": "nonnegative"}
    base = skeleton("plot --mediogram", datasets, data, layout)
    return merge_spec(base, user), datasets
