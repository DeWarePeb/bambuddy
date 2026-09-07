# Modification notice (AGPL-3.0 section 5(a))

This repository (`DeWarePeb/bambuddy`, branch `voron`, shown in the UI as **Printhok**) is a modified
version of [maziggy/bambuddy](https://github.com/maziggy/bambuddy). The upstream license (AGPL-3.0),
copyright and history are kept unchanged. The backend, service name, install paths and HTTP API are
upstream's; the modifications are a thin patch series rebased onto upstream release tags.

Base: upstream tag `v1.2.5.5`.

## What was changed

- **Klipper / Moonraker printers** (`provider = "klipper"`): `backend/app/services/moonraker_client.py`
  (new) is a Moonraker-backed client with the same surface as upstream's Bambu MQTT client;
  `backend/app/services/moonraker_dispatch.py` (new) unpacks plate G-code from a sliced 3MF and uploads
  it to Moonraker. Hooks in `backend/app/services/printer_manager.py`, `print_scheduler.py`,
  `backend/app/api/routes/printers.py`, `backend/app/models/printer.py`, `backend/app/schemas/printer.py`,
  `backend/app/core/database.py` (migration) and `backend/app/main.py`.
- **Happy Hare MMU**: gates reported as AMS units, gate map written with `MMU_GATE_MAP`, load/unload,
  tool-to-gate map at dispatch, per-tool filament booking (`backend/app/services/usage_tracker.py`).
- **Raw `.gcode` in the library** once a Klipper printer exists, with slicer-comment metadata
  (`backend/app/services/gcode_metadata.py`, new; `backend/app/api/routes/library.py`).
- **Archive for prints started outside Bambuddy on a Klipper printer** fetches the G-code from Moonraker
  (`backend/app/services/klipper_archive.py`, new).
- **Frontend**: printer type toggle with Moonraker fields, Klipper badge, per-gate MMU slots, file manager
  over Moonraker with "start on printer", and a display brand loaded from `/brand.json`
  (`frontend/src/brand.ts`, new; `frontend/public/img/brand.json`, `frontend/public/img/printhok_logo_*.svg`).
- Translation keys for the above in all fourteen locales.

The full list of modifications is the commit range `v1.2.5.5..voron` in this repository's history;
every commit touching upstream files carries a message explaining the change.
