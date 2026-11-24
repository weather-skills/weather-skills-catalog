// Mean ± 1 standard deviation as a shaded band, with the mean as a line.
// Plotly adds the band up by stacking: the lower edge (mean − SD) is drawn with no fill,
// then the SD is stacked on it twice, so the shading runs from mean − SD to mean + SD. C
// holds precip_avg and precip_std (clim-fetch, as an area mean with summarize-dim). LOW
// is mean − SD: edit-coords on C with --rename precip_avg=mean --rename
// precip_std=precip_avg (the SD under the mean's name), then difference -i C -i (that
// file) -v precip_avg.
// plot.py -i clim_area.zarr=C -i clim_low.zarr=LOW --spec sd_band.jsonnet -o band.png
local edge = { type: 'scatter', mode: 'lines', x: '@C.time', stackgroup: 'sd', line: { width: 0 },
               hoverinfo: 'skip', showlegend: false };
local shade = 'rgba(76,114,176,0.3)';
{
  data: [
    edge { y: '@LOW.precip_avg', fillcolor: 'rgba(0,0,0,0)' },
    edge { y: '@C.precip_std', fillcolor: shade, name: '± 1 SD', showlegend: true },
    edge { y: '@C.precip_std', fillcolor: shade },
    { type: 'scatter', mode: 'lines', x: '@C.time', y: '@C.precip_avg', name: 'Mean',
      line: { color: 'black', width: 2 } },
  ],
  layout: {
    title: { text: 'Daily rainfall climatology' },
    // Rain below zero means nothing: the axis stops at 0 even where mean − SD is negative.
    yaxis: { title: { text: 'mm/day' }, autorangeoptions: { minallowed: 0 } },
  },
}
