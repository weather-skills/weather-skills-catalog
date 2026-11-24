// One variable against another, with year labels and El Niño / La Niña threshold lines.
// Both come from one file (merge two files first). The thresholds sit on the x axis, so
// they are constants passed with --const, not numbers typed into the spec.
// plot.py -i index_vs_rain.zarr=IDX --const el_nino=0.5 --const la_nina=-0.5 --spec scatter_xy.jsonnet -o scatter.png
local threshold(name) = {
  type: 'line', xref: 'x', x0: '@const.' + name, x1: '@const.' + name, yref: 'paper', y0: 0, y1: 1,
  line: { dash: 'dash', color: 'grey' },
};
{
  data: [
    { type: 'scatter', mode: 'markers+text', x: '@IDX.nino34', y: '@IDX.precip', text: '@IDX.time.dt.year',
      textposition: 'top center', marker: { size: 9 } },
  ],
  layout: {
    title: { text: 'Seasonal rainfall against Niño3.4' },
    xaxis: { title: { text: 'Niño3.4 anomaly (°C)' }, zeroline: true },
    yaxis: { title: { text: 'Rainfall (mm)' } },
    shapes: [threshold('el_nino'), threshold('la_nina')],
  },
}
