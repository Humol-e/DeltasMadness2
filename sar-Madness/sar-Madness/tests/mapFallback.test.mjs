import test from 'node:test';
import assert from 'node:assert/strict';

import { buildFallbackStyle } from '../src/lib/mapController.js';
import { resolveUrlWithBase } from '../src/lib/registry.js';

test('buildFallbackStyle builds globe + sky style for MapLibre without requiring an API token', () => {
  const style = buildFallbackStyle('satellite', { engine: 'maplibre' });
  assert.equal(style.version, 8);
  assert.deepEqual(style.projection, { type: 'globe' });
  assert.ok(style.sky && style.sky['sky-color']);
  assert.equal(style.sources['esri-fallback'].type, 'raster');
  assert.match(style.sources['esri-fallback'].tiles[0], /arcgisonline\.com/);
});

test('buildFallbackStyle preserves light/streets open raster providers and omits MapLibre-only fields for mapbox engine', () => {
  const light = buildFallbackStyle('light', { engine: 'maplibre' });
  assert.match(light.sources['esri-fallback'].tiles[0], /cartocdn\.com/);

  const streets = buildFallbackStyle('streets', { engine: 'mapbox' });
  assert.match(streets.sources['esri-fallback'].tiles[0], /tile\.openstreetmap\.org/);
  assert.equal(streets.projection, undefined);
  assert.equal(streets.sky, undefined);
});

test('resolveUrlWithBase handles root, GitHub Pages subpath, leading dots/slashes, and external URLs', () => {
  assert.equal(resolveUrlWithBase('/images/delta-logo.png', '/'), '/images/delta-logo.png');
  assert.equal(resolveUrlWithBase('/images/delta-logo.png', '/DeltasMadness2/'), '/DeltasMadness2/images/delta-logo.png');
  assert.equal(resolveUrlWithBase('data/catalog.json', '/DeltasMadness2'), '/DeltasMadness2/data/catalog.json');
  assert.equal(resolveUrlWithBase('./data/cases/x/case.json', '/DeltasMadness2/'), '/DeltasMadness2/data/cases/x/case.json');
  assert.equal(resolveUrlWithBase('https://example.com/a.png', '/DeltasMadness2/'), 'https://example.com/a.png');
});
