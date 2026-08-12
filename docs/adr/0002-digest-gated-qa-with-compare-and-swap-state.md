# 0002 — Digest-gated QA with compare-and-swap state, persisted only after QA passes

- **Status:** Accepted
- **Date:** 2026-08-12

## Context

The lab must run QA against `ghcr.io/frostyard/{snow,cayo,snowfield}:latest`
whenever a new image is published, without a webhook from the registry and
without re-testing an unchanged image every poll. That requires durable
"last seen digest" state, and three failure modes make naive state handling
wrong:

- Recording a digest whose QA run *failed* marks a broken image as seen and
  silently suppresses every retry — the one unrecoverable failure.
- A transient registry error that reads as "digest changed" triggers a
  pointless multi-hour QA run.
- The state lives in a ConfigMap inside the GitOps-managed `manifests/`
  path ([ADR-0001](0001-two-argocd-applications-and-hand-applied-bootstrap.md)),
  so `selfHeal` would reset every stored digest to `""` on each reconcile
  and re-run QA against unchanged images forever.

## Decision

Image polling is the `image-poller` WorkflowTemplate
([argo/workflow-templates/image-poller.yaml](../../argo/workflow-templates/image-poller.yaml)),
invoked by one CronWorkflow per image:tag (`manifests/image-poll-*.yaml`),
with this fixed sequence:

1. **Fetch** — `skopeo inspect` resolves the live digest (3 attempts with
   backoff). A failed fetch emits the sentinel `none`, which
   `compare-local` treats as *unchanged*: a registry blip never reads as a
   new image.
2. **Compare** — against the `image-polling-digests` ConfigMap key named by
   `state-key`; unchanged means exit cleanly.
3. **QA pinned to the digest** — the QA pipeline receives
   `image-digest` from the fetch step, not the tag, so a tag that moves
   mid-run cannot split results across two images.
4. **Persist last, compare-and-swap** — `update-digest` runs only after
   `run-pipeline.Succeeded || run-pipeline.Skipped`, and `update-local`
   re-reads the stored value and skips the write if it moved since the
   check (`skipping stale write`). Persisting last is what makes a failed
   run retry on the next poll instead of being silently recorded as seen.

Two ownership rules make the state safe:

- **Per-key single-flight:** a workflow-level mutex
  `image-poll-{{workflow.parameters.state-key}}` serializes polls for the
  same key, so a queued poll re-reads the ConfigMap after the previous one
  persists rather than racing it.
- **Git owns the key set, the cluster owns the values:**
  [manifests/image-polling-digests.yaml](../../manifests/image-polling-digests.yaml)
  declares every key with value `""`, and the infra Application carries
  `ignoreDifferences` on that ConfigMap's `/data`
  ([argocd/infra-application.yaml](../../argocd/infra-application.yaml))
  plus `RespectIgnoreDifferences=true`, so reconciles never reset runtime
  digests.

## Consequences

- A broken image is retried on every poll until it passes; there is no
  state in which a failure is recorded as success.
- Removing the `ignoreDifferences` stanza has a known symptom — every poll
  re-runs QA against an unchanged image
  ([docs/ops/bootstrap.md](../ops/bootstrap.md), failure modes).
- Enforced offline by two tests in
  [tests/test_kubernetes_manifests.py](../../tests/test_kubernetes_manifests.py):
  `test_image_pollers_and_digest_state_are_one_to_one` (every poller's
  `state-key` has exactly one ConfigMap key, seeded `""`) and
  `test_image_poller_persists_only_after_qa_or_explicit_opt_out` (the DAG
  ordering, the `when` guards, and the CAS text in `update-local` are
  pinned literally).
- The pattern is duplicated once, deliberately simplified: the temporary
  [argo/secure-install-watch.yaml](../../argo/secure-install-watch.yaml)
  watcher uses its own state key (`secure-install-cayo-latest`) so it and
  the poller advance independently, records the digest at *submission* (not
  after QA — the lane it launches is standalone, see
  [ADR-0010](0010-vacuous-success-is-forbidden.md)), and seeds its baseline
  without triggering, so enabling it does not immediately test an image
  already known broken.

## Alternatives considered

- **Persist the digest before/with QA:** rejected; a failed run would be
  recorded as seen and never retried — this is the invariant the whole
  design exists to protect.
- **Registry webhooks / GitHub Actions triggers:** the cluster has no
  public ingress surface; polling with a 3-hour cadence is sufficient and
  keeps the cluster unreachable from outside.
- **Failing the poll on fetch errors:** a red poller per registry blip is
  noise; the `none` sentinel makes transient failure indistinguishable
  from "no change", which is the correct reading.
- **State in git (a committed file):** would give CAS via git push, but
  turns every digest change into a commit and gives the cluster write
  access to the repo's manifests path; the ConfigMap split keeps writes in
  the cluster and the schema in git.

## References

- Shapes: [README.md — Architecture](../../README.md#architecture)
- Implemented by:
  [argo/workflow-templates/image-poller.yaml](../../argo/workflow-templates/image-poller.yaml),
  [manifests/image-polling-digests.yaml](../../manifests/image-polling-digests.yaml),
  `manifests/image-poll-*.yaml`,
  [argocd/infra-application.yaml](../../argocd/infra-application.yaml)
- Enforced by: [tests/test_kubernetes_manifests.py](../../tests/test_kubernetes_manifests.py)
- Builds on: [ADR-0001](0001-two-argocd-applications-and-hand-applied-bootstrap.md)
