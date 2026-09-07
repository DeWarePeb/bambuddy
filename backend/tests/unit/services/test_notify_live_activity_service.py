"""Tests for the Notify Live Activity lifecycle service (voron B9).

All Notify HTTP traffic is replaced by an AsyncMock client; nothing reaches
a real phone.
"""

import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from backend.app.models.notification import NotificationProvider
from backend.app.models.notification_live_activity import NotificationLiveActivity
from backend.app.models.printer import Printer
from backend.app.services.notify_live_activity_client import NotifyLiveActivityError
from backend.app.services.notify_live_activity_service import NotifyLiveActivityService

PRINTER_ID = 7


@pytest.fixture
def live_config():
    return {
        "device_id": "DEVICE123",
        "device_token": "token",
        "base_url": "https://push.getnotifyapp.com",
        "live_activities_enabled": True,
        "live_activity_end_keep_for_seconds": 300,
    }


@pytest.fixture
async def printer(db_session):
    row = Printer(id=PRINTER_ID, name="Workshop P1S", serial_number="SN-B9", ip_address="10.0.0.9", access_code="1")
    db_session.add(row)
    await db_session.commit()
    return row


@pytest.fixture
async def notify_provider(db_session, printer, live_config):
    provider = NotificationProvider(
        name="Notify",
        provider_type="notify",
        enabled=True,
        config=json.dumps(live_config),
        on_print_start=False,
        on_print_complete=False,
        on_print_failed=False,
        on_print_stopped=False,
        on_print_progress=False,
    )
    db_session.add(provider)
    await db_session.commit()
    await db_session.refresh(provider)
    return provider


def _client(activity_id="activity-123"):
    client = AsyncMock()
    client.start = AsyncMock(return_value=activity_id)
    client.update = AsyncMock()
    client.end = AsyncMock()
    return client


async def _activities(db_session):
    return (await db_session.scalars(select(NotificationLiveActivity).order_by(NotificationLiveActivity.id))).all()


@pytest.mark.asyncio
async def test_print_start_creates_live_activity_row(db_session, notify_provider):
    client = _client()
    service = NotifyLiveActivityService(client_factory=lambda config: client)

    await service.on_print_start(
        db_session,
        printer_id=PRINTER_ID,
        printer_name="Workshop P1S",
        data={"filename": "dragon.3mf", "subtask_id": "task-1", "remaining_time": 5400},
    )

    client.start.assert_awaited_once()
    (activity,) = await _activities(db_session)
    assert activity.provider_id == notify_provider.id
    assert activity.printer_id == PRINTER_ID
    assert activity.activity_id == "activity-123"
    assert activity.subtask_id == "task-1"
    assert activity.filename == "dragon.3mf"
    assert activity.state == "active"


@pytest.mark.asyncio
async def test_print_start_is_skipped_when_live_activities_are_off(db_session, notify_provider):
    notify_provider.config = json.dumps({"device_id": "D", "device_token": "t", "live_activities_enabled": "false"})
    await db_session.commit()
    client = _client()
    service = NotifyLiveActivityService(client_factory=lambda config: client)

    await service.on_print_start(db_session, printer_id=PRINTER_ID, printer_name="P1S", data={"filename": "a.3mf"})

    client.start.assert_not_awaited()
    assert await _activities(db_session) == []


@pytest.mark.asyncio
async def test_print_start_respects_provider_printer_scope(db_session, notify_provider):
    notify_provider.printer_id = PRINTER_ID + 1
    await db_session.commit()
    client = _client()
    service = NotifyLiveActivityService(client_factory=lambda config: client)

    await service.on_print_start(db_session, printer_id=PRINTER_ID, printer_name="P1S", data={"filename": "a.3mf"})

    client.start.assert_not_awaited()


@pytest.mark.asyncio
async def test_print_start_uses_native_countdown_from_config(db_session, notify_provider):
    notify_provider.config = json.dumps(
        {
            "device_id": "DEVICE123",
            "device_token": "token",
            "live_activities_enabled": True,
            "live_activity_native_tile_countdown": "true",
        }
    )
    await db_session.commit()
    client = _client()
    service = NotifyLiveActivityService(client_factory=lambda config: client)

    await service.on_print_start(
        db_session,
        printer_id=PRINTER_ID,
        printer_name="Workshop P1S",
        data={"filename": "dragon.3mf", "remaining_time": 5400, "layer_num": 8, "total_layers": 120},
    )

    payload = client.start.await_args.args[0]
    assert payload["body"] == "6% · L8/120 · dragon"
    assert payload["endsIn"] == 5400
    assert payload["trailing"] is None


