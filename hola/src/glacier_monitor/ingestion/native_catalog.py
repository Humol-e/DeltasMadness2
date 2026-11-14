"""Stage 1A: Native Google Earth Engine catalog definitions and freshness checks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Sequence

import ee

from glacier_monitor.config import BoundingBox


@dataclass(frozen=True)
class NativeDatasetSpec:
    """Metadata descriptor for a native Google Earth Engine ImageCollection."""

    key: str
    dataset_id: str
    name: str
    bands: tuple[str, ...]
    default_bands: tuple[str, ...]
    resolution_m: int
    cadence: str


NATIVE_CATALOG: dict[str, NativeDatasetSpec] = {
    "sentinel1_grd": NativeDatasetSpec(
        key="sentinel1_grd",
        dataset_id="COPERNICUS/S1_GRD",
        name="Sentinel-1 SAR GRD (Backscatter en dB)",
        bands=("VV", "VH", "HH", "HV", "angle"),
        default_bands=("VV", "VH"),
        resolution_m=10,
        cadence="6-12 días (actualización automática en GEE)",
    ),
    "modis_snow": NativeDatasetSpec(
        key="modis_snow",
        dataset_id="MODIS/061/MOD10A1",
        name="MODIS Terra Nieve Diaria 500m (NDSI)",
        bands=(
            "NDSI_Snow_Cover",
            "NDSI_Snow_Cover_Basic_QA",
            "NDSI_Snow_Cover_Algorithm_Flags_QA",
            "Orbit_Number",
            "Snow_Albedo_Daily_Tile",
        ),
        default_bands=("NDSI_Snow_Cover", "NDSI_Snow_Cover_Basic_QA"),
        resolution_m=500,
        cadence="Diaria (actualización automática en GEE)",
    ),
    "modis_lst": NativeDatasetSpec(
        key="modis_lst",
        dataset_id="MODIS/061/MOD11A1",
        name="MODIS Terra Temperatura Superficial Diaria 1km (LST)",
        bands=(
            "LST_Day_1km",
            "QC_Day",
            "Day_view_time",
            "Day_view_angle",
            "LST_Night_1km",
            "QC_Night",
            "Night_view_time",
            "Night_view_angle",
            "Emis_31",
            "Emis_32",
            "Clear_day_cov",
            "Clear_night_cov",
        ),
        default_bands=("LST_Day_1km", "LST_Night_1km", "QC_Day"),
        resolution_m=1000,
        cadence="Diaria (actualización automática en GEE)",
    ),
}


@dataclass(frozen=True)
class NativeCollectionStatus:
    """Availability summary for a native GEE collection over an AOI and window."""

    key: str
    dataset_id: str
    name: str
    image_count: int
    earliest_time_utc: str | None
    latest_time_utc: str | None
    available_bands: tuple[str, ...]
    resolution_m: int


def get_dataset_spec(dataset_key: str) -> NativeDatasetSpec:
    """Return the NativeDatasetSpec for a catalog key or raise ValueError."""
    spec = NATIVE_CATALOG.get(dataset_key)
    if spec is None:
        valid = ", ".join(sorted(NATIVE_CATALOG))
        raise ValueError(f"Dataset nativo desconocido '{dataset_key}'. Opciones: {valid}")
    return spec


def _ms_to_iso_utc(timestamp_ms: int | float | None) -> str | None:
    if timestamp_ms is None:
        return None
    dt = datetime.fromtimestamp(float(timestamp_ms) / 1000.0, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def build_native_collection(
    dataset_key: str,
    bbox: BoundingBox,
    start_date: str,
    end_date: str,
    *,
    bands: Sequence[str] | None = None,
    select_default_bands: bool = False,
    s1_instrument_mode: str = "IW",
    s1_orbit_pass: str | None = None,
    s1_polarization: str | None = "VV",
) -> ee.ImageCollection:
    """Build a filtered ee.ImageCollection for a native GEE dataset."""
    spec = get_dataset_spec(dataset_key)
    geometry = ee.Geometry.Rectangle(bbox.to_ee_bounds())

    collection = (
        ee.ImageCollection(spec.dataset_id)
        .filterBounds(geometry)
        .filterDate(start_date, end_date)
    )

    if spec.key == "sentinel1_grd":
        if s1_instrument_mode:
            collection = collection.filter(
                ee.Filter.eq("instrumentMode", s1_instrument_mode)
            )
        if s1_orbit_pass:
            collection = collection.filter(
                ee.Filter.eq("orbitProperties_pass", s1_orbit_pass.upper())
            )
        if s1_polarization:
            collection = collection.filter(
                ee.Filter.listContains(
                    "transmitterReceiverPolarisation", s1_polarization.upper()
                )
            )

    selected_bands = list(bands) if bands else (
        list(spec.default_bands) if select_default_bands else None
    )
    if selected_bands:
        collection = collection.select(selected_bands)

    return collection


def inspect_collection_status(
    dataset_key: str,
    bbox: BoundingBox,
    start_date: str,
    end_date: str,
    *,
    s1_instrument_mode: str = "IW",
    s1_orbit_pass: str | None = None,
    s1_polarization: str | None = "VV",
) -> NativeCollectionStatus:
    """Query Earth Engine for image count, date bounds, and band names."""
    spec = get_dataset_spec(dataset_key)
    collection = build_native_collection(
        dataset_key,
        bbox,
        start_date,
        end_date,
        s1_instrument_mode=s1_instrument_mode,
        s1_orbit_pass=s1_orbit_pass,
        s1_polarization=s1_polarization,
    )

    count = collection.size()
    summary = ee.Dictionary(
        {
            "count": count,
            "earliest": ee.Algorithms.If(
                count.gt(0),
                collection.sort("system:time_start", True).first().get("system:time_start"),
                None,
            ),
            "latest": ee.Algorithms.If(
                count.gt(0),
                collection.sort("system:time_start", False).first().get("system:time_start"),
                None,
            ),
            "bands": ee.Algorithms.If(
                count.gt(0),
                collection.first().bandNames(),
                [],
            ),
        }
    ).getInfo()

    return NativeCollectionStatus(
        key=spec.key,
        dataset_id=spec.dataset_id,
        name=spec.name,
        image_count=int(summary.get("count", 0)),
        earliest_time_utc=_ms_to_iso_utc(summary.get("earliest")),
        latest_time_utc=_ms_to_iso_utc(summary.get("latest")),
        available_bands=tuple(summary.get("bands") or ()),
        resolution_m=spec.resolution_m,
    )


def inspect_all_native_datasets(
    bbox: BoundingBox,
    start_date: str,
    end_date: str,
    *,
    s1_instrument_mode: str = "IW",
    s1_orbit_pass: str | None = None,
    s1_polarization: str | None = "VV",
) -> list[NativeCollectionStatus]:
    """Inspect all registered Stage 1 native GEE datasets over an AOI."""
    return [
        inspect_collection_status(
            dataset_key,
            bbox,
            start_date,
            end_date,
            s1_instrument_mode=s1_instrument_mode,
            s1_orbit_pass=s1_orbit_pass,
            s1_polarization=s1_polarization,
        )
        for dataset_key in NATIVE_CATALOG
    ]
