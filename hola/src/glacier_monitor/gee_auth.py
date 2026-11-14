"""Google Earth Engine and Google Cloud Storage authentication helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import ee
from google.cloud import storage

from glacier_monitor.config import PROJECT_ROOT, Settings


def resolve_service_account_path(key_path: Path) -> Path:
    """Resolve a Service Account key path against CWD or the project root."""
    if key_path.exists() or key_path.is_absolute():
        return key_path
    project_candidate = PROJECT_ROOT / key_path
    if project_candidate.exists():
        return project_candidate
    return key_path


def _load_service_account_metadata(key_path: Path) -> dict[str, Any]:
    resolved_path = resolve_service_account_path(key_path)
    if not resolved_path.exists():
        raise FileNotFoundError(
            f"No se encontró la clave JSON de Service Account en: {key_path}"
        )
    with resolved_path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"El archivo de credenciales {resolved_path} no contiene un objeto JSON.")
    if ("installed" in data or "web" in data) and "client_email" not in data:
        raise ValueError(
            f"El archivo {resolved_path} es un JSON de cliente OAuth, no una clave de Service Account."
        )
    return data


def initialize_earth_engine(settings: Settings) -> str:
    """Initialize Earth Engine via Service Account key or default user/ADC credentials."""
    project_id = settings.gee_project_id

    if settings.gee_private_key_path is not None:
        key_path = resolve_service_account_path(settings.gee_private_key_path)
        key_meta = _load_service_account_metadata(key_path)
        service_account = settings.gee_service_account or key_meta.get("client_email")
        if not service_account:
            raise ValueError(
                "Define GEE_SERVICE_ACCOUNT o incluye 'client_email' en el archivo JSON."
            )
        project_id = project_id or key_meta.get("project_id")
        if not project_id:
            raise ValueError(
                "Define GEE_PROJECT_ID o incluye 'project_id' en el archivo JSON."
            )
        credentials = ee.ServiceAccountCredentials(
            service_account, str(key_path)
        )
        ee.Initialize(credentials=credentials, project=project_id)
        return project_id

    if project_id:
        ee.Initialize(project=project_id)
        return project_id

    ee.Initialize()
    return "default"


def build_gcs_client(settings: Settings) -> storage.Client:
    """Create a Google Cloud Storage client using Service Account JSON or ADC."""
    if settings.gee_private_key_path is not None:
        key_path = resolve_service_account_path(settings.gee_private_key_path)
        key_meta = _load_service_account_metadata(key_path)
        project_id = settings.gee_project_id or key_meta.get("project_id")
        return storage.Client.from_service_account_json(
            str(key_path),
            project=project_id,
        )

    return storage.Client(project=settings.gee_project_id)
