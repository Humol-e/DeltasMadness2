"""Planetary Computer native STAC catalog & GeoPulse wildfire Scout feeds."""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal, Sequence

from wildfire_monitor.config import BoundingBox, Settings
from wildfire_monitor.pc_auth import PlanetaryComputerClient, initialize_planetary_computer

SensorKey = Literal["sentinel2_l2a", "sentinel1_rtc", "cop_dem"]

S2_BANDS: tuple[str, ...] = ("B02", "B03", "B04", "B08", "B11", "B12")
S1_BANDS: tuple[str, ...] = ("vv", "vh")
MAX_SCENES: int = 4
MAX_S2_CLOUD: int = 60

EONET_URL = "https://eonet.gsfc.nasa.gov/api/v3/events?category=wildfires&status=open&limit=30"
GDACS_RSS_URL = "https://www.gdacs.org/xml/rss.xml"
CEMS_RSS_URL = "https://mapping.emergency.copernicus.eu/activations/rss/"


@dataclass(frozen=True)
class NativeDatasetSpec:
    """Metadata describing a Planetary Computer native STAC collection used by GeoPulse."""

    key: SensorKey
    collection_id: str
    sensor_family: str
    default_bands: tuple[str, ...]
    spatial_resolution_m: int
    description: str


NATIVE_CATALOG: dict[SensorKey, NativeDatasetSpec] = {
    "sentinel2_l2a": NativeDatasetSpec(
        key="sentinel2_l2a",
        collection_id="sentinel-2-l2a",
        sensor_family="Sentinel-2 L2A (Surface reflectance)",
        default_bands=S2_BANDS + ("SCL",),
        spatial_resolution_m=10,
        description=(
            "10-20 m multispectral optical (B02/B03/B04/B08/B11/B12 + SCL cloud mask) "
            "for dNBR, NDVI, MNDWI, and Key & Benson burn severity."
        ),
    ),
    "sentinel1_rtc": NativeDatasetSpec(
        key="sentinel1_rtc",
        collection_id="sentinel-1-rtc",
        sensor_family="Sentinel-1 SAR RTC (Linear Sigma/Gamma0)",
        default_bands=S1_BANDS,
        spatial_resolution_m=10,
        description=(
            "Terrain-corrected C-band synthetic aperture radar (VV/VH), "
            "operational through dense smoke and cloud cover via orbit matching."
        ),
    ),
    "cop_dem": NativeDatasetSpec(
        key="cop_dem",
        collection_id="cop-dem-glo-30",
        sensor_family="Copernicus DEM GLO-30",
        default_bands=("data",),
        spatial_resolution_m=30,
        description="Global 30 m digital elevation model and topographic slope.",
    ),
}


@dataclass(frozen=True)
class STACSceneSummary:
    """Normalized metadata summary for a Planetary Computer STAC item."""

    item_id: str
    collection: str
    datetime_utc: str
    cloud_cover: float | None
    relative_orbit: int | None
    orbit_state: str | None
    platform: str | None
    processing_baseline: float
    assets: tuple[str, ...] = ()
    geometry: dict[str, Any] | None = None

    def to_geopulse_record(self, valid_fraction: float | None = None) -> dict[str, Any]:
        """Serialize into the exact scene record format used in GeoPulse provenance.json."""
        record: dict[str, Any] = {
            "id": self.item_id,
            "collection": self.collection,
            "datetime": self.datetime_utc,
            "cloud_cover": self.cloud_cover,
            "orbit": self.orbit_state,
            "relative_orbit": self.relative_orbit,
            "platform": self.platform,
        }
        if valid_fraction is not None:
            record["valid_fraction"] = round(valid_fraction, 4)
        return record


@dataclass(frozen=True)
class NativeCollectionStatus:
    """Query status summary for a native STAC collection over an AOI/window."""

    key: str
    collection_id: str
    start_date: str
    end_date: str
    scene_count: int
    earliest_time_utc: str | None
    latest_time_utc: str | None
    default_bands: tuple[str, ...]
    spatial_resolution_m: int


