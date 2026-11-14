// Utilidades de ráster local (gray8): colorizado con la leyenda, compuestos RGB y lectura de valores.
// Codificación DEMO: 0 = sin dato; 1..255 = min..max de la leyenda de la variable.
const grayCache = new Map();

export function loadGray(url) {
  if (!grayCache.has(url)) {
    grayCache.set(url, new Promise((resolve, reject) => {
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
      img.onerror = () => { grayCache.delete(url); reject(new Error(`No se pudo cargar la imagen ${url.split('/').pop()}`)); };
      img.src = url;
    }));
  }
  return grayCache.get(url);
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
  for (let i = 0; i < gray.g.length; i++) {
    const g = gray.g[i];
    if (g === nodata) continue; // alfa 0
    const idx = Math.round(((g - 1) / 254) * 255);
    img.data[i * 4] = lut[idx * 3];
    img.data[i * 4 + 1] = lut[idx * 3 + 1];
    img.data[i * 4 + 2] = lut[idx * 3 + 2];
    img.data[i * 4 + 3] = 255;
  }
  ctx.putImageData(img, 0, 0);
  return c;
}

/** Compuesto RGB: cada canal (r, g, b) recibe el gris de una variable o null (canal en 0). */
export function composeRgb(grays, nodata = 0) {
  const present = ['r', 'g', 'b'].filter((k) => grays[k]);
  const ref = grays[present[0]];
  if (present.some((k) => grays[k].w !== ref.w || grays[k].h !== ref.h)) throw new Error('Las variables del compuesto tienen distinta resolución en este frame');
  const c = newCanvas(ref.w, ref.h);
  const ctx = c.getContext('2d');
  const img = ctx.createImageData(ref.w, ref.h);
  for (let i = 0; i < ref.g.length; i++) {
    if (present.some((k) => grays[k].g[i] === nodata)) continue;
    ['r', 'g', 'b'].forEach((k, ch) => { img.data[i * 4 + ch] = grays[k] ? grays[k].g[i] : 0; });
    img.data[i * 4 + 3] = 255;
  }
  ctx.putImageData(img, 0, 0);
  return c;
}

export function grayToValue(g, legend, nodata = 0) {
  if (g === nodata) return null;
  return legend.min + ((g - 1) / 254) * (legend.max - legend.min);
}
