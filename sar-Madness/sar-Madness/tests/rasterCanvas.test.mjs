import assert from 'node:assert/strict';
import test from 'node:test';
import {
  buildLegendLut,
  getDiscreteSwatches,
  grayToValue,
  hexToRgba,
  legendCssBackground,
} from '../src/lib/rasterCanvas.js';

test('hexToRgba parses 6-digit and 8-digit hex colors with alpha', () => {
  assert.deepEqual(hexToRgba('#eb461e00'), [235, 70, 30, 0]);
  assert.deepEqual(hexToRgba('#c8140aeb'), [200, 20, 10, 235]);
  assert.deepEqual(hexToRgba('#64748b'), [100, 116, 139, 255]);
});

test('buildLegendLut keeps burn_prob <= 0.2 transparent and maps high values', () => {
  const legend = {
    min: 0,
    max: 1,
    colors: ['#eb461e', '#c8140a'],
    stops: [
      [0.2, '#eb461e00'],
      [0.5, '#eb461e96'],
      [1.0, '#c8140aeb'],
    ],
  };
  const lut = buildLegendLut(legend, 0);
  assert.equal(lut[3], 0, 'nodata byte 0 stays transparent');
  assert.equal(lut[51 * 4 + 3], 0, 'prob ~0.197 stays transparent below 0.2');
  assert.ok(lut[128 * 4 + 3] >= 145 && lut[128 * 4 + 3] <= 155, 'prob 0.5 has mid alpha');
  assert.deepEqual([...lut.slice(255 * 4, 256 * 4)], [200, 20, 10, 235]);

  const bg = legendCssBackground(legend);
  assert.match(bg, /0\.0%.*20\.0%.*100\.0%/);
});

test('buildLegendLut snaps discrete burn severity classes without smearing', () => {
  const legend = {
    min: 0,
    max: 4,
    colors: ['#7fffd4', '#ffff00', '#ff8c00', '#ff0000'],
    discrete: {
      1: '#7fffd4dc',
      2: '#ffff00dc',
      3: '#ff8c00e6',
      4: '#ff0000eb',
    },
    nodata_label: '1 Low · 2 Mod-low · 3 Mod-high · 4 High',
  };
  const lut = buildLegendLut(legend, 0);
  assert.equal(lut[1 * 4 + 3], 0, 'class 0 stays transparent');
  assert.deepEqual([...lut.slice(64 * 4, 65 * 4)], [127, 255, 212, 220]);
  assert.deepEqual([...lut.slice(128 * 4, 129 * 4)], [255, 255, 0, 220]);
  assert.deepEqual([...lut.slice(191 * 4, 192 * 4)], [255, 140, 0, 230]);
  assert.deepEqual([...lut.slice(255 * 4, 256 * 4)], [255, 0, 0, 235]);

  assert.equal(grayToValue(191, legend, 0), 3);
  const swatches = getDiscreteSwatches(legend);
  assert.equal(swatches.length, 4);
  assert.equal(swatches[0].label, '1 Low');
});

test('buildLegendLut anchors zero accurately on asymmetric diverging ΔAGB legend', () => {
  const legend = {
    min: -120.0,
    max: 60.0,
    colors: ['#991b1b', '#ef4444', '#f97316', '#64748b', '#22c55e', '#15803d'],
    stops: [
      [-120.0, '#991b1b'],
      [-60.0, '#ef4444'],
      [-20.0, '#f97316'],
      [0.0, '#64748b'],
      [25.0, '#22c55e'],
      [60.0, '#15803d'],
    ],
  };
  const lut = buildLegendLut(legend, 0);
  // Byte 170 corresponds to -120 + (169/254)*180 ≈ -0.24 Mg/ha -> should sit at neutral slate #64748b, not green.
  const r = lut[170 * 4];
  const g = lut[170 * 4 + 1];
  const b = lut[170 * 4 + 2];
  assert.ok(Math.abs(r - 100) <= 4 && Math.abs(g - 116) <= 4 && Math.abs(b - 139) <= 4, `got rgb(${r},${g},${b})`);
});
