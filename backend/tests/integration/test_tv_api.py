"""Integration tests for the token-authenticated TV feed (voron B10).

Same shape as ``test_camwall_api``: the interesting cases are the negative
ones. A TV token is deliberately *wider* than a Cam Wall token — these tiles
name the file on the bed and the spool feeding it — so the tests pin down that
the widening went one way only: a ``tv`` token reaches the TV feed and the
camera snapshots, and no other scope reaches the TV feed.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def _setup_admin(async_client: AsyncClient, *, suffix: str) -> str:
    await async_client.post(
        "/api/v1/auth/setup",
        json={
            "auth_enabled": True,
            "admin_username": f"tvadmin{suffix}",
            "admin_password": "AdminPass1!",
        },
    )
    login = await async_client.post(
        "/api/v1/auth/login",
        json={"username": f"tvadmin{suffix}", "password": "AdminPass1!"},
    )
    return login.json()["access_token"]


async def _mint(async_client: AsyncClient, jwt: str, *, scope: str, name: str = "wall") -> str:
    response = await async_client.post(
        "/api/v1/auth/tokens",
        headers={"Authorization": f"Bearer {jwt}"},
        json={"name": name, "expires_in_days": 30, "scope": scope},
    )
    assert response.status_code == 201, response.text
    assert response.json()["scope"] == scope
    return response.json()["token"]


@pytest.fixture
async def printer_row(db_session):
    """Insert the printer straight into the DB — POST /printers probes the real
    device first, and there is none on the other end of a test run.
    """
    from backend.app.models.printer import Printer

    printer = Printer(
        name="Wall P1S",
        ip_address="192.168.1.77",
        access_code="12345678",
        serial_number="01P00A000000001",
        model="P1S",
        location="Workshop",
    )
    db_session.add(printer)
    await db_session.commit()
    return printer


class TestTvFeedAuth:
    async def test_no_token_is_rejected(self, async_client: AsyncClient):
        await _setup_admin(async_client, suffix="_notoken")
        response = await async_client.get("/api/v1/tv/printers")
        assert response.status_code == 401

    async def test_garbage_token_is_rejected(self, async_client: AsyncClient):
        await _setup_admin(async_client, suffix="_garbage")
        response = await async_client.get("/api/v1/tv/printers?token=bblt_aaaaaaaa_nope")
        assert response.status_code == 401

    @pytest.mark.parametrize("scope", ["camera_stream", "camwall", "overlay"])
    async def test_other_scopes_cannot_reach_the_feed(self, async_client: AsyncClient, scope: str):
        """The point of the separate scope.

        A Cam Wall token was handed out on the promise that the wall never
        names the part; an overlay token was granted for one printer. Shipping
        /tv must not retroactively widen either of them.
        """
        jwt = await _setup_admin(async_client, suffix=f"_wrong_{scope}")
        other_token = await _mint(async_client, jwt, scope=scope)

        response = await async_client.get(f"/api/v1/tv/printers?token={other_token}")
        assert response.status_code == 401

    async def test_tv_token_reaches_the_feed(self, async_client: AsyncClient, printer_row):
        jwt = await _setup_admin(async_client, suffix="_rightscope")
        tv_token = await _mint(async_client, jwt, scope="tv")

        response = await async_client.get(f"/api/v1/tv/printers?token={tv_token}")
        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body) == 1
        assert body[0]["name"] == "Wall P1S"

    async def test_revoked_tv_token_is_rejected(self, async_client: AsyncClient):
        jwt = await _setup_admin(async_client, suffix="_revoked")
        created = await async_client.post(
            "/api/v1/auth/tokens",
            headers={"Authorization": f"Bearer {jwt}"},
            json={"name": "wall", "expires_in_days": 30, "scope": "tv"},
        )
        tv_token = created.json()["token"]
        await async_client.delete(
            f"/api/v1/auth/tokens/{created.json()['id']}",
            headers={"Authorization": f"Bearer {jwt}"},
        )

        response = await async_client.get(f"/api/v1/tv/printers?token={tv_token}")
        assert response.status_code == 401

    async def test_tv_token_does_not_open_the_camwall_feed(self, async_client: AsyncClient):
        """And the other way round: a new scope must not quietly inherit the
        older feeds either.
        """
        from backend.app.core.auth import verify_camwall_token, verify_overlay_token

        jwt = await _setup_admin(async_client, suffix="_narrow")
        tv_token = await _mint(async_client, jwt, scope="tv")

        assert await verify_camwall_token(tv_token) is False
        assert await verify_overlay_token(tv_token) is False


class TestTvFeedPayload:
    async def test_payload_withholds_secrets(self, async_client: AsyncClient, printer_row):
        """A URL taped to a screen must not disclose printer credentials.

        Unlike the Cam Wall the filename *is* served — that is what the scope
        buys — but serial, IP and access code never are.
        """
        jwt = await _setup_admin(async_client, suffix="_payload")
        tv_token = await _mint(async_client, jwt, scope="tv")

        response = await async_client.get(f"/api/v1/tv/printers?token={tv_token}")
        assert response.status_code == 200
        entry = response.json()[0]

        for leaked in ("serial_number", "ip_address", "access_code", "api_url", "auth_token"):
            assert leaked not in entry, f"{leaked} must not be served to a wall token"

        assert set(entry) == {
            "id",
            "name",
            "model",
            "location",
            "provider",
            "external_camera_enabled",
            "camera_rotation",
            "connected",
            "state",
            "current_print",
            "subtask_name",
            "gcode_file",
            "progress",
            "remaining_time",
            "layer_num",
            "total_layers",
            "hms_errors",
            "tray",
        }

    async def test_disconnected_printer_reports_connected_false(self, async_client: AsyncClient, printer_row):
        """No MQTT client runs in tests, so the printer has no state at all —
        the tile must render as offline rather than blank.
        """
        jwt = await _setup_admin(async_client, suffix="_offline")
        tv_token = await _mint(async_client, jwt, scope="tv")

        entry = (await async_client.get(f"/api/v1/tv/printers?token={tv_token}")).json()[0]
        assert entry["connected"] is False
        assert entry["state"] is None
        assert entry["hms_errors"] == []
        assert entry["tray"] is None

    async def test_inactive_printers_are_left_out(self, async_client: AsyncClient, db_session, printer_row):
        """The signed-in page filters on is_active; the feed does it server-side
        so both modes show the same wall.
        """
        from backend.app.models.printer import Printer

        db_session.add(
            Printer(
                name="Retired A1",
                ip_address="192.168.1.78",
                access_code="12345679",
                serial_number="01P00A000000002",
                model="A1",
                is_active=False,
            )
        )
        await db_session.commit()

        jwt = await _setup_admin(async_client, suffix="_inactive")
        tv_token = await _mint(async_client, jwt, scope="tv")

        body = (await async_client.get(f"/api/v1/tv/printers?token={tv_token}")).json()
        assert [entry["name"] for entry in body] == ["Wall P1S"]


class TestTvTokenReachesTheVideo:
    """A wall that can list the tiles but not fill them is useless — the same
    token has to satisfy the camera-stream gate.
    """

    async def test_tv_token_passes_the_camera_stream_gate(self, async_client: AsyncClient):
        from backend.app.core.auth import verify_camera_stream_token

        jwt = await _setup_admin(async_client, suffix="_video")
        tv_token = await _mint(async_client, jwt, scope="tv")

        assert await verify_camera_stream_token(tv_token) is True
