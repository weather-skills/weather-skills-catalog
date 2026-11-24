// A verification grid for one week: observation, forecast per lead, and hit maps.
// The observation, then one column per lead's forecast on one rain colorbar, with each
// lead's verify map underneath on its own colorbar.
// Inputs hold one time each; the forecasts are on the observation's grid (verify
// needs that too). V1 / V2 are verify --metric hits outputs (event_hit: -1 / 0 / 1).
// plot.py -i obs_week.zarr=OW -i fc_w1.zarr=FC1 -i fc_w2.zarr=FC2 -i hits_w1.zarr=V1 -i hits_w2.zarr=V2 --spec verify_grid.jsonnet -o verify.png
local ws = import 'ws.libsonnet';
local inputs = std.extVar('inputs');
local bbox = inputs.OW.bbox;
local leads = [{ fc: 'FC1', v: 'V1', title: 'Week 1 lead' }, { fc: 'FC2', v: 'V2', title: 'Week 2 lead' }];
local cols = std.length(leads) + 1;
local map(name, field, i, axis) = { type: 'heatmap', x: '@%s.lon' % name, y: '@%s.lat' % name,
                                     z: '@%s.%s.squeeze()' % [name, field], coloraxis: axis } + ws.axes(i);
{
  data: [map('OW', 'precip', 0, 'coloraxis')]
        + [map(leads[k].fc, 'precip', k + 1, 'coloraxis') for k in std.range(0, std.length(leads) - 1)]
        + [map(leads[k].v, 'event_hit', cols + k + 1, 'coloraxis2') for k in std.range(0, std.length(leads) - 1)]
        + std.flattenArrays([ws.basemap(bbox, i, rivers=false, admin=false) for i in std.range(0, 2 * cols - 1) if i != cols]),
  layout: ws.grid(bbox, rows=2, cols=cols, empty=[cols])
          + ws.classBar(ws.palettes.ppt_week, 'mm', y=0.55, len=0.4)
          + ws.classBar(ws.palettes.hits, 'Event ≥ 10 mm', coloraxis='coloraxis2', y=0.05, len=0.4) + {
    title: { text: 'Rainfall %s' % ws.date(inputs.OW.coords.time[0]) },
    annotations+: [ws.panelTitle(0, 'Observed')]
                  + [ws.panelTitle(k + 1, leads[k].title) for k in std.range(0, std.length(leads) - 1)],
  },
}
