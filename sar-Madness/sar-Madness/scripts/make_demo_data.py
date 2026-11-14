"""Genera datos SINTÉTICOS de demostración (no son observaciones satelitales).
Sin dependencias externas. Uso: python3 scripts/make_demo_data.py
Salida: public/data/catalog.json y public/data/cases/<caso>/ (case.json, GeoJSON y PNG).

Codificación ráster DEMO (gray8): PNG de 8 bits en escala de grises; 0 = sin dato y
1..255 = min..max de la leyenda de la variable. Cuando el pipeline real defina su formato,
solo hay que cambiar "source" y "files" en el JSON del caso (ver docs/SCHEMA.md).
"""
import datetime as dt
import json
import math
import os
import random
import struct
import zlib

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "public", "data")
NOTE = "DEMO / SIMULACIÓN: valores sintéticos, no son observaciones satelitales."


def write_json(rel, obj):
    full = os.path.join(OUT, rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)


def write_png(rel, w, h, rows):
    full = os.path.join(OUT, rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    raw = b"".join(b"\x00" + bytes(r) for r in rows)

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))
    with open(full, "wb") as f:
        f.write(png)


def fc(features):
    return {"type": "FeatureCollection", "features": features}


def feat(geom, props):
    return {"type": "Feature", "geometry": geom, "properties": props}


def ring(cx, cy, rx, ry, n=28, seed=1, jit=0.12, rot=0.0):
    r = random.Random(seed)
    pts = []
    for i in range(n):
        a = 2 * math.pi * i / n
        k = 1 + r.uniform(-jit, jit)
        x, y = rx * k * math.cos(a), ry * k * math.sin(a)
        pts.append([round(cx + x * math.cos(rot) - y * math.sin(rot), 5), round(cy + x * math.sin(rot) + y * math.cos(rot), 5)])
    pts.append(pts[0])
    return {"type": "Polygon", "coordinates": [pts]}


def bbox_line(w, s, e, n):
    return {"type": "LineString", "coordinates": [[w, s], [e, s], [e, n], [w, n], [w, s]]}


def corridor(a, b, w0, w1, n=14, sway=0.004):
    left, right = [], []
    dx, dy = b[0] - a[0], b[1] - a[1]
    L = math.hypot(dx, dy)
    nx, ny = -dy / L, dx / L
    for i in range(n + 1):
        t = i / n
        cx, cy = a[0] + dx * t + math.sin(t * 6) * sway, a[1] + dy * t
        w = w0 + (w1 - w0) * t
        left.append([round(cx + nx * w, 5), round(cy + ny * w, 5)])
        right.append([round(cx - nx * w, 5), round(cy - ny * w, 5)])
    return {"type": "Polygon", "coordinates": [left + right[::-1] + [left[0]]]}


AOI_PAINT = {"line-color": "#FF9F1C", "line-width": 2, "line-dasharray": [2, 2]}


def aoi_layer(case, extent):
    w, s, e, n = extent
    write_json(f"cases/{case}/aoi.geojson", fc([feat(bbox_line(w, s, e, n), {"name": "Área de estudio (DEMO)"})]))
    return {"id": "aoi", "name": "Área de estudio", "type": "line", "status": "demo", "simulated": True,
            "source": {"type": "geojson", "data": f"data/cases/{case}/aoi.geojson"}, "paint": AOI_PAINT,
            "description": "Límite del recorte de ejemplo.", "popupFields": [["name", "Nombre"]]}


# ═════════════════ Caso principal: flujo SAR-1 + SAR-2 + MODIS (rásteres sintéticos) ═════════════════
EXTENT = [85.46, 28.13, 85.61, 28.31]  # [oeste, sur, este, norte]
day = lambda s: dt.date.fromisoformat(s).toordinal()
PH = (0.7, 2.1, 4.0)


def terrain(u, v):
    return (0.5 + 0.25 * math.sin(2 * math.pi * (1.3 * u + 0.7 * v) + PH[0])
            + 0.15 * math.sin(2 * math.pi * (2.9 * u - 1.9 * v) + PH[1])
            + 0.08 * math.sin(2 * math.pi * (6.1 * u + 4.3 * v) + PH[2]))


ANG = math.radians(62)


