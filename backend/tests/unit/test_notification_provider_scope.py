"""Voron patch series: notification providers narrowed to a set of printers."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.app.models.notification import NotificationProvider
from backend.app.services.notification_service import NotificationService


def _provider(printer_id=None, printer_ids=None):
    return NotificationProvider(name="p", provider_type="ntfy", printer_id=printer_id, printer_ids=printer_ids)


class TestCoversPrinter:
    def test_unscoped_provider_covers_everything(self):
        assert _provider().covers_printer(1)
        assert _provider().covers_printer(None)

    def test_legacy_single_printer_still_works(self):
        p = _provider(printer_id=2)
        assert p.covers_printer(2)
        assert not p.covers_printer(3)

    def test_printer_list_wins_over_legacy_id(self):
        p = _provider(printer_id=9, printer_ids="[1, 3]")
        assert p.covers_printer(1)
        assert p.covers_printer(3)
        assert not p.covers_printer(9)
        assert p.scoped_printer_ids() == [1, 3]

    def test_event_without_printer_reaches_every_provider(self):
        assert _provider(printer_ids="[1]").covers_printer(None)

    @pytest.mark.parametrize("raw", ["", "[]", "not json", '{"a": 1}', '["x"]'])
    def test_unreadable_list_means_not_narrowed(self, raw):
        p = _provider(printer_ids=raw)
        assert p.scoped_printer_ids() == []
        assert p.covers_printer(5)


class TestGetProvidersForEvent:
    @pytest.mark.asyncio
    async def test_filters_by_printer_list_after_query(self):
        service = NotificationService()
        everyone = _provider()
        voron_only = _provider(printer_ids="[2]")
        shop_only = _provider(printer_ids="[1, 3]")
        for p in (everyone, voron_only, shop_only):
            p.enabled = True
            p.on_print_complete = True

        db = MagicMock()
        db.no_autoflush = MagicMock()
        result = MagicMock()
        result.scalars.return_value.all.return_value = [everyone, voron_only, shop_only]
        db.execute = AsyncMock(return_value=result)

        got = await service._get_providers_for_event(db, "on_print_complete", 2)
        assert got == [everyone, voron_only]

        got = await service._get_providers_for_event(db, "on_print_complete", None)
        assert got == [everyone, voron_only, shop_only]
