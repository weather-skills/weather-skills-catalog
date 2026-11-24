// A sea-level pressure chart: labelled isobars every 4 hPa, and H / L at the centres.
// Each centre shows its central pressure. HI and LO come from local-extrema --kind high
// and --kind low run on the whole MSL file; a point is NaN in maps where it is not a
// centre, so .dropna('station_id') keeps this map's centres, and .lon / .lat after it
// are their positions. MSL is in hPa (unit-convert first if it is in Pa).
// plot.py -i msl_hpa.zarr=MSL -i highs.zarr=HI -i lows.zarr=LO --spec mslp.jsonnet -o mslp.png
local ws = import 'ws.libsonnet';
local inputs = std.extVar('inputs');
local bbox = inputs.MSL.bbox;
local t = 0;  // which time to draw; loop over it in frames to animate
local centres(name, letter, color) = {
  local at = '@%s.msl.isel(time=%d).dropna("station_id")' % [name, t],
  type: 'scatter', mode: 'text', x: at + '.lon', y: at + '.lat', customdata: at, showlegend: false,
  texttemplate: '<b>' + letter + '</b><br>%{customdata:.0f}', textfont: { color: color, size: 16 }, cliponaxis: false,
};
{
  data: [
    { type: 'contour', x: '@MSL.lon', y: '@MSL.lat', z: '@MSL.msl.isel(time=%d)' % t, showscale: false, showlegend: false,
      contours: { coloring: 'none', showlabels: true, start: 900, end: 1100, size: 4 }, line: { color: 'black', width: 1 } },
    centres('HI', 'H', '#1f4e9c'),
    centres('LO', 'L', '#c0392b'),
  ] + ws.basemap(bbox, rivers=false, admin=false),
  layout: ws.map(bbox) + { title: { text: 'Sea-level pressure (hPa), %s' % ws.date(inputs.MSL.coords.time[t]) } },
}
