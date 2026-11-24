// Past seasons and this year's forecast plume on one day-of-year axis.
// Each year is its own input; the forecast is on valid dates (step-to-time) with a
// number dim.
// plot.py -i y2024.zarr=Y2024 -i y2025.zarr=Y2025 -i fc_valid.zarr=FCA --spec seasons.jsonnet -o seasons.png
local ws = import 'ws.libsonnet';
local inputs = std.extVar('inputs');
local years = ['Y2024', 'Y2025'];
{
  data: [
    { type: 'scatter', mode: 'lines', name: std.lstripChars(y, 'Y'),
      x: '@%s.time.dt.dayofyear' % y, y: '@%s.precip' % y }
    for y in years
  ] + [
    { type: 'scatter', mode: 'lines', x: '@FCA.time.dt.dayofyear', y: '@FCA.precip.isel(number=%d)' % m,
      line: { color: 'grey', width: 0.6 }, legendgroup: 'fc', showlegend: m == 0, name: 'Forecast members' }
    for m in std.range(0, inputs.FCA.dims.number - 1)
  ],
  layout: {
    title: { text: 'Daily rainfall by day of year' },
    xaxis: ws.dayOfYearTicks,  // 1 Jan, 1 Feb, … at their day of year
    yaxis: { title: { text: 'mm/day' }, rangemode: 'tozero' },
  },
}
