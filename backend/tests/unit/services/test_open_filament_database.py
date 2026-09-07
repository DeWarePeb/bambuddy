"""Unit tests for the Open Filament Database client (voron B6).

The upstream API is replaced by an httpx.MockTransport serving a tiny
brand -> material -> filament -> variant tree, so these run offline.
"""

from __future__ import annotations

import json

import httpx
import pytest

from backend.app.services.open_filament_database import (
    OFDB_SOURCE,
    OpenFilamentDatabaseBadResponse,
    OpenFilamentDatabaseClient,
    OpenFilamentDatabaseNotFound,
    OpenFilamentDatabaseUnavailable,
    _clean_segment,
    _derive_subtype,
    _first_preferred_size,
    _rgba_from_hex,
)

BASE = "https://ofdb.test/api/v1"

TREE: dict[str, dict] = {
    "brands/index.json": {
        "version": "2026.09.06",
        "count": 1,
        "brands": [{"id": "b1", "name": "ELEGOO", "slug": "elegoo", "origin": "CN", "material_count": 1}],
    },
    "brands/elegoo/index.json": {
        "id": "b1",
        "name": "ELEGOO",
        "slug": "elegoo",
        "origin": "CN",
        "website": "https://elegoo.com/",
        "materials": [{"id": "m1", "material": "PLA", "slug": "PLA", "filament_count": 2}],
    },
    "brands/elegoo/materials/PLA/index.json": {
        "id": "m1",
        "brand_id": "b1",
        "material": "PLA",
        "slug": "PLA",
        "material_class": "FFF",
        "filaments": [
            {"id": "f1", "name": "PLA", "slug": "pla", "variant_count": 1},
            {"id": "f2", "name": "PLA MATTE", "slug": "pla_matte", "variant_count": 3},
        ],
    },
    "brands/elegoo/materials/PLA/filaments/pla_matte/index.json": {
        "id": "f2",
        "name": "PLA MATTE",
        "slug": "pla_matte",
        "material": "PLA",
        "density": 1.24,
        "min_print_temperature": 190,
        "max_print_temperature": 230,
        "slicer_settings": {
            "orcaslicer": {"id": "OGFE05", "generic_id": "GFL99", "profile_name": "Elegoo PLA Matte (Orca)"},
            "bambustudio": {"id": "GFE05", "generic_id": "GFL99", "profile_name": "Elegoo PLA Matte"},
        },
        "variants": [{"id": "v1", "name": "Matte Black", "slug": "matte_black", "color_hex": "#101010"}],
    },
    "brands/elegoo/materials/PLA/filaments/pla_matte/variants/matte_black.json": {
        "id": "v1",
        "name": "Matte Black",
        "slug": "matte_black",
        "color_hex": "#101010",
        "traits": {"industrially_compostable": True},
        "sizes": [
            {"id": "s-refill", "filament_weight": 1000, "diameter": 1.75, "spool_refill": True},
            {"id": "s-old", "filament_weight": 1000, "diameter": 1.75, "discontinued": True},
            {"id": "s-1kg", "filament_weight": 1000, "diameter": 1.75, "empty_spool_weight": 180},
        ],
    },
}


def _mock_client(*, invalid_json: bool = False, fail: bool = False) -> OpenFilamentDatabaseClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if fail:
            raise httpx.ConnectError("boom", request=request)
        path = request.url.path.removeprefix("/api/v1/")
        if invalid_json:
            return httpx.Response(200, text="<html>not json</html>")
        body = TREE.get(path)
        if body is None:
            return httpx.Response(404, text="<html>Page not found</html>")
        return httpx.Response(200, text=json.dumps(body))

    return OpenFilamentDatabaseClient(base_url=BASE, transport=httpx.MockTransport(handler))


async def test_get_variant_builds_spool_prefill():
    result = await _mock_client().get_variant("elegoo", "pla", "pla_matte", "matte_black")

    assert result["source"] == OFDB_SOURCE
    assert result["brand"] == {"id": "b1", "slug": "elegoo", "name": "ELEGOO"}
    assert result["variant"]["color_hex"] == "#101010"
    # Current, non-refill size wins over the refill and the discontinued one.
    assert result["selected_size"]["id"] == "s-1kg"
    assert result["spool_prefill"] == {
        "brand": "ELEGOO",
        "material": "PLA",
        "subtype": "MATTE",
        "color_name": "Matte Black",
        "rgba": "101010FF",
        "label_weight": 1000,
        "core_weight": 180,
        "nozzle_temp_min": 190,
        "nozzle_temp_max": 230,
        # Bambu Studio block preferred over Orca.
        "slicer_filament": "GFE05",
        "slicer_filament_name": "Elegoo PLA Matte",
        "data_origin": OFDB_SOURCE,
    }


async def test_search_filters_within_brand_material():
    result = await _mock_client().search_filaments("elegoo", "PLA", "matte")
    assert result["count"] == 1
    assert result["filaments"][0]["slug"] == "pla_matte"

    everything = await _mock_client().search_filaments("elegoo", "PLA", "")
    assert everything["count"] == 2


async def test_material_is_uppercased_for_the_path():
    result = await _mock_client().get_material("elegoo", "pla")
    assert result["material"] == "PLA"
    assert len(result["filaments"]) == 2


async def test_missing_entry_raises_not_found():
    with pytest.raises(OpenFilamentDatabaseNotFound) as exc:
        await _mock_client().get_brand("nope")
    assert exc.value.upstream_status == 404
    assert exc.value.status_code == 404


async def test_invalid_json_raises_bad_response():
    with pytest.raises(OpenFilamentDatabaseBadResponse):
        await _mock_client(invalid_json=True).get_brands()


async def test_transport_error_raises_unavailable():
    with pytest.raises(OpenFilamentDatabaseUnavailable):
        await _mock_client(fail=True).get_brands()


async def test_path_segments_are_validated():
    with pytest.raises(ValueError):
        await _mock_client().get_brand("../secrets")
    with pytest.raises(ValueError):
        await _mock_client().get_brand("  ")
    assert _clean_segment("pla matte") == "pla%20matte"


def test_helpers():
    assert _rgba_from_hex("#1a2b3c") == "1A2B3CFF"
    assert _rgba_from_hex("1A2B3C") == "1A2B3CFF"
    assert _rgba_from_hex("#fff") is None
    assert _rgba_from_hex("#zzzzzz") is None
    assert _rgba_from_hex(None) is None

    assert _derive_subtype("PLA", "PLA Basic") == "Basic"
    assert _derive_subtype("PLA", "PLA") == "PLA"
    assert _derive_subtype("PLA", "PolyTerra PLA") == "PolyTerra"
    assert _derive_subtype("PETG", "HF") == "HF"
    assert _derive_subtype("PLA", None) is None

    assert _first_preferred_size([]) is None
    only_refill = [{"filament_weight": 1000, "spool_refill": True}]
    assert _first_preferred_size(only_refill) is only_refill[0]
    sizes = [{"filament_weight": 250}, {"filament_weight": 750}, {"filament_weight": 1000}]
    assert _first_preferred_size(sizes)["filament_weight"] == 1000
