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
cp -n hive.yaml.seed hive.yaml  # first deploy only — hive.yaml is untracked
docker compose up -d            # add --profile auto-update for watchtower
```

Dashboard: http://10.0.1.200:3002 (same port as the k3s days) · the nginx
gateway (3001) and ttyd (7681) are bound to selfie's loopback only.

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

## Layout and deviations from upstream

`docker-compose.yaml` is upstream's published file plus deviations that each
cover a breakage observed on this image (details in the compose file):

- source `build:` block omitted — published image only
- `HIVE_ID=frostyard` pins the instance id so existing `hive/frostyard` repo
  labels stay stable
- entrypoint override: symlinks `/usr/bin/gh` → `/opt/hive/bin/gh-real`
  (the image's `gh` wrapper hardcodes a path nothing installs; without it
  every agent `gh` call dies) and repairs `/data` ownership on each start
  (`chown 1001:1000`, group-writable + setgid on the shared trees — the
  uid-1001 permissions watcher can't fix root-owned files itself)

`HIVE_GITHUB_TOKEN` stays unset — auth is the App key. `deploy/nginx.conf`
is vendored verbatim from upstream.

Ports (learned the hard way — upstream's `hive.yaml.example` is wrong):
the Go API + UI with real auth serve on **3002** (`dashboard.port`, matches
the entrypoint's `HIVE_API_PORT` and the container healthcheck) — published
on the LAN; a node proxy serves the UI on **3001** (`HIVE_PROXY_PORT`),
fronted by the nginx gateway — loopback only, see below.

## Config: hive.yaml.seed vs hive.yaml

`hive.yaml.seed` is the tracked seed. The deployed copy `hive.yaml` is
**gitignored** (upstream's own layout): the running process rewrites it with
env vars expanded — including the dashboard token in plaintext — so it must
never be tracked. After first boot the overlay on the data volume
(`/data/hive.yaml.runtime`) wins over the file anyway; change
agents/models/settings through the dashboard. To force a reseed: stop the
stack, wipe the `hive_hive-data` volume, re-copy the seed, start.

## Security posture

The LAN entrance is the Go API on 3002, which enforces per-user device-flow
auth against the `authorized_users` allowlist — the same posture the k3s
deployment had. The nginx gateway → node proxy path (3001) is bound to
selfie's loopback only, for two reasons: the proxy **vouches for every API
request** (`X-Hive-Internal`) and hands the dashboard token to any visitor,
and its blanket Bearer requirement on POST blocks the device-flow login
outright (`Login error: Unauthorized` before the GitHub redirect — the UI
can't obtain the shared token on an allowlisted spoke, by design). Upstream's
proxy model assumes an open, token-only spoke on a trusted network; ours is
allowlisted. Reach 3001 via `ssh -L` if ever needed. Do not port-forward
either port off the LAN.

## First-run checklist (dashboard at http://10.0.1.200:3002)

1. Sign in — GitHub device flow (allowlist: `bketelsen`).
2. Settings → Copilot: run the device-flow login so agents get a
   `COPILOT_GITHUB_TOKEN` (persisted on the `hive-data` volume, survives
   restarts).
3. Confirm the ACMM level / agent roster.
4. After the first agent kick, spot-check `gh` works as the App bot and
   `/data` permissions hold across a `docker compose restart hive` — see
   known issues above.
