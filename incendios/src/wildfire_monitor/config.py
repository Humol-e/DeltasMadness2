"""Configuration, settings, and GeoPulse wildfire AOI presets."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_ROOT = PROJECT_ROOT.parent
DEFAULT_GEOPULSE_DIR = PROJECT_ROOT
DEFAULT_SAR_PLATFORM_DIR = WORKSPACE_ROOT / "sar-Madness" / "sar-Madness"


@dataclass(frozen=True)
class BoundingBox:
    """Geographic bounding box in WGS84 decimal degrees (lon/lat)."""

    min_lon: float
    min_lat: float
    max_lon: float
    max_lat: float

    def __post_init__(self) -> None:
        if self.min_lon >= self.max_lon:
            raise ValueError(
                f"Invalid bounding box: min_lon ({self.min_lon}) >= max_lon ({self.max_lon})"
            )
        if self.min_lat >= self.max_lat:
            raise ValueError(
                f"Invalid bounding box: min_lat ({self.min_lat}) >= max_lat ({self.max_lat})"
            )
        if not (-180.0 <= self.min_lon <= 180.0 and -180.0 <= self.max_lon <= 180.0):
            raise ValueError("Longitude must be within [-180, 180].")
        if not (-90.0 <= self.min_lat <= 90.0 and -90.0 <= self.max_lat <= 90.0):
            raise ValueError("Latitude must be within [-90, 90].")

    def to_ee_bounds(self) -> list[float]:
        """Return [min_lon, min_lat, max_lon, max_lat]."""
        return [self.min_lon, self.min_lat, self.max_lon, self.max_lat]

    def center(self) -> tuple[float, float]:
        """Return geographic center (lon, lat)."""
        return (
            round((self.min_lon + self.max_lon) / 2.0, 6),
            round((self.min_lat + self.max_lat) / 2.0, 6),
        )

    def area_km2(self) -> float:
        """Approximate bounding box surface area in square kilometers."""
        mid_lat = math.radians((self.min_lat + self.max_lat) / 2.0)
        width_km = (self.max_lon - self.min_lon) * 111.320 * math.cos(mid_lat)
        height_km = (self.max_lat - self.min_lat) * 110.574
        return round(abs(width_km * height_km), 3)

    def to_geojson_polygon(self) -> dict[str, Any]:
        """Return GeoJSON Polygon geometry matching GeoPulse AOI format."""
        return {
            "type": "Polygon",
            "coordinates": [
                [
                    [self.min_lon, self.min_lat],
                    [self.max_lon, self.min_lat],
                    [self.max_lon, self.max_lat],
                    [self.min_lon, self.max_lat],
                    [self.min_lon, self.min_lat],
                ]
            ],
        }

    def to_wkt(self) -> str:
        """Return WKT POLYGON string."""
        return (
            f"POLYGON(({self.min_lon} {self.min_lat}, {self.max_lon} {self.min_lat}, "
            f"{self.max_lon} {self.max_lat}, {self.min_lon} {self.max_lat}, "
            f"{self.min_lon} {self.min_lat}))"
        )


@dataclass(frozen=True)
class AOIPreset:
    """Preconfigured wildfire study area matching GeoPulse wildfire examples."""

    key: str
    name: str
    region: str
    description: str
    bbox: BoundingBox
    before: str
    after: str
    sensors: tuple[str, ...] = ("s1", "s2")
    example_dir_name: str | None = None

    @property
    def case_id(self) -> str:
        """Normalized case ID for sar-platform (`wildfire-...`)."""
        return self.key.replace("_", "-")


AOI_PRESETS: dict[str, AOIPreset] = {
    "palisades": AOIPreset(
        key="wildfire_palisades_la",
        name="Palisades Fire — Los Angeles, CA (2025)",
        region="Los Angeles, California, USA",
        description=(
            "Urban-wildland fire in Pacific Palisades and the Santa Monica Mountains (January 2025). "
            "Combines Sentinel-2 L2A (dNBR/NDVI), Sentinel-1 RTC (VH/VV backscatter), and Copernicus 30 m DEM."
        ),
        bbox=BoundingBox(min_lon=-118.62, min_lat=34.03, max_lon=-118.5, max_lat=34.12),
        before="2024-12-01/2025-01-06",
        after="2025-02-01/2025-03-31",
        sensors=("s1", "s2"),
        example_dir_name="wildfire_palisades_la",
    ),
    "camp_paradise": AOIPreset(
        key="wildfire_camp_paradise",
        name="Camp Fire — Paradise, CA (2018)",
        region="Butte County, California, USA",
        description=(
            "Catastrophic Camp Fire in Paradise and Butte Creek (November 2018). "
            "Analyzes Key & Benson burn severity (dNBR) and structural/forest loss from Sentinel-1/2."
        ),
        bbox=BoundingBox(min_lon=-121.78, min_lat=39.68, max_lon=-121.63, max_lat=39.79),
        before="2018-09-15/2018-11-07",
        after="2018-11-26/2019-01-31",
        sensors=("s1", "s2"),
        example_dir_name="wildfire_camp_paradise",
    ),
    "martin_nv": AOIPreset(
        key="wildfire_martin_nv",
        name="Martin Fire — Sagebrush Steppe, NV (2018)",
        region="Elko / Humboldt County, Nevada, USA",
        description=(
            "Large sagebrush and steppe Martin Fire in Nevada (July 2018). "
            "Detects burn scar across semi-arid vegetation using dNBR, pre-fire NDVI, and Sentinel-1 VH."
        ),
        bbox=BoundingBox(min_lon=-116.65, min_lat=41.5, max_lon=-116.5, max_lat=41.63),
        before="2018-05-15/2018-07-04",
        after="2018-07-25/2018-09-15",
        sensors=("s1", "s2"),
        example_dir_name="wildfire_martin_nv",
    ),
    "maui_lahaina": AOIPreset(
        key="wildfire_maui_lahaina",
        name="Lahaina / Kula Fire — Maui, HI (2023)",
        region="Maui, Hawaii, USA",
        description="Fast-moving coastal wildfire driven by strong winds on Maui.",
        bbox=BoundingBox(min_lon=-156.72, min_lat=20.82, max_lon=-156.58, max_lat=20.94),
        before="2023-07-10/2023-08-05",
        after="2023-08-12/2023-09-05",
        sensors=("s1", "s2"),
    ),
    "jasper_ab": AOIPreset(
        key="wildfire_jasper_ab",
        name="Jasper National Park Wildfire — Alberta (2024)",
        region="Canadian Rockies, Canada",
        description="Boreal wildfire in subalpine conifers with high dNBR burn severity.",
        bbox=BoundingBox(min_lon=-118.22, min_lat=52.76, max_lon=-117.92, max_lat=52.96),
        before="2024-06-20/2024-07-18",
        after="2024-08-08/2024-09-05",
        sensors=("s1", "s2"),
    ),
    "valparaiso_cl": AOIPreset(
        key="wildfire_valparaiso_cl",
        name="Viña del Mar / Valparaíso (2024)",
        region="Central Zone, Chile",
        description="Megafire across forest plantations, ravines, and the urban-wildland interface.",
        bbox=BoundingBox(min_lon=-71.56, min_lat=-33.12, max_lon=-71.36, max_lat=-32.98),
        before="2024-01-05/2024-01-31",
        after="2024-02-06/2024-03-01",
        sensors=("s1", "s2"),
    ),
    "evros_gr": AOIPreset(
        key="wildfire_evros_gr",
        name="Dadia / Alexandroupolis — Evros (2023)",
        region="Eastern Thrace, Greece",
        description="Large Mediterranean forest wildfire assessed via optical and SAR fusion.",
        bbox=BoundingBox(min_lon=25.75, min_lat=40.82, max_lon=26.35, max_lat=41.20),
        before="2023-07-15/2023-08-15",
        after="2023-09-02/2023-09-25",
        sensors=("s1", "s2"),
    ),
    "tenerife_es": AOIPreset(
        key="wildfire_tenerife_es",
        name="Corona Forestal, Tenerife (2023)",
        region="Canary Islands, Spain",
        description="Canary Island pine forest on steep volcanic slopes.",
        bbox=BoundingBox(min_lon=-16.58, min_lat=28.26, max_lon=-16.34, max_lat=28.44),
        before="2023-07-15/2023-08-12",
        after="2023-08-26/2023-09-20",
        sensors=("s1", "s2"),
    ),
    "tarahumara_mx": AOIPreset(
        key="wildfire_tarahumara_mx",
        name="Sierra Tarahumara / Bocoyna (2024)",
        region="Chihuahua, Mexico",
        description="Temperate pine-oak forest affected by severe drought.",
        bbox=BoundingBox(min_lon=-107.80, min_lat=27.60, max_lon=-107.45, max_lat=27.90),
        before="2024-03-01/2024-04-05",
        after="2024-06-01/2024-06-30",
        sensors=("s1", "s2"),
    ),
    "pantanal_br": AOIPreset(
        key="wildfire_pantanal_br",
        name="Northern Pantanal / Poconé (2024)",
        region="Mato Grosso, Brazil",
        description="Extensive burning in a tropical wetland under persistent smoke.",
        bbox=BoundingBox(min_lon=-56.95, min_lat=-16.85, max_lon=-56.45, max_lat=-16.45),
        before="2024-05-01/2024-06-15",
        after="2024-09-10/2024-10-15",
        sensors=("s1", "s2"),
    ),
    "los_alerces_ar": AOIPreset(
        key="wildfire_los_alerces_ar",
        name="Los Alerces National Park / Esquel (2024)",
        region="Andean Patagonia, Argentina",
        description="Andean Patagonian forest affected by summer wildfires.",
        bbox=BoundingBox(min_lon=-71.75, min_lat=-42.95, max_lon=-71.45, max_lat=-42.72),
        before="2024-01-01/2024-01-23",
        after="2024-02-12/2024-03-10",
        sensors=("s1", "s2"),
    ),
}

_PRESET_ALIASES: dict[str, str] = {
    "palisades": "palisades",
    "wildfire_palisades_la": "palisades",
    "wildfire-palisades-la": "palisades",
    "camp": "camp_paradise",
    "camp_paradise": "camp_paradise",
    "wildfire_camp_paradise": "camp_paradise",
    "wildfire-camp-paradise": "camp_paradise",
    "martin": "martin_nv",
    "martin_nv": "martin_nv",
    "wildfire_martin_nv": "martin_nv",
    "wildfire-martin-nv": "martin_nv",
    "maui_lahaina": "maui_lahaina",
    "maui-lahaina": "maui_lahaina",
    "jasper_ab": "jasper_ab",
    "jasper-ab": "jasper_ab",
    "valparaiso_cl": "valparaiso_cl",
    "valparaiso-cl": "valparaiso_cl",
    "evros_gr": "evros_gr",
    "evros-gr": "evros_gr",
    "tenerife_es": "tenerife_es",
    "tenerife-es": "tenerife_es",
    "tarahumara_mx": "tarahumara_mx",
    "tarahumara-mx": "tarahumara_mx",
    "pantanal_br": "pantanal_br",
    "pantanal-br": "pantanal_br",
    "los_alerces_ar": "los_alerces_ar",
    "los-alerces-ar": "los_alerces_ar",
}


def bbox_from_geojson(geojson: dict[str, Any]) -> BoundingBox:
    """Extract a BoundingBox from a GeoJSON Feature, FeatureCollection, or Geometry."""
    if geojson.get("type") == "FeatureCollection":
        geom = geojson["features"][0]["geometry"]
    elif geojson.get("type") == "Feature":
        geom = geojson["geometry"]
    else:
        geom = geojson

    coords: list[list[float]] = []
    geom_type = geom.get("type")
    raw = geom.get("coordinates", [])
    if geom_type == "Polygon":
        coords = raw[0]
    elif geom_type == "MultiPolygon":
        for poly in raw:
            coords.extend(poly[0])
    else:
        raise ValueError(f"Unsupported GeoJSON geometry type for AOI: {geom_type}")

    lons = [float(pt[0]) for pt in coords]
    lats = [float(pt[1]) for pt in coords]
    return BoundingBox(
        min_lon=min(lons),
        min_lat=min(lats),
        max_lon=max(lons),
        max_lat=max(lats),
    )


def load_geopulse_example(
    example_name: str,
    geopulse_dir: Path | None = None,
) -> AOIPreset:
    """Load an AOIPreset from `<geopulse_dir>/examples/<example_name>` with fallback."""
    base_dir = (geopulse_dir or DEFAULT_GEOPULSE_DIR) / "examples" / example_name
    req_path = base_dir / "request.yaml"
    aoi_path = base_dir / "aoi.geojson"
    if req_path.exists() and aoi_path.exists():
        req = yaml.safe_load(req_path.read_text(encoding="utf-8")) or {}
        aoi_doc = json.loads(aoi_path.read_text(encoding="utf-8"))
        bbox = bbox_from_geojson(aoi_doc)
        canonical_key = _PRESET_ALIASES.get(example_name.lower())
        fallback = AOI_PRESETS[canonical_key] if canonical_key else None
        return AOIPreset(
            key=example_name,
            name=str(req.get("title") or (fallback.name if fallback else example_name)),
            region=fallback.region if fallback else "GeoPulse AOI",
            description=(
                fallback.description
                if fallback
                else f"GeoPulse wildfire case: {req.get('title', example_name)}"
            ),
            bbox=bbox,
            before=str(req["before"]),
            after=str(req["after"]),
            sensors=tuple(req.get("sensors") or ("s1", "s2")),
            example_dir_name=example_name,
        )

    canonical_key = _PRESET_ALIASES.get(example_name.lower())
    if canonical_key:
        return AOI_PRESETS[canonical_key]
    raise FileNotFoundError(f"GeoPulse wildfire example not found: {base_dir}")


def resolve_aoi(aoi_spec: str, *, geopulse_dir: Path | None = None) -> tuple[str, BoundingBox]:
    """Resolve a GeoPulse wildfire preset name or 'min_lon,min_lat,max_lon,max_lat' bbox."""
    key = aoi_spec.strip().lower()
    if key in _PRESET_ALIASES:
        canonical = _PRESET_ALIASES[key]
        preset = AOI_PRESETS[canonical]
        if preset.example_dir_name:
            try:
                preset = load_geopulse_example(preset.example_dir_name, geopulse_dir)
            except FileNotFoundError:
                pass
        return preset.name, preset.bbox

    candidate = Path(aoi_spec)
    if candidate.exists() and candidate.is_file():
        doc = json.loads(candidate.read_text(encoding="utf-8"))
        return f"AOI GeoJSON ({candidate.name})", bbox_from_geojson(doc)

    parts = [part.strip() for part in aoi_spec.split(",")]
    if len(parts) != 4:
        valid = ", ".join(AOI_PRESETS.keys())
        raise ValueError(
            f"Unknown AOI '{aoi_spec}'. Use one of [{valid}], a GeoJSON path, or 'min_lon,min_lat,max_lon,max_lat'."
        )
    try:
        min_lon, min_lat, max_lon, max_lat = (float(p) for p in parts)
    except ValueError as exc:
        raise ValueError(f"Bounding box coordinates must be numeric: '{aoi_spec}'") from exc
    return f"Custom AOI ({aoi_spec})", BoundingBox(
        min_lon=min_lon,
        min_lat=min_lat,
        max_lon=max_lon,
        max_lat=max_lat,
    )


def resolve_event(aoi_spec: str, *, geopulse_dir: Path | None = None) -> AOIPreset | None:
    """Return full AOIPreset if `aoi_spec` names a known GeoPulse preset, else None."""
    key = aoi_spec.strip().lower()
    if key not in _PRESET_ALIASES:
        return None
    preset = AOI_PRESETS[_PRESET_ALIASES[key]]
    if preset.example_dir_name:
        try:
            return load_geopulse_example(preset.example_dir_name, geopulse_dir)
        except FileNotFoundError:
            return preset
    return preset


class Settings(BaseSettings):
    """Environment-backed runtime settings for wildfire acquisition and export."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    stac_api_url: str = "https://planetarycomputer.microsoft.com/api/stac/v1"
    titiler_api_url: str = "https://planetarycomputer.microsoft.com/api/data/v1"
    sas_token_url: str = "https://planetarycomputer.microsoft.com/api/sas/v1/token"
    geopulse_dir: Path = DEFAULT_GEOPULSE_DIR
    local_download_dir: Path = Path("data/downloads")
    local_cases_dir: Path = Path("data/cases")
    sar_platform_cases_dir: Path = DEFAULT_SAR_PLATFORM_DIR / "public" / "data" / "cases"
    sar_platform_downloads_dir: Path = DEFAULT_SAR_PLATFORM_DIR / "public" / "data" / "downloads"
    max_cloud_cover: int = 60
    grid_max_dim: int = 512
    min_zoom: int = 10
    max_zoom: int = 14
    http_timeout_seconds: float = 30.0

    @field_validator(
        "geopulse_dir",
        "local_download_dir",
        "local_cases_dir",
        "sar_platform_cases_dir",
        "sar_platform_downloads_dir",
        mode="before",
    )
    @classmethod
    def _coerce_path(cls, value: Any) -> Path | None:
        if isinstance(value, str):
            cleaned = value.strip()
            return Path(cleaned) if cleaned else None
        return value

    def _resolve_relative(self, path: Path) -> Path:
        if path.is_absolute():
            return path
        return PROJECT_ROOT / path

    def resolved_geopulse_dir(self) -> Path:
        return self._resolve_relative(self.geopulse_dir)

    def resolved_download_dir(self) -> Path:
        return self._resolve_relative(self.local_download_dir)

    def resolved_cases_dir(self) -> Path:
        return self._resolve_relative(self.local_cases_dir)

    def resolved_sar_platform_cases_dir(self) -> Path:
        return self._resolve_relative(self.sar_platform_cases_dir)

    def resolved_sar_platform_downloads_dir(self) -> Path:
        return self._resolve_relative(self.sar_platform_downloads_dir)
