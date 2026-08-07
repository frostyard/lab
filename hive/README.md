# hive

[Hive](https://github.com/kubestellar/hive) (v2) — AI agent orchestrator for
the frostyard org, running standalone (no kubestellar hub) on `selfie`.
Agents use the **copilot** CLI backend; GitHub access is via the
**frostyard-hive** GitHub App.

Synced by the `frostyard-hive` Argo CD Application
(`argocd/hive-application.yaml`) from this directory.

## Secret (manual, never in git)

The Deployment mounts `hive-secrets`, created by hand:

```bash
kubectl -n hive create secret generic hive-secrets \
  --from-literal=dashboard-token="$(openssl rand -hex 32)" \
  --from-file=gh-app-key.pem=/path/to/frostyard-hive.YYYY-MM-DD.private-key.pem
```

The namespace must exist first (`kubectl apply -f hive/namespace.yaml` on
first bootstrap, before the Argo app syncs).

## GitHub App (frostyard-hive)

Created at https://github.com/organizations/frostyard/settings/apps/new with:

- Webhook: **disabled** (a standalone spoke consumes no webhooks)
- Repository permissions: Contents RW, Issues RW, Pull requests RW,
  Checks Read, Metadata Read
- Installed on: snosi, testsuite, lab, updex

`github.app_id` / `installation_id` live in `configmap.yaml`. The private key
lives only in the `hive-secrets` Secret.

## Config precedence — read before editing configmap.yaml

The ConfigMap is only the **seed**. After first boot, the dashboard writes an
overlay to the PVC (`/data/hive.yaml.dashboard`) which wins over the seed.
Change agents/models/settings through the dashboard at
http://10.0.1.200:3002 — editing `configmap.yaml` post-boot generally does
nothing.

## First-run checklist (dashboard at http://10.0.1.200:3002)

1. Sign in — GitHub device flow (allowlist: `bketelsen`).
2. Settings → Copilot: run the device-flow login so agents get a
   `COPILOT_GITHUB_TOKEN` (persisted on the PVC, survives restarts).
3. Confirm the ACMM level / agent roster.
