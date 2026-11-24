// Horizontal bars per region from a file with a name coordinate (county).
// plot.py -i county_totals.zarr=CTY --spec bars.jsonnet -o bars.png
{
  data: [
    { type: 'bar', orientation: 'h', x: '@CTY.precip', y: '@CTY.county', marker: { color: '#4c72b0' } },
  ],
  layout: { title: { text: 'Weekly rainfall by county' }, xaxis: { title: { text: 'mm' } }, yaxis: { automargin: true } },
}
