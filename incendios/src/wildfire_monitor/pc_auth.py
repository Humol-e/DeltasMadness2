"""Planetary Computer STAC / TiTiler / SAS client & environment diagnostics."""

from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from wildfire_monitor.config import PROJECT_ROOT, Settings


def resolve_local_resource(path: Path) -> Path:
    """Resolve an input file/dir path relative to CWD or `PROJECT_ROOT`."""
    if path.exists() or path.is_absolute():
        return path
    candidate = PROJECT_ROOT / path
    if candidate.exists():
        return candidate
    return path


@dataclass(frozen=True)
class HTTPClientConfig:
    """Configuration for Planetary Computer HTTP queries with retry/backoff."""

    stac_api_url: str
    titiler_api_url: str
    sas_token_url: str
    timeout_seconds: float = 30.0
    max_retries: int = 4


class PlanetaryComputerClient:
    """Lightweight stdlib HTTP client for Microsoft Planetary Computer STAC & TiTiler."""

    def __init__(self, config: HTTPClientConfig) -> None:
        self.config = config
        self._sas_cache: dict[str, dict[str, Any]] = {}

    def request_bytes(
        self,
        url: str,
        *,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> bytes:
        """Perform GET/POST request with jittered exponential backoff on 429/5xx."""
        req_headers = {"User-Agent": "wildfire-monitor/0.1.0", **(headers or {})}
        for attempt in range(self.config.max_retries + 1):
            req = urllib.request.Request(url, data=data, headers=req_headers)
            try:
                with urllib.request.urlopen(req, timeout=self.config.timeout_seconds) as resp:
                    return resp.read()
            except urllib.error.HTTPError as exc:
                if exc.code in (429, 500, 502, 503, 504) and attempt < self.config.max_retries:
                    delay = (0.5 * (2**attempt)) * (0.6 + 0.8 * random.random())
                    time.sleep(delay)
                    continue
                raise
            except urllib.error.URLError:
                if attempt < self.config.max_retries:
                    delay = (0.5 * (2**attempt)) * (0.6 + 0.8 * random.random())
                    time.sleep(delay)
                    continue
                raise
        raise RuntimeError(f"Unreachable retry loop exit for {url}")

    def request_json(
        self,
        url: str,
        *,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Request JSON response (POST when `payload` is provided, else GET)."""
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"Content-Type": "application/json"} if payload is not None else None
        raw = self.request_bytes(url, data=data, headers=headers)
        return json.loads(raw.decode("utf-8"))


def initialize_planetary_computer(settings: Settings) -> PlanetaryComputerClient:
    """Construct a configured Planetary Computer HTTP client."""
    return PlanetaryComputerClient(
        HTTPClientConfig(
            stac_api_url=settings.stac_api_url.rstrip("/"),
            titiler_api_url=settings.titiler_api_url.rstrip("/"),
            sas_token_url=settings.sas_token_url.rstrip("/"),
            timeout_seconds=settings.http_timeout_seconds,
        )
    )


def check_stac_connectivity(settings: Settings) -> dict[str, Any]:
    """Check whether Planetary Computer STAC API answers cleanly."""
    client = initialize_planetary_computer(settings)
    try:
        root = client.request_json(client.config.stac_api_url)
        return {
            "ok": True,
            "title": root.get("title", "Planetary Computer STAC API"),
            "stac_version": root.get("stac_version"),
        }
    except Exception as exc:  # pragma: no cover - network/offline dependent
        return {
            "ok": False,
            "error": str(exc),
        }
