# SAR Cambios · NASA Space Apps 2026 — "Bailando con los SAR"

Visor interactivo (Mapbox GL JS + Vite, JavaScript puro) para explorar cambios en la superficie con capas SAR.
**Estado actual: prototipo con datos SINTÉTICOS (DEMO).** No detecta emergencias ni usa datos en tiempo real.

## Ejecutar
```bash
npm install
cp .env.example .env      # pega tu token público pk.* en VITE_MAPBOX_ACCESS_TOKEN
npm run dev
```
`npm run demo-data` regenera los datos demo (requiere Python 3).

## Controles del mapa
- **Arranque:** globo 3D con un punto por zona de estudio. Toca un punto (o usa la lista) para acercarte con una animación; **Ver el mundo** regresa al globo.
- **Vista:** *Globo 3D*, *Mapa plano* o *Terreno 3D* (relieve con elevación, cielo y luz). Con zoom alto el globo pasa solo a mapa plano.
- **Mapa base:** satélite, claro o calles. Las capas SAR se reaplican solas al cambiar de mapa base.
- **Terreno:** sombreado de relieve, exageración (0,5–3×) y azimut de la luz solar. El terreno usa el DEM de Mapbox y **no es dato SAR**.
- **Capas SAR (DEMO):** panel y leyenda aparte, con el control antes/después intacto.

## Estructura
```
src/main.js               UI (selector de caso, slider, capas, leyenda, panel de atributos)
src/lib/mapController.js  Mapbox: cargar caso, filtrar por paso, visibilidad, clic
src/lib/registry.js       Registro/validación de casos y capas, carga de GeoJSON
public/data/cases/        index.json + una carpeta por caso (case.json + capas)
scripts/make_demo_data.py Generador de datos sintéticos
```

## Formato de caso (`public/data/cases/<id>/case.json`)
```json
{
  "id": "mi-caso", "title": "Título", "description": "Texto",
  "view": { "center": [lon, lat], "zoom": 11, "pitch": 0, "bearing": 0 },
  "timeline": [ { "label": "Antes", "date": "2026-08-01", "note": "…" }, { "label": "Después", "date": "2026-08-27" } ],
  "layers": [ { ...capa } ]
}
```
Capa: `id, name, type (fill|line|circle|raster), source, paint, description, popupFields, legend, attribution, demo`.
- `source`: `{ "type": "geojson", "data": "data/cases/mi-caso/x.geojson" }` o `{ "type": "raster", "tiles": ["https://…/{z}/{x}/{y}.png"], "tileSize": 256 }`.
- `time`: `{ "property": "step", "mode": "exact" | "cumulative" }`. Cada feature trae `properties.step` (índice de `timeline`). `exact` = solo ese paso (antes/después); `cumulative` = hasta ese paso (avance de incendio).
- `steps`: `[0, 2]` muestra la capa solo en esos pasos (útil para un ráster por fecha).
- `popupFields`: `[["clave", "Etiqueta", "unidad"]]`.

## Añadir un caso nuevo
1. Crea `public/data/cases/mi-caso/` con `case.json` y tus `.geojson`.
2. Añade `"data/cases/mi-caso/case.json"` a `public/data/cases/index.json`.
3. Recarga. Si algo falta o falla, la UI lo indica y el resto sigue funcionando.

## Añadir una capa nueva
Agrega un objeto a `layers` del `case.json` (ver arriba). Desde código: `registerCase(def)` en `registry.js`.

## Conectar un pipeline SAR (pendiente)
El pipeline (Emiliano y Raul) solo debe escribir archivos con este formato:
- Vectores (puntos de velocidad InSAR, polígonos de cambio, perímetros) → GeoJSON con `properties.step`.
- Ráster (retrodispersión σ0 HH/HV/VV, coherencia, interferogramas, mapas de cambio) → tiles XYZ (COG + titiler, o tiles estáticos) referenciados como `source.type = "raster"`.

## Datos reales necesarios para validez científica
- Sentinel-1 / NISAR / SAOCOM / RADARSAT vía ASF Vertex o NASA Earthdata (verifica disponibilidad y fechas de NISAR para tu zona).
- InSAR: pares interferométricos (p. ej. ASF HyP3), coherencia, series de desplazamiento (LOS), corrección de atmósfera y DEM.
- Cambio: σ0 calibrado (γ0/σ0 en dB), speckle filtrado, ratio/diferencia entre fechas, comparación por polarización.
- Incendios: focos térmicos (NASA FIRMS) para validar; el SAR delimita cicatriz, no mide calor.
- Contexto: imagen óptica, DEM, inventarios de glaciares/deslizamientos.

## Limitaciones
- Todo dato incluido es sintético. Fechas `null` = sin definir.
- El caso Langtang es ilustrativo; el evento del 26-ago-2026 debe verificarse con fuentes antes de citarlo.
- Antes/después es por pasos discretos (no hay swipe con dos mapas ni mezcla ráster aún).
- Sin backend, sin autenticación, sin alertas reales.
