"""Integration tests for the Open Filament Database proxy routes (voron B6).

The upstream client is monkeypatched; what is under test is the setting gate,
the routing and the error mapping.
"""

import pytest
from httpx import AsyncClient

from backend.app.services.open_filament_database import (
    OpenFilamentDatabaseClient,
    OpenFilamentDatabaseNotFound,
)

PREFIX = "/api/v1/open-filament-database"


async def _enable(async_client: AsyncClient) -> None:
    response = await async_client.put("/api/v1/settings/", json={"open_filament_database_enabled": True})
    assert response.status_code == 200


@pytest.mark.integration
async def test_setting_defaults_off_and_persists(async_client: AsyncClient):
    response = await async_client.get("/api/v1/settings/")
    assert response.status_code == 200
    assert response.json()["open_filament_database_enabled"] is False

    await _enable(async_client)

    response = await async_client.get("/api/v1/settings/")
    assert response.json()["open_filament_database_enabled"] is True


@pytest.mark.integration
async def test_routes_refuse_when_disabled(monkeypatch, async_client: AsyncClient):
    called = False

    async def fake_brands(self):
        nonlocal called
        called = True
        return {"brands": []}

    monkeypatch.setattr(OpenFilamentDatabaseClient, "get_brands", fake_brands)

    response = await async_client.get(f"{PREFIX}/brands")

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "ofdb_disabled"
    assert called is False


@pytest.mark.integration
async def test_search_filaments_filters_brand_material(monkeypatch, async_client: AsyncClient):
    async def fake_search(self, brand_slug: str, material: str, query: str | None = None):
        assert (brand_slug, material, query) == ("elegoo", "PLA", "matte")
        return {
            "source": "openfilamentdatabase",
            "brand_slug": brand_slug,
            "material": material,
            "query": query,
            "count": 1,
            "filaments": [{"id": "f2", "name": "PLA MATTE", "slug": "pla_matte", "variant_count": 17}],
        }

    monkeypatch.setattr(OpenFilamentDatabaseClient, "search_filaments", fake_search)
    await _enable(async_client)

    response = await async_client.get(f"{PREFIX}/search?brand=elegoo&material=PLA&q=matte")

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1
    assert payload["filaments"][0]["slug"] == "pla_matte"


@pytest.mark.integration
async def test_variant_endpoint_returns_spool_prefill(monkeypatch, async_client: AsyncClient):
    prefill = {
        "brand": "ELEGOO",
        "material": "PLA",
        "subtype": "PLA",
        "color_name": "Black",
        "rgba": "000000FF",
        "label_weight": 1000,
        "nozzle_temp_min": 190,
        "nozzle_temp_max": 230,
        "slicer_filament": "GFE00",
        "slicer_filament_name": "Elegoo PLA",
        "data_origin": "openfilamentdatabase",
    }

    async def fake_variant(self, brand_slug: str, material: str, filament_slug: str, variant_slug: str):
        assert (brand_slug, material, filament_slug, variant_slug) == ("elegoo", "PLA", "pla", "black")
        return {"source": "openfilamentdatabase", "spool_prefill": prefill}

    monkeypatch.setattr(OpenFilamentDatabaseClient, "get_variant", fake_variant)
    await _enable(async_client)

    response = await async_client.get(f"{PREFIX}/brands/elegoo/materials/PLA/filaments/pla/variants/black")

    assert response.status_code == 200
    assert response.json()["spool_prefill"] == prefill


@pytest.mark.integration
async def test_not_found_maps_to_structured_404(monkeypatch, async_client: AsyncClient):
    async def fake_material(self, brand_slug: str, material: str):
        raise OpenFilamentDatabaseNotFound("Open Filament Database entry was not found", upstream_status=404)

    monkeypatch.setattr(OpenFilamentDatabaseClient, "get_material", fake_material)
    await _enable(async_client)

    response = await async_client.get(f"{PREFIX}/brands/nope/materials/PLA/filaments")

    assert response.status_code == 404
    assert response.json()["detail"] == {
        "code": "ofdb_not_found",
        "message": "Open Filament Database entry was not found",
        "upstream_status": 404,
    }


@pytest.mark.integration
async def test_bad_slug_maps_to_422(async_client: AsyncClient):
    await _enable(async_client)

    response = await async_client.get(f"{PREFIX}/brands/%20")

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "ofdb_invalid_path"
