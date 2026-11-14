# SAR Cambios · NASA Space Apps 2026 — "Bailando con los SAR"

Visor interactivo (Mapbox GL JS + Vite, JavaScript puro) con globo 3D, relieve, rásteres por fecha, comparador antes/después y vectores GeoJSON.
**Estado: DEMO con datos sintéticos.** No son observaciones satelitales, ni alertas, ni datos en tiempo real.

## Ejecutar
```bash
npm install
cp .env.example .env      # token PÚBLICO pk.* en VITE_MAPBOX_ACCESS_TOKEN (solo en .env; está en .gitignore)
npm run dev
```
- `npm run validate`: valida el catálogo y que existan los archivos referenciados (también corre antes de `npm run build`).
- `npm run demo-data`: regenera los datos sintéticos (Python 3, sin dependencias).

## Arquitectura
```
src/lib/mapController.js  MOTOR del mapa (globo, relieve, capas, swipe). No conoce ningún caso ni dataset concreto.
src/lib/schema.js         Esquema v2, validación y lector JSON con comentarios
src/lib/timeline.js       Eje temporal y "último frame ≤ fecha" por fuente
src/lib/rasterCanvas.js   Colorizado gray8, compuestos RGB, valor de píxel
src/lib/registry.js       Carga del catálogo y los casos
src/main.js, style.css    Interfaz
public/data/catalog.json  Lista de casos
public/data/cases/<id>/   case.json + rásteres (PNG) + GeoJSON
docs/                     SCHEMA.md, case.example.jsonc (comentado), case.template.json (vacía)
```
Para agregar o reemplazar un dataset solo se edita un JSON y se copian archivos; el controlador no cambia. Referencia completa: `docs/SCHEMA.md`.

## Caso demo `flujo-demo`
Representa el flujo esperado: **SAR-1** (HH + HV, 4 fechas), **SAR-2** (solo HV, 3 fechas, otra resolución) y **MODIS temperatura** (5 fechas, píxel grueso, nubes simuladas), sobre la misma zona. Las fechas no coinciden a propósito. Los nombres de satélites, productos, unidades, CRS y formatos están `null` («pendiente») hasta que el equipo los defina.

## Cómo probar la animación
1. `npm run dev`, abre la app y elige **Flujo de datos: SAR-1 + SAR-2 + MODIS (sintético)** (o toca su punto en el globo).
2. En **Tiempo**: *Reproducir* recorre los 12 frames del eje (unión de todas las fechas); *Anterior/Siguiente* avanzan de uno en uno; el selector fija la velocidad.
3. Cada tarjeta de **Rásteres** muestra la fecha de SU frame y cuántos días difiere de la fecha elegida («no simultáneo»). Activa MODIS (empieza apagado) y baja la opacidad para comparar.
4. **Comparar antes / después**: actívalo, elige dataset, variable y dos fechas, y arrastra el corte.
5. Haz clic en el mapa: verás los atributos del vector (los faltantes dicen «No disponible») y el valor de cada ráster en ese punto.
6. Cambia a **Terreno 3D** y a otros mapas base: las capas se mantienen.

La animación solo recorre frames existentes; no hay interpolación ni transiciones inventadas.

## Cómo añadir...
**Una fecha (frame):** copia el PNG a `public/data/cases/<caso>/rasters/` y añade un objeto a `frames` del dataset:
```json
{ "id": "s1-20260421", "datetime": "2026-04-21", "files": { "HH": "data/cases/<caso>/rasters/sar1_HH_20260421.png" } }
```
**Un ráster (dataset):** añade un objeto a `datasets` (copia uno existente o `docs/case.template.json`): `id`, `variables`, `source`, `extent`, `frames`.
Para teselas XYZ: `"source": { "type": "tiles", "tileSize": 256 }` y `files` con URL `https://…/{z}/{x}/{y}.png` (sin credenciales en el frontend: usa un proxy). Con teselas no hay swipe ni valor de píxel.
**Una polarización:** añade una entrada a `variables` del dataset (`{ "id": "VV", "polarization": "VV", "unit": …, "legend": … }`) y un archivo por frame en `files.VV`. Cada satélite declara SUS polarizaciones; un frame puede omitir una variable (se muestra «La variable … no viene en este frame»).
**Un compuesto RGB:** añade a `composites` del dataset `{ "id": "…", "label": "…", "channels": { "r": "HH", "g": "HV", "b": null } }`. No hay compuestos por defecto.
**Un polígono o punto:** crea un GeoJSON con `properties.date` (ISO 8601) y añade un objeto a `vectors` con `time: { "property": "date", "mode": "asof" }`. Deshielo, riesgo, SWE o lluvia son atributos/campos opcionales en `popupFields`; si no son simulados, define `indicator.validation`.
Después ejecuta `npm run validate`.

## Reemplazar los datos sintéticos
1. Con el pipeline (ASF HyP3 / Google Earth Engine) genera por fecha un archivo por variable y copia los archivos (o publica teselas).
2. Edita `case.json`: `status: "real"`, `platform`, `sensor`, `provider`, `product`, `unit`, `crs`, `resolution`, `extent`, leyendas y `frames`. Si el formato real no es PNG gray8, cambia `source` (`rgba` o `tiles`).
3. Quita `simulated` de los vectores solo cuando existan datos, unidades, reglas y validación definidos por el equipo.
4. Borra `scripts/make_demo_data.py` y los casos demo cuando ya no se necesiten.

## Errores y rendimiento
- Una sola fuente y capa por dataset (el cambio de fecha actualiza la imagen; no se duplican). Con teselas, una capa por frame creada solo cuando se necesita y cacheada. Los frames coloreados se cachean (máx. 60) y se precarga el siguiente durante la reproducción.
- Errores: se muestran con la causa (por ejemplo «HTTP 429: el servidor limitó las peticiones») y la fuente afectada; el token se oculta en los mensajes. No hay reintentos automáticos: el botón «Reintentar» es manual. Si una capa falla, las otras siguen y la tarjeta indica cuál no cargó.
- El sombreado de relieve solo se pide desde el zoom 5, para no descargar el DEM de todo el planeta en la vista de globo (causa probable de los 429 y del aviso genérico «Error del mapa»). Si persiste, desactiva «Sombreado de relieve», evita recargar muchas veces en desarrollo y revisa los límites de tu token en el panel de Mapbox.

## Limitaciones conocidas
- Los rásteres locales se estiran entre las esquinas del `extent`: sirven para zonas pequeñas, y el pipeline debe entregarlos alineados (lon/lat).
- El swipe divide la zona (oeste–este), no la pantalla, y requiere `image` + `gray8`.
- La app no calcula coherencia, InSAR ni riesgos: solo muestra lo que traiga cada dataset.