@dataclass(frozen=True)
class ScoutWildfireAlert:
    """Normalized active wildfire alert from GeoPulse Scout feeds."""

    alert_id: str
    source: str
    title: str
    date_utc: str
    lon: float
    lat: float
    bbox: BoundingBox
    url: str | None = None
    severity_note: str | None = None


def resolve_native_spec(key: str) -> NativeDatasetSpec:
    """Resolve collection key or alias into a NativeDatasetSpec."""
    normalized = key.strip().lower()
    aliases = {
        "s2": "sentinel2_l2a",
        "sentinel2": "sentinel2_l2a",
        "sentinel-2": "sentinel2_l2a",
        "sentinel-2-l2a": "sentinel2_l2a",
        "sentinel2_l2a": "sentinel2_l2a",
        "s1": "sentinel1_rtc",
        "sentinel1": "sentinel1_rtc",
        "sentinel-1": "sentinel1_rtc",
        "sentinel-1-rtc": "sentinel1_rtc",
        "sentinel1_rtc": "sentinel1_rtc",
        "dem": "cop_dem",
        "cop-dem": "cop_dem",
        "cop-dem-glo-30": "cop_dem",
        "cop_dem": "cop_dem",
    }
    if normalized not in aliases:
        valid = ", ".join(NATIVE_CATALOG.keys())
        raise KeyError(f"Unknown native STAC dataset '{key}'. Choose one of: {valid}")
    return NATIVE_CATALOG[aliases[normalized]]


def _format_stac_datetime(date_range: str | None) -> str | None:
    if not date_range:
        return None
    parts = [p.strip() for p in date_range.split("/") if p.strip()]
    if len(parts) == 1:
        start = end = parts[0]
    elif len(parts) == 2:
        start, end = parts
    else:
        raise ValueError(f"Invalid date window '{date_range}'. Expected YYYY-MM-DD/YYYY-MM-DD.")
    if "T" not in start:
        start = f"{start}T00:00:00Z"
    if "T" not in end:
        end = f"{end}T23:59:59Z"
    return f"{start}/{end}"


def _parse_stac_feature(feature: dict[str, Any]) -> STACSceneSummary:
    props = feature.get("properties") or {}
    rel_orbit = props.get("sat:relative_orbit")
    pb_raw = props.get("s2:processing_baseline", "0")
    try:
        pb = float(pb_raw) if pb_raw is not None else 0.0
    except (TypeError, ValueError):
        pb = 0.0
    cloud = props.get("eo:cloud_cover")
    return STACSceneSummary(
        item_id=str(feature["id"]),
        collection=str(feature.get("collection") or ""),
        datetime_utc=str(props.get("datetime") or ""),
        cloud_cover=float(cloud) if cloud is not None else None,
        relative_orbit=int(rel_orbit) if rel_orbit is not None else None,
        orbit_state=props.get("sat:orbit_state"),
        platform=props.get("platform"),
        processing_baseline=pb,
        assets=tuple((feature.get("assets") or {}).keys()),
        geometry=feature.get("geometry"),
    )


def search_stac_scenes(
    collection_key: str,
    bbox: BoundingBox,
    date_window: str | None = None,
    *,
    max_cloud_cover: int | None = MAX_S2_CLOUD,
    limit: int = 100,
    client: PlanetaryComputerClient | None = None,
    settings: Settings | None = None,
) -> list[STACSceneSummary]:
    """Query Planetary Computer STAC for scenes intersecting `bbox` sorted chronologically."""
    spec = resolve_native_spec(collection_key)
    resolved_settings = settings or Settings()
    pc_client = client or initialize_planetary_computer(resolved_settings)

    body: dict[str, Any] = {
        "collections": [spec.collection_id],
        "intersects": bbox.to_geojson_polygon(),
        "limit": limit,
    }
    if spec.collection_id != "cop-dem-glo-30" and date_window:
        body["datetime"] = _format_stac_datetime(date_window)
    if spec.collection_id == "sentinel-2-l2a" and max_cloud_cover is not None:
        body["filter"] = {
            "op": "<",
            "args": [{"property": "eo:cloud_cover"}, int(max_cloud_cover)],
        }
        body["filter-lang"] = "cql2-json"

    payload = pc_client.request_json(
        f"{pc_client.config.stac_api_url}/search",
        payload=body,
    )
    features = payload.get("features") or []
    scenes = [_parse_stac_feature(feat) for feat in features]
    scenes.sort(key=lambda s: s.datetime_utc)
    return scenes


