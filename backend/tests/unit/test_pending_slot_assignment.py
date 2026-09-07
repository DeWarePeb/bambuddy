"""Unit tests for the pending spool-to-slot assignment service (voron B8).

Exercises ``backend/app/services/pending_slot_assignment.py`` against the test
database: create / replace / cancel, lazy timeouts, and the tray-matching rules
that decide which waiting spool a freshly loaded tray completes.

``assign_spool`` runs for real (the printer is not connected, so its MQTT push
is a no-op) — the tests assert on the ``spool_assignment`` row it writes.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.pending_slot_assignment import (
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_PENDING,
    STATUS_TIMED_OUT,
    PendingSlotAssignment,
)
from backend.app.models.spool import Spool
from backend.app.models.spool_assignment import SpoolAssignment
from backend.app.services import pending_slot_assignment as svc

pytestmark = pytest.mark.unit

GENERIC_TRAY = {"id": 1, "tray_type": "PLA", "tray_color": "FF0000FF", "tag_uid": "", "tray_uuid": ""}
UUID_A = "A1B2C3D4E5F60718293A4B5C6D7E8F90"
UUID_B = "0F1E2D3C4B5A69788796A5B4C3D2E1F0"


@pytest.fixture
async def spool_factory(db_session: AsyncSession):
    async def _create(**kwargs) -> Spool:
        defaults = {"material": "PLA", "brand": "Sunlu", "color_name": "Red", "rgba": "FF0000FF"}
        defaults.update(kwargs)
        spool = Spool(**defaults)
        db_session.add(spool)
        await db_session.commit()
        await db_session.refresh(spool)
        return spool

    return _create


@pytest.fixture(autouse=True)
def _quiet_broadcast():
    with patch.object(svc.ws_manager, "broadcast", new=AsyncMock()) as mock:
        yield mock


async def _slot_rows(db: AsyncSession) -> list[SpoolAssignment]:
    return list((await db.execute(select(SpoolAssignment))).scalars().all())


def _bambu_tray(tray_uuid: str, tag_uid: str = "0000000000000000") -> dict:
    return {
        "id": 0,
        "tray_type": "PLA",
        "tray_color": "00FF00FF",
        "tray_uuid": tray_uuid,
        "tag_uid": tag_uid,
        "tray_info_idx": "GFA00",
    }


class TestCreateAndCancel:
    async def test_create_marks_spool_pending_for_printer(self, db_session, spool_factory, printer_factory):
        printer = await printer_factory(name="P2S")
        spool = await spool_factory()

        row = await svc.create_pending_assignment(
            db_session, spool_id=spool.id, printer_id=printer.id, timeout_seconds=600
        )

        assert row.status == STATUS_PENDING
        assert row.printer_id == printer.id
        assert row.printer_name == "P2S"
        assert row.timeout_seconds == 600
        assert row.expires_at is not None

    async def test_second_mark_replaces_the_first(self, db_session, spool_factory, printer_factory):
        p1 = await printer_factory(name="P2S")
        p2 = await printer_factory(name="X2D")
        spool = await spool_factory()

        first = await svc.create_pending_assignment(
            db_session, spool_id=spool.id, printer_id=p1.id, timeout_seconds=600
        )
        second = await svc.create_pending_assignment(
            db_session, spool_id=spool.id, printer_id=p2.id, timeout_seconds=600
        )

        await db_session.refresh(first)
        assert first.status == STATUS_CANCELLED
        assert second.status == STATUS_PENDING
        pending = await svc.list_assignments(db_session, spool_id=spool.id)
        assert [r.id for r in pending] == [second.id]

    async def test_create_rejects_missing_spool(self, db_session):
        with pytest.raises(svc.PendingAssignmentError) as exc:
            await svc.create_pending_assignment(db_session, spool_id=99999, printer_id=None, timeout_seconds=600)
        assert exc.value.status_code == 404

    async def test_create_rejects_archived_spool(self, db_session, spool_factory):
        spool = await spool_factory(archived_at=datetime.now(timezone.utc))
        with pytest.raises(svc.PendingAssignmentError) as exc:
            await svc.create_pending_assignment(db_session, spool_id=spool.id, printer_id=None, timeout_seconds=600)
        assert exc.value.status_code == 400

    async def test_create_rejects_unknown_printer(self, db_session, spool_factory):
        spool = await spool_factory()
        with pytest.raises(svc.PendingAssignmentError) as exc:
            await svc.create_pending_assignment(db_session, spool_id=spool.id, printer_id=4242, timeout_seconds=600)
        assert exc.value.status_code == 404

    async def test_cancel(self, db_session, spool_factory):
        spool = await spool_factory()
        row = await svc.create_pending_assignment(db_session, spool_id=spool.id, printer_id=None, timeout_seconds=600)

        cancelled = await svc.cancel_pending_assignment(db_session, row.id)
        assert cancelled is not None and cancelled.status == STATUS_CANCELLED
        assert cancelled.completed_at is not None
        # Only pending rows can be cancelled — a second cancel finds nothing.
        assert await svc.cancel_pending_assignment(db_session, row.id) is None
        assert await svc.cancel_pending_assignment(db_session, 123456) is None


class TestLazyTimeout:
    async def test_overdue_rows_flip_to_timed_out_on_read(self, db_session, spool_factory):
        spool = await spool_factory()
        row = await svc.create_pending_assignment(db_session, spool_id=spool.id, printer_id=None, timeout_seconds=60)
        row.created_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=120)
        await db_session.commit()

        assert row.is_expired
        assert await svc.list_assignments(db_session) == []
        await db_session.refresh(row)
        assert row.status == STATUS_TIMED_OUT
        assert row.completed_at is not None

    async def test_fresh_row_is_not_expired(self, db_session, spool_factory):
        spool = await spool_factory()
        row = await svc.create_pending_assignment(db_session, spool_id=spool.id, printer_id=None, timeout_seconds=60)
        assert not row.is_expired
        assert [r.id for r in await svc.list_assignments(db_session)] == [row.id]

    async def test_expired_request_does_not_claim_a_tray(self, db_session, spool_factory, printer_factory):
        printer = await printer_factory()
        spool = await spool_factory()
        row = await svc.create_pending_assignment(
            db_session, spool_id=spool.id, printer_id=printer.id, timeout_seconds=60
        )
        row.created_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
        await db_session.commit()

        done = await svc.try_complete_pending_assignment(
            db_session, printer_id=printer.id, ams_id=0, tray_id=1, tray=GENERIC_TRAY
        )
        assert done is None
        assert await _slot_rows(db_session) == []


class TestTrayMatching:
    async def test_generic_tray_completes_oldest_request(self, db_session, spool_factory, printer_factory):
        printer = await printer_factory()
        older = await spool_factory(color_name="Older")
        newer = await spool_factory(color_name="Newer")
        first = await svc.create_pending_assignment(
            db_session, spool_id=older.id, printer_id=printer.id, timeout_seconds=600
        )
        first.created_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=30)
        await db_session.commit()
        await svc.create_pending_assignment(db_session, spool_id=newer.id, printer_id=printer.id, timeout_seconds=600)

        done = await svc.try_complete_pending_assignment(
            db_session, printer_id=printer.id, ams_id=0, tray_id=1, tray=GENERIC_TRAY
        )

        assert done is not None and done.id == first.id
        assert done.status == STATUS_COMPLETED
        assert (done.assigned_printer_id, done.assigned_ams_id, done.assigned_tray_id) == (printer.id, 0, 1)
        rows = await _slot_rows(db_session)
        assert [(r.spool_id, r.printer_id, r.ams_id, r.tray_id) for r in rows] == [(older.id, printer.id, 0, 1)]
        # The newer request is still waiting for the next tray.
        assert [r.spool_id for r in await svc.list_assignments(db_session)] == [newer.id]

    async def test_request_for_another_printer_is_ignored(self, db_session, spool_factory, printer_factory):
        p1 = await printer_factory(name="P2S")
        p2 = await printer_factory(name="X2D")
        spool = await spool_factory()
        await svc.create_pending_assignment(db_session, spool_id=spool.id, printer_id=p1.id, timeout_seconds=600)

        done = await svc.try_complete_pending_assignment(
            db_session, printer_id=p2.id, ams_id=0, tray_id=0, tray=GENERIC_TRAY
        )
        assert done is None
        assert await _slot_rows(db_session) == []

    async def test_any_printer_request_takes_first_loaded_tray(self, db_session, spool_factory, printer_factory):
        printer = await printer_factory()
        spool = await spool_factory()
        await svc.create_pending_assignment(db_session, spool_id=spool.id, printer_id=None, timeout_seconds=600)

        done = await svc.try_complete_pending_assignment(
            db_session, printer_id=printer.id, ams_id=1, tray_id=2, tray=GENERIC_TRAY
        )
        assert done is not None and done.assigned_printer_id == printer.id

    async def test_tagged_tray_must_match_a_tagged_spool(self, db_session, spool_factory, printer_factory):
        printer = await printer_factory()
        spool = await spool_factory(tray_uuid=UUID_A)
        await svc.create_pending_assignment(db_session, spool_id=spool.id, printer_id=printer.id, timeout_seconds=600)

        wrong = await svc.try_complete_pending_assignment(
            db_session, printer_id=printer.id, ams_id=0, tray_id=0, tray=_bambu_tray(UUID_B)
        )
        assert wrong is None
        assert await _slot_rows(db_session) == []

        right = await svc.try_complete_pending_assignment(
            db_session, printer_id=printer.id, ams_id=0, tray_id=0, tray=_bambu_tray(UUID_A.lower())
        )
        assert right is not None and right.status == STATUS_COMPLETED

    async def test_tag_owned_by_another_spool_is_left_to_rfid(self, db_session, spool_factory, printer_factory):
        printer = await printer_factory()
        await spool_factory(color_name="Owner", tray_uuid=UUID_A)
        waiting = await spool_factory(color_name="Waiting")
        await svc.create_pending_assignment(db_session, spool_id=waiting.id, printer_id=printer.id, timeout_seconds=600)

        done = await svc.try_complete_pending_assignment(
            db_session, printer_id=printer.id, ams_id=0, tray_id=0, tray=_bambu_tray(UUID_A)
        )

        assert done is None
        assert await _slot_rows(db_session) == []
        assert [r.spool_id for r in await svc.list_assignments(db_session)] == [waiting.id]

    async def test_unknown_tag_is_linked_to_untagged_waiting_spool(self, db_session, spool_factory, printer_factory):
        printer = await printer_factory()
        waiting = await spool_factory()
        await svc.create_pending_assignment(db_session, spool_id=waiting.id, printer_id=printer.id, timeout_seconds=600)

        done = await svc.try_complete_pending_assignment(
            db_session, printer_id=printer.id, ams_id=0, tray_id=3, tray=_bambu_tray(UUID_A, "04AABBCCDD112233")
        )

        assert done is not None and done.status == STATUS_COMPLETED
        await db_session.refresh(waiting)
        assert waiting.tray_uuid == UUID_A
        assert waiting.tag_uid == "04AABBCCDD112233"
        assert waiting.data_origin == "rfid_linked"

    async def test_archived_spool_request_is_skipped(self, db_session, spool_factory, printer_factory):
        printer = await printer_factory()
        spool = await spool_factory()
        await svc.create_pending_assignment(db_session, spool_id=spool.id, printer_id=printer.id, timeout_seconds=600)
        spool.archived_at = datetime.now(timezone.utc)
        await db_session.commit()

        done = await svc.try_complete_pending_assignment(
            db_session, printer_id=printer.id, ams_id=0, tray_id=0, tray=GENERIC_TRAY
        )
        assert done is None

    async def test_failed_assign_keeps_request_pending(self, db_session, spool_factory, printer_factory):
        printer = await printer_factory()
        spool = await spool_factory()
        row = await svc.create_pending_assignment(
            db_session, spool_id=spool.id, printer_id=printer.id, timeout_seconds=600
        )

        with patch("backend.app.api.routes.inventory.assign_spool", new=AsyncMock(side_effect=RuntimeError("boom"))):
            done = await svc.try_complete_pending_assignment(
                db_session, printer_id=printer.id, ams_id=0, tray_id=0, tray=GENERIC_TRAY
            )

        assert done is None
        await db_session.refresh(row)
        assert row.status == STATUS_PENDING

    async def test_completion_broadcasts_change_event(
        self, db_session, spool_factory, printer_factory, _quiet_broadcast
    ):
        printer = await printer_factory()
        spool = await spool_factory()
        await svc.create_pending_assignment(db_session, spool_id=spool.id, printer_id=printer.id, timeout_seconds=600)
        _quiet_broadcast.reset_mock()

        await svc.try_complete_pending_assignment(
            db_session, printer_id=printer.id, ams_id=0, tray_id=0, tray=GENERIC_TRAY
        )

        events = [c.args[0] for c in _quiet_broadcast.await_args_list if c.args[0].get("type") == svc.WS_EVENT]
        assert [e["event"] for e in events] == ["completed"]
        assert events[0]["spool_id"] == spool.id
        assert events[0]["assigned_tray_id"] == 0

    async def test_nothing_pending_is_a_cheap_no(self, db_session, printer_factory):
        printer = await printer_factory()
        assert (
            await svc.try_complete_pending_assignment(
                db_session, printer_id=printer.id, ams_id=0, tray_id=0, tray=GENERIC_TRAY
            )
            is None
        )
        assert (await db_session.execute(select(PendingSlotAssignment))).scalars().all() == []
