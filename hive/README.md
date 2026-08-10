# hive

[Hive](https://github.com/kubestellar/hive) (v2) — AI agent orchestrator for
the frostyard org, running standalone (no kubestellar hub) on `selfie` via
**docker compose** (the former k3s deployment is retired). Agents use the
**copilot** CLI backend; GitHub access is via the **frostyard-hive** GitHub
App.

Deployed by hand from this directory — git is still the source of truth, but
nothing syncs it automatically:

```bash
ssh selfie
git -C ~/lab pull
cd ~/lab/hive
docker compose up -d            # add --profile auto-update for watchtower
```

Dashboard: http://10.0.1.200:3001 · Terminal (ttyd): port 7681.

## Secrets (manual, never in git)

Two pieces, both gitignored:

```bash
cd ~/lab/hive
mkdir -p secrets && chmod 700 secrets
cp /path/to/frostyard-hive.YYYY-MM-DD.private-key.pem secrets/gh-app-key.pem
chmod 600 secrets/gh-app-key.pem

cp .env.example .env && chmod 600 .env
# set HIVE_DASHBOARD_TOKEN=$(openssl rand -hex 32)
```

`secrets/` is mounted read-only at `/secrets` in the container;
`hive.yaml` points `github.key_file` at `/secrets/gh-app-key.pem`.

## GitHub App (frostyard-hive)

Created at https://github.com/organizations/frostyard/settings/apps/new with:

- Webhook: **disabled** (a standalone spoke consumes no webhooks)
- Repository permissions: Contents RW, Issues RW, Pull requests RW,
  Checks Read, Metadata Read
- Installed on: snosi, testsuite, lab, updex

`github.app_id` / `installation_id` live in `hive.yaml`. The private key
lives only in `secrets/` on selfie. To rotate it: GitHub App settings →
Generate a private key → replace `secrets/gh-app-key.pem` →
`docker compose restart hive`.

## Layout

`docker-compose.yaml` is upstream's published file verbatim, except the
source `build:` block is omitted (no source tree here) and `HIVE_ID=frostyard`
pins the instance id so existing `hive/frostyard` repo labels stay stable.
`HIVE_GITHUB_TOKEN` stays unset — auth is the App key. `deploy/nginx.conf` is
vendored verbatim from upstream.

## Known issues to watch (fixed by hand on k3s; unverified on compose)

The k3s deployment carried two workarounds that are deliberately **not**
ported. If the symptoms reappear, apply the deviation then:

- **Agent `gh` calls fail** ("gh CLI is not available", agents fall back to
  the GitHub MCP under the wrong identity): the image's `gh` wrapper
  hardcodes `/usr/bin/gh` but the binary lives at `/opt/hive/bin/gh-real`.
  Check `docker exec hive ls -l /usr/bin/gh`; fix with
  `docker exec hive ln -sf /opt/hive/bin/gh-real /usr/bin/gh` (or an
  entrypoint override if it must survive container recreation).
- **Cross-agent permission errors** on `/data/home` / `/data/beads` after
  restarts (per-agent UIDs 2001+ vs the uid-1001 watcher): the k8s init
  container ran `chown -R 1001:1000 /data` plus group-writable/setgid repair
  on every start.

## Config precedence — read before editing hive.yaml

`hive.yaml` is the **seed**; after first boot the dashboard's overlay on the
data volume (`/data/hive.yaml.dashboard`) wins, and the process may rewrite
the seed on dashboard Save (checkout drift on selfie is expected). Change
agents/models/settings through the dashboard, not the file.

## First-run checklist (dashboard at http://10.0.1.200:3001)

1. Sign in — GitHub device flow (allowlist: `bketelsen`).
2. Settings → Copilot: run the device-flow login so agents get a
   `COPILOT_GITHUB_TOKEN` (persisted on the `hive-data` volume, survives
   restarts).
3. Confirm the ACMM level / agent roster.
4. After the first agent kick, spot-check `gh` works as the App bot and
   `/data` permissions hold across a `docker compose restart hive` — see
   known issues above.
