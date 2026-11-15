import assert from 'node:assert/strict';
import test from 'node:test';
import {
  buildCliCommands,
  findNearestCase,
  parseCoordinates,
  searchLocalMatches,
} from '../src/lib/placeSearch.js';

const SAMPLE_CASES = [
  {
    id: 'wildfire-palisades-la',
    title: 'Palisades Fire — Los Angeles, CA (2025)',
    region: 'Los Angeles, California, USA',
    category: 'wildfire',
    view: { center: [-118.56, 34.075] },
  },
  {
    id: 'glacier-langtang',
    title: 'Langtang Valley — Glacier Snowmelt',
    region: 'Nepal (Himalaya)',
    category: 'glacier',
    view: { center: [85.62, 28.24] },
  },
];

test('parseCoordinates parses valid lat,lon and 4-value bbox while rejecting invalid strings', () => {
  const pt = parseCoordinates('19.4326, -99.1332');
  assert.equal(pt.kind, 'coords');
  assert.deepEqual(pt.center, [-99.1332, 19.4326]);
  assert.equal(pt.bbox.length, 4);
  assert.ok(pt.bbox[0] < pt.bbox[2] && pt.bbox[1] < pt.bbox[3]);

  const box = parseCoordinates('-107.80, 27.60, -107.45, 27.90');
  assert.equal(box.kind, 'coords');
  assert.deepEqual(box.bbox, [-107.8, 27.6, -107.45, 27.9]);

  assert.equal(parseCoordinates('Palisades'), null);
  assert.equal(parseCoordinates('95, -99.13'), null);
  assert.equal(parseCoordinates('10, 20, 5, 30'), null);
});

test('searchLocalMatches matches accent-insensitively across real cases and CLI presets', () => {
  const hitsMx = searchLocalMatches('mexico', SAMPLE_CASES);
  assert.ok(hitsMx.some((h) => h.kind === 'preset' && h.key === 'tarahumara_mx'));

  const hitsLa = searchLocalMatches('palisades', SAMPLE_CASES);
  assert.equal(hitsLa.length, 1);
  assert.equal(hitsLa[0].kind, 'case');
  assert.equal(hitsLa[0].id, 'wildfire-palisades-la');
});

test('findNearestCase and buildCliCommands produce valid outputs', () => {
  const nearest = findNearestCase([-118.24, 34.05], SAMPLE_CASES);
  assert.equal(nearest.item.id, 'wildfire-palisades-la');
  assert.ok(nearest.distanceKm > 20 && nearest.distanceKm < 45);

  const presetCli = buildCliCommands({ key: 'tarahumara_mx', bbox: [-107.8, 27.6, -107.45, 27.9] });
  assert.match(presetCli.wildfireCmd, /wildfire-stage1 run-wildfire --aoi tarahumara_mx/);
  assert.match(presetCli.glacierCmd, /glacier-stage1 publish-madness --aoi "-107\.8000,27\.6000,-107\.4500,27\.9000"/);

  const customCli = buildCliCommands({ bbox: [-99.2, 19.35, -99.05, 19.5] });
  assert.match(customCli.wildfireCmd, /--aoi "-99\.2000,19\.3500,-99\.0500,19\.5000"/);
});
