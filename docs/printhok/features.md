# Printhok feature register

Every change this fork makes to [maziggy/bambuddy](https://github.com/maziggy/bambuddy), what it is
for, which files carry it, and what is still open. This is the checklist for a rebase: after moving
the series onto a new upstream base, everything below must still be true.

Base: upstream `main` at `9b2c49d8`. Commits are given as they exist on `voron` today; a rebase
rewrites the SHAs but not the order or the subjects.

**Legend:** ✅ done and in use · ⚠️ done with a known gap · ⬜ deliberately not done

---

## Part A — Klipper and Moonraker (fork-original)

### A0 · A Klipper printer is just a printer

| | |
|---|---|
| **Status** | ✅ |
| **Commits** | `e1b216d9` model · `3f87c069` client · `40308741` manager · `54de892a` routes + scheduler · `1f2b48f2` frontend · `eff07678` tests · `0af92dca` no FTPS sweep · `830717a1` chamber light |
| **New files** | `backend/app/services/moonraker_client.py`, `backend/app/services/moonraker_dispatch.py`, `backend/tests/unit/services/test_moonraker_client.py` |
| **Touched upstream** | `models/printer.py`, `schemas/printer.py`, `core/database.py`, `services/printer_manager.py`, `services/print_scheduler.py`, `api/routes/printers.py`, `main.py`, `frontend/src/api/client.ts`, `frontend/src/pages/PrintersPage.tsx` |

**Why it is built this way.** `MoonrakerClient` exposes the same `state: PrinterState` and the same
methods as upstream's `BambuMQTTClient`. Because of that duck-typing, the printer manager, the status
routes, the WebSocket, the scheduler, the Home Assistant sensors and the archive need no provider
branch at all — the diff inside upstream's files is about 60 lines. Printbuddy solved the same problem
with a provider-factory package, which touches far more upstream code and is the reason its merges
became unmanageable.

**What it does.** New `Printer` columns `provider` (default `bambu`), `api_url`, `auth_token`,
`provider_options`, with an ALTER migration for SQLite and Postgres. Klipper rows get the synthetic
serial `KLIPPER-<host>` and an IP derived from the Moonraker URL. A poll thread on
`/printer/objects/query` maps Klipper state onto `PrinterState`. Create and test probe Moonraker and
register its configured webcam as an external camera. Queue dispatch uploads to Moonraker instead of
FTPS, and the two FTPS cleanups are skipped. `main.py` skips the FTPS archive sweep for Klipper
printers. The chamber light is selected by name so MMU and toolhead LED strips are not driven.

**Guard against upstream drift:** if upstream ever adds its own `provider` column or a non-Bambu
transport, this is the commit to reconcile first — see `upstream-sync.md`.

### A1 · External spool slot for printers without an AMS

| | |
|---|---|
| **Status** | ✅ |
| **Commit** | `6ca70bbc` |

A printer with no AMS gets a single external slot you assign a spool to, and filament is booked from
the grams the archive records. Upstream only knows Bambu's external spool (tray 254/255), so without
this a Klipper print produces no filament usage and no cost anywhere in the archive or reports.

### A2 · Klipper printer card

| | |
|---|---|
| **Status** | ✅ |
| **Commits** | `e6dd9e3d`, `92345de7` |

A "Klipper" badge, no MQTT diagnostics panel, and no AMS, calibration or drying blocks — those simply
never render, because a Klipper printer reports no AMS. Jog, home, extrude and temperature controls
were already in upstream's card and work as-is over Moonraker G-code, so no separate control panel was
needed. Bambu-only folders (`/cache`, `/model`, `/timelapse`) return an empty list in the file manager
rather than a warning.

### A3 / A4 · File manager over Moonraker, start a file in place

| | |
|---|---|
| **Status** | ✅ |
| **Commits** | `518b07f1`, `54234c7f` |

List, download and delete files on the printer, and start one that is already there — for G-code that
was uploaded through Mainsail without going back through the library.

### A5 · Smart plug energy per print

| | |
|---|---|
| **Status** | ✅ configuration only, no code |

Link the printer to its smart plug under Settings → Smart plugs and fill in the energy-total sensor;
kWh then flows through upstream's existing plumbing. Nothing in the fork changes.

### A6 · Chamber temperature

| | |
|---|---|
| **Status** | ⬜ needs printer-side config, no code |

Add a `[temperature_sensor chamber]` section to `printer.cfg` on the Klipper host and the client picks
it up automatically.

### A7 · Fourteen locales

| | |
|---|---|
| **Status** | ⚠️ |
| **Commit** | `e6dd9e3d` |

All Klipper keys exist in all fourteen locales. The exception is B10's `printers.tv.*` block — see
below.

### A8 · Archive a print started outside Printhok

| | |
|---|---|
| **Status** | ✅ |
| **Commits** | `e6dd9e3d`, hardening in `cdb8351e` |
| **New file** | `backend/app/services/klipper_archive.py` |

Start a print from Mainsail and the archive row still gets its G-code, fetched back from Moonraker,
instead of an empty fallback. `cdb8351e` fixed a real path bug found by the SSRF/path backstop test:
the filename filter allowed dots through, so a printer reporting a file literally named `..` resolved
one directory above the archive. Dot-only names now fall back to `print.gcode`.

### A9 · Happy Hare MMU as AMS units

| | |
|---|---|
| **Status** | ✅ |
| **Commits** | `17cb2a29`, `2a16e30c` |

`mmu`, `mmu_machine` and `save_variables` are polled. Gates map onto AMS trays (unit = `gate // 4`,
tray = `gate % 4`, so the global tray id is the gate number), the loaded gate follows `tray_now`, and
bypass stays slot 254. Assigning a spool writes `MMU_GATE_MAP`. Load and unload are `MMU_SELECT` +
`MMU_LOAD` / `MMU_UNLOAD`. A queue dispatch carrying an AMS mapping sends `MMU_TTG_MAP`, and filament
is booked per tool through that map using the per-extruder grams from the slicer comments. `2a16e30c`
makes an MMU unit render one slot per gate instead of a fixed four.

### A10 · Raw `.gcode` in the library

| | |
|---|---|
| **Status** | ✅ |
| **Commits** | `2287722c`, `2d9064c7` |
| **New file** | `backend/app/services/gcode_metadata.py` |

Upstream accepts 3MF only. Once an active Klipper printer exists, a bare `.gcode` file is accepted and
its slicer comments are parsed for print time, filament, layers and temperatures under the same keys
the 3MF parser produces, so the library card, the queue and the cost calculation all behave normally.
With no Klipper printer configured the upstream rejection is unchanged, which keeps upstream's tests
green.

### A11 · Branding

| | |
|---|---|
| **Status** | ✅ |
| **Commits** | `ebc0d486`, `e241ee12` |
| **New file** | `frontend/src/brand.ts` |

Display name and logo from `/brand.json`, used by the sidebar, login, setup, stream overlay and tab
title. `e241ee12` moved it under `/img/` so it bypasses the SPA catch-all route. Changing the name on a
running install is a file edit and a page reload — no rebuild. The backend, service name, install
paths, API and translated strings deliberately keep saying Bambuddy: renaming those is pure rebase
surface.

---

## Part B — Ported from Printbuddy

Reimplemented against this codebase, not merged. A trial merge of Printbuddy onto upstream put more
than a hundred source files in conflict; the nine files both projects touch heavily
(`PrintersPage.tsx`, `print_scheduler.py`, `routes/printers.py`, `core/database.py` and friends) are
exactly the ones a merge cannot resolve cleanly.

### B1 · "Print almost done" at 97 %, with a snapshot ✅ `d581c6ba`

A fourth notification event beside start, finish and failure, carrying a camera snapshot — so you walk
to the printer as it finishes rather than after.
Touches `services/notification_service.py`, `models/notification_template.py`, `main.py`,
`components/AddNotificationModal.tsx`, `NotificationProviderCard.tsx`, `en`/`nl` locales.

### B2 · A provider can target several printers ✅ `f5ae369d` + `ee8a6e9e`

Upstream allows one printer or all. This adds a `printer_ids` JSON column with a migration, schema and
UI. `ee8a6e9e` is the follow-up that put `printer_ids` on `NotificationProvider` /
`NotificationProviderCreate` instead of on `FailureAnalysis` and `updateArchive`, where the first pass
had wrongly placed it — five `tsc` errors that only appeared once the whole series was built together.

### B3 · Multi-word inventory search ✅ `4a0b8b8b`

`abs black` matches across fields instead of demanding one substring.
`frontend/src/utils/inventorySearch.ts`.

### B4 · Project progress counts ✅ `a16f8293`

Parts done, failed and queued on one line under the parts bar of `ProjectsPage.tsx`. A deliberately
small local version of Printbuddy's Farm Command Center, which is built for twenty-plus printers.

### B5 · One alert banner ✅ `5549e216`

`GET /alerts/summary` (new `api/routes/alerts.py`) plus an amber banner in `Layout.tsx`, combining
spools running low and maintenance that is due.

### B6 · Open Filament Database lookup ✅ `0a5211bf`

Search OFD from the Add Spool form and fill the fields from the result. New
`services/open_filament_database.py` and `api/routes/open_filament_database.py`, plus
`components/spool-form/OpenFilamentDatabaseSearch.tsx`. Off by default — it is an external API call —
under Settings → Filament controls. Documented separately in
[`docs/open-filament-database.md`](../open-filament-database.md).

### B7 · Moonraker subnet scan ✅ `fff5c138`

"Add printer" discovery also probes port 7125, so a second Klipper machine is found the same way a
Bambu is. `services/discovery.py`.

### B8 · Pending spool-to-slot assignment ✅ `fdca5c3b`, `81334b7e`

"This spool goes into the next slot that gets loaded." New table `pending_slot_assignments` with a
30-minute timeout, `services/pending_slot_assignment.py`, and controls in the inventory list and the
slot dialog. The default target is **any printer** — that is the point of the feature — and you can
still pick one; opened from a slot dialog, that printer is preselected.

### B9 · Notify (iOS) Live Activity provider ⚠️ `39537544`

A provider that pushes a Live Activity countdown tile to the iOS app *Notify*. Ported line by line
from Printbuddy: same endpoints, query strings and field names (`title`, `body`, `progress`, `status`,
`symbol`, `tint`). Two deliberate differences — `groupType` is `bambuddy` rather than `printbuddy` (a
free grouping string), and `iconUrl` is omitted because Printbuddy points at its own icon on GitHub
and there is no public URL for the Printhok logo, so pushes show the app's default icon.
**Gap:** never tested against the paid app. Whether Notify accepts the payloads is unverified.

### B10 · `/tv` kiosk wall ⚠️ `9adb27eb` + `b3312677`

A wall of tiles for a screen in the workshop, with a refresh interval.

`b3312677` added the part that makes it usable on a TV: a token mode, so the screen does not have to
stay logged in. It is a **fourth long-lived scope, `tv`**, not a widening of `camwall` — a camwall
token is issued on the promise that the wall never names the part, and a TV tile names the file on the
bed and the spool feeding the hotend. Its own feed `GET /tv/printers` returns one response per refresh
containing exactly the fields a tile draws; serials, IPs and access codes stay server-side, and
`tray_now` is resolved on the server so a wall token sees the loaded spool and no other tray. The page
keeps a single render path — both modes collapse into `TvTileData` via `tileFromStatus` /
`tileFromFeed` — so the kiosk wall and the logged-in wall cannot drift apart. Tokens are minted on the
existing Camera API Tokens page, which hands back a ready `/tv?token=…` URL (`&cams=0` hides the camera
images, `&refresh=10` sets the poll cadence).
**Gap:** the `printers.tv.*` block — the page's own strings, including `tokenRejected` — exists in
`en` and `nl` only; the other twelve locales fall back to English. The four `cameraTokens` strings
that mint a TV token *are* in all fourteen, so the admin side is fully translated and the wall itself
is not.

### B11 · Farm command center at `/farm` ✅

A single screen for the whole operation: fleet tiles bucketed by group, fleet
utilization, parts completed today, active projects, a roll-up of everything that
wants a hand, and the shortcuts out to inventory and maintenance.

**Backend (new):** `models/printer_fleet_group.py`, `schemas/printer_fleet_group.py`,
`api/routes/printer_fleet_groups.py`, `tests/integration/test_printer_fleet_groups_api.py`.
A fleet group is a name plus a set of printers — deliberately *not* upstream's
`models/group.py`, which is a user permission group. Grouping is presentation only:
nothing schedules or restricts against it. Reads need `printers:read`, writes
`printers:update`. Two deliberate differences from Printbuddy: a duplicate name
answers 400 rather than letting the unique constraint surface as a 500, and unknown
printer ids are rejected before the table is touched.

**Frontend (new):** `pages/FarmCommandCenterPage.tsx`, `utils/farmFleet.ts` and tests
for both. Every calculation lives in the util so it is testable without rendering.

`classifyFleetState` mirrors `classifyPrinterStatus` in `PrintersPage.tsx` instead of
importing it: that function is module-private there, and exporting it would mean
editing the file upstream changes most, on every release, forever. The buckets have to
stay in step — FAILED with no attached HMS code is terminal like FINISH, not an alert,
and a disconnected printer is offline whatever its last state said.
`PrintersPageBucketing.test.ts` mirrors the same rules for the same reason.

**Touched upstream:** `main.py` and `core/database.py` (router, model registry, idempotent
migration), `models/__init__.py`, `api/client.ts`, `App.tsx` (route), `Layout.tsx`
(sidebar entry), `en`/`nl` locales.

Ported from Printbuddy's `FarmCommandCenterPage`, with three changes: it is translated
rather than hardcoded English, "TV mode" points at this fork's `/tv` rather than
Printbuddy's `/farm-monitor`, and the alert builder returns i18n keys instead of
sentences. Printbuddy's *Dispatch Suggestions* panel is not included — it only ever
restated queued and failed counts that B4 already puts on the Projects page.

**Gap:** `farm.*` exists in `en` and `nl` only; the other twelve locales fall back to
English.

---

## Part C — deliberately not ported

Other printer brands (Prusa Link/Connect, Elegoo SDCP, Creality CFS, Snapmaker U1) — every extra
provider doubles the conflict surface, and the fork's whole design is one non-Bambu transport. Panda
Breath. Printbuddy's staging, batch review, production plans and alert groups — all aimed at
twenty-plus printers. Its Dispatch Suggestions panel, which only restated counts B4 already shows.
The Docker self-update sidecar, which upstream already covers with in-app updates.

The Farm Command Center itself *was* skipped on these grounds and later ported anyway, on request —
see B11. Three of its seven panels already existed in smaller form as B4, B5 and B10.

Also not ported because **upstream already has it**: print progress in the browser tab, camera view
mode on the card, quantity when creating a spool, empty-spool weight from the catalogue, go2rtc
snapshots, the A2L model, in-app update, and Cam Wall.

Things upstream has that Printbuddy does not, and that this fork therefore keeps: the sensors tab,
SpoolBuddy, finance and invoicing, Dutch, print progress in the tab title, plate restore for the final
photo, automatic archive and file cleanup, the trash bin, and fourteen languages against nine.

---

## Open items

| | |
|---|---|
| B9 | Notify payloads unverified against the real iOS app |
| B10 | `printers.tv.*` page strings in `en` and `nl` only; the `cameraTokens` strings are in all fourteen |
| A6 | Chamber temperature waits on `[temperature_sensor chamber]` in `printer.cfg` |
| B11 | `farm.*` page strings in `en` and `nl` only |
