// Local raster utilities (gray8 and rgba): canvas loading, LUT colorization, and pixel readout.
import { resolveUrl } from './registry.js';

const MAX_SOURCE_CACHE = 48;
const MAX_FRAME_CACHE = 60;
const grayCache = new Map();
const rgbaCache = new Map();
const renderedFrameCache = new Map();
const IS_LITTLE_ENDIAN = new Uint8Array(new Uint32Array([1]).buffer)[0] === 1;

function lruGet(cache, key) {
  if (!cache.has(key)) return undefined;
  const value = cache.get(key);
  cache.delete(key);
  cache.set(key, value);
  return value;
}

function lruSet(cache, key, value, maxSize) {
  if (cache.has(key)) cache.delete(key);
  cache.set(key, value);
  if (cache.size > maxSize) cache.delete(cache.keys().next().value);
}

export function loadGray(url) {
  const cached = lruGet(grayCache, url);
  if (cached) return cached;
  const job = new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => {
      const c = document.createElement('canvas');
      c.width = img.naturalWidth;
      c.height = img.naturalHeight;
      const ctx = c.getContext('2d', { willReadFrequently: true });
      ctx.drawImage(img, 0, 0);
      const { data } = ctx.getImageData(0, 0, c.width, c.height);
      const g = new Uint8Array(c.width * c.height);
      for (let i = 0; i < g.length; i++) g[i] = data[i * 4];
      resolve({ w: c.width, h: c.height, g });
    };
    img.onerror = () => {
      grayCache.delete(url);
      reject(new Error(`Failed to load image ${url.split('/').pop()}`));
    };
    img.src = url;
  });
  lruSet(grayCache, url, job, MAX_SOURCE_CACHE);
  return job;
}

export function loadRgbaCanvas(url) {
  const cached = lruGet(rgbaCache, url);
  if (cached) return cached;
  const job = new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => {
      const c = newCanvas(img.naturalWidth, img.naturalHeight);
      c.getContext('2d').drawImage(img, 0, 0);
      resolve(c);
    };
    img.onerror = () => {
      rgbaCache.delete(url);
      reject(new Error(`Failed to load image ${url.split('/').pop()}`));
    };
    img.src = url;
  });
  lruSet(rgbaCache, url, job, MAX_SOURCE_CACHE);
  return job;
}

/** Parse `#rgb`, `#rgba`, `#rrggbb`, or `#rrggbbaa` into `[r, g, b, a]` (0..255). */
export function hexToRgba(hex) {
  const raw = String(hex ?? '').trim().replace('#', '');
  if (raw.length === 3 || raw.length === 4) {
    return [
      parseInt(raw[0] + raw[0], 16),
      parseInt(raw[1] + raw[1], 16),
      parseInt(raw[2] + raw[2], 16),
      raw.length === 4 ? parseInt(raw[3] + raw[3], 16) : 255,
    ];
  }
  if (raw.length === 6 || raw.length === 8) {
    return [
      parseInt(raw.slice(0, 2), 16),
      parseInt(raw.slice(2, 4), 16),
      parseInt(raw.slice(4, 6), 16),
      raw.length === 8 ? parseInt(raw.slice(6, 8), 16) : 255,
    ];
  }
  return [0, 0, 0, 0];
}

const rgbaToCss = ([r, g, b, a]) => `rgba(${r}, ${g}, ${b}, ${(a / 255).toFixed(3)})`;

function interpolateStops(rgbaList, t) {
  if (rgbaList.length === 1) return rgbaList[0];
  const scaled = Math.max(0, Math.min(1, t)) * (rgbaList.length - 1);
  const k = Math.min(Math.floor(scaled), rgbaList.length - 2);
  const f = scaled - k;
  const a = rgbaList[k];
  const b = rgbaList[k + 1];
  return [
    Math.round(a[0] + (b[0] - a[0]) * f),
    Math.round(a[1] + (b[1] - a[1]) * f),
    Math.round(a[2] + (b[2] - a[2]) * f),
    Math.round(a[3] + (b[3] - a[3]) * f),
  ];
}

export function buildLut(colors) {
  const rgba = colors.map(hexToRgba);
  const lut = new Uint8ClampedArray(256 * 3);
  for (let i = 0; i < 256; i++) {
    const [r, g, b] = interpolateStops(rgba, i / 255);
    lut[i * 3] = r;
    lut[i * 3 + 1] = g;
    lut[i * 3 + 2] = b;
  }
  return lut;
}

/**
 * Build a 256-entry RGBA LUT indexed directly by raw gray8 byte value (`0..255`).
 * Supports `legend.discrete` class maps, thresholded RGBA `legend.stops`, and linear `legend.colors`.
 */
