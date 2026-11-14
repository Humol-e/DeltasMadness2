"""Tests for GEE and GCS authentication initialization."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from glacier_monitor import gee_auth
from glacier_monitor.config import Settings


def test_initialize_earth_engine_service_account(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    key_file = tmp_path / "sa.json"
    key_file.write_text(
        json.dumps(
            {
                "client_email": "gee@demo.iam.gserviceaccount.com",
                "project_id": "demo-proj",
            }
        ),
        encoding="utf-8",
    )

    mock_ee = MagicMock()
    mock_ee.ServiceAccountCredentials.return_value = "CREDS"
    monkeypatch.setattr(gee_auth, "ee", mock_ee)

    settings = Settings(_env_file=None, gee_private_key_path=key_file)
    project = gee_auth.initialize_earth_engine(settings)

    assert project == "demo-proj"
    mock_ee.ServiceAccountCredentials.assert_called_once_with(
        "gee@demo.iam.gserviceaccount.com", str(key_file)
    )
    mock_ee.Initialize.assert_called_once_with(credentials="CREDS", project="demo-proj")


def test_initialize_earth_engine_missing_key_file(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, gee_private_key_path=tmp_path / "missing.json")
    with pytest.raises(FileNotFoundError):
        gee_auth.initialize_earth_engine(settings)


def test_build_gcs_client_uses_service_account_or_adc(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    key_file = tmp_path / "sa.json"
    key_file.write_text(json.dumps({"project_id": "demo-proj"}), encoding="utf-8")

    storage_mock = MagicMock()
    monkeypatch.setattr(gee_auth.storage, "Client", storage_mock)

    gee_auth.build_gcs_client(Settings(_env_file=None, gee_private_key_path=key_file))
    storage_mock.from_service_account_json.assert_called_once_with(
        str(key_file), project="demo-proj"
    )

    gee_auth.build_gcs_client(Settings(_env_file=None, gee_project_id="adc-proj"))
    storage_mock.assert_called_once_with(project="adc-proj")


def test_load_service_account_rejects_oauth_client_json(tmp_path: Path) -> None:
    oauth_file = tmp_path / "client_secret.json"
    oauth_file.write_text(
        json.dumps({"installed": {"client_id": "123", "project_id": "demo-proj"}}),
        encoding="utf-8",
    )
    settings = Settings(_env_file=None, gee_private_key_path=oauth_file)
    with pytest.raises(ValueError, match="OAuth"):
        gee_auth.initialize_earth_engine(settings)


def test_resolve_service_account_path_falls_back_to_project_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    key_file = tmp_path / "sa.json"
    key_file.write_text(json.dumps({"client_email": "a@b.c"}), encoding="utf-8")
    other_cwd = tmp_path / "elsewhere"
    other_cwd.mkdir()

    monkeypatch.chdir(other_cwd)
    monkeypatch.setattr(gee_auth, "PROJECT_ROOT", tmp_path)

    resolved = gee_auth.resolve_service_account_path(Path("sa.json"))
    assert resolved == key_file