def event_mask(u, v):
    du, dv = u - 0.52, v - 0.42
    x = du * math.cos(ANG) + dv * math.sin(ANG)
    y = -du * math.sin(ANG) + dv * math.cos(ANG)
    return math.exp(-((x / 0.38) ** 2 + (y / 0.10) ** 2))


def progress(date):  # cambio sintético entre el 22 y el 26 de marzo
    return min(1.0, max(0.0, (day(date) - day("2026-03-22")) / 4))


def to_gray(val, lo, hi):
    return 1 + max(0, min(254, round((val - lo) / (hi - lo) * 254)))


def make_raster(rel, w, h, fn, lo, hi, nodata_prob=0.0, seed=0):
    rnd = random.Random(seed)
    rows = []
    for y in range(h):
        row = []
        for x in range(w):
            u, v = (x + 0.5) / w, (y + 0.5) / h
            row.append(0 if rnd.random() < nodata_prob else to_gray(fn(u, v, rnd), lo, hi))
        rows.append(row)
    write_png(rel, w, h, rows)


SAR1 = ["2026-03-04", "2026-03-16", "2026-03-28", "2026-04-09"]
SAR2 = ["2026-03-09", "2026-03-21", "2026-04-02"]
MODIS = ["2026-03-06", "2026-03-14", "2026-03-22", "2026-03-30", "2026-04-07"]
tag = lambda s: s.replace("-", "")
C = "flujo-demo"

for d in SAR1:
    p = progress(d)
    make_raster(f"cases/{C}/rasters/sar1_HH_{tag(d)}.png", 144, 192,
                lambda u, v, r, p=p: -12 + 10 * (terrain(u, v) - 0.5) + r.gauss(0, 1.2) - 5.5 * p * event_mask(u, v), -25, 0, seed=1 + day(d))
    make_raster(f"cases/{C}/rasters/sar1_HV_{tag(d)}.png", 144, 192,
                lambda u, v, r, p=p: -20 + 8 * (terrain(u, v) - 0.5) + r.gauss(0, 1.0) - 6.0 * p * event_mask(u, v), -30, -5, seed=2 + day(d))
for d in SAR2:  # SAR-2: solo HV, otra resolución, otra fecha
    p = progress(d)
    make_raster(f"cases/{C}/rasters/sar2_HV_{tag(d)}.png", 108, 144,
                lambda u, v, r, p=p: -20.8 + 8 * (terrain(u, v) - 0.5) + r.gauss(0, 1.1) - 5.0 * p * event_mask(u, v), -30, -5, seed=3 + day(d))
for i, d in enumerate(MODIS):  # MODIS: píxel grueso y algunas "nubes" sin dato
    make_raster(f"cases/{C}/rasters/modis_temp_{tag(d)}.png", 14, 20,
                lambda u, v, r, d=d: 4 + 7 * (1 - terrain(u, v)) + 0.12 * (day(d) - day("2026-03-06")) + r.gauss(0, 0.4), -10, 15,
                nodata_prob=0.12 if i in (1, 3) else 0.0, seed=4 + i)


def rframes(prefix, dates, var_files):
    return [{"id": f"{prefix}-{tag(d)}", "datetime": d,
             "files": {v: f"data/cases/{C}/rasters/{name}_{tag(d)}.png" for v, name in var_files.items()}} for d in dates]


GRAY = ["#0B0F14", "#8A97A6", "#F4F7FA"]
HEAT = ["#2A4D9B", "#6EC3C9", "#F4E9A8", "#F4A261", "#C0392B"]
w, s, e, n = EXTENT


def footprint(k):
    pts = []
    for i in range(36):
        a = 2 * math.pi * i / 36
        x, y = 0.38 * k * math.cos(a), 0.10 * k * math.sin(a)
        du, dv = x * math.cos(ANG) - y * math.sin(ANG), x * math.sin(ANG) + y * math.cos(ANG)
        pts.append([round(w + (0.52 + du) * (e - w), 5), round(n - (0.42 + dv) * (n - s), 5)])
    pts.append(pts[0])
    return {"type": "Polygon", "coordinates": [pts]}


