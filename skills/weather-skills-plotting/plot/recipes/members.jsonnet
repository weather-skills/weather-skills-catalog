// Ensemble members as thin grey lines with the ensemble mean in bold, at one point.
// The point is the grid cell nearest a lat / lon. Both inputs are on valid dates
// (step-to-time); the mean comes from summarize-dim --dim number --method mean.
// plot.py -i forecast_valid.zarr=ENS -i forecast_valid_mean.zarr=MEAN --spec members.jsonnet -o members.png
local inputs = std.extVar('inputs');
local ws = import 'ws.libsonnet';
local lat = 1.2;
local lon = 10.8;
local pt = '.sel(lat=%g, lon=%g, method="nearest")' % [lat, lon];
local deg(v, pos, neg) = '%g°%s' % [std.abs(v), if v < 0 then neg else pos];
{
  data: [
    { type: 'scatter', mode: 'lines', x: '@ENS.time', y: '@ENS.precip%s.isel(number=%d)' % [pt, m],
      line: { color: 'grey', width: 0.7 }, legendgroup: 'members', showlegend: m == 0, name: 'Members' }
    for m in std.range(0, inputs.ENS.dims.number - 1)
  ] + [
    { type: 'scatter', mode: 'lines', x: '@MEAN.time', y: '@MEAN.precip' + pt, name: 'Mean', line: { color: 'red', width: 3 } },
  ],
  layout: {
    title: { text: 'Ensemble rainfall at %s, %s, init %s' % [deg(lat, 'N', 'S'), deg(lon, 'E', 'W'), ws.date(inputs.ENS.coords.time[0])] },
    yaxis: { title: { text: 'mm/day' }, rangemode: 'tozero' },
  },
}
