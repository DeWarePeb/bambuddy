"""Voron patch series (B11): CRUD for /api/v1/printer-fleet-groups."""

import pytest
from httpx import AsyncClient

BASE = "/api/v1/printer-fleet-groups/"


class TestPrinterFleetGroupsAPI:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_empty_by_default(self, async_client: AsyncClient):
        response = await async_client.get(BASE)
        assert response.status_code == 200
        assert response.json() == []

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_returns_members_and_lists_them(self, async_client: AsyncClient, printer_factory):
        voron = await printer_factory(name="Voron")
        p2s = await printer_factory(name="P2S")

        created = await async_client.post(
            BASE, json={"name": "Shop", "color": "#FD8700", "printer_ids": [p2s.id, voron.id]}
        )
        assert created.status_code == 200
        body = created.json()
        assert body["name"] == "Shop"
        assert body["color"] == "#FD8700"
        # Members come back sorted by printer id, not insertion order.
        assert body["printer_ids"] == sorted([voron.id, p2s.id])

        listed = await async_client.get(BASE)
        assert listed.status_code == 200
        assert [group["name"] for group in listed.json()] == ["Shop"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_name_is_trimmed_and_duplicates_are_rejected(self, async_client: AsyncClient, printer_factory):
        await printer_factory(name="Voron")
        first = await async_client.post(BASE, json={"name": "  Shop  ", "printer_ids": []})
        assert first.status_code == 200
        assert first.json()["name"] == "Shop"

        # Same name, different case: 400 from our own check, not a 500 from the
        # unique constraint.
        duplicate = await async_client.post(BASE, json={"name": "shop", "printer_ids": []})
        assert duplicate.status_code == 400
        assert "already exists" in duplicate.json()["detail"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_unknown_printer_id_is_rejected(self, async_client: AsyncClient):
        response = await async_client.post(BASE, json={"name": "Ghosts", "printer_ids": [4242]})
        assert response.status_code == 400
        assert "4242" in response.json()["detail"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_duplicate_printer_ids_are_collapsed(self, async_client: AsyncClient, printer_factory):
        voron = await printer_factory(name="Voron")
        response = await async_client.post(BASE, json={"name": "Shop", "printer_ids": [voron.id, voron.id]})
        assert response.status_code == 200
        assert response.json()["printer_ids"] == [voron.id]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_replaces_the_member_set(self, async_client: AsyncClient, printer_factory):
        voron = await printer_factory(name="Voron")
        p2s = await printer_factory(name="P2S")
        group_id = (await async_client.post(BASE, json={"name": "Shop", "printer_ids": [voron.id]})).json()["id"]

        updated = await async_client.put(f"{BASE}{group_id}", json={"name": "Shop floor", "printer_ids": [p2s.id]})
        assert updated.status_code == 200
        assert updated.json()["name"] == "Shop floor"
        # Replaced, not appended.
        assert updated.json()["printer_ids"] == [p2s.id]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_keeping_its_own_name_is_allowed(self, async_client: AsyncClient, printer_factory):
        voron = await printer_factory(name="Voron")
        group_id = (await async_client.post(BASE, json={"name": "Shop", "printer_ids": [voron.id]})).json()["id"]

        response = await async_client.put(f"{BASE}{group_id}", json={"name": "Shop", "printer_ids": []})
        assert response.status_code == 200
        assert response.json()["printer_ids"] == []

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_delete_removes_the_group(self, async_client: AsyncClient, printer_factory):
        voron = await printer_factory(name="Voron")
        group_id = (await async_client.post(BASE, json={"name": "Shop", "printer_ids": [voron.id]})).json()["id"]

        assert (await async_client.delete(f"{BASE}{group_id}")).status_code == 204
        assert (await async_client.get(BASE)).json() == []
        assert (await async_client.delete(f"{BASE}{group_id}")).status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_missing_group_is_404(self, async_client: AsyncClient):
        assert (await async_client.put(f"{BASE}999", json={"name": "x"})).status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_groups_are_ordered_by_sort_order_then_name(self, async_client: AsyncClient):
        await async_client.post(BASE, json={"name": "Zebra", "sort_order": 0})
        await async_client.post(BASE, json={"name": "Alpha", "sort_order": 1})
        await async_client.post(BASE, json={"name": "Beta", "sort_order": 0})

        names = [group["name"] for group in (await async_client.get(BASE)).json()]
        assert names == ["Beta", "Zebra", "Alpha"]
