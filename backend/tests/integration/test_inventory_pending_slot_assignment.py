"""Integration tests for pending spool-to-slot assignments (voron B8).

Covers the ``/api/v1/inventory/assignments/pending`` endpoints and the real
hook: ``on_ams_change`` reporting a freshly loaded tray completes the request
through the normal ``assign_spool`` path (slot row + MQTT filament settings).

Adapted from Printbuddy's ``test_inventory_next_assign.py``; the request shape
is spool-based here (spool_id + printer_id) instead of NFC/QR identifiers.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.spool import Spool
from backend.app.models.spool_assignment import SpoolAssignment

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

PENDING_URL = "/api/v1/inventory/assignments/pending"


@pytest.fixture
async def spool_factory(db_session: AsyncSession):
    async def _create(**kwargs) -> Spool:
        defaults = {
            "material": "PLA",
            "brand": "Sunlu",
            "color_name": "Red",
            "rgba": "FF0000FF",
            "label_weight": 1000,
            "weight_used": 0,
            "slicer_filament": "GFL99",
        }
        defaults.update(kwargs)
        spool = Spool(**defaults)
        db_session.add(spool)
        await db_session.commit()
        await db_session.refresh(spool)
        return spool

    return _create


def _mock_status(ams_data):
    status = MagicMock()
    status.raw_data = {"ams": {"ams": ams_data}}
    status.nozzles = [MagicMock(nozzle_diameter="0.4")]
    status.ams_extruder_map = None
    status.state = "IDLE"
    return status


class TestPendingEndpoints:
    async def test_create_list_get_cancel(self, async_client: AsyncClient, spool_factory, printer_factory):
        printer = await printer_factory(name="P2S")
        spool = await spool_factory()

        created = await async_client.post(PENDING_URL, json={"spool_id": spool.id, "printer_id": printer.id})
        assert created.status_code == 202, created.text
        body = created.json()
        assert body["status"] == "pending"
        assert body["spool_id"] == spool.id
        assert body["printer_id"] == printer.id
        assert body["printer_name"] == "P2S"
        assert body["timeout_seconds"] == 1800
        assert body["source"] == "ui"
        assert body["expires_at"] is not None
        assert body["spool"]["id"] == spool.id

        listed = await async_client.get(PENDING_URL)
        assert listed.status_code == 200
        assert [r["id"] for r in listed.json()] == [body["id"]]

        by_spool = await async_client.get(PENDING_URL, params={"spool_id": spool.id + 1})
        assert by_spool.json() == []

        single = await async_client.get(f"{PENDING_URL}/{body['id']}")
        assert single.status_code == 200
        assert single.json()["status"] == "pending"

        cancelled = await async_client.delete(f"{PENDING_URL}/{body['id']}")
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "cancelled"
        assert cancelled.json()["completed_at"] is not None

        # Gone from the default list, still readable, not cancellable twice.
        assert (await async_client.get(PENDING_URL)).json() == []
        finished = await async_client.get(PENDING_URL, params={"include_finished": "true"})
        assert [r["status"] for r in finished.json()] == ["cancelled"]
        assert (await async_client.get(f"{PENDING_URL}/{body['id']}")).json()["status"] == "cancelled"
        assert (await async_client.delete(f"{PENDING_URL}/{body['id']}")).status_code == 404

    async def test_remark_replaces_previous_request(self, async_client: AsyncClient, spool_factory, printer_factory):
        p1 = await printer_factory(name="P2S")
        p2 = await printer_factory(name="X2D")
        spool = await spool_factory()

        first = (await async_client.post(PENDING_URL, json={"spool_id": spool.id, "printer_id": p1.id})).json()
        second = (await async_client.post(PENDING_URL, json={"spool_id": spool.id, "printer_id": p2.id})).json()

        pending = (await async_client.get(PENDING_URL)).json()
        assert [r["id"] for r in pending] == [second["id"]]
        assert pending[0]["printer_name"] == "X2D"
        assert (await async_client.get(f"{PENDING_URL}/{first['id']}")).json()["status"] == "cancelled"

    async def test_any_printer_and_custom_timeout(self, async_client: AsyncClient, spool_factory):
        spool = await spool_factory()
        resp = await async_client.post(PENDING_URL, json={"spool_id": spool.id, "timeout_seconds": 300})
        assert resp.status_code == 202
        assert resp.json()["printer_id"] is None
        assert resp.json()["printer_name"] is None
        assert resp.json()["timeout_seconds"] == 300

    async def test_validation_and_lookup_errors(self, async_client: AsyncClient, spool_factory):
        spool = await spool_factory()
        archived = await spool_factory(archived_at=datetime(2026, 1, 1, tzinfo=timezone.utc))

        assert (
            await async_client.post(PENDING_URL, json={"spool_id": spool.id, "timeout_seconds": 5})
        ).status_code == 422
        assert (
            await async_client.post(PENDING_URL, json={"spool_id": spool.id, "timeout_seconds": 10**6})
        ).status_code == 422
        assert (await async_client.post(PENDING_URL, json={"spool_id": spool.id, "source": "nfc"})).status_code == 422
        assert (await async_client.post(PENDING_URL, json={"printer_id": 1})).status_code == 422
        assert (await async_client.post(PENDING_URL, json={"spool_id": 99999})).status_code == 404
        assert (
            await async_client.post(PENDING_URL, json={"spool_id": spool.id, "printer_id": 99999})
        ).status_code == 404
        assert (await async_client.post(PENDING_URL, json={"spool_id": archived.id})).status_code == 400
        assert (await async_client.get(f"{PENDING_URL}/99999")).status_code == 404
        assert (await async_client.delete(f"{PENDING_URL}/99999")).status_code == 404


class TestTrayLoadCompletesRequest:
    async def test_loaded_tray_assigns_waiting_spool(
        self, async_client: AsyncClient, db_session: AsyncSession, spool_factory, printer_factory
    ):
        """A generic (no RFID) spool inserted into an unassigned tray takes the pending spool."""
        from backend.app.main import on_ams_change

        printer = await printer_factory(name="P2S")
        spool = await spool_factory(slicer_filament="GFL99", material="PLA")
        created = (await async_client.post(PENDING_URL, json={"spool_id": spool.id, "printer_id": printer.id})).json()

        ams_data = [{"id": 0, "tray": [{"id": 2, "tray_type": "PLA", "tray_color": "FF0000FF", "state": 11}]}]
        mock_client = MagicMock()
        mock_client.ams_set_filament_setting.return_value = True
        mock_client.extrusion_cali_sel.return_value = True
        status = _mock_status(ams_data)

        with (
            patch("backend.app.main.printer_manager") as pm_main,
            patch("backend.app.services.printer_manager.printer_manager") as pm_inv,
            patch("backend.app.main.mqtt_relay") as relay,
            patch("backend.app.main.ws_manager") as ws_main,
        ):
            pm_main.get_printer.return_value = MagicMock(name="P2S", serial_number="00M09A000000001")
            pm_main.get_status.return_value = status
            pm_main.get_client.return_value = mock_client
            pm_main.get_model.return_value = "P2S"
            pm_inv.get_client.return_value = mock_client
            pm_inv.get_status.return_value = status
            pm_inv.get_model.return_value = "P2S"
            relay.on_ams_change = AsyncMock()
            ws_main.send_printer_status = AsyncMock()
            ws_main.broadcast = AsyncMock()

            await on_ams_change(printer.id, ams_data)

        rows = (await db_session.execute(select(SpoolAssignment))).scalars().all()
        assert [(r.spool_id, r.printer_id, r.ams_id, r.tray_id) for r in rows] == [(spool.id, printer.id, 0, 2)]

        # The slot got the spool's filament settings, like a manual assign.
        mock_client.ams_set_filament_setting.assert_called_once()
        kwargs = mock_client.ams_set_filament_setting.call_args.kwargs
        assert (kwargs["ams_id"], kwargs["tray_id"]) == (0, 2)

        done = (await async_client.get(f"{PENDING_URL}/{created['id']}")).json()
        assert done["status"] == "completed"
        assert (done["assigned_printer_id"], done["assigned_ams_id"], done["assigned_tray_id"]) == (printer.id, 0, 2)
        assert (await async_client.get(PENDING_URL)).json() == []

    async def test_tray_already_assigned_is_not_taken(
        self, async_client: AsyncClient, db_session: AsyncSession, spool_factory, printer_factory
    ):
        """A tray that already has an assignment never completes a pending request."""
        from backend.app.main import on_ams_change

        printer = await printer_factory(name="P2S")
        loaded = await spool_factory(color_name="Loaded")
        waiting = await spool_factory(color_name="Waiting")
        db_session.add(
            SpoolAssignment(spool_id=loaded.id, printer_id=printer.id, ams_id=0, tray_id=0, fingerprint_type="PLA")
        )
        await db_session.commit()
        await async_client.post(PENDING_URL, json={"spool_id": waiting.id, "printer_id": printer.id})

        ams_data = [{"id": 0, "tray": [{"id": 0, "tray_type": "PLA", "tray_color": "FF0000FF", "state": 11}]}]
        with (
            patch("backend.app.main.printer_manager") as pm_main,
            patch("backend.app.services.printer_manager.printer_manager") as pm_inv,
            patch("backend.app.main.mqtt_relay") as relay,
            patch("backend.app.main.ws_manager") as ws_main,
        ):
            pm_main.get_printer.return_value = MagicMock(name="P2S", serial_number="00M09A000000001")
            pm_main.get_status.return_value = _mock_status(ams_data)
            pm_main.get_client.return_value = None
            pm_main.get_model.return_value = "P2S"
            pm_inv.get_client.return_value = None
            pm_inv.get_status.return_value = _mock_status(ams_data)
            relay.on_ams_change = AsyncMock()
            ws_main.send_printer_status = AsyncMock()
            ws_main.broadcast = AsyncMock()

            await on_ams_change(printer.id, ams_data)

        pending = (await async_client.get(PENDING_URL)).json()
        assert [r["spool_id"] for r in pending] == [waiting.id]
        rows = (await db_session.execute(select(SpoolAssignment))).scalars().all()
        assert [r.spool_id for r in rows] == [loaded.id]
