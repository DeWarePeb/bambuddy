"""Voron patch series (B5): /api/v1/alerts/summary gathers maintenance due + low stock."""

from datetime import datetime

import pytest
from httpx import AsyncClient

from backend.app.api.routes.alerts import spool_is_low
from backend.app.models.spool import Spool


class TestSpoolIsLow:
    def test_uses_global_threshold_when_spool_has_none(self):
        spool = Spool(material="PLA", label_weight=1000, weight_used=850, low_stock_threshold_pct=None)
        is_low, remaining_g, remaining_pct = spool_is_low(spool, 20.0)
        assert is_low
        assert remaining_g == 150
        assert remaining_pct == 15.0

    def test_spool_override_wins(self):
        spool = Spool(material="PLA", label_weight=1000, weight_used=850, low_stock_threshold_pct=10)
        assert spool_is_low(spool, 20.0)[0] is False
        spool.low_stock_threshold_pct = 30
        assert spool_is_low(spool, 20.0)[0] is True

    def test_empty_label_weight_counts_as_low(self):
        spool = Spool(material="PLA", label_weight=0, weight_used=0)
        assert spool_is_low(spool, 20.0) == (True, 0.0, 0.0)


class TestAlertsSummaryAPI:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_summary_lists_low_spools_and_has_maintenance_sections(
        self, async_client: AsyncClient, printer_factory, db_session
    ):
        await printer_factory(name="Voron")
        db_session.add_all(
            [
                Spool(material="ABS", brand="Bambu Lab", color_name="Black", label_weight=1000, weight_used=920),
                Spool(material="PETG", color_name="Orange", label_weight=1000, weight_used=100),
                # Archived spools never count, however empty.
                Spool(
                    material="PLA", color_name="Old", label_weight=1000, weight_used=999, archived_at=datetime.utcnow()
                ),
            ]
        )
        await db_session.commit()

        response = await async_client.get("/api/v1/alerts/summary")
        assert response.status_code == 200
        data = response.json()

        assert set(data) >= {"maintenance_due", "maintenance_warning", "low_stock", "low_stock_threshold_pct", "total"}
        low = data["low_stock"]
        assert [s["material"] for s in low] == ["ABS"]
        assert low[0]["color_name"] == "Black"
        assert low[0]["remaining_g"] == 80
        assert low[0]["remaining_pct"] == 8.0
        assert isinstance(data["maintenance_due"], list)
        assert data["total"] == len(data["maintenance_due"]) + 1