export function buildLegendLut(legendOrColors, nodata = 0) {
  const lut = new Uint8ClampedArray(256 * 4);
  const fullRange = nodata == null;
  const legend = Array.isArray(legendOrColors)
    ? { min: 0, max: 255, colors: legendOrColors }
    : (legendOrColors ?? {});
  const min = Number(legend.min ?? 0);
  const max = Number(legend.max ?? 1);
  const span = max - min;

  const discreteEntries = legend.discrete && typeof legend.discrete === 'object'
    ? Object.entries(legend.discrete)
      .map(([k, hex]) => [Number(k), hexToRgba(hex)])
      .filter(([k]) => Number.isFinite(k))
    : null;

  const stops = Array.isArray(legend.stops) && legend.stops.length >= 2
    ? legend.stops
      .map(([v, hex]) => [Number(v), hexToRgba(hex)])
      .filter(([v]) => Number.isFinite(v))
      .sort((a, b) => a[0] - b[0])
    : null;

  const colors = Array.isArray(legend.colors) && legend.colors.length
    ? legend.colors.map(hexToRgba)
    : null;

  for (let g = 0; g < 256; g++) {
    if (!fullRange && g === nodata) continue;
    const frac = fullRange ? g / 255 : Math.max(0, Math.min(1, (g - 1) / 254));
    const val = span !== 0 ? min + frac * span : min;
    let rgba = [0, 0, 0, 0];

    if (discreteEntries?.length) {
      let best = null;
      let bestDist = Infinity;
      for (const entry of discreteEntries) {
        const dist = Math.abs(val - entry[0]);
        if (dist < bestDist) {
          bestDist = dist;
          best = entry;
        }
      }
      if (best && bestDist <= 0.55) rgba = best[1];
    } else if (stops?.length >= 2) {
      if (val <= stops[0][0]) rgba = stops[0][1];
      else if (val >= stops[stops.length - 1][0]) rgba = stops[stops.length - 1][1];
      else {
        for (let s = 0; s < stops.length - 1; s++) {
          const [v0, c0] = stops[s];
          const [v1, c1] = stops[s + 1];
          if (val >= v0 && val <= v1) {
            const f = v1 !== v0 ? (val - v0) / (v1 - v0) : 0;
            rgba = [
              Math.round(c0[0] + (c1[0] - c0[0]) * f),
              Math.round(c0[1] + (c1[1] - c0[1]) * f),
              Math.round(c0[2] + (c1[2] - c0[2]) * f),
              Math.round(c0[3] + (c1[3] - c0[3]) * f),
            ];
            break;
          }
        }
      }
    } else if (colors?.length) {
      rgba = interpolateStops(colors, frac);
    }

    lut[g * 4] = rgba[0];
    lut[g * 4 + 1] = rgba[1];
    lut[g * 4 + 2] = rgba[2];
    lut[g * 4 + 3] = rgba[3];
  }
  return lut;
}

/** Build discrete swatch descriptors when `legend.discrete` is defined, or null. */
export function getDiscreteSwatches(legend) {
  if (!legend?.discrete || typeof legend.discrete !== 'object') return null;
  const entries = Object.entries(legend.discrete)
    .map(([key, hex]) => [Number(key), key, hexToRgba(hex)])
    .filter(([num]) => Number.isFinite(num))
    .sort((a, b) => a[0] - b[0]);
  if (!entries.length) return null;

  const hintByKey = new Map();
  if (legend.nodata_label) {
    String(legend.nodata_label).split('·').forEach((part) => {
      const m = part.trim().match(/^(-?\d+)\s+(.+)$/);
      if (m) hintByKey.set(m[1], m[2].trim());
    });
  }

  return entries.map(([, key, rgba]) => ({
    key,
    rgba,
    cssColor: rgbaToCss(rgba),
    label: hintByKey.has(key) ? `${key} ${hintByKey.get(key)}` : key,
  }));
}

/** Build CSS `linear-gradient(...)` matching `legend.stops` (with alpha) or `legend.colors`. */
export function legendCssBackground(legend) {
  if (!legend) return 'none';
  if (Array.isArray(legend.stops) && legend.stops.length >= 2) {
    const min = Number(legend.min ?? 0);
    const max = Number(legend.max ?? 1);
    const span = max - min || 1;
    const sorted = [...legend.stops]
      .map(([v, hex]) => [Number(v), hexToRgba(hex)])
      .filter(([v]) => Number.isFinite(v))
      .sort((a, b) => a[0] - b[0]);
    const parts = sorted.map(([v, rgba]) => {
      const pct = Math.max(0, Math.min(100, ((v - min) / span) * 100));
      return `${rgbaToCss(rgba)} ${pct.toFixed(1)}%`;
    });
    if (sorted[0][0] > min) parts.unshift(`${rgbaToCss(sorted[0][1])} 0.0%`);
    if (sorted[sorted.length - 1][0] < max) parts.push(`${rgbaToCss(sorted[sorted.length - 1][1])} 100.0%`);
    return `linear-gradient(90deg, ${parts.join(', ')})`;
  }
  if (Array.isArray(legend.colors) && legend.colors.length) {
    return `linear-gradient(90deg, ${legend.colors.map((c) => rgbaToCss(hexToRgba(c))).join(', ')})`;
  }
  return 'none';
}

function newCanvas(w, h) {
  const c = document.createElement('canvas');
  c.width = w;
  c.height = h;
  return c;
}

