import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import {
  buildLegendLut,
  getDiscreteSwatches,
  grayToValue,
  legendCssBackground,
} from '../src/lib/rasterCanvas.js';

const GLACIERS = ['glacier-khumbu', 'glacier-langtang'];

function wetSnowDataset(caseId) {
  const url = new URL(`../public/data/cases/${caseId}/case.json`, import.meta.url);
  const def = JSON.parse(readFileSync(url, 'utf8'));
  const ds = def.datasets.find((d) => d.id === 'sar_wetsnow');
  assert.ok(ds, `${caseId} must publish the sar_wetsnow dataset`);
  return { def, ds };
}

for (const caseId of GLACIERS) {
  test(`${caseId}: SAR wet-snow class legend decodes the Python gray8 encoding`, () => {
    const { ds } = wetSnowDataset(caseId);
    assert.equal(ds.source.nodata, 0);
    const legend = ds.variables.find((v) => v.id === 'wet_class').legend;
    const lut = buildLegendLut(legend, 0);
    // encode_class_png writes class/3*255 -> 0, 85, 170, 255
    assert.equal(lut[0 * 4 + 3], 0, 'class 0 (outside snow domain) is transparent');
    for (const [byte, cls] of [[85, 1], [170, 2], [255, 3]]) {
      assert.equal(grayToValue(byte, legend, 0), cls, `byte ${byte} -> class ${cls}`);
      assert.ok(lut[byte * 4 + 3] > 0, `class ${cls} is visible`);
    }
    assert.notDeepEqual(
      Array.from(lut.slice(170 * 4, 170 * 4 + 3)),
      Array.from(lut.slice(255 * 4, 255 * 4 + 3)),
      'possible-wet and wet snow use different colours',
    );
    assert.equal(getDiscreteSwatches(legend).length, 3);
  });

  test(`${caseId}: SAR ratio legends keep 0 dB transparent and drops visible`, () => {
    const { ds } = wetSnowDataset(caseId);
    for (const id of ['ratio_vv', 'ratio_vh']) {
      const legend = ds.variables.find((v) => v.id === id).legend;
      const lut = buildLegendLut(legend, 0);
      const byteFor = (db) => 1 + Math.round(((db - legend.min) / (legend.max - legend.min)) * 254);
      assert.equal(lut[0 * 4 + 3], 0, 'nodata byte stays transparent');
      assert.equal(lut[byteFor(0) * 4 + 3], 0, `${id}: 0 dB is transparent`);
      assert.ok(lut[byteFor(-4) * 4 + 3] > 150, `${id}: a -4 dB drop is clearly visible`);
      assert.ok(lut[byteFor(-4) * 4 + 2] > lut[byteFor(-4) * 4], `${id}: drops render bluish`);
      assert.ok(lut[byteFor(3) * 4 + 3] > 0, `${id}: increases are visible`);
      assert.ok(lut[byteFor(3) * 4] > lut[byteFor(3) * 4 + 2], `${id}: increases render warm`);
      assert.ok(Math.abs(grayToValue(byteFor(-2), legend, 0) - -2) < 0.06, 'decoded value matches within 0.06 dB');
      assert.match(legendCssBackground(legend), /^linear-gradient/);
    }
  });

  test(`${caseId}: SAR wet-snow product is fully wired in the published case`, () => {
    const { def, ds } = wetSnowDataset(caseId);
    assert.deepEqual(
      def.datasets.map((d) => d.id).slice(0, 4),
      ['s2_rgb', 's2_ndsi', 's1_sar', 'sar_wetsnow'],
      'inserted right after the Sentinel-1 dataset',
    );
    assert.equal(ds.frames.length, 1);
    assert.equal(ds.frames[0].phase, 'change');
    assert.ok(def.vectors.some((v) => v.id === 'sar-wetsnow-extent'));
    const tifs = def.downloads.filter((d) => d.datasetId === 'sar_wetsnow');
    assert.equal(tifs.length, 3);
    assert.ok(def.metrics.sarWetSnowKm2 > 0 && def.metrics.sarWetSnowPct > 0);
  });
}
