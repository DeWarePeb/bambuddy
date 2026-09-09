"""Integration tests for Prometheus Metrics API endpoint.

Tests the /api/v1/metrics endpoint for Prometheus scraping.
"""

from unittest.mock import patch

import pytest
from httpx import AsyncClient

from backend.app.services.bambu_mqtt import PrinterState


class TestMetricsAPI:
    """Integration tests for /api/v1/metrics endpoint."""

    # ========================================================================
    # Metrics endpoint access control
    # ========================================================================

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_metrics_disabled_returns_404(self, async_client: AsyncClient):
        """Verify metrics endpoint returns 404 when disabled."""
        # Ensure prometheus is disabled
        await async_client.put("/api/v1/settings/", json={"prometheus_enabled": False})

        response = await async_client.get("/api/v1/metrics")

        assert response.status_code == 404
        assert "not enabled" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_metrics_enabled_without_token(self, async_client: AsyncClient):
        """Verify metrics endpoint works when enabled without token."""
        # Enable prometheus without token
        await async_client.put("/api/v1/settings/", json={"prometheus_enabled": True, "prometheus_token": ""})

        response = await async_client.get("/api/v1/metrics")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/plain")

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_metrics_with_token_requires_auth(self, async_client: AsyncClient):
        """Verify metrics endpoint requires auth when token is set."""
        # Enable prometheus with token
        await async_client.put("/api/v1/settings/", json={"prometheus_enabled": True, "prometheus_token": "secret123"})

        # Request without auth
        response = await async_client.get("/api/v1/metrics")
        assert response.status_code == 401

        # Request with wrong token
        response = await async_client.get("/api/v1/metrics", headers={"Authorization": "Bearer wrongtoken"})
        assert response.status_code == 401

        # Request with correct token
        response = await async_client.get("/api/v1/metrics", headers={"Authorization": "Bearer secret123"})
        assert response.status_code == 200

    # ========================================================================
    # Metrics content validation
    # ========================================================================

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_metrics_format(self, async_client: AsyncClient):
        """Verify metrics are in Prometheus text format."""
        # Enable prometheus
        await async_client.put("/api/v1/settings/", json={"prometheus_enabled": True, "prometheus_token": ""})

        response = await async_client.get("/api/v1/metrics")

        assert response.status_code == 200
        content = response.text

        # Check for Prometheus format markers
        assert "# HELP" in content
        assert "# TYPE" in content

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_metrics_contains_expected_metrics(self, async_client: AsyncClient):
        """Verify expected metrics are present."""
        # Enable prometheus
        await async_client.put("/api/v1/settings/", json={"prometheus_enabled": True, "prometheus_token": ""})

        response = await async_client.get("/api/v1/metrics")

        assert response.status_code == 200
        content = response.text

        # Check for key metrics
        assert "bambuddy_printers_connected" in content
        assert "bambuddy_printers_total" in content
        assert "bambuddy_prints_total" in content
        assert "bambuddy_filament_used_grams" in content
        assert "bambuddy_print_time_seconds" in content
        assert "bambuddy_queue_pending" in content

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_metrics_printer_metrics_when_no_printers(self, async_client: AsyncClient):
        """Verify printer metrics work when no printers configured."""
        # Enable prometheus
        await async_client.put("/api/v1/settings/", json={"prometheus_enabled": True, "prometheus_token": ""})

        response = await async_client.get("/api/v1/metrics")

        assert response.status_code == 200
        content = response.text

        # Should still have system metrics
        assert "bambuddy_printers_total" in content
        assert "bambuddy_printers_connected" in content

    # ========================================================================
    # Settings persistence
    # ========================================================================

    # ========================================================================
    # Chamber gauge: optional hardware, so absent means no series (fork)
    # ========================================================================

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_klipper_without_a_chamber_emits_no_chamber_series(
        self, async_client: AsyncClient, printer_factory
    ):
        """A Klipper printer passes the model gate — the list is a list of Bambu
        models and a Voron will never be on it — so the gate cannot also stand in
        for "this machine has a chamber". Most Klipper printers have no chamber
        object configured at all, and a gauge at 0.0 does not read as "no sensor"
        on a dashboard; it reads as a chamber at freezing.
        """
        await async_client.put("/api/v1/settings/", json={"prometheus_enabled": True, "prometheus_token": ""})
        printer = await printer_factory(name="Voron", model="Voron 2.4", provider="klipper", api_url="http://v:7125")

        status = PrinterState()
        status.temperatures = {"nozzle": 27.0, "bed": 23.0}  # no chamber configured
        with patch("backend.app.api.routes.metrics.printer_manager") as mock_pm:
            mock_pm.get_all_statuses.return_value = {printer.id: status}
            body = (await async_client.get("/api/v1/metrics")).text

        assert "bambuddy_chamber_temp_celsius{" not in body

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_klipper_with_a_chamber_is_reported(self, async_client: AsyncClient, printer_factory):
        """And when the user has picked a sensor, it must come through — the
        whole point of making the model gate provider-aware."""
        await async_client.put("/api/v1/settings/", json={"prometheus_enabled": True, "prometheus_token": ""})
        printer = await printer_factory(name="Voron", model="Voron 2.4", provider="klipper", api_url="http://v:7125")

        status = PrinterState()
        status.temperatures = {"nozzle": 27.0, "bed": 23.0, "chamber": 30.3}
        with patch("backend.app.api.routes.metrics.printer_manager") as mock_pm:
            mock_pm.get_all_statuses.return_value = {printer.id: status}
            body = (await async_client.get("/api/v1/metrics")).text

        assert "bambuddy_chamber_temp_celsius{" in body
        assert "30.3" in body

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_bambu_model_without_a_chamber_is_still_skipped(
        self, async_client: AsyncClient, printer_factory
    ):
        """A P1P's invented chamber_temper must keep being dropped."""
        await async_client.put("/api/v1/settings/", json={"prometheus_enabled": True, "prometheus_token": ""})
        printer = await printer_factory(name="Mini", model="P1P")

        status = PrinterState()
        status.temperatures = {"nozzle": 27.0, "bed": 23.0, "chamber": 99.0}
        with patch("backend.app.api.routes.metrics.printer_manager") as mock_pm:
            mock_pm.get_all_statuses.return_value = {printer.id: status}
            body = (await async_client.get("/api/v1/metrics")).text

        assert "bambuddy_chamber_temp_celsius{" not in body

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_prometheus_settings_persist(self, async_client: AsyncClient):
        """Verify prometheus settings are saved correctly."""
        # Update settings
        await async_client.put("/api/v1/settings/", json={"prometheus_enabled": True, "prometheus_token": "mytoken"})

        # Read back settings
        response = await async_client.get("/api/v1/settings/")
        settings = response.json()

        assert settings["prometheus_enabled"] is True
        assert settings["prometheus_token"] == "mytoken"

        # Disable and verify
        await async_client.put("/api/v1/settings/", json={"prometheus_enabled": False})
        response = await async_client.get("/api/v1/settings/")
        settings = response.json()

        assert settings["prometheus_enabled"] is False