def select_scenes(
    items: Sequence[STACSceneSummary],
    period: Literal["pre", "post"],
    how: str = "closest",
    max_scenes: int = MAX_SCENES,
) -> list[STACSceneSummary]:
    """Select scenes matching `geopulse.data.select_scenes` temporal ordering."""
    ordered = list(items)
    if len(ordered) <= max_scenes:
        return ordered
    if how == "spread":
        indices = [round(i * (len(ordered) - 1) / (max_scenes - 1)) for i in range(max_scenes)]
        unique_indices = list(dict.fromkeys(indices))
        return [ordered[i] for i in unique_indices]
    return ordered[-max_scenes:] if period == "pre" else ordered[:max_scenes]


def match_orbits(
    pre_items: Sequence[STACSceneSummary],
    post_items: Sequence[STACSceneSummary],
    how: str = "closest",
    max_scenes: int = MAX_SCENES,
) -> tuple[list[STACSceneSummary], list[STACSceneSummary], list[int] | None]:
    """Restrict S1 pre scenes to match post relative orbit(s), identical to GeoPulse."""
    chosen_post = select_scenes(post_items, "post", how=how, max_scenes=max_scenes)
    post_orbits = {s.relative_orbit for s in chosen_post if s.relative_orbit is not None}
    if not post_orbits:
        return select_scenes(pre_items, "pre", how=how, max_scenes=max_scenes), chosen_post, None

    same_orbit_pre = [s for s in pre_items if s.relative_orbit in post_orbits]
    restricted: list[int] | None = None
    if same_orbit_pre and len(same_orbit_pre) < len(pre_items):
        candidate_pre = same_orbit_pre
        restricted = sorted(post_orbits)
    else:
        candidate_pre = list(pre_items)
    return (
        select_scenes(candidate_pre, "pre", how=how, max_scenes=max_scenes),
        chosen_post,
        restricted,
    )


def inspect_collection_status(
    dataset_key: str,
    bbox: BoundingBox,
    start_date: str,
    end_date: str,
    *,
    max_cloud_cover: int | None = MAX_S2_CLOUD,
    client: PlanetaryComputerClient | None = None,
    settings: Settings | None = None,
) -> NativeCollectionStatus:
    """Inspect available STAC scene count and time span for a dataset and window."""
    spec = resolve_native_spec(dataset_key)
    window = f"{start_date}/{end_date}"
    scenes = search_stac_scenes(
        spec.key,
        bbox,
        window,
        max_cloud_cover=max_cloud_cover,
        client=client,
        settings=settings,
    )
    earliest = scenes[0].datetime_utc if scenes else None
    latest = scenes[-1].datetime_utc if scenes else None
    return NativeCollectionStatus(
        key=spec.key,
        collection_id=spec.collection_id,
        start_date=start_date,
        end_date=end_date,
        scene_count=len(scenes),
        earliest_time_utc=earliest,
        latest_time_utc=latest,
        default_bands=spec.default_bands,
        spatial_resolution_m=spec.spatial_resolution_m,
    )


def inspect_all_native_datasets(
    bbox: BoundingBox,
    start_date: str,
    end_date: str,
    *,
    max_cloud_cover: int | None = MAX_S2_CLOUD,
    client: PlanetaryComputerClient | None = None,
    settings: Settings | None = None,
) -> list[NativeCollectionStatus]:
    """Inspect all Planetary Computer wildfire collections over an AOI/window."""
    return [
        inspect_collection_status(
            key,
            bbox,
            start_date,
            end_date,
            max_cloud_cover=max_cloud_cover,
            client=client,
            settings=settings,
        )
        for key in NATIVE_CATALOG
    ]


def _padded_bbox(lon: float, lat: float, half_deg: float = 0.08) -> BoundingBox:
    return BoundingBox(
        min_lon=max(-179.9, round(lon - half_deg, 5)),
        min_lat=max(-85.0, round(lat - half_deg, 5)),
        max_lon=min(179.9, round(lon + half_deg, 5)),
        max_lat=min(85.0, round(lat + half_deg, 5)),
    )