write_json(f"cases/{C}/risk_zones_sim.geojson", fc([
    feat(footprint(0.8), {"date": "2026-03-28", "zone_id": "Z-SIM-1", "risk_level_sim": "SIMULADO (nivel medio)", "area_km2_sim": 4.1, "note": NOTE}),
    feat(footprint(1.0), {"date": "2026-04-09", "zone_id": "Z-SIM-1", "risk_level_sim": "SIMULADO (nivel alto)", "area_km2_sim": 6.3, "note": NOTE}),
]))
pt = lambda u, v: {"type": "Point", "coordinates": [round(w + u * (e - w), 5), round(n - v * (n - s), 5)]}
write_json(f"cases/{C}/zone_metrics_sim.geojson", fc([
    feat(pt(0.45, 0.30), {"date": "2026-03-28", "name": "Punto sintético A", "melt_index_sim": 0.31, "swe_mm_sim": 220, "rain_mm_sim": 4.0, "note": NOTE}),
    feat(pt(0.60, 0.55), {"date": "2026-03-28", "name": "Punto sintético B", "melt_index_sim": 0.52, "rain_mm_sim": 6.5, "note": NOTE}),
    feat(pt(0.45, 0.30), {"date": "2026-04-09", "name": "Punto sintético A", "melt_index_sim": 0.47, "swe_mm_sim": 180, "rain_mm_sim": 9.0, "note": NOTE}),
    feat(pt(0.60, 0.55), {"date": "2026-04-09", "name": "Punto sintético B", "melt_index_sim": 0.74, "note": NOTE}),
]))

write_json(f"cases/{C}/case.json", {
    "schemaVersion": 2, "id": C, "status": "demo",
    "title": "Flujo de datos: SAR-1 + SAR-2 + MODIS (sintético)",
    "description": "Tres rásteres sintéticos sobre la misma zona y con fechas distintas, más polígonos y métricas SIMULADOS. Sirve para probar la interfaz; los nombres de satélites, productos, unidades y formatos reales están pendientes.",
    "view": {"center": [85.535, 28.22], "zoom": 11.2, "pitch": 45, "bearing": 15},
    "datasets": [
        {"id": "sar1", "name": "SAR satélite 1", "kind": "raster", "status": "demo",
         "platform": None, "sensor": None, "provider": None, "product": None, "attribution": "Datos sintéticos (DEMO)",
         "variables": [
             {"id": "HH", "label": "HH", "quantity": "backscatter", "polarization": "HH", "unit": "dB", "legend": {"min": -25, "max": 0, "colors": GRAY, "nodata_label": "Transparente = sin dato"}},
             {"id": "HV", "label": "HV", "quantity": "backscatter", "polarization": "HV", "unit": "dB", "legend": {"min": -30, "max": -5, "colors": GRAY}}],
         "composites": [{"id": "rgb-demo", "label": "Compuesto RGB de ejemplo", "channels": {"r": "HH", "g": "HV", "b": "HH"}, "note": "Asignación de canales solo de ejemplo; el equipo debe definirla."}],
         "source": {"type": "image", "encoding": "gray8", "nodata": 0}, "extent": EXTENT, "crs": "EPSG:4326", "resolution": None,
         "render": {"opacity": 0.85, "resampling": "linear"},
         "frames": rframes("s1", SAR1, {"HH": "sar1_HH", "HV": "sar1_HV"})},
        {"id": "sar2", "name": "SAR satélite 2 (solo HV en la demo)", "kind": "raster", "status": "demo",
         "platform": None, "sensor": None, "provider": None, "product": None, "attribution": "Datos sintéticos (DEMO)",
         "variables": [{"id": "HV", "label": "HV", "quantity": "backscatter", "polarization": "HV", "unit": "dB", "legend": {"min": -30, "max": -5, "colors": GRAY}}],
         "source": {"type": "image", "encoding": "gray8", "nodata": 0}, "extent": EXTENT, "crs": "EPSG:4326", "resolution": None,
         "render": {"opacity": 0.85, "resampling": "linear"},
         "frames": rframes("s2", SAR2, {"HV": "sar2_HV"})},
        {"id": "modis_temp", "name": "MODIS temperatura", "kind": "raster", "status": "demo",
         "platform": None, "sensor": "MODIS", "provider": None, "product": None, "attribution": "Datos sintéticos (DEMO)",
         "variables": [{"id": "T", "label": "Temperatura (simulada)", "quantity": "temperature", "polarization": None, "unit": "°C", "legend": {"min": -10, "max": 15, "colors": HEAT, "nodata_label": "Transparente = sin dato (nubes simuladas)"}}],
         "source": {"type": "image", "encoding": "gray8", "nodata": 0}, "extent": EXTENT, "crs": "EPSG:4326", "resolution": None,
         "render": {"opacity": 0.7, "resampling": "nearest"}, "defaultVisible": False,
         "frames": rframes("m", MODIS, {"T": "modis_temp"})}],
    "vectors": [
        aoi_layer(C, EXTENT),
        {"id": "risk-zones-sim", "name": "Zona de riesgo (SIMULADA)", "type": "fill", "status": "demo", "simulated": True,
         "indicator": {"kind": "risk_zone", "simulated": True, "validation": None},
         "source": {"type": "geojson", "data": f"data/cases/{C}/risk_zones_sim.geojson"}, "time": {"property": "date", "mode": "asof"},
         "paint": {"fill-color": "#E4572E", "fill-opacity": 0.35, "fill-outline-color": "#7A1F1F"},
         "legend": {"title": "Zona de riesgo", "items": [{"color": "#E4572E", "label": "Polígono SIMULADO (sin reglas ni validación)"}]},
         "description": "Polígono inventado para probar la interfaz. No es un cálculo ni una alerta.",
         "popupFields": [["zone_id", "Zona"], ["risk_level_sim", "Nivel de riesgo"], ["area_km2_sim", "Área", "km²"], ["note", "Aviso"]]},
        {"id": "zone-metrics-sim", "name": "Indicadores por punto (SIMULADOS)", "type": "circle", "status": "demo", "simulated": True,
         "indicator": {"kind": "metrics", "simulated": True, "validation": None},
         "source": {"type": "geojson", "data": f"data/cases/{C}/zone_metrics_sim.geojson"}, "time": {"property": "date", "mode": "asof"},
         "paint": {"circle-radius": 8, "circle-color": "#FFFFFF", "circle-stroke-color": "#E4572E", "circle-stroke-width": 3},
         "legend": {"title": "Indicadores", "items": [{"color": "#FFFFFF", "label": "Punto con deshielo, SWE y lluvia SIMULADOS"}]},
         "description": "Valores inventados; algunos atributos faltan a propósito para mostrar «No disponible».",
         "popupFields": [["melt_index_sim", "Índice de deshielo (sim.)"], ["swe_mm_sim", "SWE (sim.)", "mm"], ["rain_mm_sim", "Lluvia (sim.)", "mm"], ["note", "Aviso"]]}],
})

