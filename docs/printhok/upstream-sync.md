# Staying current with upstream

The goal of this fork is stated in one sentence: **every upstream release, plus every feature in
[`features.md`](features.md), forever.** This file is how that is done.

Upstream ships roughly four releases a month. Budget one to two hours per sync. Skipping three in a
row is how Printbuddy lost the ability to follow upstream at all — and the reason this repository is a
patch series instead of a divergent fork.

---

## The one rule

**Rebase. Never merge.**

`git merge upstream/main` into this branch is forbidden, not discouraged. A merge preserves the fork's
commits as a tangle instead of a stack, and from then on every subsequent sync re-resolves the same
conflicts. The series must stay a readable list of subjects sitting on top of a clean upstream base —
that is what keeps each conflict small and recognisable.

Corollaries:

- One commit per subject. Never a "misc fixes" commit
- A commit that touches an upstream file explains, in its message, what upstream code it changes
- Never force-push `upstream`. Ever
- `main` and `voron` in this repository always point at the same commit

---

## Repository layout

| Ref | Meaning |
|---|---|
| `upstream` | `maziggy/bambuddy` — read only |
| `printbuddy` | `vmhomelab/printbuddy` — read only, the source for the B series |
| `origin` | `DeWarePeb/printhok` — this fork |
| `voron` | the patch series. The working branch |
| `main` | forced to the same commit as `voron` after every sync, so upstream's installer default branch works |
| `voron-b*` | frozen snapshots from earlier rounds. History only; do not build on them |

`main` deliberately does **not** track upstream `main`. That is what lets
`install.sh --path /opt/bambuddy` install the fork with its default branch.

---

## The sync

### 1. Fetch and look before you rebase

```bash
git fetch upstream --tags
git log --oneline <current-base>..upstream/main
git diff --stat <current-base>..upstream/main -- \
  backend/app/services/printer_manager.py \
  backend/app/services/print_scheduler.py \
  backend/app/api/routes/printers.py \
  backend/app/core/database.py \
  frontend/src/pages/PrintersPage.tsx
```

The current base is recorded at the top of [`features.md`](features.md) and in
[`../../NOTICE-modifications.md`](../../NOTICE-modifications.md); at the time of writing it is
`9b2c49d8`.

**Look at `upstream/dev`, not only at `upstream/main`.** This is the part that is easy to get wrong.
Upstream develops on `dev` and moves `main` at release, so `main` can sit still for weeks while the
next version is being built in plain sight. The daily beta tags (`v1.2.6b1-daily.*`) are cut from
`dev`. Watching only `main` means the first time you see a release is the moment you have to rebase
onto it.

The two branches genuinely diverge — this is not a fast-forward relationship — so compare with three
dots, and never rebase onto `dev`. It is a preview, not a base:

```bash
git log --oneline upstream/main...upstream/dev              # what is coming, both directions
git diff --name-only upstream/main...upstream/dev > /tmp/dev.txt
git diff --name-only <current-base>..voron > /tmp/fork.txt
grep -Fxf /tmp/fork.txt /tmp/dev.txt                        # the files the next rebase will fight over
```

*Measured 2026-09-09:* `main` at `9b2c49d8`, `dev` 21 commits ahead of it with 11 commits on `main`
that are not on `dev`. The overlap was 28 files, and the shape of it is worth remembering: the hot
ones are `routes/printers.py`, `print_scheduler.py`, `main.py`, `client.ts` and `PrintersPage.tsx` —
and **all fourteen locale files**, every single time. The fork adds keys to all fourteen and so does
upstream, so locale conflicts are not a sign anything went wrong; they are the standing cost of the
translation work. Resolve them by keeping both sides' keys, then let `check:i18n` prove nothing was
dropped.

Read the upstream log for two things specifically:

- **A feature the fork already has.** If upstream ships, say, its own "almost done" notification, the
  fork's version must be *dropped*, not kept alongside. Delete that commit during the rebase, note it
  in `features.md` under a "landed upstream" line, and let upstream's version win — that is a smaller
  patch series, which is the whole point
- **A change to the printer abstraction.** If upstream adds its own provider concept or a second
  transport, A0 is the commit to reconcile first, before anything else in the series

### 2. Rebase the series

```bash
git checkout voron
git rebase --onto upstream/main <current-base> voron
```

Or onto a specific release tag: `git rebase --onto v1.2.6 9b2c49d8 voron`.

### 3. Resolve

