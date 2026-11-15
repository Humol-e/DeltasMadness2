import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import {
  buildLegendLut,
  getDiscreteSwatches,
  grayToValue,
  legendCssBackground,
} from '../src/lib/rasterCanvas.js';

const WILDFIRES = [
  'wildfire-camp-paradise',
  'wildfire-martin-nv',
  'wildfire-palisades-la',
];

function sarBurnDataset(caseId) {
  const url = new URL(`../public/data/cases/${caseId}/case.json`, import.meta.url);
  const def = JSON.parse(readFileSync(url, 'utf8'));
  const ds = def.datasets.find((d) => d.id === 'sar_burn');
  assert.ok(ds, `${caseId} must publish the sar_burn dataset`);
  return { def, ds };
}

for (const caseId of WILDFIRES) {
  test(`${caseId}: SAR burn class legend decodes discrete gray8 classes 0..3`, () => {
    const { ds } = sarBurnDataset(caseId);
    assert.equal(ds.source.nodata, 0);
    const legend = ds.variables.find((v) => v.id === 'sar_burn_class').legend;
    const lut = buildLegendLut(legend, 0);
    assert.equal(lut[0 * 4 + 3], 0, 'class 0 (unburned/nodata) is transparent');
    for (const [byte, cls] of [[85, 1], [170, 2], [255, 3]]) {
      assert.equal(grayToValue(byte, legend, 0), cls, `byte ${byte} -> class ${cls}`);
      assert.ok(lut[byte * 4 + 3] > 0, `class ${cls} is visible`);
    }
    assert.notDeepEqual(
      Array.from(lut.slice(170 * 4, 170 * 4 + 3)),
      Array.from(lut.slice(255 * 4, 255 * 4 + 3)),
      'moderate and high SAR burn classes use distinct colors',
    );
    assert.equal(getDiscreteSwatches(legend).length, 3);
  });

  test(`${caseId}: continuous OPERA DIST, ΔRVI, and LogRBR legends map values accurately`, () => {
    const { ds } = sarBurnDataset(caseId);
    const byteFor = (val, legend) =>
      1 + Math.round(((val - legend.min) / (legend.max - legend.min)) * 254);

    // 1. OPERA DIST-S1 z-score [0, 4]
    const distLegend = ds.variables.find((v) => v.id === 'opera_dist').legend;
    const distLut = buildLegendLut(distLegend, 0);
    assert.equal(distLut[0 * 4 + 3], 0, 'nodata byte stays transparent');
    assert.equal(distLut[byteFor(0.2, distLegend) * 4 + 3], 0, 'disturbance <= 0.5 sigma is transparent');
    assert.ok(distLut[byteFor(2.8, distLegend) * 4 + 3] > 180, 'high OPERA disturbance is clearly visible');
    assert.ok(Math.abs(grayToValue(byteFor(2.0, distLegend), distLegend, 0) - 2.0) < 0.03);
    assert.match(legendCssBackground(distLegend), /^linear-gradient/);

    // 2. ΔRVI [-0.6, 0.3] and LogRBR [-6, 3] keep 0 neutral/transparent and drops visible
    for (const [varId, dropVal, tol] of [['delta_rvi', -0.35, 0.01], ['log_rbr', -3.0, 0.05]]) {
      const legend = ds.variables.find((v) => v.id === varId).legend;
      const lut = buildLegendLut(legend, 0);
      assert.equal(lut[byteFor(0, legend) * 4 + 3], 0, `${varId}: 0 stays transparent`);
      assert.ok(lut[byteFor(dropVal, legend) * 4 + 3] > 150, `${varId}: burn drop is visible`);
      assert.ok(Math.abs(grayToValue(byteFor(dropVal, legend), legend, 0) - dropVal) < tol);
      assert.match(legendCssBackground(legend), /^linear-gradient/);
    }
  });

  test(`${caseId}: SAR burn & OPERA DIST product is wired into schema v2 case`, () => {
    const { def, ds } = sarBurnDataset(caseId);
    const s1Index = def.datasets.findIndex((d) => d.id === 's1_sar');
    const sarBurnIndex = def.datasets.findIndex((d) => d.id === 'sar_burn');
    assert.ok(s1Index >= 0 && sarBurnIndex === s1Index + 1, 'sar_burn placed right after s1_sar');
    assert.deepEqual(
      def.datasets[s1Index].variables.map((v) => v.id),
      ['VH', 'VV'],
      's1_sar exposes both VH and VV channels',
    );
    assert.equal(ds.frames.length, 1);
    assert.equal(ds.frames[0].phase, 'change');
    assert.ok(def.vectors.some((v) => v.id === 'sar-burn-extent'));
    const tifs = def.downloads.filter((d) => d.datasetId === 'sar_burn');
    assert.equal(tifs.length, 3);
    assert.ok(def.metrics.sarBurnKm2 > 0);
    assert.ok(def.metrics.sarDisturbedKm2 >= def.metrics.sarBurnKm2);
  });
}
