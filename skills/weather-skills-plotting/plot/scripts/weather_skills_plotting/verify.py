"""Forecast verification grid: the observation, each lead's forecast, and its verify map.

Row 1 is the observation then the week-1 … week-N forecasts on one shared color
scale; row 2 holds each lead's verify map (hits, bias, or MAE) on a second scale.
The cell under the observation names the metric. Used by ``plot --obs --forecast
--verify``.
"""

import sys

import numpy as np
from weather_skills_core import UsageError
from weather_skills_core.cf import cf_dim
from weather_skills_core.units import units_equal, variable_units

from weather_skills_plotting.bind import pick_variable
from weather_skills_plotting.cli import skeleton, warn
from weather_skills_plotting.geodata import plain
from weather_skills_plotting.kinds import aggregation_days, is_precip
from weather_skills_plotting.labels import (
    combine_display_labels,
    dataset_display_label,
    format_plot_date_range,
    format_units_for_display,
)
from weather_skills_plotting.layout import axis_suffix, panel_title_annotation
from weather_skills_plotting.spec import merge_spec

VERIFY_VARS = {"hits": "event_hit", "bias": "bias", "mae": "mae"}
METRIC_LABELS = {"hits": "Hits", "bias": "Bias", "mae": "MAE"}


def _as_list(value):
    return [] if value is None else (value if isinstance(value, list) else [value])


def _single_map(ds, variable, role):
    """The field must already be the verifying week: one time, no step."""
    da = ds[variable]
    if "step" in da.dims:
        raise UsageError(
            f"{role} still has a step axis; run step-to-time and select the verifying week first"
        )
    if "number" in da.dims and da.sizes["number"] > 1:
        raise UsageError(
            f"{role} has {da.sizes['number']} ensemble members; average them first with "
            "summarize-dim --dim number --method mean (verify averages them itself)"
        )
    for dim in da.dims:
        if dim in (cf_dim(da, "latitude"), cf_dim(da, "longitude"), "number"):
            continue
        if da.sizes[dim] != 1:
            raise UsageError(
                f"{role} has {dim} size {da.sizes[dim]}; select the verifying week (one time) "
                "with the select skill first"
            )
    return da


def _spacing(coord):
    vals = np.asarray(coord.values, dtype=float)
    return float(np.median(np.abs(np.diff(vals)))) if vals.size > 1 else None


def _require_same_grid(left, right, left_role, right_role):
    """Cells are compared side by side; coarsen obs onto the forecast, not the reverse."""
    bad = []
    for axis in ("latitude", "longitude"):
        a, b = cf_dim(left, axis), cf_dim(right, axis)
        if a in left.dims and b in right.dims:
            da, db = _spacing(left[a]), _spacing(right[b])
            if da and db and not np.isclose(da, db, rtol=0.01, atol=1e-6):
                bad.append(axis)
    if bad:
        raise UsageError(
            f"{right_role} grid spacing does not match {left_role} on {' and '.join(bad)}; "
            "coarsen --obs onto the forecast with --reference-grid <forecast.zarr>"
        )


def _week_title(da):
    if "time" not in da.coords:
        return None
    start = np.asarray(da["time"].values).reshape(-1)
    if not start.size or start.dtype.kind != "M":
        return None
    start = start[0].astype("datetime64[D]")
    days = aggregation_days(da)
    span = int(round(days)) if days and days >= 2 else 7
    return format_plot_date_range(start, start + np.timedelta64(span - 1, "D"))


def _user_variable(user, uid):
    trace = next((t for t in user.get("data") or [] if t.get("uid") == uid), {})
    return ((trace.get("meta") or {}).get("source") or {}).get("variable")


