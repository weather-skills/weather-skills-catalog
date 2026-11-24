// Wind at a station on polar axes, colored by speed.
// Each observation sits at the direction it blows from (north up, clockwise) and its
// speed from the centre. wind_speed and wind_from_direction come from the
// wind-speed-direction skill.
// plot.py -i station_wind_sd.zarr=WS --spec windrose.jsonnet -o windrose.png
local station = '.isel(station_id=0)';
{
  data: [
    { type: 'scatterpolar', mode: 'markers', r: '@WS.wind_speed' + station, theta: '@WS.wind_from_direction' + station,
      marker: { size: 8, color: '@WS.wind_speed' + station, colorscale: 'YlGnBu', showscale: true,
                colorbar: { title: { text: 'm/s' } } } },
  ],
  layout: {
    title: { text: 'Wind by direction it blows from' },
    polar: { angularaxis: { rotation: 90, direction: 'clockwise' }, radialaxis: { angle: 90, tickangle: 90 } },
  },
}