@pytest.mark.asyncio
async def test_progress_update_is_paced_for_routine_frames(db_session, notify_provider):
    client = _client()
    service = NotifyLiveActivityService(client_factory=lambda config: client)
    await service.on_print_start(
        db_session,
        printer_id=PRINTER_ID,
        printer_name="P1S",
        data={"filename": "dragon.3mf", "subtask_id": "task-1", "remaining_time": 5400, "layer_num": 5},
    )

    # A routine frame seconds after the last one is coalesced, not sent.
    await service.on_print_progress(
        db_session,
        printer_id=PRINTER_ID,
        printer_name="P1S",
        filename="dragon.3mf",
        progress=6,
        remaining_time=5300,
        subtask_id="task-1",
        layer_num=6,
        total_layers=120,
    )
    client.update.assert_not_awaited()

    # Once the interval has elapsed the next frame goes out.
    (activity,) = await _activities(db_session)
    activity.updated_at = datetime.utcnow() - timedelta(seconds=61)
    await db_session.commit()
    await service.on_print_progress(
        db_session,
        printer_id=PRINTER_ID,
        printer_name="P1S",
        filename="dragon.3mf",
        progress=7,
        remaining_time=5200,
        subtask_id="task-1",
        layer_num=7,
        total_layers=120,
    )
    client.update.assert_awaited_once()
    assert client.update.await_args.args[0] == "activity-123"
    assert client.update.await_args.args[1]["status"] == "7%"
    await db_session.refresh(activity)
    assert activity.last_progress == 7
    assert activity.last_layer_num == 7


@pytest.mark.asyncio
async def test_progress_update_honours_configured_interval_but_not_below_apple_floor(db_session, notify_provider):
    notify_provider.config = json.dumps(
        {
            "device_id": "D",
            "device_token": "t",
            "live_activities_enabled": True,
            "live_activity_update_interval_seconds": 5,  # below the 15s floor
        }
    )
    await db_session.commit()
    client = _client()
    service = NotifyLiveActivityService(client_factory=lambda config: client)
    await service.on_print_start(
        db_session, printer_id=PRINTER_ID, printer_name="P1S", data={"filename": "a.3mf", "layer_num": 3}
    )
    (activity,) = await _activities(db_session)
    activity.updated_at = datetime.utcnow() - timedelta(seconds=10)
    await db_session.commit()

    await service.on_print_progress(
        db_session, printer_id=PRINTER_ID, printer_name="P1S", filename="a.3mf", progress=4, layer_num=4
    )

    client.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_final_layer_and_pause_bypass_pacing(db_session, notify_provider):
    client = _client()
    service = NotifyLiveActivityService(client_factory=lambda config: client)
    await service.on_print_start(
        db_session,
        printer_id=PRINTER_ID,
        printer_name="P1S",
        data={"filename": "a.3mf", "layer_num": 50, "total_layers": 100, "remaining_time": 600},
    )

    await service.on_print_progress(
        db_session,
        printer_id=PRINTER_ID,
        printer_name="P1S",
        filename="a.3mf",
        progress=50,
        remaining_time=600,
        layer_num=50,
        total_layers=100,
        state="PAUSE",
    )
    assert client.update.await_count == 1
    assert client.update.await_args.args[1]["status"] == "Paused · 50%"

    await service.on_print_progress(
        db_session,
        printer_id=PRINTER_ID,
        printer_name="P1S",
        filename="a.3mf",
        progress=99,
        remaining_time=30,
        layer_num=100,
        total_layers=100,
    )
    assert client.update.await_count == 2


@pytest.mark.asyncio
async def test_progress_without_activity_recovers_by_starting_one(db_session, notify_provider):
    client = _client("recovered-1")
    service = NotifyLiveActivityService(client_factory=lambda config: client)

    await service.on_print_progress(
        db_session,
        printer_id=PRINTER_ID,
        printer_name="P1S",
        filename="dragon.3mf",
        progress=42,
        remaining_time=1000,
        subtask_id="task-9",
    )

    client.start.assert_awaited_once()
    (activity,) = await _activities(db_session)
    assert activity.activity_id == "recovered-1"
    assert activity.subtask_id == "task-9"
    assert activity.last_progress == 42


