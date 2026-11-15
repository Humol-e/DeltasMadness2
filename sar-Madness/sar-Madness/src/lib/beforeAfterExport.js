// PNG export and download utilities (Before, After, and side-by-side Comparison) from real satellite data.
import { buildLegendLut, getDiscreteSwatches, renderFrameCanvas } from './rasterCanvas.js';
import { DEFAULT_LANG, getLocalizedCaseTitle, getUiCopy, localizeFrameLabel, translateTerm } from './i18n.js';

export function getBeforeAfterPair(dataset) {
  const frames = dataset?.frames ?? [];
  if (frames.length >= 2) {
    const explicitBefore = frames.find((f) => f.phase === 'before') ?? frames[0];
    const explicitAfter = [...frames].reverse().find((f) => f.phase === 'after') ?? frames[frames.length - 1];
    return { hasPair: explicitBefore.id !== explicitAfter.id, before: explicitBefore, after: explicitAfter };
  }
  return { hasPair: false, before: null, after: frames[0] ?? null };
}

export function frameShortTitle(frame, fallback, lang = DEFAULT_LANG) {
  if (!frame) return fallback;
  return localizeFrameLabel(frame.label, lang) || String(frame.datetime || fallback).slice(0, 10);
}

export async function triggerCanvasPngDownload(canvas, filename) {
  const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/png'));
  if (!blob) return;
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1500);
}

