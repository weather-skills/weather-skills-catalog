// A shaded 10–90% band between two quantile lines, and an observed line on top.
// The quantiles come from summarize-dim --dim number --method quantile.
// plot.py -i quantiles.zarr=Q -i obs_area.zarr=AREA --spec band.jsonnet -o band.png
{
  data: [
    { type: 'scatter', mode: 'lines', x: '@Q.time', y: '@Q.precip.sel(quantile=0.1)', line: { width: 0 },
      showlegend: false, hoverinfo: 'skip' },
    { type: 'scatter', mode: 'lines', x: '@Q.time', y: '@Q.precip.sel(quantile=0.9)', line: { width: 0 },
      fill: 'tonexty', fillcolor: 'rgba(76,114,176,0.3)', name: '10–90% of members' },
    { type: 'scatter', mode: 'lines+markers', x: '@AREA.time', y: '@AREA.precip', name: 'Observed', line: { color: 'black' } },
  ],
  layout: { title: { text: 'Forecast range and observations' }, yaxis: { title: { text: 'mm/day' }, rangemode: 'tozero' } },
}
