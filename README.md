# 🛰️ DeltasMadness2

**Arquitectura de Ingesta Geoespacial Todo-Clima, Fusión Multisensor e Integración SAR/NISAR para Hidrología de Alta Montaña y Criósfera.**

[![Python](https://img.shields.io/badge/Python-100%25-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![NASA Space Apps](https://img.shields.io/badge/NASA_Space_Apps-Dancing_with_the_SARs-0B3D91?logo=nasa&logoColor=white)](https://www.spaceappschallenge.org/)
[![SAR Ready](https://img.shields.io/badge/SAR-Sentinel--1%20%7C%20RADARSAT--1%20%7C%20NISAR-success)](#4-integración-con-la-misión-nisar-y-teledetección-sar)
[![ETL](https://img.shields.io/badge/Pipeline-In--Memory_Streamlined_ETL-orange)](#2-arquitectura-del-pipeline-in-memory-streamlined-etl)

---

## 📋 Tabla de Contenidos

1. [Visión General y Desafío Científico](#1-visión-general-y-desafío-científico)
2. [Arquitectura del Pipeline (In-Memory Streamlined ETL)](#2-arquitectura-del-pipeline-in-memory-streamlined-etl)
3. [Formulación Matemática y Física](#3-formulación-matemática-y-física)
4. [Integración con la Misión NISAR y Teledetección SAR](#4-integración-con-la-misión-nisar-y-teledetección-sar)
5. [Estructura del Repositorio y Ecosistema](#5-estructura-del-repositorio-y-ecosistema)
6. [Instalación y Uso Rápido](#6-instalación-y-uso-rápido)
7. [Equipo y Contribuyentes](#7-equipo-y-contribuyentes)

---

## 1. Visión General y Desafío Científico

**DeltasMadness2** es una plataforma de ingesta y fusión de datos geoespaciales desarrollada en el marco del desafío internacional **NASA Space Apps Challenge (*"Dancing with the SARs"*)**. Su objetivo es resolver la problemática del monitoreo ambiental continuo en cuencas hidrológicas complejas y regiones de alta montaña mediante **Fusión de Datos Multisensor (*Multi-Sensor Data Fusion*)**.

Mientras que los sensores ópticos convencionales (como **VIIRS** o **MODIS**) quedan inoperativos durante la noche o bajo cobertura nubosa persistente, los sensores de Radar de Apertura Sintética (**SAR**) como **Sentinel-1**, **RADARSAT-1** y **NISAR** penetran las nubes y operan las $24$ horas. Sin embargo, interpretar las variaciones en la retrodispersión radar requiere contexto meteorológico en superficie para distinguir entre derretimiento nival, precipitación líquida o humedad antecedente del suelo.

### Tareas Operativas Críticas

1. **Cálculo de Deshielo y Equivalente de Agua en Nieve (SWE):** Cruza la cobertura nival de MODIS (`ndsi_coverage_pct`) con el espesor del manto de nieve de NASA POWER (`SNODP`) y las temperaturas diarias (`T2M`, `T2M_MAX`, `T2M_MIN`).
2. **Monitoreo Todo-Clima bajo Nubosidad:** Cuantifica el bloqueo por nubes en imágenes ópticas (`cloud_bright_pct` en VIIRS) y conmuta automáticamente a escenas SAR (vía CMR, ASF o EODMS) cuando el cielo está cubierto.
3. **Detección de Nieve Húmeda, Inundaciones y Avalanchas:** Evalúa la caída drástica en la retrodispersión radar causada por la constante dieléctrica del agua líquida, combinando la polarización del radar (`polarization`) con precipitación corregida (`PRECTOTCORR`) y humedad superficial del suelo (`GWETTOP`).
4. **Extrapolación Térmica por Altitud:** Utiliza la elevación de referencia de la celda climática (`ref_cell_elev_m`) y el gradiente térmico vertical (`PROV_POWER_LAPSE`) para ubicar dinámicamente la altitud de la isoterma de $0^\circ\text{C}$.

---

## 2. Arquitectura del Pipeline (In-Memory Streamlined ETL)

El sistema implementa un patrón **ETL al vuelo en memoria** que evita el almacenamiento intermedio en disco. Las respuestas JSON e imágenes raster son extraídas, depuradas y transformadas directamente en memoria mediante bibliotecas livianas (`requests`, `numpy`, `Pillow`).

```text
+-------------------------------------------------------------------------+
|                        FUENTES CIENTÍFICAS (APIs)                       |
|  [NASA CMR]   [ASF Search]   [NASA POWER]   [NASA GIBS]   [CSA EODMS]   |
+------------------------------------+------------------------------------+
                                     |
                                     v
+-------------------------------------------------------------------------+
|                    EXTRACCIÓN HTTP (requests.Session)                   |
+------------------------------------+------------------------------------+
                                     |
                                     v
+-------------------------------------------------------------------------+
|                    TRANSFORMACIÓN Y LIMPIEZA AL VUELO                   |
|  • Filtro estricto de URLs HTTPS y casting numérico a float             |
|  • Aplanado de arreglos anidados y extracción de 8 campos orbitales     |
|  • Imputación de valores centinela (-999.0) y validación valid_core     |
|  • Inversión de ejes BBOX para WMS 1.3.0 (s, w, n, e)                   |
|  • Control radiométrico de franjas negras y opacidad alfa en rasters    |
|  • Lectura parcial por ventana espacial de Cloud-Optimized GeoTIFFs     |
+------------------------------------+------------------------------------+
                                     |
                                     v
+-------------------------------------------------------------------------+
|               CARGA EN MEMORIA (Diccionarios Planos / Base64)           |
+-------------------------------------------------------------------------+
```

### Catálogo de Endpoints y Reglas de Procesamiento

| Constante (`.config`) | Función | Endpoint Oficial | Limpieza y Estructuración en Memoria |
| :--- | :--- | :--- | :--- |
| `CMR_GRANULES_URL` | `query_cmr_granules` | `https://cmr.earthdata.nasa.gov/search/granules.json` | Filtra enlaces `https://` con `"data#"`, excluyendo directorios (`/`) y páginas `"search."`. Convierte `granule_size_mb` a `float` y calcula `swath_bounds`. |
| `ASF_SEARCH_URL` | `query_asf_orbit_metadata` | `https://api.daac.asf.alaska.edu/services/search/param` | Aplana la lista anidada `data[0]` con manejo seguro de excepciones HTTP y extrae 8 atributos orbitales clave (`granuleName`, `platform`, `flightDirection`, `beamMode`, `pathNumber`, `frameNumber`, `polarization`, `startTime`). |
| `NASA_POWER_URL` | `fetch_nasa_power_daily` | `https://power.larc.nasa.gov/api/temporal/daily/point` | Redondea coordenadas a $4$ decimales, asigna altitud por defecto ($2000.0\text{ m}$), filtra centinelas $\le -900.0$ (invalidando el día si faltan `T2M_MAX`/`T2M_MIN`) y transpone a fechas ISO-8601 (`YYYY-MM-DD`). |
| `NASA_GIBS_WMS_URL` | `fetch_gibs_viirs_and_ndsi` | `https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi` | Invierte ejes a `(s, w, n, e)` para WMS 1.3.0, aplica *fallback* temporal de días y sensores (`VIIRS_SNPP` / `VIIRS_NOAA20`), valida brillo/opacidad y codifica recortes en Base64. |
| `EODMS_RAPI_SEARCH_URL` | `query_radarsat1_open_archive` | `https://www.eodms-sgdot.nrcan-rncan.gc.ca/wes/rapi/search` | Filtra archivos históricos `.tif` en AWS aplicando filtro estacional invernal (meses $12, 1, 2, 3, 4$) y lee exclusivamente la ventana `bbox` mediante `read_radarsat1_cog_window`. |

---

## 3. Formulación Matemática y Física

### 3.1 Transformaciones Geométricas Espaciales

Partiendo de un cuadro delimitador $\text{bbox} = (w, s, e, n)$ en coordenadas geográficas WGS84 (`EPSG:4326`), donde $w, e \in [-180.0^\circ, +180.0^\circ]$ y $s, n \in [-90.0^\circ, +90.0^\circ]$:

$$\text{BBOX}_{\text{CMR}} = (w, s, e, n)$$

$$\text{BBOX}_{\text{WMS 1.3.0}} = (s, w, n, e)$$

$$P_{\text{WKT}} = \text{POLYGON}((w\ s, e\ s, e\ n, w\ n, w\ s))$$

La representación $P_{\text{WKT}}$ garantiza el cierre topológico del polígono en sentido antihorario para las consultas espaciales en las APIs de ASF y EODMS.

### 3.2 Saneamiento Climático y Gradiente Térmico Vertical

Para cada parámetro meteorológico $p$ y fecha $d$ en NASA POWER MERRA-2 con valor crudo $v_{p,d}$:

$$\text{row}_p = \begin{cases} 0.0 & \text{si } v_{p,d} = \text{None} \lor v_{p,d} \le -900.0 \\ \text{float}(v_{p,d}) & \text{en otro caso} \end{cases}$$

$$\text{valid\_core}_d = (v_{\text{T2M\_MAX}, d} > -900.0) \land (v_{\text{T2M\_MIN}, d} > -900.0)$$

La variación de la temperatura del aire $T(z)$ respecto a la altitud $z$ y la elevación de la celda de referencia $z_{\text{ref}}$ (`ref_cell_elev_m`) se modela mediante el gradiente ambiental vertical ($\Gamma \approx 0.0065\text{ }^\circ\text{C/m}$):

$$T(z) = T_{\text{ref}} - \Gamma \cdot (z - z_{\text{ref}})$$

Esto permite despejar la altitud exacta de la isoterma de congelación ($T(z) = 0^\circ\text{C}$) sobre cuencas de montaña.

### 3.3 Estadística Radiométrica y Cobertura de Nubes (VIIRS)

Sobre una imagen RGB de dimensiones $H \times W \times 3$ con canales $R, G, B \in [0, 255]$, se descartan franjas orbitales vacías verificando que el tamaño supere $1800\text{ bytes}$ y evaluando la media $\mu$ y desviación estándar $\sigma$:

$$\mu = \frac{1}{3HW} \sum_{i=1}^{H} \sum_{j=1}^{W} \sum_{c \in \{R,G,B\}} \text{arr}_{i,j,c} > 12.0 \quad \land \quad \sigma = \sqrt{\frac{1}{3HW} \sum_{i=1}^{H} \sum_{j=1}^{W} \sum_{c \in \{R,G,B\}} (\text{arr}_{i,j,c} - \mu)^2} > 5.0$$

El porcentaje de nubosidad brillante (`cloud_bright_pct`) aísla píxeles altamente reflectantes y acromáticos mediante la función indicadora $\mathbb{I}(\cdot)$:

$$\text{cloud\_bright\_pct} = \text{round}\left(\frac{1}{HW} \sum_{i=1}^{H} \sum_{j=1}^{W} \mathbb{I}\left(\min(R_{i,j}, G_{i,j}, B_{i,j}) > 195\right) \times 100.0, 1\right)$$

### 3.4 Cobertura de Nieve NDSI (MODIS Terra)

Físicamente, el Índice Diferencial Normalizado de Nieve ($\text{NDSI}$) aprovecha la alta reflectancia de la nieve en el espectro visible verde ($0.55\ \mu\text{m}$) y su fuerte absorción en el infrarrojo de onda corta SWIR ($1.64\ \mu\text{m}$):

$$\text{NDSI}_{\text{fisico}} = \frac{\text{Reflectancia}_{\text{Verde}}(0.55\ \mu\text{m}) - \text{Reflectancia}_{\text{SWIR}}(1.64\ \mu\text{m})}{\text{Reflectancia}_{\text{Verde}}(0.55\ \mu\text{m}) + \text{Reflectancia}_{\text{SWIR}}(1.64\ \mu\text{m})}$$

A partir de la capa RGBA ($H \times W \times 4$) servida por NASA GIBS WMS, el módulo estima `ndsi_coverage_pct` evaluando el canal Alfa ($A$) y los canales Azul ($B$) y Verde ($G$):

$$\text{ndsi\_coverage\_pct} = \text{round}\left(\frac{1}{HW} \sum_{i=1}^{H} \sum_{j=1}^{W} \mathbb{I}\left(A_{i,j} > 20 \land (B_{i,j} > 60 \lor G_{i,j} > 80)\right) \times 100.0, 1\right)$$

---

## 4. Integración con la Misión NISAR y Teledetección SAR

### 4.1 Trazabilidad con el NISAR Cookbook

El motor de ingesta está alineado con las especificaciones del *NISAR Cookbook* (Lewandowski & White / Alaska Satellite Facility) y prepara la ingesta nativa de productos de la misión **NASA-ISRO Synthetic Aperture Radar (NISAR)**:

* **`GCOV` (*Geocoded Polarimetric Covariance Matrix*):** Complementado por el pipeline mediante variables de humedad antecedente (`GWETTOP`) y manto nival (`SNODP`).
* **`GSLC` / `RSLC` (*Single Look Complex*):** La función `query_asf_orbit_metadata` extrae explícitamente `pathNumber`, `frameNumber`, `beamMode` y `flightDirection`, parámetros indispensables para el corregistro interferométrico (**InSAR**) y polarimétrico (**polSAR**).
* **Subsetting en la Nube:** Al igual que los flujos de recorte espacial de RSLC, el módulo `.sar_readers` extrae ventanas parciales de archivos *Cloud-Optimized GeoTIFF* (`COG`) sin descargar escenas completas de múltiples gigabytes.

### 4.2 Cronograma Oficial de Disponibilidad de Datos NISAR

Lanzado el **30 de julio de 2025**, NISAR distribuye datos en **Banda L** a través de **ASF DAAC** y datos en **Banda S** mediante el portal **Bhoonidhi** de ISRO:

| Hito / Fecha | Fase de Datos | Descripción Técnica |
| :--- | :--- | :--- |
| **Julio 2025** | Lanzamiento | Puesta en órbita (30 de julio de 2025) e inicio de fase *Commissioning*. |
| **Enero 2026** | Productos Muestra | Liberación de $25$ productos pre-calibración representativos (Niveles 1 a 3). |
| **Febrero 2026** | Datos **BETA** | Más de $100{,}000$ productos ($> 500\text{ TB}$) adquiridos entre oct-2025 y ene-2026. |
| **Julio 2026** | Datos **PROVISIONALES** | Versión calibrada parcialmente (`CRID P05023`), incluyendo Nivel-0B `RRSD`. *(Nota: brecha instrumental entre `2026-07-27T22:03:25Z` y `2026-08-10T00:55:27Z`)*. |
| **Noviembre 2026** | Datos **VALIDADOS** | Productos completamente calibrados y validados Niveles L0–L2 (`CRID P05036`, adquisiciones desde el 21 de octubre de 2026). |
| **2027** | Reprocesamiento & `SME2` | Reprocesamiento global histórico y liberación del producto validado Nivel 3 de Humedad del Suelo (`SME2`). |

### 4.3 Mecanismos de Dispersión Radar y Polarizaciones

* **Dispersión Especular (*Specular Scattering*):** En cuerpos de agua abierta y nieve húmeda superficial, la señal microondas se refleja lejos del sensor, generando tonos oscuros (baja retrodispersión).
* **Doble Rebote (*Double Bounce*):** En bosques o vegetación inundada, el pulso rebota entre la lámina de agua y los troncos verticales, retornando una señal muy intensa (tonos brillantes).
* **Polarización `HH` vs `HV`:** `HH` maximiza la sensibilidad a la dispersión especular y de doble rebote para mapeo de inundaciones; `HV` captura la dispersión volumétrica en el dosel forestal. La **Banda L** ($\lambda \approx 23.8\text{ cm}$, validada en casos como el Huracán Florence con UAVSAR) penetra el follaje denso donde la Banda C o los sensores ópticos quedan bloqueados.

---

## 5. Estructura del Repositorio y Ecosistema

```text
DeltasMadness2/
├── JSDEMO/                     # Demostraciones interactivas de visualización en JavaScript
├── test.py                     # Suite de pruebas y validación del pipeline ETL
├── config.py                   # Constantes de endpoints oficiales (CMR, ASF, POWER, GIBS, EODMS)
├── sar_readers.py              # Lectores especializados SAR y ventanas parciales COG en AWS
└── docs/                       # Documentación técnica de síntesis y referencias NISAR
```

### Ecosistema de Herramientas Complementarias

Para flujos avanzados de procesamiento fuera de línea y despliegue en la nube, **DeltasMadness2** se integra conceptualmente con:

* **[`sar-asf-to-gee`](https://github.com/gee-community/sar-asf-to-gee):** Automatización de transferencia de productos procesados en ASF HyP3 (interferogramas InSAR GAMMA / ISCE Burst) hacia Google Cloud Storage y colecciones de Google Earth Engine.
* **[`pyroSAR`](https://github.com/johntruckenbrodt/pyroSAR):** Organización masiva y preprocesamiento radiométrico/geométrico de escenas SAR con SNAP (ESA) y GAMMA para generación de *Data Cubes*.
* **Tutoriales SAR de la CSA:** Guías de manipulación raster y vectorial con `GDAL` (`osgeo`), `Cartopy` y `Matplotlib` para misiones RADARSAT-2 y RCM3.

---

## 6. Instalación y Uso Rápido

### Requisitos Previos

El núcleo de ingesta está diseñado para ejecutarse con dependencias mínimas de Python ($3.9+$), sin requerir visores pesados como HDFView:

```bash
pip install requests numpy Pillow
```

### Ejecución de Pruebas del Pipeline

1. Clona el repositorio:
   ```bash
   git clone https://github.com/Humol-e/DeltasMadness2.git
   cd DeltasMadness2
   ```
2. Ejecuta el script de verificación y extracción multisensor:
   ```bash
   python test.py
   ```

*(Nota: Para descargar gránulos protegidos directamente desde NASA Earthdata CMR o ASF, configura tus credenciales en un archivo `~/.netrc` válido).*

---

## 7. Equipo y Contribuyentes

Proyecto desarrollado colaborativamente por:

* **Emiliano Castro** — [@Humol-e](https://github.com/Humol-e)
* **Raúl** — [@Peanut-Butter-Arc](https://github.com/Peanut-Butter-Arc)
* **Karla Yelitza** — [@karlayelitza020705-ui](https://github.com/karlayelitza020705-ui)