/** Compose a downloadable side-by-side PNG comparison with Before (left), After (right), dates, and legend. */
export function buildComparisonCanvas({
  caseDef,
  dataset,
  variable,
  beforeFrame,
  afterFrame,
  beforeCanvas,
  afterCanvas,
  lang = DEFAULT_LANG,
}) {
  const copy = getUiCopy(lang).export;
  const panelW = Math.max(460, Math.min(680, Math.max(beforeCanvas.width, afterCanvas.width) * 2));
  const aspect = beforeCanvas.height / Math.max(1, beforeCanvas.width);
  const panelH = Math.max(220, Math.min(540, Math.round(panelW * aspect)));

  const pad = 28;
  const gap = 24;
  const headerH = 74;
  const subH = 34;
  const footerH = variable?.legend ? 56 : 38;
  const width = pad * 2 + panelW * 2 + gap;
  const height = headerH + subH + panelH + footerH + pad;

  const out = document.createElement('canvas');
  out.width = width;
  out.height = height;
  const ctx = out.getContext('2d');

  // Clean dark geospatial analysis background
  ctx.fillStyle = '#0d111a';
  ctx.fillRect(0, 0, width, height);

  // Header with real case metadata
  ctx.fillStyle = '#eef2ff';
  ctx.font = 'bold 20px system-ui, -apple-system, Segoe UI, sans-serif';
  ctx.fillText(getLocalizedCaseTitle(caseDef, lang) || caseDef.id, pad, 34);

  ctx.fillStyle = '#93c5fd';
  ctx.font = '14px system-ui, -apple-system, Segoe UI, sans-serif';
  const dsName = translateTerm(dataset.name, lang);
  const varName = variable ? translateTerm(variable.label, lang) : '';
  const varLabel = varName ? `${dsName} · ${varName}` : dsName;
  ctx.fillText(varLabel, pad, 58);

  const badge = copy.badge;
  ctx.font = 'bold 11px system-ui, -apple-system, Segoe UI, sans-serif';
  const bw = ctx.measureText(badge).width + 18;
  ctx.fillStyle = '#134e4a';
  ctx.fillRect(width - pad - bw, 18, bw, 24);
  ctx.fillStyle = '#5eead4';
  ctx.fillText(badge, width - pad - bw + 9, 34);

  const drawPanel = (canvas, x, tag, subtitle, accent) => {
    const yImg = headerH + subH;
    ctx.fillStyle = '#161d2c';
    ctx.fillRect(x, headerH, panelW, subH + panelH);
    ctx.fillStyle = accent;
    ctx.font = 'bold 13px system-ui, -apple-system, Segoe UI, sans-serif';
    ctx.fillText(tag, x + 10, headerH + 22);
    const subtitleOffset = Math.max(78, Math.ceil(ctx.measureText(tag).width) + 14);
    ctx.fillStyle = '#cbd5e1';
    ctx.font = '13px system-ui, -apple-system, Segoe UI, sans-serif';
    ctx.fillText(subtitle, x + 10 + subtitleOffset, headerH + 22);

    ctx.imageSmoothingEnabled = canvas.width < 260 ? false : true;
    ctx.drawImage(canvas, x, yImg, panelW, panelH);

    ctx.strokeStyle = '#2d3748';
    ctx.lineWidth = 1.5;
    ctx.strokeRect(x, headerH, panelW, subH + panelH);
  };

  drawPanel(beforeCanvas, pad, copy.beforeTag, frameShortTitle(beforeFrame, copy.preFallback, lang), '#38bdf8');
  drawPanel(afterCanvas, pad + panelW + gap, copy.afterTag, frameShortTitle(afterFrame, copy.postFallback, lang), '#fb923c');

  const fy = headerH + subH + panelH + 16;
  const swatches = variable?.legend ? getDiscreteSwatches(variable.legend) : null;
  if (swatches?.length) {
    let sx = pad;
    ctx.font = '12px system-ui, -apple-system, Segoe UI, sans-serif';
    for (const sw of swatches) {
      ctx.fillStyle = '#0f172a';
      ctx.fillRect(sx, fy, 12, 12);
      ctx.fillStyle = sw.cssColor;
      ctx.fillRect(sx, fy, 12, 12);
      ctx.strokeStyle = '#334155';
      ctx.strokeRect(sx, fy, 12, 12);
      ctx.fillStyle = '#cbd5e1';
      ctx.fillText(sw.label, sx + 17, fy + 10);
      sx += ctx.measureText(sw.label).width + 32;
    }
  } else if (variable?.legend && (variable.legend.colors?.length || variable.legend.stops?.length)) {
    const barW = 280;
    const barH = 12;
    const lut = buildLegendLut(variable.legend, null);
    ctx.fillStyle = '#0f172a';
    ctx.fillRect(pad, fy, barW, barH);
    for (let px = 0; px < barW; px++) {
      const idx = Math.round((px / (barW - 1)) * 255);
      const o = idx * 4;
      ctx.fillStyle = `rgba(${lut[o]},${lut[o + 1]},${lut[o + 2]},${(lut[o + 3] / 255).toFixed(3)})`;
      ctx.fillRect(pad + px, fy, 1, barH);
    }
    ctx.strokeStyle = '#334155';
    ctx.strokeRect(pad, fy, barW, barH);
    ctx.fillStyle = '#cbd5e1';
    ctx.font = '12px system-ui, -apple-system, Segoe UI, sans-serif';
    const localizedUnit = variable.unit ? translateTerm(variable.unit, lang) : '';
    const unit = localizedUnit ? ` ${localizedUnit}` : '';
    ctx.fillText(`${variable.legend.min}${unit}`, pad, fy + 28);
    ctx.fillText(`${variable.legend.max}${unit}`, pad + barW - ctx.measureText(`${variable.legend.max}${unit}`).width, fy + 28);
  } else {
    ctx.fillStyle = '#94a3b8';
    ctx.font = '12px system-ui, -apple-system, Segoe UI, sans-serif';
    const product = translateTerm(dataset.product || dataset.sensor, lang) || copy.satelliteObservation;
    ctx.fillText(`${copy.productPrefix} ${product} · ${dataset.provider || ''}`, pad, fy + 16);
  }

  return out;
}

export async function downloadSingleRenderedPng(caseId, dataset, frame, selectionId, tag) {
  const { canvas } = await renderFrameCanvas(dataset, frame, selectionId);
  await triggerCanvasPngDownload(canvas, `${caseId}_${dataset.id}_${selectionId}_${tag}.png`);
}

export async function downloadSideBySidePng(caseDef, dataset, selectionId, lang = DEFAULT_LANG) {
  const { hasPair, before, after } = getBeforeAfterPair(dataset);
  if (!hasPair || !before || !after) {
    const only = after || before;
    if (!only) return;
    await downloadSingleRenderedPng(caseDef.id, dataset, only, selectionId, 'real');
    return;
  }
  const [rb, ra] = await Promise.all([
    renderFrameCanvas(dataset, before, selectionId),
    renderFrameCanvas(dataset, after, selectionId),
  ]);
  const variable = (dataset.variables ?? []).find((v) => v.id === selectionId) ?? null;
  const comp = buildComparisonCanvas({
    caseDef,
    dataset,
    variable,
    beforeFrame: before,
    afterFrame: after,
    beforeCanvas: rb.canvas,
    afterCanvas: ra.canvas,
    lang,
  });
  await triggerCanvasPngDownload(comp, `${caseDef.id}_${dataset.id}_${selectionId}_before_after.png`);
}