# ═════════════════ Casos solo vectoriales (ilustrativos, migrados al esquema v2) ═════════════════
# ---- Colapso glaciar tipo Langtang ----
L = "langtang-demo"
LE = [85.46, 28.13, 85.61, 28.31]
D3 = ["2026-03-04", "2026-03-28", "2026-04-09"]
a, b = (85.515, 28.275), (85.560, 28.170)
rnd = random.Random(7)
deform = []
for step in range(3):
    for i in range(34):
        t = rnd.uniform(0.0, 0.38)
        lon, lat = a[0] + (b[0] - a[0]) * t + rnd.uniform(-0.006, 0.006), a[1] + (b[1] - a[1]) * t + rnd.uniform(-0.004, 0.004)
        head = 1 - t / 0.38
        if step == 0:
            val = rnd.uniform(0.5, 3.0)
        elif step == 1:
            val = 4 + head * rnd.uniform(14, 30) + rnd.uniform(0, 4)
        else:
            if i % 6:
                continue
            val = rnd.uniform(0.0, 1.0)
        deform.append(feat({"type": "Point", "coordinates": [round(lon, 5), round(lat, 5)]}, {"date": D3[step], "vel_mm_day": round(val, 1), "note": NOTE}))
write_json(f"cases/{L}/deformation.geojson", fc(deform))
zone = corridor(a, (85.528, 28.228), 0.010, 0.016, n=8)
write_json(f"cases/{L}/coherence.geojson", fc([feat(zone, {"date": D3[i], "coherence": c, "note": NOTE}) for i, c in enumerate((0.85, 0.38, 0.08))]))
write_json(f"cases/{L}/impact.geojson", fc([feat(corridor(a, b, 0.008, 0.020, n=20), {"date": D3[2], "backscatter_change_db": -7.5, "zone": "Trayectoria de avalancha y flujo (ilustrativa)", "note": NOTE})]))
write_json(f"cases/{L}/case.json", {
    "schemaVersion": 2, "id": L, "status": "demo", "title": "Colapso glaciar tipo Langtang (vectores ilustrativos)",
    "description": "Escenario ilustrativo con vectores sintéticos (incluye valores tipo InSAR SIMULADOS). El evento descrito por el equipo está pendiente de verificar con fuentes.",
    "view": {"center": [85.537, 28.22], "zoom": 11.2, "pitch": 50, "bearing": 20}, "datasets": [],
    "vectors": [
        aoi_layer(L, LE),
        {"id": "coherence", "name": "Coherencia (simulada)", "type": "fill", "status": "demo", "simulated": True,
         "source": {"type": "geojson", "data": f"data/cases/{L}/coherence.geojson"}, "time": {"property": "date", "mode": "asof"},
         "paint": {"fill-color": ["interpolate", ["linear"], ["get", "coherence"], 0.0, "#7A1F1F", 0.4, "#E9C46A", 0.85, "#2A9D8F"], "fill-opacity": 0.45},
         "legend": {"title": "Coherencia (0–1)", "items": [{"color": "#2A9D8F", "label": "Alta (~0,85)"}, {"color": "#E9C46A", "label": "Media (~0,4)"}, {"color": "#7A1F1F", "label": "Baja (<0,1)"}]},
         "description": "Valores inventados para la demo.", "popupFields": [["coherence", "Coherencia"], ["note", "Aviso"]]},
        {"id": "deformation", "name": "Velocidad de deformación (simulada)", "type": "circle", "status": "demo", "simulated": True,
         "source": {"type": "geojson", "data": f"data/cases/{L}/deformation.geojson"}, "time": {"property": "date", "mode": "asof"},
         "paint": {"circle-radius": 6, "circle-stroke-color": "#FFFFFF", "circle-stroke-width": 1, "circle-color": ["interpolate", ["linear"], ["get", "vel_mm_day"], 0, "#2A9D8F", 5, "#E9C46A", 15, "#E4572E", 30, "#7A1F1F"]},
         "legend": {"title": "Velocidad (mm/día)", "items": [{"color": "#2A9D8F", "label": "0–5"}, {"color": "#E9C46A", "label": "5–15"}, {"color": "#E4572E", "label": "15–30"}, {"color": "#7A1F1F", "label": ">30"}]},
         "description": "Valores inventados; no provienen de un procesamiento InSAR.", "popupFields": [["vel_mm_day", "Velocidad", "mm/día"], ["note", "Aviso"]]},
        {"id": "impact", "name": "Zona de impacto (simulada)", "type": "fill", "status": "demo", "simulated": True,
         "source": {"type": "geojson", "data": f"data/cases/{L}/impact.geojson"}, "time": {"property": "date", "mode": "asof"},
         "paint": {"fill-color": "#E4572E", "fill-opacity": 0.5, "fill-outline-color": "#FFFFFF"},
         "legend": {"title": "Impacto", "items": [{"color": "#E4572E", "label": "Cambio de retrodispersión (dB)"}]},
         "description": "Corredor inventado.", "popupFields": [["zone", "Zona"], ["backscatter_change_db", "Cambio σ0", "dB"], ["note", "Aviso"]]}],
})

