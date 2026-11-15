"""Tests for Planetary Computer STAC catalog queries, orbit matching, and GeoPulse Scout feeds."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from wildfire_monitor.config import AOI_PRESETS
from wildfire_monitor.ingestion import native_catalog


def test_native_catalog_contains_geopulse_collections() -> None:
    assert native_catalog.NATIVE_CATALOG["sentinel2_l2a"].collection_id == "sentinel-2-l2a"
    assert native_catalog.NATIVE_CATALOG["sentinel1_rtc"].collection_id == "sentinel-1-rtc"
    assert native_catalog.NATIVE_CATALOG["cop_dem"].collection_id == "cop-dem-glo-30"
    assert native_catalog.resolve_native_spec("s2").collection_id == "sentinel-2-l2a"
    assert native_catalog.resolve_native_spec("dem").collection_id == "cop-dem-glo-30"


def test_search_stac_scenes_sorts_chronologically() -> None:
    fake_client = MagicMock()
    fake_client.config.stac_api_url = "https://pc.example/stac/v1"
    fake_client.request_json.return_value = {
        "features": [
            {
                "id": "S2_LATER",
                "collection": "sentinel-2-l2a",
                "properties": {
                    "datetime": "2025-01-05T18:30:00Z",
                    "eo:cloud_cover": 12.5,
                    "s2:processing_baseline": "05.11",
                },
                "assets": {"B04": {}, "SCL": {}},
            },
            {
                "id": "S2_EARLIER",
                "collection": "sentinel-2-l2a",
                "properties": {
                    "datetime": "2024-12-20T18:30:00Z",
                    "eo:cloud_cover": 4.0,
                    "s2:processing_baseline": "03.00",
                },
                "assets": {"B04": {}, "SCL": {}},
            },
        ]
    }

    scenes = native_catalog.search_stac_scenes(
        "sentinel2_l2a",
        AOI_PRESETS["palisades"].bbox,
        "2024-12-01/2025-01-06",
        client=fake_client,
    )
    assert [s.item_id for s in scenes] == ["S2_EARLIER", "S2_LATER"]
    assert scenes[0].processing_baseline == 3.0
    assert scenes[1].processing_baseline == 5.11


def test_match_orbits_restricts_pre_scenes_to_post_relative_orbits() -> None:
    def make_s1(sid: str, dt: str, orbit: int) -> native_catalog.STACSceneSummary:
        return native_catalog.STACSceneSummary(
            item_id=sid,
            collection="sentinel-1-rtc",
            datetime_utc=dt,
            cloud_cover=None,
            relative_orbit=orbit,
            orbit_state="descending",
            platform="sentinel-1a",
            processing_baseline=0.0,
        )

    pre = [make_s1("P1", "2024-12-01T00:00:00Z", 64), make_s1("P2", "2024-12-10T00:00:00Z", 71)]
    post = [make_s1("A1", "2025-02-01T00:00:00Z", 71)]
    sel_pre, sel_post, restricted = native_catalog.match_orbits(pre, post)
    assert [s.item_id for s in sel_pre] == ["P2"]
    assert [s.item_id for s in sel_post] == ["A1"]
    assert restricted == [71]


def test_fetch_wildfire_scout_alerts_parses_eonet_and_rss() -> None:
    fake_client = MagicMock()
    eonet_payload = {
        "events": [
            {
                "id": "EONET_999",
                "title": "Test Canyon Wildfire",
                "link": "https://eonet.example/999",
                "geometry": [{"type": "Point", "date": "2025-02-01T12:00:00Z", "coordinates": [-118.5, 34.08]}],
            }
        ]
    }
    fake_client.request_bytes.return_value = json.dumps(eonet_payload).encode("utf-8")
    alerts = native_catalog.fetch_wildfire_scout_alerts("eonet", client=fake_client)
    assert len(alerts) == 1
    assert alerts[0].source == "EONET"
    assert alerts[0].lon == pytest.approx(-118.5)

    rss_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0" xmlns:geo="http://www.w3.org/2003/01/geo/wgs84_pos#">
      <channel>
        <item>
          <guid>GDACS-WF-1</guid>
          <title>Forest fire in Northern California</title>
          <pubDate>Tue, 07 Jan 2025 10:00:00 GMT</pubDate>
          <geo:lat>39.74</geo:lat>
          <geo:long>-121.70</geo:long>
        </item>
      </channel>
    </rss>"""
    fake_client.request_bytes.return_value = rss_xml.encode("utf-8")
    rss_alerts = native_catalog.fetch_wildfire_scout_alerts("gdacs", client=fake_client)
    assert len(rss_alerts) == 1
    assert rss_alerts[0].source == "GDACS"
    assert rss_alerts[0].lat == pytest.approx(39.74)
