# Esquema de casos v2 (`schemaVersion: 2`)

`public/data/catalog.json` lista los casos: `{ "schemaVersion": 2, "cases": ["data/cases/<id>/case.json"] }`.
Cada caso se valida al cargar (y con `npm run validate`). Un caso inválido se omite con un mensaje; el resto sigue funcionando.
Todos los JSON admiten comentarios `//` y `/* */`. Plantilla vacía: `docs/case.template.json`. Ejemplo comentado: `docs/case.example.jsonc`.

## Caso
| Campo | Req. | Descripción |
|---|---|---|
| `schemaVersion` | sí | Debe ser `2`. |
| `id`, `title` | sí | `id`: letras, números, `_`, `-`. |
| `status` | no | `demo` (por defecto) o `real`. Nada se muestra como real si no se declara. |
| `view` | sí | `center [lon,lat]`, `zoom`, `pitch`, `bearing`. |
| `datasets[]` | * | Rásteres. |
| `vectors[]` | * | GeoJSON. (*) Al menos uno de los dos. |

## Dataset ráster
| Campo | Req. | Descripción |
|---|---|---|
| `id`, `name` | sí | `id` sin `:` ni espacios. |
| `status` | no | `demo` (por defecto) / `real`. |
| `platform`, `sensor`, `provider`, `product`, `attribution` | no | `null` = pendiente. |
| `variables[]` | sí | `id`, `label`, `quantity`, `polarization`, `unit`, `legend`. Cada dataset declara SUS variables (SAR-1 puede traer HH+HV y SAR-2 solo HV). |
| `variables[].legend` | gray8: sí | `min`, `max`, `colors[]` (≥ 2, interpolados), `nodata_label` opcional. |
| `composites[]` | no | `{ id, label, channels: {r,g,b}, note }`; cada canal es un `id` de variable o `null`. Solo `image` + `gray8`. |
| `source` | sí | `type`: `image` o `tiles`. `encoding`: `gray8` (defecto en image) o `rgba` (defecto en tiles). `nodata` (gray8, por defecto 0). `tileSize`, `minzoom`, `maxzoom` (tiles). |
| `extent` | image: sí | `[oeste, sur, este, norte]` en grados. Si se da en tiles, limita las teselas. |
| `crs`, `resolution` | no | Informativos (`null` = pendiente). |
| `render` | no | `opacity` (0–1), `resampling` (`linear` / `nearest`). |
| `defaultVisible` | no | `false` para empezar apagado. |
| `frames[]` | sí | `datetime` (ISO 8601, obligatorio), `id` (opcional), `files` `{ idVariable: ruta | URL }`. |

### Codificación `gray8` (DEMO)
PNG de 8 bits en escala de grises. `0` = sin dato. `1..255` se mapea linealmente a `legend.min..legend.max`.
Con esto la app puede colorear, componer RGB, mostrar valores por píxel y hacer swipe. Es una convención de la demo: cuando el pipeline defina su formato real, se cambia `source` y `files` (p. ej. a `tiles` o `rgba`) sin tocar el código.
Los PNG deben estar alineados al `extent` (lon/lat). La app los estira entre las esquinas del `extent` (aproximación válida para zonas pequeñas).

## Vector (GeoJSON)
| Campo | Req. | Descripción |
|---|---|---|
| `id`, `name`, `type` | sí | `type`: `fill`, `line`, `circle`. |
| `source.data` | sí | Ruta a un GeoJSON (o un objeto inline). |
| `time` | no | `{ property, mode }`. `property` guarda la fecha ISO de cada feature. `asof`: fecha más reciente ≤ la elegida. `cumulative`: todo hasta esa fecha. |
| `paint`, `layout` | no | Estilo Mapbox GL. |
| `legend` | no | `{ title, items: [{color,label}] }`. |
| `popupFields` | no | `[[clave, etiqueta, unidad?]]`. Si falta un valor se muestra «No disponible». |
| `status`, `simulated` | no | `simulated: true` rotula SIMULADO. |
| `indicator` | no | `{ kind, simulated, validation }` para deshielo, zonas de riesgo, SWE, lluvia u otros. La app **no calcula** indicadores. Sin `simulated` y sin `validation` se avisa «sin validación». |

## Tiempo
El eje temporal es la unión de todas las fechas de frames y vectores. En cada fecha, cada dataset muestra **su último frame ≤ la fecha elegida**, indicando su fecha real y cuánto difiere. Nunca se interpolan datos entre frames.