@pytest.mark.asyncio
async def test_gone_activity_is_replaced_on_update(db_session, notify_provider):
    client = _client("activity-2")
    service = NotifyLiveActivityService(client_factory=lambda config: client)
    await service.on_print_start(
        db_session, printer_id=PRINTER_ID, printer_name="P1S", data={"filename": "a.3mf", "layer_num": 2}
    )
    client.start.reset_mock()
    client.update = AsyncMock(side_effect=NotifyLiveActivityError("gone", status_code=410))

    await service.on_print_progress(
        db_session, printer_id=PRINTER_ID, printer_name="P1S", filename="a.3mf", progress=100, layer_num=3
    )

    client.start.assert_awaited_once()
    old, new = await _activities(db_session)
    assert old.state == "ended"
    assert new.state == "active"
    assert new.activity_id == "activity-2"


@pytest.mark.asyncio
async def test_print_end_ends_activity_with_terminal_state(db_session, notify_provider):
    client = _client()
    service = NotifyLiveActivityService(client_factory=lambda config: client)
    await service.on_print_start(
        db_session, printer_id=PRINTER_ID, printer_name="P1S", data={"filename": "a.3mf", "subtask_id": "t1"}
    )

    await service.on_print_end(
        db_session,
        printer_id=PRINTER_ID,
        printer_name="P1S",
        status="failed",
        data={"filename": "a.3mf", "subtask_id": "t1", "failure_reason": "Filament runout"},
    )

    client.end.assert_awaited_once()
    args, kwargs = client.end.await_args
    assert args[0] == "activity-123"
    assert args[1]["status"] == "Failed · Filament runout"
    assert kwargs["keep_for_seconds"] == 300
    (activity,) = await _activities(db_session)
    assert activity.state == "ended"
    assert activity.ended_at is not None


@pytest.mark.asyncio
async def test_new_print_replaces_activity_of_previous_print(db_session, notify_provider):
    client = _client("activity-b")
    service = NotifyLiveActivityService(client_factory=lambda config: client)
    await service.on_print_start(
        db_session, printer_id=PRINTER_ID, printer_name="P1S", data={"filename": "a.3mf", "subtask_id": "t1"}
    )
    await service.on_print_start(
        db_session, printer_id=PRINTER_ID, printer_name="P1S", data={"filename": "b.3mf", "subtask_id": "t2"}
    )

    client.end.assert_awaited_once()
    assert client.end.await_args.args[1]["status"] == "Stopped · Replaced by a new print"
    first, second = await _activities(db_session)
    assert first.state == "ended"
    assert second.state == "active"
    assert second.subtask_id == "t2"

    # Same print again: reuse, no new start.
    client.start.reset_mock()
    await service.on_print_start(
        db_session, printer_id=PRINTER_ID, printer_name="P1S", data={"filename": "b.3mf", "subtask_id": "t2"}
    )
    client.start.assert_not_awaited()


@pytest.mark.asyncio
async def test_keepalive_ends_activity_when_printer_is_idle_and_repairs_missing_one(db_session, notify_provider):
    client = _client("repaired-1")
    idle = SimpleNamespace(connected=True, state="IDLE")
    running = SimpleNamespace(
        connected=True,
        state="RUNNING",
        subtask_name="bowl",
        gcode_file="bowl.3mf",
        subtask_id="t5",
        progress=40,
        remaining_time=30,
        layer_num=40,
        total_layers=100,
        stg_cur=0,
    )
    status = {"value": idle}
    service = NotifyLiveActivityService(
        client_factory=lambda config: client,
        status_getter=lambda printer_id: status["value"],
        printer_name_getter=lambda printer_id: "P1S",
    )
    await service.on_print_start(db_session, printer_id=PRINTER_ID, printer_name="P1S", data={"filename": "a.3mf"})
    client.start.reset_mock()

    # Printer went idle without a print-end edge: keepalive closes the tile.
    await service.keepalive_once(db_session)
    client.end.assert_awaited_once()
    assert client.end.await_args.args[1]["status"] == "Stopped · Printer is no longer printing"
    (ended,) = await _activities(db_session)
    assert ended.state == "ended"

    # Printer is running but has no tile (missed start): keepalive creates one.
    status["value"] = running
    await service.keepalive_once(db_session)
    client.start.assert_awaited_once()
    payload = client.start.await_args.args[0]
    assert payload["body"] == "40% · L40/100 · bowl"
    assert payload["trailing"] == "30:00"
    _, repaired = await _activities(db_session)
    assert repaired.state == "active"
    assert repaired.activity_id == "repaired-1"
    assert repaired.subtask_id == "t5"

    # Next keepalive tick refreshes the running tile.
    await service.keepalive_once(db_session)
    client.update.assert_awaited_once_with("repaired-1", client.update.await_args.args[1])
    assert client.update.await_args.args[1]["status"] == "40%"
