"""Read-only proxy for the Open Filament Database, used by the spool form.

The frontend cannot call api.openfilamentdatabase.org itself (CORS), and the
proxy keeps upstream errors from leaking as raw HTML: every failure is a
structured ``{"code", "message", "upstream_status"}`` detail.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import RequirePermissionIfAuthEnabled
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.models.settings import Settings
from backend.app.models.user import User
from backend.app.services.open_filament_database import (
    OpenFilamentDatabaseClient,
    OpenFilamentDatabaseError,
)

router = APIRouter(prefix="/open-filament-database", tags=["open-filament-database"])


async def _require_enabled(db: AsyncSession) -> None:
    """The lookup is opt-in (default off); refuse to call out when it is off."""
    result = await db.execute(select(Settings).where(Settings.key == "open_filament_database_enabled"))
    row = result.scalar_one_or_none()
    if row is None or row.value.lower() != "true":
        raise HTTPException(
            status_code=403,
            detail={"code": "ofdb_disabled", "message": "Open Filament Database lookup is disabled in Settings"},
        )


async def _call_ofdb(db: AsyncSession, method: str, *args: Any) -> Any:
    await _require_enabled(db)
    client = OpenFilamentDatabaseClient()
    try:
        return await getattr(client, method)(*args)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "ofdb_invalid_path", "message": str(exc)}) from exc
    except OpenFilamentDatabaseError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message, "upstream_status": exc.upstream_status},
        ) from exc


@router.get("/brands")
async def list_brands(
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_READ),
):
    """List brands known to Open Filament Database."""
    return await _call_ofdb(db, "get_brands")


@router.get("/brands/{brand_slug}")
async def get_brand(
    brand_slug: str,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_READ),
):
    """One brand and its material list."""
    return await _call_ofdb(db, "get_brand", brand_slug)


@router.get("/brands/{brand_slug}/materials/{material}/filaments")
async def list_material_filaments(
    brand_slug: str,
    material: str,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_READ),
):
    """Filament families for a brand/material pair."""
    return await _call_ofdb(db, "get_material", brand_slug, material)


@router.get("/search")
async def search_filaments(
    brand: str = Query(..., min_length=1, description="OFDB brand slug, e.g. elegoo"),
    material: str = Query(..., min_length=1, description="Material identifier, e.g. PLA"),
    q: str = Query("", description="Optional filament family search text"),
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_READ),
):
    """Search filament families within one brand + material (OFDB has no global search)."""
    return await _call_ofdb(db, "search_filaments", brand, material, q)


@router.get("/brands/{brand_slug}/materials/{material}/filaments/{filament_slug}")
async def get_filament(
    brand_slug: str,
    material: str,
    filament_slug: str,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_READ),
):
    """One filament family with its variants and spool-prefill fields."""
    return await _call_ofdb(db, "get_filament", brand_slug, material, filament_slug)


@router.get("/brands/{brand_slug}/materials/{material}/filaments/{filament_slug}/variants/{variant_slug}")
async def get_variant(
    brand_slug: str,
    material: str,
    filament_slug: str,
    variant_slug: str,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_READ),
):
    """One colour variant with sizes and the complete spool-prefill block."""
    return await _call_ofdb(db, "get_variant", brand_slug, material, filament_slug, variant_slug)
