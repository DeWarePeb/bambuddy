# Open Filament Database lookup (voron B6)

When **Settings → Filament checks → Search spools via Open Filament Database** is on,
the Add Spool form shows an OFDB panel above the filament fields: pick a brand,
a material, a filament family and a colour variant, and the form is prefilled with
brand, material, subtype, colour name + hex, label weight, empty spool weight (when
OFDB lists it), nozzle temperatures and the Bambu Studio preset id/name when the
entry carries one. Everything stays editable before saving. Spools created this way
get `data_origin = "openfilamentdatabase"` and may be saved without a slicer preset.

The setting is off by default because the backend calls an external API
(`https://api.openfilamentdatabase.org/api/v1`). The browser never calls it directly.

Ported from PrintBuddy (vmhomelab) commits 27b18a1b, 8246f7b1, b231ffd9, e12217e7,
9f917031, 1af0e09c, 256d33c4, rewritten against Bambuddy 1.2.5.5.

## Backend proxy

`backend/app/services/open_filament_database.py` (client + normalisation) and
`backend/app/api/routes/open_filament_database.py`. All routes are read-only,
require `inventory:read` when auth is enabled, and return **403 `ofdb_disabled`**
while the setting is off.

```text
GET /api/v1/open-filament-database/brands
GET /api/v1/open-filament-database/brands/{brand_slug}
GET /api/v1/open-filament-database/brands/{brand_slug}/materials/{MATERIAL}/filaments
GET /api/v1/open-filament-database/brands/{brand_slug}/materials/{MATERIAL}/filaments/{filament_slug}
GET /api/v1/open-filament-database/brands/{brand_slug}/materials/{MATERIAL}/filaments/{filament_slug}/variants/{variant_slug}
GET /api/v1/open-filament-database/search?brand={brand_slug}&material={MATERIAL}&q={text}
```

`/search` filters within one brand + material; OFDB is a static JSON tree and has
no global text search. Upstream failures become structured details:
404 → `ofdb_not_found`, network/timeout/5xx → 503 `ofdb_unavailable`,
non-JSON → 502 `ofdb_bad_response`, bad slug → 422 `ofdb_invalid_path`.

## Field mapping (`spool_prefill`)

| OFDB | Spool field |
| --- | --- |
| brand `name` | `brand` |
| material `material` | `material` |
| filament `name` minus the material word | `subtype` (`PLA Basic` → `Basic`) |
| filament `min/max_print_temperature` | `nozzle_temp_min` / `nozzle_temp_max` |
| filament `slicer_settings.bambustudio.id` (else orcaslicer, …) | `slicer_filament` |
| same block `profile_name` | `slicer_filament_name` |
| variant `name` | `color_name` |
| variant `color_hex` `#RRGGBB` | `rgba` `RRGGBBFF` |
| preferred size `filament_weight` (current, not a refill, 1 kg first) | `label_weight` (default 1000) |
| preferred size `empty_spool_weight` when present | `core_weight` |

OFDB carries purchase links but no prices, so `cost_per_kg` is not prefilled.

Smoke test once enabled:

```bash
curl 'http://localhost:8000/api/v1/open-filament-database/search?brand=elegoo&material=PLA&q=matte'
curl 'http://localhost:8000/api/v1/open-filament-database/brands/bambu_lab/materials/PLA/filaments/basic/variants/black'
```
