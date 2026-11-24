// One map: a grid on a stepped palette, the base map, and a region of your own outlined.
// The base map draws country borders; outline a region it doesn't (a county from
// resolve-region, your own polygon). A country's outline would draw its border twice.
// plot.py -i rain_week.zarr=OBS -i kitui.geojson=REGION --spec map.jsonnet -o map.png
local ws = import 'ws.libsonnet';
local inputs = std.extVar('inputs');
local bbox = inputs.OBS.bbox;  // the data's box, or {n: 5.5, w: 33.5, s: -5, e: 42.5}
local t = inputs.OBS.coords.time;
{
  data: [
    { type: 'heatmap', x: '@OBS.lon', y: '@OBS.lat', z: '@OBS.precip.isel(time=0)', coloraxis: 'coloraxis' },
  ] + ws.basemap(bbox) + [
    { type: 'scatter', mode: 'lines', x: '@REGION.x', y: '@REGION.y', line: { color: 'crimson', width: 2 },
      hoverinfo: 'skip', showlegend: false },
  ],
  layout: ws.map(bbox) + ws.classBar(ws.palettes.ppt_week, 'mm') + {
    title: { text: 'Rainfall, week of %s' % ws.date(t[0]) },
  },
}
