"""Configuration, environment settings, and glacier AOI definitions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class BoundingBox(BaseModel):
    """Geographic WGS84 bounding box [min_lon, min_lat, max_lon, max_lat]."""

    min_lon: float = Field(..., ge=-180.0, le=180.0)
    min_lat: float = Field(..., ge=-90.0, le=90.0)
    max_lon: float = Field(..., ge=-180.0, le=180.0)
    max_lat: float = Field(..., ge=-90.0, le=90.0)

    @model_validator(mode="after")
    def _validate_order(self) -> BoundingBox:
        if self.min_lon >= self.max_lon:
            raise ValueError(
                f"min_lon ({self.min_lon}) must be less than max_lon ({self.max_lon})."
            )
        if self.min_lat >= self.max_lat:
            raise ValueError(
                f"min_lat ({self.min_lat}) must be less than max_lat ({self.max_lat})."
            )
        return self

    def to_ee_bounds(self) -> list[float]:
        """Return [west, south, east, north] for ee.Geometry.Rectangle."""
        return [self.min_lon, self.min_lat, self.max_lon, self.max_lat]

    def to_ring(self) -> list[list[float]]:
        """Return closed counter-clockwise coordinate ring."""
        return [
            [self.min_lon, self.min_lat],
            [self.max_lon, self.min_lat],
            [self.max_lon, self.max_lat],
            [self.min_lon, self.max_lat],
            [self.min_lon, self.min_lat],
        ]

    def to_wkt(self) -> str:
        """Return polygon WKT string compatible with asf_search.geo_search."""
        coords = ", ".join(f"{lon} {lat}" for lon, lat in self.to_ring())
        return f"POLYGON(({coords}))"


@dataclass(frozen=True)
class AOIPreset:
    """Named Area of Interest preset for glacier monitoring."""

    key: str
    name: str
    country: str
    bbox: BoundingBox


AOI_PRESETS: dict[str, AOIPreset] = {
    "langtang": AOIPreset(
        key="langtang",
        name="Langtang Valley (Langtang Glacier / Lirung Lake)",
        country="Nepal",
        bbox=BoundingBox(min_lon=85.50, min_lat=28.18, max_lon=85.74, max_lat=28.30),
    ),
    "khumbu": AOIPreset(
        key="khumbu",
        name="Khumbu Valley (Khumbu Glacier)",
        country="Nepal",
        bbox=BoundingBox(min_lon=86.78, min_lat=27.90, max_lon=86.88, max_lat=28.01),
    ),
}


def resolve_aoi(aoi_spec: str) -> tuple[str, BoundingBox]:
    """Resolve an AOI preset key or 'min_lon,min_lat,max_lon,max_lat' string."""
    cleaned = aoi_spec.strip()
    preset = AOI_PRESETS.get(cleaned.lower())
    if preset is not None:
        return preset.name, preset.bbox

    parts = [part.strip() for part in cleaned.split(",")]
    if len(parts) != 4:
        available = ", ".join(sorted(AOI_PRESETS))
        raise ValueError(
            f"Invalid AOI '{aoi_spec}'. Use a preset ({available}) "
            "or four coordinates 'min_lon,min_lat,max_lon,max_lat'."
        )

    try:
        min_lon, min_lat, max_lon, max_lat = (float(value) for value in parts)
    except ValueError as exc:
        raise ValueError(
            f"AOI coordinates '{aoi_spec}' must be numeric."
        ) from exc

    return f"Custom AOI ({cleaned})", BoundingBox(
        min_lon=min_lon,
        min_lat=min_lat,
        max_lon=max_lon,
        max_lat=max_lat,
    )


def _empty_to_none(value: Any) -> Any:
    if isinstance(value, str) and not value.strip():
        return None
    return value


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables and .env."""

    model_config = SettingsConfigDict(
        env_file=(PROJECT_ROOT / ".env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    gee_project_id: str | None = None
    gee_service_account: str | None = None
    gee_private_key_path: Path | None = None

    gcs_bucket: str | None = None
    gcs_prefix: str = "asf-sar-ingest"
    gee_asset_collection: str | None = None

    earthdata_token: str | None = None
    earthdata_username: str | None = None
    earthdata_password: str | None = None

    local_download_dir: Path = Path("data/downloads")
    gee_ingestion_timeout_sec: int = Field(default=900, ge=10)

    @field_validator(
        "gee_project_id",
        "gee_service_account",
        "gee_private_key_path",
        "gcs_bucket",
        "gee_asset_collection",
        "earthdata_token",
        "earthdata_username",
        "earthdata_password",
        mode="before",
    )
    @classmethod
    def _normalize_empty(cls, value: Any) -> Any:
        return _empty_to_none(value)

    def resolved_asset_collection(self) -> str | None:
        """Return configured GEE ImageCollection path or a default under GEE_PROJECT_ID."""
        if self.gee_asset_collection:
            return self.gee_asset_collection.strip("/")
        if self.gee_project_id:
            return f"projects/{self.gee_project_id}/assets/glacier_sar_asf"
        return None
