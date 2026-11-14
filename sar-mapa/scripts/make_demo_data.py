"""Genera datos SINTÉTICOS de demostración (no son observaciones SAR reales).
Uso: python3 scripts/make_demo_data.py
Salida: public/data/cases/<caso>/*.geojson + case.json + index.json
"""
import json, math, random, os

OUT = os.path.join(os.path.dirname(__file__), "..", "public", "data", "cases")


def write(path, obj):
    full = os.path.join(OUT, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)


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
        pts.append([round(cx + x * math.cos(rot) - y * math.sin(rot), 5),
                    round(cy + x * math.sin(rot) + y * math.cos(rot), 5)])
    pts.append(pts[0])
    return {"type": "Polygon", "coordinates": [pts]}


def bbox_line(w, s, e, n):
    return {"type": "LineString", "coordinates": [[w, s], [e, s], [e, n], [w, n], [w, s]]}


def corridor(a, b, w0, w1, n=14, sway=0.004):
    """Polígono de un corredor de A a B con ancho creciente (trayectoria de avalancha)."""
    left, right = [], []
    dx, dy = b[0] - a[0], b[1] - a[1]
    L = math.hypot(dx, dy)
    nx, ny = -dy / L, dx / L
    for i in range(n + 1):
        t = i / n
        cx = a[0] + dx * t + math.sin(t * 6) * sway
        cy = a[1] + dy * t
        w = w0 + (w1 - w0) * t
        left.append([round(cx + nx * w, 5), round(cy + ny * w, 5)])
        right.append([round(cx - nx * w, 5), round(cy - ny * w, 5)])
    ring_ = left + right[::-1] + [left[0]]
    return {"type": "Polygon", "coordinates": [ring_]}


DEMO_NOTE = "DEMO / SIMULACIÓN: valores sintéticos, no son observaciones SAR reales."

# ───────────────────────── Caso 1: colapso glaciar (ilustrativo) ─────────────────────────
a, b = (85.515, 28.275), (85.560, 28.170)
rnd = random.Random(7)
deform = []
for step in range(3):
    for i in range(34):
        t = rnd.uniform(0.0, 0.38)
        lon = a[0] + (b[0] - a[0]) * t + rnd.uniform(-0.006, 0.006)
        lat = a[1] + (b[1] - a[1]) * t + rnd.uniform(-0.004, 0.004)
        head = 1 - t / 0.38  # más cerca de la cabecera = más aceleración
        if step == 0:
            v = rnd.uniform(0.5, 3.0)
        elif step == 1:
            v = 4 + head * rnd.uniform(14, 30) + rnd.uniform(0, 4)
        else:
            if i % 6:  # tras el colapso casi no quedan puntos coherentes
                continue
            v = rnd.uniform(0.0, 1.0)
        deform.append(feat({"type": "Point", "coordinates": [round(lon, 5), round(lat, 5)]},
                           {"step": step, "vel_mm_day": round(v, 1), "note": DEMO_NOTE}))
write("langtang-demo/deformation.geojson", fc(deform))

zone = corridor(a, (85.528, 28.228), 0.010, 0.016, n=8)
write("langtang-demo/coherence.geojson", fc([
    feat(zone, {"step": 0, "coherence": 0.85, "note": DEMO_NOTE}),
    feat(zone, {"step": 1, "coherence": 0.38, "note": DEMO_NOTE}),
    feat(zone, {"step": 2, "coherence": 0.08, "note": DEMO_NOTE}),
]))
write("langtang-demo/impact.geojson", fc([
    feat(corridor(a, b, 0.008, 0.020, n=20), {"step": 2, "backscatter_change_db": -7.5,
                                                 "zone": "Trayectoria de avalancha y flujo (ilustrativa)", "note": DEMO_NOTE})
]))
write("langtang-demo/aoi.geojson", fc([feat(bbox_line(85.46, 28.13, 85.61, 28.31), {"name": "Área de estudio (DEMO)"})]))