def _parse_eonet_wildfires(raw_json: str, max_events: int) -> list[ScoutWildfireAlert]:
    payload = json.loads(raw_json)
    alerts: list[ScoutWildfireAlert] = []
    for event in payload.get("events") or []:
        geoms = event.get("geometry") or []
        if not geoms:
            continue
        latest = geoms[-1]
        if latest.get("type") != "Point":
            continue
        coords = latest.get("coordinates") or []
        if len(coords) < 2:
            continue
        lon, lat = float(coords[0]), float(coords[1])
        alerts.append(
            ScoutWildfireAlert(
                alert_id=str(event.get("id") or f"eonet-{len(alerts)}"),
                source="EONET",
                title=str(event.get("title") or "EONET Wildfire"),
                date_utc=str(latest.get("date") or datetime.now(timezone.utc).isoformat()),
                lon=lon,
                lat=lat,
                bbox=_padded_bbox(lon, lat),
                url=event.get("link"),
                severity_note="Open wildfire event",
            )
        )
        if len(alerts) >= max_events:
            break
    return alerts


def _parse_rss_wildfires(raw_xml: str, source_name: str, max_events: int) -> list[ScoutWildfireAlert]:
    root = ET.fromstring(raw_xml)
    alerts: list[ScoutWildfireAlert] = []
    geo_ns = "{http://www.w3.org/2003/01/geo/wgs84_pos#}"
    georss_ns = "{http://www.georss.org/georss}"
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        desc = (item.findtext("description") or "").strip()
        haystack = f"{title} {desc}".lower()
        if not re.search(r"\b(wildfire|forest fire|fire|incendio|wf)\b", haystack):
            continue

        lat_text = item.findtext(f"{geo_ns}lat")
        lon_text = item.findtext(f"{geo_ns}long")
        if lat_text is None or lon_text is None:
            point_text = item.findtext(f"{georss_ns}point")
            if point_text:
                parts = point_text.strip().split()
                if len(parts) >= 2:
                    lat_text, lon_text = parts[0], parts[1]
        if lat_text is None or lon_text is None:
            continue

        lon, lat = float(lon_text), float(lat_text)
        alerts.append(
            ScoutWildfireAlert(
                alert_id=(item.findtext("guid") or title or f"{source_name}-{len(alerts)}").strip(),
                source=source_name,
                title=title or f"{source_name} Wildfire",
                date_utc=(item.findtext("pubDate") or "").strip(),
                lon=lon,
                lat=lat,
                bbox=_padded_bbox(lon, lat),
                url=(item.findtext("link") or "").strip() or None,
                severity_note=desc[:140] if desc else None,
            )
        )
        if len(alerts) >= max_events:
            break
    return alerts


def fetch_wildfire_scout_alerts(
    source: Literal["eonet", "gdacs", "cems", "all"] = "eonet",
    *,
    max_events: int = 10,
    client: PlanetaryComputerClient | None = None,
    settings: Settings | None = None,
) -> list[ScoutWildfireAlert]:
    """Fetch active wildfire events from GeoPulse Scout public feeds (EONET/GDACS/CEMS)."""
    pc_client = client or initialize_planetary_computer(settings or Settings())
    results: list[ScoutWildfireAlert] = []
    sources = ("eonet", "gdacs", "cems") if source == "all" else (source,)
    for src in sources:
        if src == "eonet":
            raw = pc_client.request_bytes(EONET_URL).decode("utf-8")
            results.extend(_parse_eonet_wildfires(raw, max_events))
        elif src == "gdacs":
            raw = pc_client.request_bytes(GDACS_RSS_URL).decode("utf-8", errors="replace")
            results.extend(_parse_rss_wildfires(raw, "GDACS", max_events))
        elif src == "cems":
            raw = pc_client.request_bytes(CEMS_RSS_URL).decode("utf-8", errors="replace")
            results.extend(_parse_rss_wildfires(raw, "CEMS", max_events))
        else:
            raise ValueError(f"Unknown Scout wildfire source '{src}'.")
    return results[:max_events]
