# 0009 — No artifact store: the workflow log is the surface, host disk the fallback

- **Status:** Accepted
- **Date:** 2026-08-12

## Context

Argo Workflows can persist output artifacts, but only with an artifact
repository (S3/GCS/etc.) configured — infrastructure this single-node,
no-public-surface lab does not run. Lane evidence is dominated by console
and harness logs; the dashboard needs only a verdict and a checks list
([ADR-0004](0004-one-way-evidence-pipeline.md)). Meanwhile pod logs are not
durable: kubelet rotation has truncated the decisive error of the secure
lane "three separate times — most recently leaving ten lines that named an
exit code with none of the output explaining it"
([run-secure-install-tests.yaml](../../argo/workflow-templates/run-secure-install-tests.yaml)).

## Decision

**Nothing is declared as an Argo output artifact** — no `outputs.artifacts`
block exists anywhere in `argo/`. Evidence has three tiers:

1. **Output parameters** carry the verdict: `result` and `checks` read from
   `/tmp/results/` (an emptyDir), sized for a ConfigMap-scale payload, not
   logs ([ADR-0004](0004-one-way-evidence-pipeline.md)).
2. **The workflow log is the primary evidence surface**, with console dumps
   sized by outcome: success prints a short confirmation (last 40 lines),
   failure a generous dump (last 400 lines) — "with no artifact store, this
   log is the only evidence of what the guest was doing"
   ([run-incus-vm-tests.yaml](../../argo/workflow-templates/run-incus-vm-tests.yaml)).
3. **Host disk is the fallback for logs that outgrow pod logging.** The
   secure lane persists its full harness log and guest serial console to
   `/var/lib/snosi-lab/secure/logs/<workflow>.log` on the host,
   deliberately *outside* the work directory so the cleanup trap cannot
   delete it, and on failure additionally dumps the installed ESP's
   partition table and signature summaries before the trap destroys the
   target disk — small, decisive post-mortems in place of the 2 GiB
   artifacts that would otherwise need re-pulling by hand.

Post-mortem blocks must never be able to kill themselves: they run with
`set +e` inside, because one unguarded non-zero under
`set -euo pipefail` silently truncates everything after it — "in the block
whose whole job is leaving a trace."

## Consequences

- Zero artifact-store infrastructure to run, secure, or garbage-collect.
- Evidence for a failed run is wherever the failure was: the workflow log
  first, then `/var/lib/snosi-lab/secure/logs` on the host for the secure
  lane. Operators must know both places
  ([README.md — Operating it](../../README.md#operating-it)).
- Host-persisted logs are unbounded by any rotation; the directory needs
  occasional manual pruning (accepted for one lane's worth of logs).
- Log-surface evidence is lost when the Workflow object ages out of
  retention (failed 30 days, successful 7); only what the collector
  captured in `runs.json` survives ([ADR-0004](0004-one-way-evidence-pipeline.md)).
- The 40/400 sizing convention is the pattern for new lanes: dump little on
  success, everything useful on failure, and always through `|| true`.

## Alternatives considered

- **MinIO/S3 artifact repository:** real infrastructure with credentials,
  capacity, and GC on a cluster whose design goal is minimal surface;
  rejected as long as host disk plus sized log dumps answer every
  post-mortem that has actually occurred.
- **Trusting pod logs:** empirically failed three times on the lane with
  the largest output; kubelet rotation is not under this repo's control.
- **Keeping guest disks/VMs on failure for inspection:** the incus fixture
  is shared and serialized ([ADR-0007](0007-cross-workflow-concurrency-via-template-semaphores.md));
  a kept VM blocks the next run. The ESP dump extracts the decisive
  fraction instead.

## References

- Shapes: [README.md — Reporting](../../README.md#reporting)
- Implemented by:
  [argo/workflow-templates/run-secure-install-tests.yaml](../../argo/workflow-templates/run-secure-install-tests.yaml),
  [argo/workflow-templates/run-incus-vm-tests.yaml](../../argo/workflow-templates/run-incus-vm-tests.yaml),
  [argo/workflow-templates/run-incus-disk-tests.yaml](../../argo/workflow-templates/run-incus-disk-tests.yaml),
  and the `/tmp/results` + console-tail pattern in every lane
- Builds on: core
  [ADR-0004](https://github.com/frostyard/core/blob/main/docs/adr/0004-product-namespaced-filesystem-tiers.md);
  [ADR-0004](0004-one-way-evidence-pipeline.md) (repo-local)
- Related: [ADR-0005](0005-console-marker-protocol-for-agentless-guests.md)