write("langtang-demo/case.json", {
    "id": "langtang-demo",
    "title": "Colapso glaciar tipo Langtang (ilustrativo)",
    "description": "Escenario ilustrativo basado en el evento descrito por el equipo (Langtang Lirung, 26-ago-2026, pendiente de verificar con fuentes). La geometría y los valores son sintéticos.",
    "view": {"center": [85.537, 28.22], "zoom": 11.2, "pitch": 50, "bearing": 20},
    "timeline": [
        {"label": "Antes", "date": None, "note": "Deformación lenta y coherencia alta."},
        {"label": "Precursor", "date": None, "note": "Aceleración de la deformación y pérdida de coherencia en la cabecera."},
        {"label": "Después", "date": None, "note": "Superficie reorganizada: cambio fuerte de retrodispersión y coherencia casi nula."}
    ],
    "layers": [
        {"id": "aoi", "name": "Área de estudio", "type": "line", "demo": True,
         "source": {"type": "geojson", "data": "data/cases/langtang-demo/aoi.geojson"},
         "paint": {"line-color": "#FF9F1C", "line-width": 2, "line-dasharray": [2, 2]},
         "description": "Límite del recorte de ejemplo.", "popupFields": [["name", "Nombre"]]},
        {"id": "coherence", "name": "Coherencia InSAR", "type": "fill", "demo": True,
         "source": {"type": "geojson", "data": "data/cases/langtang-demo/coherence.geojson"},
         "time": {"property": "step", "mode": "exact"},
         "paint": {"fill-color": ["interpolate", ["linear"], ["get", "coherence"], 0.0, "#7A1F1F", 0.4, "#E9C46A", 0.85, "#2A9D8F"], "fill-opacity": 0.45},
         "legend": {"title": "Coherencia (0–1)", "items": [{"color": "#2A9D8F", "label": "Alta (~0,85)"}, {"color": "#E9C46A", "label": "Media (~0,4)"}, {"color": "#7A1F1F", "label": "Baja (<0,1)"}]},
         "description": "Una caída de coherencia temporal indica fracturas o cambios en la superficie entre adquisiciones.",
         "popupFields": [["coherence", "Coherencia", ""], ["note", "Aviso"]]},
        {"id": "deformation", "name": "Velocidad de deformación (LOS)", "type": "circle", "demo": True,
         "source": {"type": "geojson", "data": "data/cases/langtang-demo/deformation.geojson"},
         "time": {"property": "step", "mode": "exact"},
         "paint": {"circle-radius": 6, "circle-stroke-color": "#FFFFFF", "circle-stroke-width": 1,
                   "circle-color": ["interpolate", ["linear"], ["get", "vel_mm_day"], 0, "#2A9D8F", 5, "#E9C46A", 15, "#E4572E", 30, "#7A1F1F"]},
         "legend": {"title": "Velocidad (mm/día)", "items": [{"color": "#2A9D8F", "label": "0–5"}, {"color": "#E9C46A", "label": "5–15"}, {"color": "#E4572E", "label": "15–30"}, {"color": "#7A1F1F", "label": ">30"}]},
         "description": "Desplazamiento a lo largo de la línea de visión del satélite. La aceleración sostenida es un precursor de colapso.",
         "popupFields": [["vel_mm_day", "Velocidad", "mm/día"], ["note", "Aviso"]]},
        {"id": "impact", "name": "Zona de impacto", "type": "fill", "demo": True,
         "source": {"type": "geojson", "data": "data/cases/langtang-demo/impact.geojson"},
         "time": {"property": "step", "mode": "exact"},
         "paint": {"fill-color": "#E4572E", "fill-opacity": 0.5, "fill-outline-color": "#FFFFFF"},
         "legend": {"title": "Impacto", "items": [{"color": "#E4572E", "label": "Cambio de retrodispersión (dB)"}]},
         "description": "Área con cambio abrupto de rugosidad y retrodispersión entre fechas anteriores y posteriores.",
         "popupFields": [["zone", "Zona"], ["backscatter_change_db", "Cambio σ0", "dB"], ["note", "Aviso"]]}
    ]
})

# ───────────────────────── Caso 2: cambio forestal (ilustrativo) ─────────────────────────
rnd = random.Random(11)
clear = []
cx0, cy0 = -62.90, -9.80
sites = [(cx0 + rnd.uniform(-0.07, 0.07), cy0 + rnd.uniform(-0.05, 0.05)) for _ in range(12)]
for i, (x, y) in enumerate(sites):
    step = 0 if i < 3 else (1 if i < 7 else 2)
    clear.append(feat(ring(x, y, rnd.uniform(0.004, 0.011), rnd.uniform(0.003, 0.008), n=14, seed=i, rot=rnd.uniform(0, 3)),
                      {"step": step, "area_ha": round(rnd.uniform(40, 260)), "backscatter_drop_db": round(-rnd.uniform(3, 8), 1), "note": DEMO_NOTE}))
write("forest-demo/clearings.geojson", fc(clear))
write("forest-demo/aoi.geojson", fc([feat(bbox_line(-63.0, -9.88, -62.80, -9.72), {"name": "Área de estudio (DEMO)"})]))
write("forest-demo/case.json", {
    "id": "forest-demo",
    "title": "Cambio forestal / tala (ilustrativo)",
    "description": "Parches sintéticos de pérdida de cobertura. Con datos reales, la caída de retrodispersión en cross-pol (HV) y la pérdida de coherencia señalan remoción de dosel.",
    "view": {"center": [-62.90, -9.80], "zoom": 10.8, "pitch": 0, "bearing": 0},
    "timeline": [
        {"label": "Fecha 1", "date": None, "note": "Línea base."},
        {"label": "Fecha 2", "date": None, "note": "Aparecen nuevos claros."},
        {"label": "Fecha 3", "date": None, "note": "Los claros se acumulan."}
    ],
    "layers": [
        {"id": "aoi", "name": "Área de estudio", "type": "line", "demo": True,
         "source": {"type": "geojson", "data": "data/cases/forest-demo/aoi.geojson"},
         "paint": {"line-color": "#FF9F1C", "line-width": 2, "line-dasharray": [2, 2]},
         "description": "Límite del recorte de ejemplo.", "popupFields": [["name", "Nombre"]]},
        {"id": "clearings", "name": "Claros detectados (acumulado)", "type": "fill", "demo": True,
         "source": {"type": "geojson", "data": "data/cases/forest-demo/clearings.geojson"},
         "time": {"property": "step", "mode": "cumulative"},
         "paint": {"fill-color": ["match", ["get", "step"], 0, "#E9C46A", 1, "#F4A261", "#E4572E"], "fill-opacity": 0.6, "fill-outline-color": "#FFFFFF"},
         "legend": {"title": "Primera detección", "items": [{"color": "#E9C46A", "label": "Fecha 1"}, {"color": "#F4A261", "label": "Fecha 2"}, {"color": "#E4572E", "label": "Fecha 3"}]},
         "description": "Cada polígono aparece en la fecha en que se detectó por primera vez y se mantiene en las siguientes.",
         "popupFields": [["step", "Paso de detección"], ["area_ha", "Área", "ha"], ["backscatter_drop_db", "Caída σ0 HV", "dB"], ["note", "Aviso"]]}
    ]
})

