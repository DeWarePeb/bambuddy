# Updating Printhok

> **This is the fork's copy.** Printhok is a rebased patch series on
> [maziggy/bambuddy](https://github.com/maziggy/bambuddy) — see [`README.md`](README.md).
> The commands below point at `DeWarePeb/bambuddy`, not upstream. Everything else
> about updating is upstream's, unchanged.

> **In-app updates:** the **Update** button follows whichever `origin` and branch
> the install tree is on, which for a Printhok install is this fork. It has been
> unreliable across large version jumps since 0.2.3; the commands below are the
> safe path and can be run repeatedly.

Pick the section that matches how Printhok was installed.

---

## Docker

**There is no published Printhok image.** `docker-compose.yml` still names
`ghcr.io/maziggy/bambuddy:latest`, which is upstream. A `docker compose pull`
against an unedited compose file will therefore replace Printhok with Bambuddy
and lose the Klipper support. Rebuild from source instead:

```bash
cd /path/to/your/printhok/checkout
git pull
docker compose up -d --build
```

**If your `docker-compose.yml` predates 0.2.3,** also refresh it from the repo —
recent releases added `cap_add: NET_BIND_SERVICE`, extra virtual-printer ports
for bridge mode, and an optional Postgres block:

```bash
curl -fsSL https://raw.githubusercontent.com/DeWarePeb/bambuddy/main/docker-compose.yml \
  -o docker-compose.yml.new
# Diff against yours, merge by hand, then:
docker compose up -d --build
```

---

## Native install (`install.sh` or manual `git clone`)

Both paths produce a git working tree at the install directory, so the update
is the same. Preferred:

```bash
sudo /opt/bambuddy/install/update.sh
```

`update.sh` stops the service, snapshots the database via the built-in backup
API, resets to `origin/<branch>`, installs Python deps, rebuilds the frontend,
and restarts the service. It rolls back automatically if any step fails. It is
branch- and remote-agnostic, so upstream's script needs no fork changes: it
follows whichever `origin` your tree has, which for a Printhok install is
`DeWarePeb/bambuddy`.

> On a machine with 2 GB of RAM the frontend build needs a heap cap, or Node is
> killed mid-build: `NODE_OPTIONS=--max-old-space-size=1400`.

### Manual equivalent

If you'd rather run the steps yourself:

```bash
cd /opt/bambuddy
sudo systemctl stop bambuddy
sudo -u bambuddy git fetch origin
sudo -u bambuddy git reset --hard origin/main
sudo -u bambuddy venv/bin/pip install -r requirements.txt
sudo systemctl start bambuddy
```

Replace `/opt/bambuddy` with your install path if different. Database schema
migrations run automatically on startup — no Alembic step is required.

---

## Installed from a GitHub ZIP or tarball download

These installs have no `.git` directory, so neither `update.sh` nor a plain
`git pull` will work. Reinstall cleanly.

> Do **not** curl upstream's `install.sh` and run it against an empty directory:
> it clones `maziggy/bambuddy` and you get Bambuddy, not Printhok. Clone the
> fork first, then point the installer at the tree it created.

```bash
# 1. Back up your stateful data
sudo systemctl stop bambuddy
sudo tar czf ~/bambuddy-backup.tgz -C /opt/bambuddy \
  data bambuddy.db bambuddy.db-shm bambuddy.db-wal \
  virtual_printer archive projects icons .env 2>/dev/null || true

# 2. Remove the old install, clone the fork, and run its installer
sudo rm -rf /opt/bambuddy
sudo git clone https://github.com/DeWarePeb/bambuddy.git /opt/bambuddy
sudo bash /opt/bambuddy/install/install.sh --path /opt/bambuddy

# 3. Restore your data
sudo systemctl stop bambuddy
sudo tar xzf ~/bambuddy-backup.tgz -C /opt/bambuddy
sudo systemctl start bambuddy
```

---

## Before you upgrade

Take a backup. Settings → Backup → **Create Backup** downloads a ZIP containing
the database and all stateful directories. Any bare-metal update via
`update.sh` does this automatically; Docker and manual upgrades do not.