# ---- Cambio forestal ----
F = "forest-demo"
FE = [-63.0, -9.88, -62.80, -9.72]
DF = ["2026-02-01", "2026-03-01", "2026-04-01"]
rnd = random.Random(11)
sites = [(-62.90 + rnd.uniform(-0.07, 0.07), -9.80 + rnd.uniform(-0.05, 0.05)) for _ in range(12)]
clear = []
for i, (x, y) in enumerate(sites):
    step = 0 if i < 3 else (1 if i < 7 else 2)
    clear.append(feat(ring(x, y, rnd.uniform(0.004, 0.011), rnd.uniform(0.003, 0.008), n=14, seed=i, rot=rnd.uniform(0, 3)),
                      {"date": DF[step], "area_ha": round(rnd.uniform(40, 260)), "backscatter_drop_db": round(-rnd.uniform(3, 8), 1), "note": NOTE}))
write_json(f"cases/{F}/clearings.geojson", fc(clear))
write_json(f"cases/{F}/case.json", {
    "schemaVersion": 2, "id": F, "status": "demo", "title": "Cambio forestal / tala (vectores ilustrativos)",
    "description": "Parches sintéticos de pérdida de cobertura acumulados en el tiempo.",
    "view": {"center": [-62.90, -9.80], "zoom": 10.8, "pitch": 0, "bearing": 0}, "datasets": [],
    "vectors": [
        aoi_layer(F, FE),
        {"id": "clearings", "name": "Claros (acumulado, simulados)", "type": "fill", "status": "demo", "simulated": True,
         "source": {"type": "geojson", "data": f"data/cases/{F}/clearings.geojson"}, "time": {"property": "date", "mode": "cumulative"},
         "paint": {"fill-color": ["match", ["get", "date"], DF[0], "#E9C46A", DF[1], "#F4A261", "#E4572E"], "fill-opacity": 0.6, "fill-outline-color": "#FFFFFF"},
         "legend": {"title": "Primera aparición", "items": [{"color": "#E9C46A", "label": DF[0]}, {"color": "#F4A261", "label": DF[1]}, {"color": "#E4572E", "label": DF[2]}]},
         "description": "Cada polígono aparece en su fecha y se mantiene en las siguientes.",
         "popupFields": [["area_ha", "Área", "ha"], ["backscatter_drop_db", "Caída σ0 HV", "dB"], ["note", "Aviso"]]}],
})