# ───────────────────────── Caso 3: avance de incendio (ilustrativo) ─────────────────────────
fx, fy = -120.90, 39.60
steps = [(0, 0.004, 0.003), (6, 0.012, 0.006), (12, 0.022, 0.009), (24, 0.040, 0.013)]
per = []
for i, (h, rx, ry) in enumerate(steps):
    per.append(feat(ring(fx + rx * 0.9, fy + ry * 0.1, rx, ry, n=36, seed=3, jit=0.10, rot=0.25),
                    {"step": i, "hours_since_ignition": h, "area_km2": round(math.pi * (rx * 85) * (ry * 111), 1), "note": DEMO_NOTE}))
write("fire-demo/perimeters.geojson", fc(per))
write("fire-demo/ignition.geojson", fc([feat({"type": "Point", "coordinates": [fx, fy]}, {"name": "Punto de ignición (DEMO)"})]))
write("fire-demo/aoi.geojson", fc([feat(bbox_line(-121.02, 39.50, -120.70, 39.70), {"name": "Área de estudio (DEMO)"})]))
write("fire-demo/case.json", {
    "id": "fire-demo",
    "title": "Avance de incendio (ilustrativo)",
    "description": "Perímetros sintéticos que crecen con el viento. Con datos reales, el SAR ve a través de humo y nubes; la pérdida de coherencia y la caída de retrodispersión ayudan a delimitar el área quemada.",
    "view": {"center": [-120.87, 39.60], "zoom": 10.6, "pitch": 0, "bearing": 0},
    "timeline": [
        {"label": "T+0 h", "date": None, "note": "Ignición."},
        {"label": "T+6 h", "date": None, "note": "Primer avance."},
        {"label": "T+12 h", "date": None, "note": "El frente se alarga con el viento."},
        {"label": "T+24 h", "date": None, "note": "Perímetro máximo del ejemplo."}
    ],
    "layers": [
        {"id": "aoi", "name": "Área de estudio", "type": "line", "demo": True,
         "source": {"type": "geojson", "data": "data/cases/fire-demo/aoi.geojson"},
         "paint": {"line-color": "#FF9F1C", "line-width": 2, "line-dasharray": [2, 2]},
         "description": "Límite del recorte de ejemplo.", "popupFields": [["name", "Nombre"]]},
        {"id": "perimeters", "name": "Perímetro del incendio", "type": "fill", "demo": True,
         "source": {"type": "geojson", "data": "data/cases/fire-demo/perimeters.geojson"},
         "time": {"property": "step", "mode": "cumulative"},
         "paint": {"fill-color": ["match", ["get", "step"], 0, "#7A1F1F", 1, "#E4572E", 2, "#F4A261", "#E9C46A"], "fill-opacity": 0.4, "fill-outline-color": "#FFFFFF"},
         "legend": {"title": "Perímetro", "items": [{"color": "#7A1F1F", "label": "T+0 h"}, {"color": "#E4572E", "label": "T+6 h"}, {"color": "#F4A261", "label": "T+12 h"}, {"color": "#E9C46A", "label": "T+24 h"}]},
         "description": "Perímetros acumulados: cada paso suma el área alcanzada.",
         "popupFields": [["hours_since_ignition", "Horas desde ignición", "h"], ["area_km2", "Área", "km²"], ["note", "Aviso"]]},
        {"id": "ignition", "name": "Punto de ignición", "type": "circle", "demo": True,
         "source": {"type": "geojson", "data": "data/cases/fire-demo/ignition.geojson"},
         "paint": {"circle-radius": 7, "circle-color": "#FFFFFF", "circle-stroke-color": "#E4572E", "circle-stroke-width": 3},
         "description": "Origen supuesto del incendio de ejemplo.", "popupFields": [["name", "Nombre"]]}
    ]
})

write("index.json", {"cases": ["data/cases/langtang-demo/case.json", "data/cases/forest-demo/case.json", "data/cases/fire-demo/case.json"]})
print("Datos demo generados en", os.path.abspath(OUT))
