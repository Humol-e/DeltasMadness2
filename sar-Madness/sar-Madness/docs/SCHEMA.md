# Case Schema v2 (`schemaVersion: 2`)

`public/data/catalog.json` lists the published cases: `{ "schemaVersion": 2, "cases": ["data/cases/<id>/case.json"] }`.
Each case is validated when loaded (and by `npm run validate`). Invalid cases are skipped with an error message while valid cases remain active.
JSON files support `//` and `/* */` comments. Empty template: `docs/case.template.json`. Commented example: `docs/case.example.jsonc`.

## Case
| Field | Req. | Description |
|---|---|---|
| `schemaVersion` | yes | Must be `2`. |
| `id`, `title` | yes | `id`: letters, numbers, `_`, `-`. |
| `status` | yes | Must be `"real"` (`simulated` must not be `true`). |
| `view` | yes | `center [lon,lat]`, `zoom`, `pitch`, `bearing`. |
| `datasets[]` | * | Rasters. |
| `vectors[]` | * | GeoJSON overlays. (*) At least one of `datasets` or `vectors` is required. |

## Raster Dataset
| Field | Req. | Description |
|---|---|---|
| `id`, `name` | yes | `id` without `:` or spaces. |
| `status` | yes | `"real"`. |
| `platform`, `sensor`, `provider`, `product`, `attribution` | no | `null` = pending. |
| `variables[]` | yes | `id`, `label`, `quantity`, `polarization`, `unit`, `legend`. Each dataset declares its own variables (e.g., `HH+HV` or `VH` only). |
| `variables[].legend` | gray8: yes | `min`, `max`, `colors[]` (>= 2 interpolated hex colors), optional `nodata_label`. |
| `composites[]` | no | `{ id, label, channels: {r,g,b}, note }`; each channel is a variable `id` or `null`. Only for `image` + `gray8`. |
| `source` | yes | `type`: `image` or `tiles`. `encoding`: `gray8` (default for image) or `rgba` (default for tiles). `nodata` (gray8, default 0). `tileSize`, `minzoom`, `maxzoom` (tiles). |
| `extent` | image: yes | `[west, south, east, north]` in degrees. When given on `tiles`, bounds tile requests. |
| `crs`, `resolution` | no | Informational (`null` = pending). |
| `render` | no | `opacity` (0–1), `resampling` (`linear` / `nearest`). |
| `defaultVisible` | no | `false` to start hidden. |
| `frames[]` | yes | `datetime` (ISO 8601, required), `id` (optional), `files` `{ variableId: path | URL }`. |

### `gray8` Encoding
8-bit grayscale PNG: `0` = nodata (transparent). `1..255` maps linearly onto `legend.min..legend.max`.
This allows the browser viewer to colorize with LUTs, compose RGB, read pixel values under the cursor, and drive the swipe comparator.
PNGs must be aligned to `extent` (`[west, south, east, north]`).

## Vector (GeoJSON)
| Field | Req. | Description |
|---|---|---|
| `id`, `name`, `type` | yes | `type`: `fill`, `line`, `circle`. |
| `source.data` | yes | Path to a GeoJSON file (or an inline GeoJSON object). |
| `time` | no | `{ property, mode }`. `property` holds each feature's ISO date. `asof`: latest date <= active time. `cumulative`: all up to active time. |
| `paint`, `layout` | no | Mapbox GL styling properties. |
| `legend` | no | `{ title, items: [{color,label}] }`. |
| `popupFields` | no | `[[key, label, unit?]]`. Missing values show "Not available". |
| `status`, `simulated` | no | Real data requires `status: "real"` and `simulated !== true`. |
| `indicator` | no | `{ kind, simulated, validation }` for domain indicators. The app renders precomputed indicators rather than fabricating values. |

## Time
The timeline axis is the sorted union of all frame and vector timestamps. At any active timestamp, each dataset displays **its latest frame <= active time** without temporal interpolation.