| File | Expect | How |
|---|---|---|
| `frontend/src/pages/PrintersPage.tsx` | The worst one. Both sides edit the card and the add/edit dialog constantly | Resolve by hand. Keep the Bambu path byte-identical to upstream; only the `provider === "klipper"` branches are ours |
| `backend/app/services/print_scheduler.py` | Upstream rewrites large parts of it | Ours is the upload branch plus two skipped FTPS cleanups. Re-apply those against upstream's new shape rather than restoring our old lines |
| `backend/app/api/routes/printers.py` | Frequent | Ours: probe/create/test for Moonraker, PATCH deriving the IP |
| `backend/app/core/database.py` | Frequent — everyone adds migrations | Our migrations are additive. Keep both sides, ours last |
| `frontend/src/i18n/locales/*.ts` | Every release | Keep both key blocks. Fourteen locales; missing keys render as English, they do not crash |
| `README.md`, `NOTICE-modifications.md`, `docs/printhok/*` | Guaranteed, on README | Ours wins: `git checkout --ours README.md`. Then read upstream's README diff separately for new install flags or requirements worth folding into ours |
| `CHANGELOG.md` | Guaranteed, and huge | Theirs wins: `git checkout --theirs CHANGELOG.md`. The fork does not write to it — fork changes are recorded in `features.md` |

New files (`moonraker_client.py`, `moonraker_dispatch.py`, `gcode_metadata.py`,
`klipper_archive.py`, `alerts.py`, `tv.py`, `open_filament_database.py`,
`pending_slot_assignment.py`, `notify_live_activity_*.py`, `TvPage.tsx`, `NextSlotAssignment.tsx`,
`brand.ts`) never conflict. That is by design and worth protecting: when a change *can* live in a new
file, put it in a new file.

### 4. Test — on a machine, not on Windows

```bash
./test_backend.sh          # ruff check && ruff format --check, then pytest tests/ -n 30
./test_frontend.sh         # npx tsc && npm run lint && npm run test:run
./test_all.sh              # the above plus test_docker.sh and test_security.sh --full
```

Two things that have bitten before, both worth re-reading before blaming the rebase:

- **`tsc` is the gate that catches a bad port.** The B series passed per-commit checks and still
  produced five type errors when the branches were combined; see B2 in `features.md`. Always run
  `npx tsc` against the *assembled* branch, never only per commit
- **Node runs out of memory on a small box.** `NODE_OPTIONS=--max-old-space-size=1400 npm run build`
  on a 2 GB machine, and make sure no second instance is holding RAM

`test_security.sh` includes a path-join backstop (`test_no_unsafe_path_joins`) and an SSRF
classification test. Fork code has to satisfy both: `api_url` is classified in the SSRF test, and any
new service that builds a path from printer-supplied text needs either the DB-path marker or a real
sanitiser — `klipper_archive.py` needed the latter.

### 5. Publish

```bash
git push --force-with-lease origin voron
git branch -f main voron
git push --force-with-lease origin main
```

`--force-with-lease`, never `--force`. Both branches, every time — if they drift, the documented
install command silently installs an older fork.

### 6. Deploy and verify

```bash
sudo /opt/bambuddy/install/update.sh
```

It snapshots the database, resets to `origin/<branch>`, rebuilds and restarts, and rolls back on
failure. Then check, in this order:

1. `journalctl -u bambuddy -f` shows `Application startup complete` with no traceback
2. `/` returns 200 and the sidebar shows the brand name from `brand.json`
3. A Klipper printer card is connected, with live temperatures
4. `/tv` returns 200, both logged in and with a `?token=` URL
5. A queue dispatch to the Klipper printer uploads and starts

Rollback is `git reset --hard <previous commit>` in the install directory plus a service restart, or
restore the backup ZIP that `update.sh` wrote.

### 7. Record it

Update, in the same commit or immediately after:

- The base commit at the top of [`features.md`](features.md) and in `NOTICE-modifications.md`
- The base badge in `README.md`
- Any feature that upstream absorbed, or any new gap the rebase opened

A sync that is not recorded is a sync nobody can audit next month.

---

## Adding a feature to the fork

1. Check upstream first — `git log upstream/main --oneline --grep=<keyword>` and the upstream issue
   tracker. If it is coming upstream, wait for it
2. If it belongs upstream, send it there. A smaller series is a fork that survives
3. New files over edits to upstream files, every time
4. One commit, subject `<area>: <what> (<id>)`, body explaining the observed behaviour and the change
5. Translation keys in `en` and `nl` at minimum, all fourteen if the string is user-facing and short
6. Tests next to the change; `tsc` and `ruff` clean before it is pushed
7. Add an entry to [`features.md`](features.md) in the same commit — the register is the contract

---

## Health check

Run this before and after any sync. Every line should be true.

- [ ] `git rev-parse main voron` prints the same SHA twice
- [ ] `git rev-parse origin/main origin/voron` matches local
- [ ] No merge commits in the series: `git log --merges <base>..voron` is empty
- [ ] Every feature in `features.md` still has its commit in `git log <base>..voron`
- [ ] `./test_backend.sh` and `./test_frontend.sh` are green on a Linux machine
- [ ] `README.md`, `features.md` and `NOTICE-modifications.md` name the same base commit
