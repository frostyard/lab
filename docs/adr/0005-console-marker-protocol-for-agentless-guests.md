# 0005 — Console markers over serial + SMBIOS credentials drive agentless guests

- **Status:** Accepted
- **Date:** 2026-08-12

## Context

The VM lanes must run commands inside guests that offer no channel in: snosi
images ship no incus guest agent, and a live installer ISO has no
provisioned SSH key. Before a system is installed, the serial console is the
only channel that exists at all. Provisioning SSH or baking an agent into
the images would change the artifact under test to make it testable.

## Decision

Guests are driven with **zero SSH and zero agent**, using two systemd
mechanisms that work against unmodified images:

- **Inbound — SMBIOS type-11 credentials.** systemd reads credentials from
  SMBIOS type 11, and the well-known `systemd.extra-unit.<name>` credential
  defines an entire unit from thin air. Each lane passes qemu (via incus
  `raw.qemu`) two credentials — the base64-encoded QA unit and a
  `multi-user.target` drop-in that pulls it in — and the guest executes the
  work at boot with no cooperation from the image. The same mechanism
  carries a second unit set for post-install assertions after the ISO is
  detached
  ([argo/workflow-templates/run-incus-install-tests.yaml](../../argo/workflow-templates/run-incus-install-tests.yaml)).
- **Outbound — marker lines on the serial console.** The injected scripts
  `tee` to `/dev/console` and print deliberately distinctive markers that
  cannot collide with boot chatter: `SNOSI_QA__INSTALL_BEGIN`,
  `SNOSI_QA__INSTALL_OK`, `SNOSI_QA__INSTALL_FAIL`,
  `SNOSI_QA__CHECK key=value`, `SNOSI_QA__CHECKS_DONE` (the firn lane uses
  the same protocol with a `FIRN_QA__` prefix,
  [argo/workflow-templates/run-firn-install-tests.yaml](../../argo/workflow-templates/run-firn-install-tests.yaml)).
  The lane polls `incus console --show-log` for the markers with per-phase
  timeouts rather than sleeping fixed budgets, and greps the
  `SNOSI_QA__CHECK` lines into `checks.txt`
  ([ADR-0004](0004-one-way-evidence-pipeline.md)).

## Consequences

- The lab tests exactly the artifact a user receives; nothing is added to
  images for testability.
- The console is both the control signal and the evidence, which is why the
  lanes preserve it aggressively
  ([ADR-0009](0009-no-artifact-store-logs-are-the-surface.md)).
- Marker greps must be resilient: a guest that prints `CHECKS_DONE` but no
  `CHECK` lines makes `grep` exit non-zero, and under `set -euo pipefail`
  that kills the lane before the diagnostics run — the `|| true` on the
  checks extraction is load-bearing (docs/roadmap.md item A5 found and
  fixed four instances of this shape).
- The mechanism requires systemd in the guest and qemu/OVMF-style firmware
  (SMBIOS); it cannot drive non-systemd images.
- The parameter-passing surface into the guest is base64-through-qemu
  arguments, which is awkward for large payloads; scripts are staged to
  `/run` and executed there.

## Alternatives considered

- **incus guest agent:** not shipped in snosi images, and adding it would
  test a modified image.
- **SSH with an injected key:** no way to inject a key into a live ISO
  without rebuilding it; post-install it would still need a first channel
  to install the key — which is this mechanism.
- **cloud-init:** snosi images do not carry it; same objection as the
  agent.
- **Screenshot/OCR-style console scraping:** the serial console with
  greppable markers is strictly more reliable and diffable.

## References

- Shapes: [README.md — Driving a guest with no agent and no SSH](../../README.md#driving-a-guest-with-no-agent-and-no-ssh)
- Implemented by:
  [argo/workflow-templates/run-incus-install-tests.yaml](../../argo/workflow-templates/run-incus-install-tests.yaml),
  [argo/workflow-templates/run-incus-bootc-install-tests.yaml](../../argo/workflow-templates/run-incus-bootc-install-tests.yaml),
  [argo/workflow-templates/run-firn-install-tests.yaml](../../argo/workflow-templates/run-firn-install-tests.yaml),
  [argo/workflow-templates/run-incus-vm-tests.yaml](../../argo/workflow-templates/run-incus-vm-tests.yaml)
- Related: [ADR-0004](0004-one-way-evidence-pipeline.md),
  [ADR-0009](0009-no-artifact-store-logs-are-the-surface.md)
