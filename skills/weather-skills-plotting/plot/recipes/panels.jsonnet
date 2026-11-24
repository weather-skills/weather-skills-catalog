// One map per forecast step in a grid, sharing one colorbar, with panel titles.
// FC has no member dim: an ensemble mean (summarize-dim --dim number) or one member
// (select).
// plot.py -i forecast_mean.zarr=FC --spec panels.jsonnet -o panels.png
local ws = import 'ws.libsonnet';
local inputs = std.extVar('inputs');
local bbox = inputs.FC.bbox;
local n = inputs.FC.dims.step;
local cols = 3;
local rows = std.ceil(n / cols);
{
  data: std.flattenArrays([
    [{ type: 'heatmap', x: '@FC.lon', y: '@FC.lat', z: '@FC.precip.isel(step=%d)' % i, coloraxis: 'coloraxis' } + ws.axes(i)]
    + ws.basemap(bbox, i, rivers=false, admin=false)
    for i in std.range(0, n - 1)
  ]),
  layout: ws.grid(bbox, rows=rows, cols=cols, empty=std.range(n, rows * cols - 1)) + ws.classBar(ws.palettes.ppt_day, 'mm') + {
    title: { text: 'Daily rainfall by lead, init %s' % ws.date(inputs.FC.coords.time) },
    // annotations+: adds to the colorbar labels classBar wrote.
    annotations+: [ws.panelTitle(i, 'Day %d' % (i + 1)) for i in std.range(0, n - 1)],
  },
}
