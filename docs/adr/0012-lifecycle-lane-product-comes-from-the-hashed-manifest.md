# 0012 — The lifecycle lane takes its product from the hashed manifest

- **Status:** Accepted
- **Date:** 2026-10-28

## Context

The manual bootc lifecycle lane
([`run-snow-bootc-lifecycle`](../../argo/workflow-templates/run-snow-bootc-lifecycle.yaml))
accepted Snow only: `qa.py` rejected any manifest whose `family` and
`product` were not `snow`, and the image repository, the Firn catalog entry
and the signature-policy entry were fixed to `ghcr.io/frostyard/snow`. Floe,
the headless server image, uses the same updater
(`/usr/libexec/bootc-update-stage`), the same secure bootc assembly, the same
shipped signature policy and the same Firn installer media, so its update and
rollback path can be proven by the same five boots.

The lane's manifest already carries `family` and `product`. It is canonical
JSON: its SHA-256 (`manifest_sha256`) is persisted before any boot and is
bound to the preflight record and every phase tag. A WorkflowTemplate input
parameter is not part of that hash.

## Decision

The manifest field `product` selects the image: `snow` or `floe`, with
`family` equal to `product`. `qa.py` derives the repository
`ghcr.io/frostyard/<product>` from it and requires every image, immutable
version tag and controlled target tag in the manifest to be in that
repository. The ISO catalog check selects the Firn catalog entry with that
name, and the guest policy check requires the shipped `sigstoreSigned` rule
for that repository. The operator preflight
(`scripts/snow-lifecycle-preflight/`) selects the catalog entry by the same
field.

The WorkflowTemplate gets no separate `product` input parameter. The guest
probe stays product-neutral: it attests boot security, the bootc status, the
signature policy and the signed pull, LUKS and the UKI, and counts fs-verity
errors. It has no desktop check, so Floe runs it as a headless guest with no
product branch. Names that say "snow" (the template, the ConfigMap, the VM and
hostname `snow-qa`, the `SNOW_QA_*` markers and the evidence path) stay: they
name the lane, not the image.

## Consequences

- One manifest field decides the product, and it is in the evidence hash: a
  run cannot record one product and test another.
- A Snow manifest is byte-for-byte as valid as before, so the Snow offline
  tests pass unchanged.
- A Floe run reports the same result codes and checks as a Snow run; only
  the manifest (and its hash) tells them apart. Read the manifest with the
  result.
- Adding Snowfield or Sundog later is one entry in `PRODUCTS` plus a run plan;
  any product-specific guest check would then need its own decision.
- The submit command does not show the product on its own; the operator reads
  it in the reviewed manifest file.

## Alternatives considered

- **A WorkflowTemplate input `product` that must equal the manifest field:**
  rejected. It duplicates a hashed field with an unhashed one, adds a
  mismatch path, and changes the pinned template contract (and the dev-submit
  rendering) for no new evidence.
- **A second template for Floe:** rejected. It would copy about 2,500 lines of
  lane code that must stay identical for both products.

## References

- Shapes: [plans/2026-10-28-floe-bootc-lifecycle-run.md](../plans/2026-10-28-floe-bootc-lifecycle-run.md),
  [README — Manual Snow bootc lifecycle](../../README.md#manual-snow-bootc-lifecycle)
- Builds on: [ADR-0003](0003-unproven-is-distinct-from-failed.md),
  [ADR-0005](0005-console-marker-protocol-for-agentless-guests.md),
  [ADR-0010](0010-vacuous-success-is-forbidden.md)
