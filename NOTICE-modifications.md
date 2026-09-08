# Modification notice (AGPL-3.0 section 5(a))

This repository (`DeWarePeb/bambuddy`, branches `main` and `voron`, shown in the UI as **Printhok**)
is a modified version of [maziggy/bambuddy](https://github.com/maziggy/bambuddy). The upstream
license (AGPL-3.0), copyright and history are kept unchanged.

**Base:** upstream `main` at `9b2c49d8`, immediately after tag `v1.2.5.5`.
**Form:** a rebased patch series, one commit per subject. The complete list of modifications is the
commit range `9b2c49d8..voron`; every commit touching an upstream file explains the change in its
message.

The backend, the service name (`bambuddy`), the install paths and the HTTP API remain upstream's. The
modifications fall into three groups.

## 1. Klipper / Moonraker support (fork-original)

Printers with `provider = "klipper"` are driven over Moonraker instead of Bambu's MQTT.

- **New:** `backend/app/services/moonraker_client.py` — a Moonraker-backed client duck-typed against
  upstream's `BambuMQTTClient` (same `state: PrinterState`, same methods), polling
  `/printer/objects/query`.
- **New:** `backend/app/services/moonraker_dispatch.py` — unpacks plate G-code from a sliced 3MF and
  uploads it to Moonraker.
- **New:** `backend/app/services/gcode_metadata.py` — reads print time, filament, layers and
  temperatures out of slicer comments in a raw `.gcode` file.
- **New:** `backend/app/services/klipper_archive.py` — fetches the G-code back from Moonraker for
  prints that were started outside Bambuddy.
- **Modified hooks:** `backend/app/services/printer_manager.py`, `print_scheduler.py`,
  `usage_tracker.py`, `backend/app/api/routes/printers.py`, `library.py`,
  `backend/app/models/printer.py`, `backend/app/schemas/printer.py`, `backend/app/core/database.py`
  (column migration), `backend/app/main.py` (no FTPS archive sweep for Klipper printers).
- **Happy Hare MMU:** gates reported as AMS units, gate map written with `MMU_GATE_MAP`, load and
  unload, tool-to-gate map at dispatch, per-tool filament booking.
- **Frontend:** printer type toggle with Moonraker fields, Klipper badge, per-gate MMU slots, a file
  manager over Moonraker with "start on printer".
- Translation keys for the above in all fourteen locales.

## 2. Features ported from vmhomelab/printbuddy

[vmhomelab/printbuddy](https://github.com/vmhomelab/printbuddy) is itself an AGPL-3.0 fork of
Bambuddy. Eleven of its features were reimplemented here against this codebase (not merged):
"print almost done" notifications, per-provider printer subsets, multi-word inventory search, project
progress counts, a combined low-stock and maintenance alert banner, Open Filament Database lookup,
Moonraker subnet discovery, pending spool-to-slot assignments, the Notify (iOS) Live Activity
provider, and the `/tv` kiosk wall.

`backend/app/services/notify_live_activity_client.py` and `notify_live_activity_content.py` follow
Printbuddy's implementation closely; endpoints and field names are identical, with `groupType` changed
from `printbuddy` to `bambuddy` and `iconUrl` omitted.

Per-feature detail, including the files each one touches, is in
[`docs/printhok/features.md`](docs/printhok/features.md).

## 3. Branding

A display name and logo loaded at runtime from `/brand.json`: `frontend/src/brand.ts` (new),
`frontend/public/img/brand.json`, `frontend/public/img/printhok_logo_{dark,light}.svg`. Used by the
sidebar, login, setup, stream overlay and tab title. Upstream's name remains in the backend, the
service, the install paths, the API and the translated strings.
