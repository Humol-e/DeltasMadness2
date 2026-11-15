"""Generates SYNTHETIC demo data (not real SAR observations).
Usage: python3 scripts/make_demo_data.py
Output: public/data/cases/<case>/*.geojson + case.json + index.json
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
    """Polygon for a corridor from A to B with growing width (avalanche path)."""
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


DEMO_NOTE = "DEMO / SIMULATION: synthetic values, not real SAR observations."

# ───────────────────────── Case 1: glacial collapse (illustrative) ─────────────────────────
a, b = (85.515, 28.275), (85.560, 28.170)
rnd = random.Random(7)
deform = []
for step in range(3):
    for i in range(34):
        t = rnd.uniform(0.0, 0.38)
        lon = a[0] + (b[0] - a[0]) * t + rnd.uniform(-0.006, 0.006)
        lat = a[1] + (b[1] - a[1]) * t + rnd.uniform(-0.004, 0.004)
        head = 1 - t / 0.38  # closer to the headwall = stronger acceleration
        if step == 0:
            v = rnd.uniform(0.5, 3.0)
        elif step == 1:
            v = 4 + head * rnd.uniform(14, 30) + rnd.uniform(0, 4)
        else:
            if i % 6:  # very few coherent points remain after the collapse
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
                                                 "zone": "Avalanche and runout path (illustrative)", "note": DEMO_NOTE})
]))
write("langtang-demo/aoi.geojson", fc([feat(bbox_line(85.46, 28.13, 85.61, 28.31), {"name": "Study area (DEMO)"})]))

write("langtang-demo/case.json", {
    "id": "langtang-demo",
    "title": "Langtang-type glacial collapse (illustrative)",
    "description": "Illustrative scenario based on the event described by the team (Langtang Lirung, Aug 26, 2026, pending source verification). Geometry and values are synthetic.",
    "view": {"center": [85.537, 28.22], "zoom": 11.2, "pitch": 50, "bearing": 20},
    "timeline": [
        {"label": "Before", "date": None, "note": "Slow deformation and high coherence."},
        {"label": "Precursor", "date": None, "note": "Accelerating deformation and coherence loss near the headwall."},
        {"label": "After", "date": None, "note": "Reorganized surface: strong backscatter change and near-zero coherence."}
    ],
    "layers": [
        {"id": "aoi", "name": "Study area", "type": "line", "demo": True,
         "source": {"type": "geojson", "data": "data/cases/langtang-demo/aoi.geojson"},
         "paint": {"line-color": "#FF9F1C", "line-width": 2, "line-dasharray": [2, 2]},
         "description": "Sample AOI boundary.", "popupFields": [["name", "Name"]]},
        {"id": "coherence", "name": "InSAR coherence", "type": "fill", "demo": True,
         "source": {"type": "geojson", "data": "data/cases/langtang-demo/coherence.geojson"},
         "time": {"property": "step", "mode": "exact"},
         "paint": {"fill-color": ["interpolate", ["linear"], ["get", "coherence"], 0.0, "#7A1F1F", 0.4, "#E9C46A", 0.85, "#2A9D8F"], "fill-opacity": 0.45},
         "legend": {"title": "Coherence (0–1)", "items": [{"color": "#2A9D8F", "label": "High (~0.85)"}, {"color": "#E9C46A", "label": "Moderate (~0.4)"}, {"color": "#7A1F1F", "label": "Low (<0.1)"}]},
         "description": "A temporal coherence drop indicates fracturing or surface change between acquisitions.",
         "popupFields": [["coherence", "Coherence", ""], ["note", "Notice"]]},
        {"id": "deformation", "name": "Deformation velocity (LOS)", "type": "circle", "demo": True,
         "source": {"type": "geojson", "data": "data/cases/langtang-demo/deformation.geojson"},
         "time": {"property": "step", "mode": "exact"},
         "paint": {"circle-radius": 6, "circle-stroke-color": "#FFFFFF", "circle-stroke-width": 1,
                   "circle-color": ["interpolate", ["linear"], ["get", "vel_mm_day"], 0, "#2A9D8F", 5, "#E9C46A", 15, "#E4572E", 30, "#7A1F1F"]},
         "legend": {"title": "Velocity (mm/day)", "items": [{"color": "#2A9D8F", "label": "0–5"}, {"color": "#E9C46A", "label": "5–15"}, {"color": "#E4572E", "label": "15–30"}, {"color": "#7A1F1F", "label": ">30"}]},
         "description": "Displacement along the satellite line of sight. Sustained acceleration is a collapse precursor.",
         "popupFields": [["vel_mm_day", "Velocity", "mm/day"], ["note", "Notice"]]},
        {"id": "impact", "name": "Impact zone", "type": "fill", "demo": True,
         "source": {"type": "geojson", "data": "data/cases/langtang-demo/impact.geojson"},
         "time": {"property": "step", "mode": "exact"},
         "paint": {"fill-color": "#E4572E", "fill-opacity": 0.5, "fill-outline-color": "#FFFFFF"},
         "legend": {"title": "Impact", "items": [{"color": "#E4572E", "label": "Backscatter change (dB)"}]},
         "description": "Area with abrupt roughness and backscatter change between pre- and post-event dates.",
         "popupFields": [["zone", "Zone"], ["backscatter_change_db", "σ0 change", "dB"], ["note", "Notice"]]}
    ]
})

# ───────────────────────── Case 2: forest change (illustrative) ─────────────────────────
rnd = random.Random(11)
clear = []
cx0, cy0 = -62.90, -9.80
sites = [(cx0 + rnd.uniform(-0.07, 0.07), cy0 + rnd.uniform(-0.05, 0.05)) for _ in range(12)]
for i, (x, y) in enumerate(sites):
    step = 0 if i < 3 else (1 if i < 7 else 2)
    clear.append(feat(ring(x, y, rnd.uniform(0.004, 0.011), rnd.uniform(0.003, 0.008), n=14, seed=i, rot=rnd.uniform(0, 3)),
                      {"step": step, "area_ha": round(rnd.uniform(40, 260)), "backscatter_drop_db": round(-rnd.uniform(3, 8), 1), "note": DEMO_NOTE}))
write("forest-demo/clearings.geojson", fc(clear))
write("forest-demo/aoi.geojson", fc([feat(bbox_line(-63.0, -9.88, -62.80, -9.72), {"name": "Study area (DEMO)"})]))
write("forest-demo/case.json", {
    "id": "forest-demo",
    "title": "Forest change / clearing (illustrative)",
    "description": "Synthetic canopy-loss patches. With real data, cross-pol (HV) backscatter drop and coherence loss signal canopy removal.",
    "view": {"center": [-62.90, -9.80], "zoom": 10.8, "pitch": 0, "bearing": 0},
    "timeline": [
        {"label": "Date 1", "date": None, "note": "Baseline."},
        {"label": "Date 2", "date": None, "note": "New clearings appear."},
        {"label": "Date 3", "date": None, "note": "Clearings accumulate."}
    ],
    "layers": [
        {"id": "aoi", "name": "Study area", "type": "line", "demo": True,
         "source": {"type": "geojson", "data": "data/cases/forest-demo/aoi.geojson"},
         "paint": {"line-color": "#FF9F1C", "line-width": 2, "line-dasharray": [2, 2]},
         "description": "Sample AOI boundary.", "popupFields": [["name", "Name"]]},
        {"id": "clearings", "name": "Detected clearings (cumulative)", "type": "fill", "demo": True,
         "source": {"type": "geojson", "data": "data/cases/forest-demo/clearings.geojson"},
         "time": {"property": "step", "mode": "cumulative"},
         "paint": {"fill-color": ["match", ["get", "step"], 0, "#E9C46A", 1, "#F4A261", "#E4572E"], "fill-opacity": 0.6, "fill-outline-color": "#FFFFFF"},
         "legend": {"title": "First detection", "items": [{"color": "#E9C46A", "label": "Date 1"}, {"color": "#F4A261", "label": "Date 2"}, {"color": "#E4572E", "label": "Date 3"}]},
         "description": "Each polygon appears on the date it was first detected and stays visible in subsequent steps.",
         "popupFields": [["step", "Detection step"], ["area_ha", "Area", "ha"], ["backscatter_drop_db", "σ0 HV drop", "dB"], ["note", "Notice"]]}
    ]
})

# ───────────────────────── Case 3: fire progression (illustrative) ─────────────────────────
fx, fy = -120.90, 39.60
steps = [(0, 0.004, 0.003), (6, 0.012, 0.006), (12, 0.022, 0.009), (24, 0.040, 0.013)]
per = []
for i, (h, rx, ry) in enumerate(steps):
    per.append(feat(ring(fx + rx * 0.9, fy + ry * 0.1, rx, ry, n=36, seed=3, jit=0.10, rot=0.25),
                    {"step": i, "hours_since_ignition": h, "area_km2": round(math.pi * (rx * 85) * (ry * 111), 1), "note": DEMO_NOTE}))
write("fire-demo/perimeters.geojson", fc(per))
write("fire-demo/ignition.geojson", fc([feat({"type": "Point", "coordinates": [fx, fy]}, {"name": "Ignition point (DEMO)"})]))
write("fire-demo/aoi.geojson", fc([feat(bbox_line(-121.02, 39.50, -120.70, 39.70), {"name": "Study area (DEMO)"})]))
write("fire-demo/case.json", {
    "id": "fire-demo",
    "title": "Wildfire progression (illustrative)",
    "description": "Synthetic perimeters expanding with wind. With real data, SAR sees through smoke and clouds; coherence loss and backscatter drop help delineate burned areas.",
    "view": {"center": [-120.87, 39.60], "zoom": 10.6, "pitch": 0, "bearing": 0},
    "timeline": [
        {"label": "T+0 h", "date": None, "note": "Ignition."},
        {"label": "T+6 h", "date": None, "note": "Initial spread."},
        {"label": "T+12 h", "date": None, "note": "Fire front elongates with wind."},
        {"label": "T+24 h", "date": None, "note": "Maximum perimeter in this sample."}
    ],
    "layers": [
        {"id": "aoi", "name": "Study area", "type": "line", "demo": True,
         "source": {"type": "geojson", "data": "data/cases/fire-demo/aoi.geojson"},
         "paint": {"line-color": "#FF9F1C", "line-width": 2, "line-dasharray": [2, 2]},
         "description": "Sample AOI boundary.", "popupFields": [["name", "Name"]]},
        {"id": "perimeters", "name": "Fire perimeter", "type": "fill", "demo": True,
         "source": {"type": "geojson", "data": "data/cases/fire-demo/perimeters.geojson"},
         "time": {"property": "step", "mode": "cumulative"},
         "paint": {"fill-color": ["match", ["get", "step"], 0, "#7A1F1F", 1, "#E4572E", 2, "#F4A261", "#E9C46A"], "fill-opacity": 0.4, "fill-outline-color": "#FFFFFF"},
         "legend": {"title": "Perimeter", "items": [{"color": "#7A1F1F", "label": "T+0 h"}, {"color": "#E4572E", "label": "T+6 h"}, {"color": "#F4A261", "label": "T+12 h"}, {"color": "#E9C46A", "label": "T+24 h"}]},
         "description": "Cumulative perimeters: each step adds the newly reached area.",
         "popupFields": [["hours_since_ignition", "Hours since ignition", "h"], ["area_km2", "Area", "km²"], ["note", "Notice"]]},
        {"id": "ignition", "name": "Ignition point", "type": "circle", "demo": True,
         "source": {"type": "geojson", "data": "data/cases/fire-demo/ignition.geojson"},
         "paint": {"circle-radius": 7, "circle-color": "#FFFFFF", "circle-stroke-color": "#E4572E", "circle-stroke-width": 3},
         "description": "Assumed origin of the sample wildfire.", "popupFields": [["name", "Name"]]}
    ]
})

write("index.json", {"cases": ["data/cases/langtang-demo/case.json", "data/cases/forest-demo/case.json", "data/cases/fire-demo/case.json"]})
print("Demo data generated in", os.path.abspath(OUT))
