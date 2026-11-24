// Color tables. A class palette is {colors, bounds}: colors holds one color per
// class (between two bounds), or under + classes + over. A palette with no bounds
// is a list of colors for a continuous scale. labels, when given, name each class.
// ws.stepped turns one into a coloraxis, ws.classBar adds an even colorbar.
//
// ppt_day … ppt_season are rainfall totals in mm (0–50, 0–200, 0–400, 0–1000);
// ppt_anom_* are anomalies in mm (±50, ±200, ±300, ±500); ppt_total runs to 2500 mm.
// ppt_poa is percent of normal; spi the standardized precipitation index; hits,
// bias and mae color verify maps. tt_* are Tropical Tidbits model-chart palettes:
// tt_precip_rate mm/h, tt_precip_total mm, tt_pwat mm, tt_wind m/s, tt_z500 dam.
{
  ppt_day: {
    colors: ['#ffffff', '#ffffff', '#f6e8c3', '#e8d4a0', '#c8ffbe', '#78f573', '#1eb41e', '#50a5f5', '#1e6eeb', '#a08cff', '#7060dc'],
    bounds: [0, 1, 2, 5, 10, 15, 20, 30, 40, 50],
  },
  ppt_week: {
    colors: ['#ffffff', '#ffffff', '#f6e8c3', '#e8d4a0', '#c8ffbe', '#78f573', '#1eb41e', '#50a5f5', '#1e6eeb', '#a08cff', '#7060dc', '#fffaaa', '#ffa000', '#ff1400', '#a50000'],
    bounds: [0, 1, 2, 5, 10, 15, 20, 30, 40, 50, 75, 100, 150, 200],
  },
  ppt_month: {
    colors: ['#ffffff', '#ffffff', '#f6e8c3', '#e8d4a0', '#c8ffbe', '#78f573', '#1eb41e', '#50a5f5', '#1e6eeb', '#a08cff', '#7060dc', '#fffaaa', '#ffa000', '#ff1400', '#a50000', '#e68c8c'],
    bounds: [0, 1, 2, 5, 10, 15, 20, 30, 40, 50, 75, 100, 150, 200, 400],
  },
  ppt_season: {
    colors: ['#ffffff', '#ffffff', '#f6e8c3', '#e8d4a0', '#c8ffbe', '#78f573', '#1eb41e', '#50a5f5', '#1e6eeb', '#a08cff', '#7060dc', '#fffaaa', '#ffa000', '#ff1400', '#a50000', '#e68c8c', '#7a0000', '#5a0000'],
    bounds: [0, 1, 2, 5, 10, 15, 20, 30, 40, 50, 75, 100, 150, 200, 400, 700, 1000],
  },
  ppt_total: {
    colors: ['#ffffff', '#ffffff', '#c8ffbe', '#78f573', '#1eb41e', '#b4f0fa', '#50a5f5', '#1e6eeb', '#dcdcff', '#a08cff', '#7060dc', '#fffaaa', '#ffa000', '#ff1400', '#a50000', '#e68c8c', '#ffe6e6'],
    bounds: [0, 2, 5, 10, 25, 50, 75, 100, 150, 200, 300, 500, 750, 1000, 1500, 2500],
  },
  ppt_anom_day: {
    colors: ['#785046', '#b48c82', '#f0dcd2', '#ffffff', '#c8ffbe', '#78f573', '#1eb41e'],
    bounds: [-50, -25, -10, 10, 25, 50],
  },
  ppt_anom_week: {
    colors: ['#ffa000', '#ffe878', '#785046', '#b48c82', '#f0dcd2', '#ffffff', '#c8ffbe', '#78f573', '#1eb41e', '#96d2fa', '#2882f0'],
    bounds: [-200, -100, -50, -25, -10, 10, 25, 50, 100, 200],
  },
  ppt_anom_month: {
    colors: ['#ff3200', '#ffa000', '#ffe878', '#785046', '#b48c82', '#f0dcd2', '#ffffff', '#c8ffbe', '#78f573', '#1eb41e', '#96d2fa', '#2882f0', '#dcdcff'],
    bounds: [-300, -200, -100, -50, -25, -10, 10, 25, 50, 100, 200, 300],
  },
  ppt_anom_season: {
    colors: ['#c00000', '#ff3200', '#ffa000', '#ffe878', '#785046', '#b48c82', '#f0dcd2', '#ffffff', '#c8ffbe', '#78f573', '#1eb41e', '#96d2fa', '#2882f0', '#dcdcff', '#8070eb'],
    bounds: [-500, -300, -200, -100, -50, -25, -10, 10, 25, 50, 100, 200, 300, 500],
  },
  ppt_master: {
    colors: ['#ffffff', '#ffffff', '#f6e8c3', '#e8d4a0', '#c8ffbe', '#78f573', '#1eb41e', '#50a5f5', '#1e6eeb', '#a08cff', '#7060dc', '#fffaaa', '#ffa000', '#ff1400', '#a50000', '#e68c8c', '#7a0000', '#5a0000'],
    bounds: [0, 1, 2, 5, 10, 15, 20, 30, 40, 50, 75, 100, 150, 200, 400, 700, 1000],
  },
  ppt_anom_master: {
    colors: ['#c00000', '#ff3200', '#ffa000', '#ffe878', '#785046', '#b48c82', '#f0dcd2', '#ffffff', '#c8ffbe', '#78f573', '#1eb41e', '#96d2fa', '#2882f0', '#dcdcff', '#8070eb'],
    bounds: [-500, -300, -200, -100, -50, -25, -10, 10, 25, 50, 100, 200, 300, 500],
  },
  ppt_poa: {
    colors: ['#e1beb4', '#c00000', '#ff3200', '#ffa000', '#ffe878', '#ffffff', '#c8ffbe', '#78f573', '#1eb41e', '#96d2fa', '#2882f0'],
    bounds: [30, 45, 60, 75, 90, 110, 125, 150, 200, 300],
  },
  ppt_spp: {
    colors: ['#dcdcdc', '#ffffff', '#ffe878', '#ffa000', '#ff3200', '#c00000', '#c8ffbe', '#96f58c', '#37d23c', '#0fa00f', '#b4f0fa', '#78b9fa', '#2882f0', '#1464d2'],
    bounds: [0.5, 1.5, 2.5, 3.5, 4.5, 5.5, 6.5, 7.5, 8.5, 9.5, 10.5, 11.5, 12.5],
  },
  spi: {
    colors: ['#730000', '#e70000', '#ffaa00', '#ffd37b', '#ffff00', '#ffffff', '#bdebff', '#73b2ff', '#0071ff', '#004dad', '#ad00e7'],
    bounds: [-2.5, -2, -1.5, -1.2, -0.7, -0.5, 0.5, 0.7, 1.2, 1.5, 2, 2.5],
  },
  hits: {
    colors: ['#d73027', '#ffffff', '#1a9850'],
    bounds: [-1.5, -0.5, 0.5, 1.5],
    labels: ['Disagree', 'Both below', 'Hit'],
  },
  bias: {
    colors: ['#053061', '#2166ac', '#4393c3', '#92c5de', '#d1e5f0', '#ffffff', '#fddbc7', '#f4a582', '#d6604d', '#b2182b', '#67001f'],
  },
  precip_bias: {
    colors: ['#543005', '#8c510a', '#bf812d', '#dfc27d', '#f6e8c3', '#f5f5f5', '#c7eae5', '#80cdc1', '#35978f', '#01665e', '#003c30'],
  },
  mae: {
    colors: ['#ffffff', '#fddbc7', '#f4a582', '#d6604d', '#b2182b', '#67001f'],
  },
  wind_rose: {
    colors: ['#c7e9b4', '#7fcdbb', '#41b6c4', '#1d91c0', '#225ea8', '#253494', '#081d58'],
  },
  colorblind: {
    colors: ['#0173b2', '#de8f05', '#029e73', '#d55e00', '#cc78bc', '#ca9161', '#fbafe4', '#949494', '#ece133', '#56b4e9'],
  },
  rocket: {
    colors: ['#f7ccaf', '#f6ab83', '#f58860', '#f06043', '#e43841', '#cb1b4f', '#a8185a', '#841e5a', '#611f53', '#3f1b43', '#20122e'],
  },
  // Tropical Tidbits model-chart palettes.
  tt_precip_rate: {
    colors: ['#ffffff', '#00fb4c', '#00e445', '#00cd3e', '#00b537', '#009e2e', '#008528', '#006e20', '#005619', '#ffff50', '#ffd248', '#ffa442', '#ff763b', '#ff1c2d', '#de0835', '#bb003b', '#9a0041', '#c700a6', '#e400c2', '#ff00dc'],
    bounds: [0.1, 0.25, 0.5, 1, 1.5, 2, 3, 4, 5, 6, 8, 10, 12, 16, 20, 24, 30, 36, 42],
  },
  tt_precip_total: {
    colors: ['#ffffff', '#b1edcf', '#97d8b7', '#7dc19e', '#62aa85', '#48936d', '#2e7e54', '#14673c', '#14678c', '#337e9f', '#5094b5', '#6dacc8', '#8bc4de', '#a9dbf2', '#ebd5eb', '#d9bed8', '#c5a7c5', '#b38fb2', '#a0779f', '#8e5f8d', '#7a4779', '#682f67', '#6c0033', '#87253b', '#a54945', '#c16e4e', '#de9357', '#fac66c', '#fbd479', '#fde385', '#fef192'],
    bounds: [0.2, 1, 2, 3, 6, 9, 12, 15, 20, 25, 30, 35, 40, 45, 50, 60, 70, 80, 90, 100, 125, 150, 175, 200, 225, 250, 300, 350, 400, 500],
  },
  tt_pwat: {
    colors: ['#bab9b9', '#b5aba5', '#ae9e92', '#aa927f', '#a4856c', '#9f7858', '#9a6c45', '#935f32', '#9c5c27', '#aa672d', '#b87136', '#c57b3e', '#d28545', '#df904d', '#ed9b53', '#faa55c', '#fdb466', '#fdc272', '#fdd27f', '#fedf8b', '#fee899', '#fef2a9', '#fefab7', '#fbfdb9', '#f4faae', '#ecf7a2', '#e6f598', '#d5ee9b', '#c3e69f', '#b3e0a2', '#a1d9a4', '#8cd0a4', '#78c9a4', '#66c2a5', '#55afac', '#479fb3', '#3990b9', '#397eb7', '#4b66ad', '#4b66ad', '#6758a6', '#6758a6', '#897ab8', '#a492c5', '#a492c5', '#a492c5', '#bfabd4', '#e2cee4', '#e2cee4'],
    bounds: [0, 1, 2, 3, 5, 7, 9, 11, 13, 15, 17, 19, 21, 23, 25, 27, 29, 31, 33, 35, 37, 39, 41, 43, 45, 47, 49, 51, 53, 55, 57, 59, 61, 63, 65, 67, 69, 71, 73, 75, 77, 79, 81, 83, 85, 87, 89, 91, 93, 95],
  },
  tt_wind: {
    colors: ['#ffffff', '#6fedf1', '#40bbc7', '#108b9f', '#33c342', '#ccf06f', '#ffa63e', '#fe0000', '#870000', '#c546ff', '#f1b1ff', '#ffb6d5', '#ff7883', '#e74853', '#b52744', '#99183d'],
    bounds: [3.5, 8, 13, 17.5, 20.5, 23.5, 27, 30, 33, 41, 49, 56.5, 64.5, 72, 80],
  },
  tt_z500: {
    colors: ['#95ffc7', '#b8ffda', '#dcffec', '#f9dfff', '#f5c0ff', '#f09fff', '#b484ee', '#7868df', '#3a4dcf', '#0064cf', '#0098df', '#00ccee', '#00ffff', '#00cc7a', '#00a633', '#008000', '#47a200', '#acd500', '#ffff00', '#ffcc00', '#ff6600', '#e61309', '#9f0000', '#790250'],
    bounds: [468, 474, 480, 486, 492, 498, 504, 510, 516, 522, 528, 534, 540, 546, 552, 558, 564, 570, 576, 582, 588, 594, 600],
  },
}