# ---- Avance de incendio ----
I = "fire-demo"
IE = [-121.02, 39.50, -120.70, 39.70]
fx, fy = -120.90, 39.60
HOURS = [0, 6, 12, 24]
base = dt.datetime(2026, 3, 10, 0, 0)
DT = [(base + dt.timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:00Z") for h in HOURS]
radii = [(0.004, 0.003), (0.012, 0.006), (0.022, 0.009), (0.040, 0.013)]
write_json(f"cases/{I}/perimeters.geojson", fc([
    feat(ring(fx + rx * 0.9, fy + ry * 0.1, rx, ry, n=36, seed=3, jit=0.10, rot=0.25),
         {"date": DT[i], "hours_since_ignition": HOURS[i], "area_km2": round(math.pi * (rx * 85) * (ry * 111), 1), "note": NOTE})
    for i, (rx, ry) in enumerate(radii)]))
write_json(f"cases/{I}/ignition.geojson", fc([feat({"type": "Point", "coordinates": [fx, fy]}, {"name": "Punto de ignición (DEMO)"})]))
write_json(f"cases/{I}/case.json", {
    "schemaVersion": 2, "id": I, "status": "demo", "title": "Avance de incendio (vectores ilustrativos)",
    "description": "Perímetros sintéticos que crecen con el viento.",
    "view": {"center": [-120.87, 39.60], "zoom": 10.6, "pitch": 0, "bearing": 0}, "datasets": [],
    "vectors": [
        aoi_layer(I, IE),
        {"id": "perimeters", "name": "Perímetro del incendio (simulado)", "type": "fill", "status": "demo", "simulated": True,
         "source": {"type": "geojson", "data": f"data/cases/{I}/perimeters.geojson"}, "time": {"property": "date", "mode": "cumulative"},
         "paint": {"fill-color": ["match", ["get", "date"], DT[0], "#7A1F1F", DT[1], "#E4572E", DT[2], "#F4A261", "#E9C46A"], "fill-opacity": 0.4, "fill-outline-color": "#FFFFFF"},
         "legend": {"title": "Perímetro", "items": [{"color": "#7A1F1F", "label": "T+0 h"}, {"color": "#E4572E", "label": "T+6 h"}, {"color": "#F4A261", "label": "T+12 h"}, {"color": "#E9C46A", "label": "T+24 h"}]},
         "description": "Perímetros acumulados.", "popupFields": [["hours_since_ignition", "Horas desde ignición", "h"], ["area_km2", "Área", "km²"], ["note", "Aviso"]]},
        {"id": "ignition", "name": "Punto de ignición (simulado)", "type": "circle", "status": "demo", "simulated": True,
         "source": {"type": "geojson", "data": f"data/cases/{I}/ignition.geojson"},
         "paint": {"circle-radius": 7, "circle-color": "#FFFFFF", "circle-stroke-color": "#E4572E", "circle-stroke-width": 3},
         "description": "Origen supuesto del incendio de ejemplo.", "popupFields": [["name", "Nombre"]]}],
})

write_json("catalog.json", {"schemaVersion": 2, "cases": [f"data/cases/{c}/case.json" for c in (C, L, F, I)]})
print("Datos demo generados en", os.path.abspath(OUT))