export function colorizeGray(gray, legendOrColors, nodata = 0) {
  const c = newCanvas(gray.w, gray.h);
  const ctx = c.getContext('2d');
  const img = ctx.createImageData(gray.w, gray.h);
  const lut = buildLegendLut(legendOrColors, nodata);
  const gArr = gray.g;
  const len = gArr.length;

  // Precompute all 256 gray-byte -> RGBA mappings once instead of inside the per-pixel loop.
  if (IS_LITTLE_ENDIAN) {
    const table32 = new Uint32Array(256);
    for (let g = 0; g < 256; g++) {
      const o = g * 4;
      table32[g] = (lut[o + 3] << 24) | (lut[o + 2] << 16) | (lut[o + 1] << 8) | lut[o];
    }
    const dst32 = new Uint32Array(img.data.buffer);
    for (let i = 0; i < len; i++) dst32[i] = table32[gArr[i]];
  } else {
    const dst = img.data;
    for (let i = 0; i < len; i++) {
      const o = gArr[i] * 4;
      const p = i * 4;
      dst[p] = lut[o];
      dst[p + 1] = lut[o + 1];
      dst[p + 2] = lut[o + 2];
      dst[p + 3] = lut[o + 3];
    }
  }

  ctx.putImageData(img, 0, 0);
  return c;
}

/** RGB composite: each channel (r, g, b) receives one variable's grayscale array or null (channel fixed at 0). */
export function composeRgb(grays, nodata = 0) {
  const present = ['r', 'g', 'b'].filter((k) => grays[k]);
  if (!present.length) throw new Error('RGB composite requires at least one valid channel');
  const ref = grays[present[0]];
  if (present.some((k) => grays[k].w !== ref.w || grays[k].h !== ref.h)) {
    throw new Error('Composite variables have mismatched dimensions in this frame');
  }
  const c = newCanvas(ref.w, ref.h);
  const ctx = c.getContext('2d');
  const img = ctx.createImageData(ref.w, ref.h);
  const dst = img.data;
  const fullRange = nodata == null;
  const rArr = grays.r?.g ?? null;
  const gArr = grays.g?.g ?? null;
  const bArr = grays.b?.g ?? null;
  const len = ref.g.length;
  for (let i = 0; i < len; i++) {
    const rv = rArr ? rArr[i] : 0;
    const gv = gArr ? gArr[i] : 0;
    const bv = bArr ? bArr[i] : 0;
    if (!fullRange && ((rArr && rv === nodata) || (gArr && gv === nodata) || (bArr && bv === nodata))) continue;
    const p = i * 4;
    dst[p] = rv;
    dst[p + 1] = gv;
    dst[p + 2] = bv;
    dst[p + 3] = 255;
  }
  ctx.putImageData(img, 0, 0);
  return c;
}

export function grayToValue(g, legend, nodata = 0) {
  if (nodata != null && g === nodata) return null;
  const val = nodata == null
    ? legend.min + (g / 255) * (legend.max - legend.min)
    : legend.min + ((g - 1) / 254) * (legend.max - legend.min);
  return legend?.discrete ? Math.round(val) : val;
}

/** Render a frame (rgba or gray8/composite) into a reusable LRU-cached canvas and PNG URL. */
export async function renderFrameCanvas(dataset, frame, selectionId) {
  const key = `${dataset.id}|${frame.id}|${selectionId}`;
  const cached = lruGet(renderedFrameCache, key);
  if (cached) return cached;

  const job = (async () => {
    const path = frame.files?.[selectionId];
    const url = path ? resolveUrl(path) : null;
    if (dataset.source.encoding === 'rgba') {
      if (!url) throw new Error(`Missing file for ${dataset.id}/${selectionId}`);
      const canvas = await loadRgbaCanvas(url);
      return { canvas, url };
    }
    const nodata = dataset.source.nodata === undefined ? 0 : dataset.source.nodata;
    const comp = (dataset.composites ?? []).find((c) => c.id === selectionId);
    let canvas;
    if (comp) {
      const grays = {};
      for (const ch of ['r', 'g', 'b']) {
        const chKey = comp.channels[ch];
        const chPath = chKey ? frame.files?.[chKey] : null;
        if (chKey && !chPath) throw new Error(`Missing channel ${chKey} for ${dataset.id}/${selectionId}`);
        grays[ch] = chPath ? await loadGray(resolveUrl(chPath)) : null;
      }
      canvas = composeRgb(grays, nodata);
    } else {
      if (!url) throw new Error(`Missing file for ${dataset.id}/${selectionId}`);
      const v = dataset.variables.find((x) => x.id === selectionId);
      const hasPalette = Boolean(v?.legend && (v.legend.colors?.length || v.legend.stops?.length || v.legend.discrete));
      if (!hasPalette) throw new Error(`Missing color legend for ${dataset.id}/${selectionId}`);
      canvas = colorizeGray(await loadGray(url), v.legend, nodata);
    }
    return { canvas, url: canvas.toDataURL('image/png') };
  })();

  lruSet(renderedFrameCache, key, job, MAX_FRAME_CACHE);
  job.catch(() => renderedFrameCache.delete(key));
  return job;
}
