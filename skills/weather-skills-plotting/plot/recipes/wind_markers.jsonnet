// Wind speed shading with arrows drawn as Plotly markers, sized in pixels by speed.
// Each arrow is as long as its wind is strong, in pixels: no extra data step
// (wind-speed-direction alone gives wind_speed and wind_from_direction). Each arrow is a
// line-ns shaft and an arrow-down head, both centred on the cell and turned to the
// from-direction. Plotly draws line-ns 1.4 times its size long, so shaft size = speed *
// scale / 0.7 and head standoff = speed * scale put the head tip on the shaft end: the
// arrow is 2 * scale px long per unit of speed, and every head is the same size.
// Arithmetic like this is allowed only on marker sizes; the data drawn is unchanged.
// plot.py -i wind_sd.zarr=WD --spec wind_markers.jsonnet -o wind.png
local ws = import 'ws.libsonnet';
local inputs = std.extVar('inputs');
local bbox = inputs.WD.bbox;
local layout = ws.map(bbox);
// About 25 arrows across the map, and 10 m/s drawn one arrow spacing long.
local across = 25;
local step = std.max(1, std.ceil(inputs.WD.dims.longitude / across));
local spacing = (layout.width - layout.margin.l - layout.margin.r) * step / inputs.WD.dims.longitude;
local scale = spacing / (2 * 10);  // px per unit of speed, each side of the cell
local thin = '.isel(time=0, lat=slice(None, None, %d), lon=slice(None, None, %d))' % [step, step];
local speed = '@WD.wind_speed' + thin + '.flat()';
local from = '@WD.wind_from_direction' + thin + '.flat()';
local at = {
  type: 'scatter', mode: 'markers', hoverinfo: 'skip', showlegend: false, cliponaxis: false,
  x: '@WD.wind_speed' + thin + '.flat("lon")', y: '@WD.wind_speed' + thin + '.flat("lat")',
};
{
  data: [
    { type: 'heatmap', x: '@WD.lon', y: '@WD.lat', z: '@WD.wind_speed.isel(time=0)', colorscale: 'YlGnBu',
      colorbar: { title: { text: 'm/s' } } },
    at { marker: { symbol: 'line-ns', size: speed + ' * %g' % (scale / 0.7), angle: from,
                   line: { width: 1.3, color: 'black' } } },
    at { marker: { symbol: 'arrow-down', color: 'black', angle: from, standoff: speed + ' * %g' % scale,
                   size: 4 } },
  ] + ws.basemap(bbox, rivers=false, admin=false),
  layout: layout + { title: { text: '10 m wind, %s' % ws.date(inputs.WD.coords.time[0]) } },
}
