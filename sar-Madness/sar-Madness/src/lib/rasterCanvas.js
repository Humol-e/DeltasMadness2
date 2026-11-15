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

const hexToRgb = (h) => { const n = parseInt(h.replace('#', ''), 16); return [(n >> 16) & 255, (n >> 8) & 255, n & 255]; };

export function buildLut(colors) {
  const rgb = colors.map(hexToRgb);
  const lut = new Uint8ClampedArray(256 * 3);
  for (let i = 0; i < 256; i++) {
    const t = (i / 255) * (rgb.length - 1);
    const k = Math.min(Math.floor(t), rgb.length - 2);
    const f = t - k;
    for (let c = 0; c < 3; c++) lut[i * 3 + c] = rgb[k][c] + (rgb[k + 1][c] - rgb[k][c]) * f;
  }
  return lut;
}

function newCanvas(w, h) {
  const c = document.createElement('canvas');
  c.width = w;
  c.height = h;
  return c;
}

export function colorizeGray(gray, colors, nodata = 0) {
  const c = newCanvas(gray.w, gray.h);
  const ctx = c.getContext('2d');
  const img = ctx.createImageData(gray.w, gray.h);
  const lut = buildLut(colors);
  const fullRange = nodata == null;
  const gArr = gray.g;
  const len = gArr.length;

  // Precompute all 256 gray-byte -> RGBA mappings once instead of inside the per-pixel loop.
  if (IS_LITTLE_ENDIAN) {
    const table32 = new Uint32Array(256);
    for (let g = 0; g < 256; g++) {
      if (!fullRange && g === nodata) continue;
      const idx = fullRange ? g : Math.max(0, Math.min(255, Math.round(((g - 1) / 254) * 255)));
      table32[g] = (255 << 24) | (lut[idx * 3 + 2] << 16) | (lut[idx * 3 + 1] << 8) | lut[idx * 3];
    }
    const dst32 = new Uint32Array(img.data.buffer);
    for (let i = 0; i < len; i++) dst32[i] = table32[gArr[i]];
  } else {
    const table4 = new Uint8Array(256 * 4);
    for (let g = 0; g < 256; g++) {
      if (!fullRange && g === nodata) continue;
      const idx = fullRange ? g : Math.max(0, Math.min(255, Math.round(((g - 1) / 254) * 255)));
      table4[g * 4] = lut[idx * 3];
      table4[g * 4 + 1] = lut[idx * 3 + 1];
      table4[g * 4 + 2] = lut[idx * 3 + 2];
      table4[g * 4 + 3] = 255;
    }
    const dst = img.data;
    for (let i = 0; i < len; i++) {
      const o = gArr[i] * 4;
      const p = i * 4;
      dst[p] = table4[o];
      dst[p + 1] = table4[o + 1];
      dst[p + 2] = table4[o + 2];
      dst[p + 3] = table4[o + 3];
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
  if (nodata == null) return legend.min + (g / 255) * (legend.max - legend.min);
  return legend.min + ((g - 1) / 254) * (legend.max - legend.min);
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
      if (!v?.legend?.colors?.length) throw new Error(`Missing color legend for ${dataset.id}/${selectionId}`);
      canvas = colorizeGray(await loadGray(url), v.legend.colors, nodata);
    }
    return { canvas, url: canvas.toDataURL('image/png') };
  })();

  lruSet(renderedFrameCache, key, job, MAX_FRAME_CACHE);
  job.catch(() => renderedFrameCache.delete(key));
  return job;
}