def build_verify_spec(obs, forecast, verify, spec):
    """The merged verification-grid spec and its datasets, for ``cli.run``."""
    forecasts, verifies = _as_list(forecast), _as_list(verify)
    if obs is None and spec is not None:
        opened = spec.opened()
        obs = opened.get("obs")
        forecasts = forecasts or [opened[k] for k in sorted(opened) if k.startswith("forecast")]
        verifies = verifies or [opened[k] for k in sorted(opened) if k.startswith("verify")]
    if obs is None:
        raise UsageError("pass --obs, or a --spec whose layout.meta.inputs names obs")
    if not forecasts:
        raise UsageError("pass at least one --forecast")
    if len(verifies) != len(forecasts):
        raise UsageError(
            f"--verify was passed {len(verifies)} time(s) but --forecast {len(forecasts)} time(s); "
            "pass one --verify per --forecast"
        )
    metrics = set()
    for i, v in enumerate(verifies, start=1):
        metric = v.attrs.get("verify_metric")
        if metric not in VERIFY_VARS:
            raise UsageError(
                f"--verify {i} has no supported verify_metric attr; run the verify skill first"
            )
        if VERIFY_VARS[metric] not in v:
            raise UsageError(f"--verify {i} is missing its {VERIFY_VARS[metric]!r} variable")
        metrics.add(metric)
    if len(metrics) != 1:
        raise UsageError(f"all --verify inputs must share one verify_metric; got {sorted(metrics)}")
    metric = metrics.pop()
    user = spec.data if spec is not None else {}
    n = len(forecasts)
    cols = n + 1
    datasets = {
        "obs": obs,
        **{f"forecast{i}": f for i, f in enumerate(forecasts, 1)},
        **{f"verify{i}": v for i, v in enumerate(verifies, 1)},
    }

    obs_var = pick_variable(obs, _user_variable(user, "obs"), 'the "obs" trace\'s meta.source')
    obs_da = _single_map(obs, obs_var, "--obs")
    fc_vars = []
    for i, fc in enumerate(forecasts, 1):
        uid = f"forecast{i}"
        var = pick_variable(fc, _user_variable(user, uid), f"the {uid!r} trace's meta.source")
        fc_vars.append(var)
        _single_map(fc, var, f"--forecast {i}")
        u_fc, u_obs = variable_units(fc[var]), variable_units(obs_da)
        if u_fc and u_obs and not units_equal(u_fc, u_obs):
            warn(
                f"--forecast {i} {var!r} units={u_fc!r} and --obs {obs_var!r} units={u_obs!r} differ"
            )
        _require_same_grid(forecasts[0], fc, "--forecast 1", f"--forecast {i}")
    _require_same_grid(forecasts[0], obs, "--forecast 1", "--obs")
    for i, v in enumerate(verifies, 1):
        summary = v.attrs.get("verify_score_summary")
        if isinstance(summary, str) and summary.strip():
            # stderr: stdout may be the --dump-spec JSON.
            print(f"{i}-week lead  {summary.strip()}", file=sys.stderr)

    data = [
        {
            "uid": "obs",
            "type": "heatmap",
            "coloraxis": "coloraxis",
            "meta": {"source": {"input": "obs", "variable": obs_var}},
        }
    ]
    for i, var in enumerate(fc_vars, 1):
        data.append(
            {
                "uid": f"forecast{i}",
                "type": "heatmap",
                "coloraxis": "coloraxis",
                "xaxis": f"x{i + 1}",
                "yaxis": f"y{i + 1}",
                "meta": {"source": {"input": f"forecast{i}", "variable": var}},
            }
        )
    # Theme palettes: hits red / white / green ("below" is white, so the map's no-data
    # grey never reads as a class); rainfall bias brown (too dry) to green (too wet).
    palette = {"hits": "hits", "bias": "precip_bias" if is_precip(obs_da) else "bias"}
    palette = palette.get(metric, "mae")
    single = n == 1
    for i in range(1, n + 1):
        # One lead: obs | forecast | verify in one row. More: verify maps under each lead.
        p = 3 if single else cols + i + 1
        data.append(
            {
                "uid": f"verify{i}",
                "type": "heatmap",
                "coloraxis": "coloraxis2",
                "xaxis": f"x{p}",
                "yaxis": f"y{p}",
                "meta": {
                    "source": {"input": f"verify{i}", "variable": VERIFY_VARS[metric]},
                    "palette": palette,
                },
            }
        )

    obs_label = dataset_display_label(obs, "Observation")
    fc_label = combine_display_labels([dataset_display_label(f, "Forecast") for f in forecasts])
    titles = [obs_label] + [f"{i}-week lead" for i in range(1, n + 1)]
    annotations = [panel_title_annotation(p, t) for p, t in enumerate(titles)]
    units = format_units_for_display(variable_units(obs_da))
    metric_label = METRIC_LABELS[metric]
    metric_title = f"{metric_label} [{units}]" if units and metric != "hits" else metric_label
    if single:
        annotations.append(panel_title_annotation(2, f"{metric_label} · {fc_label}"))
    else:
        # Verify cells sit under their forecast column title; no date of their own.
        annotations += [
            {"name": f"panel-title-{cols + i + 1}", "text": ""} for i in range(1, n + 1)
        ]
        blank = axis_suffix(cols)
        annotations.append(
            {
                "name": "row-label-metric",
                "text": f"<b>{metric_label}</b><br>{fc_label}",
                "xref": f"x{blank} domain",
                "yref": f"y{blank} domain",
                "x": 0.5,
                "y": 0.5,
                "showarrow": False,
            }
        )
    event_title = "Event"
    try:
        # verify stamps the threshold on the variable; older files carry it on the dataset.
        hits = verifies[0][VERIFY_VARS[metric]]
        threshold = float(
            hits.attrs.get("event_threshold", verifies[0].attrs.get("event_threshold"))
        )
    except (TypeError, ValueError):
        threshold = None
    if threshold is not None:
        # Say what an event is: a 1 mm threshold on weekly totals is nearly always met.
        event_title = f"Event (≥ {threshold:g} {units})" if units else f"Event (≥ {threshold:g})"
    if metric == "hits":
        axis2 = {
            "colorbar": {
                "title": {"text": event_title},
                "tickvals": [0, 1, 2],
                "ticktext": ["disagree", "below", "hit"],
            }
        }
    elif metric == "bias":
        # Symmetric about zero, set by the bulk of the errors, not the few largest.
        values = np.concatenate(
            [
                np.abs(np.asarray(plain(v[VERIFY_VARS[metric]]).values, float)).ravel()
                for v in verifies
            ]
        )
        values = values[np.isfinite(values)]
        limit = float(np.percentile(values, 98)) if values.size else 1.0
        axis2 = {
            "cmin": -limit,
            "cmax": limit,
            "colorbar": {"title": {"text": metric_title}},
        }
    else:
        axis2 = {
            "cmin": 0,
            "colorbar": {"title": {"text": metric_title}},
        }
    # select drops the time coordinate; any input that still has it dates the week.
    week = next(
        (w for w in map(_week_title, [obs_da, *forecasts, *verifies]) if w is not None), None
    )
    if week is None:
        warn(
            "no verifying-week date found (select drops the time coordinate); put the week "
            'in the title, e.g. --spec \'{"layout": {"title": {"text": "IMERG vs ENS, 3–9 Aug"}}}\''
        )
    # The week is the default title; a spec title replaces it as given.
    title = week
    grid = {"rows": 1, "columns": 3} if single else {"rows": 2, "columns": cols}
    layout = {"grid": grid, "annotations": annotations, "coloraxis2": axis2}
    if title:
        layout["title"] = {"text": title}
    base = skeleton("plot --verify", datasets, data, layout)
    return merge_spec(base, user), datasets
