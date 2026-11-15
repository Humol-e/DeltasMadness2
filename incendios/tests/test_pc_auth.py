"""Tests for Planetary Computer HTTP client, retries, and resource helpers."""

from __future__ import annotations

import io
import json
import urllib.error
from pathlib import Path

import pytest

from wildfire_monitor import pc_auth
from wildfire_monitor.config import Settings


def test_initialize_planetary_computer_client() -> None:
    settings = Settings(_env_file=None, stac_api_url="https://example.org/stac/")
    client = pc_auth.initialize_planetary_computer(settings)
    assert client.config.stac_api_url == "https://example.org/stac"


def test_request_json_retries_transient_http_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = {"n": 0}

    class _FakeResp:
        def __init__(self, data: bytes) -> None:
            self._data = data

        def read(self) -> bytes:
            return self._data

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(req, timeout=30.0):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise urllib.error.HTTPError(
                req.full_url, 503, "Service Unavailable", {}, io.BytesIO(b"")
            )
        return _FakeResp(json.dumps({"ok": True, "tries": attempts["n"]}).encode("utf-8"))

    monkeypatch.setattr(pc_auth.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(pc_auth.time, "sleep", lambda _: None)

    client = pc_auth.initialize_planetary_computer(Settings(_env_file=None))
    res = client.request_json("https://example.org/api")
    assert res == {"ok": True, "tries": 3}


def test_resolve_local_resource_falls_back_to_project_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "sample.geojson"
    target.write_text("{}", encoding="utf-8")
    elsewhere = tmp_path / "subdir"
    elsewhere.mkdir()

    monkeypatch.chdir(elsewhere)
    monkeypatch.setattr(pc_auth, "PROJECT_ROOT", tmp_path)
    assert pc_auth.resolve_local_resource(Path("sample.geojson")) == target
