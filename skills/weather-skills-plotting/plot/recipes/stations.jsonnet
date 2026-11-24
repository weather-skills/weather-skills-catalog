// Station dots over a grid, both on one colorbar, each dot labelled with its id.
// Stations with no value that day are left out (.dropna), and .lon / .lat / .station_id
// after it are those of the stations kept, so dots, labels and colors stay matched.
// plot.py -i rain.zarr=OBS -i stations.zarr=STN --spec stations.jsonnet -o stations.png
local ws = import 'ws.libsonnet';
local inputs = std.extVar('inputs');
local bbox = inputs.OBS.bbox;
local day = '.isel(time=0)';
local kept = '@STN.precip' + day + '.dropna("station_id")';
{
  data: [
    { type: 'heatmap', x: '@OBS.lon', y: '@OBS.lat', z: '@OBS.precip' + day, coloraxis: 'coloraxis' },
    { type: 'scatter', mode: 'markers+text', name: 'Stations', x: kept + '.lon', y: kept + '.lat',
      text: kept + '.station_id', textposition: 'top center',
      marker: { size: 12, color: kept, coloraxis: 'coloraxis', line: { color: 'black', width: 1 } } },
  ] + ws.basemap(bbox),
  layout: ws.map(bbox) + ws.classBar(ws.palettes.ppt_day, 'mm/day') + {
    title: { text: 'Gridded rain and station gauges' },
  },
}
