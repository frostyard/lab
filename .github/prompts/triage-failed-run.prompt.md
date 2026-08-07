---
mode: agent
description: Diagnose a failed or stuck QA pipeline run.
---

# Triage a failed run

Goal: find out whether the failure is a real image regression, a lane defect,
or infrastructure. Do not change YAML until you know which.

## Gather

- `just runs` — recent workflows, newest first, with phase and timings.
- `just logs` — logs for the most recent workflow; use
  `argo logs -n argo <workflow>` for an older one.
- `argo get -n argo <workflow>` — which DAG node failed, and its message.
- `just status` — are the Applications and CronWorkflows healthy at all?

## Classify

- **Image regression** — the suite ran and assertions failed. Report the exact
  image, tag, digest, product variant and suite. The digest is the identity
  that matters; a tag is not enough.
- **Lane defect** — the runner errored before or around the suite (pull
  failure, nested systemd never reached its target, missing dependency in the
  test environment). This is a repo bug; fix it with
  [`workflow-template.prompt.md`](workflow-template.prompt.md).
- **Infrastructure** — timeout from `activeDeadlineSeconds`, semaphore
  starvation, orphaned pods, node pressure, registry unavailable. Fix belongs
  in `manifests/` — see [`manifest.prompt.md`](manifest.prompt.md).

## Rules

- A failed run intentionally leaves the digest unrecorded so the next poll
  retries. Do not "fix" a red run by editing the `image-polling-digests`
  ConfigMap.
- Never resolve an incident by applying to the cluster directly; `selfHeal`
  reverts it and the fix is lost.
- If the failure exposes a wrong claim about what a lane tests, correct
  `docs/roadmap.md` — the roadmap exists because that has happened before.

## Report

Summarise: workflow name, lane, image + digest, failing node, classification,
and the smallest change that would address it.
