"""Selling price for a finished print (fork).

Upstream's ``finance`` module is a chargeback system: cost centres, budgets,
per-user wallets, a print charged against a balance. It answers "who owes what
for the machine time", which is the question a makerspace or a school asks.

A shop asks the other question — what should this print sell for — and nothing
upstream answers it. The inputs are already there: every archive row carries the
filament ``cost`` and the ``energy_cost`` its smart plug measured, and
``print_time_seconds`` says how long the machine was busy.

The price itself is computed in the frontend (``utils/printPrice.ts``) so the
number reacts to a settings change without a round trip. What cannot be computed
there is the reference this module provides: the median unit cost across recent
prints, which is what makes a markup figure mean something. The list the browser
holds is paginated and filtered, so a median taken from it would be a median of
whatever page you were looking at.

Median, not mean: one resin-priced exotic filament or one nine-hour failure
would drag an average somewhere unhelpful, and the number exists to answer "what
does a typical print of mine cost".
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.archive import PrintArchive


def median(values: list[float]) -> float | None:
    """Middle value, averaging the two middle ones for an even count.

    Returns None for an empty list rather than raising: "no prints yet" is a
    normal state for a fresh install, not an error.
    """
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def unit_cost(cost: float | None, energy_cost: float | None) -> float | None:
    """What one print cost to make, before any labour or markup.

    None only when neither figure is known — a print with filament cost but no
    energy reading is still worth counting, with energy treated as zero, because
    a plug without an energy meter is a common setup and dropping those prints
    would silently narrow the sample.
    """
    if cost is None and energy_cost is None:
        return None
    return (cost or 0.0) + (energy_cost or 0.0)


async def recent_unit_costs(db: AsyncSession, days: int = 90) -> list[float]:
    """Unit cost of every completed, non-deleted print in the window."""
    since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
    result = await db.execute(
        select(PrintArchive.cost, PrintArchive.energy_cost).where(
            PrintArchive.status == "completed",
            PrintArchive.deleted_at.is_(None),
            PrintArchive.created_at >= since,
        )
    )
    costs = []
    for cost, energy_cost in result.all():
        value = unit_cost(cost, energy_cost)
        # A zero-cost row means the filament was never priced, not that the
        # print was free; counting it would pull the median toward zero.
        if value is not None and value > 0:
            costs.append(value)
    return costs
