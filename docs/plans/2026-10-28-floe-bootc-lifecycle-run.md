# Plan: First Floe bootc N → N+1 update and rollback run

This plan prepares one manual
[`run-snow-bootc-lifecycle`](../../argo/workflow-templates/run-snow-bootc-lifecycle.yaml)
run for **Floe**, the headless server image: a Firn-installed Floe image N,
staged to N+1 by the shipped updater, booted, rolled back and booted again as
N. It holds the selected public inputs, the read-only
[preflight record](floe-bootc-lifecycle-run-2026-10-28/preflight-record.txt),
the known causes of a non-`PASS` result, and the submit steps for Brian. The
lane takes the product from the manifest field `product`
([ADR-0012](../adr/0012-lifecycle-lane-product-comes-from-the-hashed-manifest.md));
everything else is the lane that passed for Snow in the
[Snow run plan](2026-10-26-snow-bootc-lifecycle-run.md). The lane stays
advisory and not a release gate
([ADR-0003](../adr/0003-unproven-is-distinct-from-failed.md),
[ADR-0010](../adr/0010-vacuous-success-is-forbidden.md)). The field contract is
in the [README](../../README.md#manual-snow-bootc-lifecycle). No run was
submitted when this plan was written.

## Selected inputs

The manifest is [`manifest.json`](floe-bootc-lifecycle-run-2026-10-28/manifest.json)
(file SHA-256 `765d005e…c4399`; canonical lane `manifest_sha256`
`f8fa7b8d…ec22c`). Every value is public. Selected on 2026-10-29 at about 01:30Z; the ISO
fields were refreshed at 14:06Z after the index rotated. snosi was read only.

| Input | Value | Why |
| --- | --- | --- |
| ISO | `snosi-installer_20261029101909_x86-64.iso`, SHA-256 `28747d49…913b42` | The only entry in the signed `SHA256SUMS` index at 14:06Z on 2026-10-29. It replaced `20261028230444` (SHA-256 `d045e27c…c45366`, the ISO at selection); only the ISO fields changed. The index rotates with each ISO build, so expect step 2 below to refresh it again (same procedure as the Snow plan). Its Firn catalog has exactly one `floe` bootc entry (snosi `test/firn-catalog-test.sh` requires the four bootc products). Firn 0.10.0, hash `2a965b92…497af`. The initramfs has no `bootc`, so Firn runs `bootc install` from image N through podman. |
| N | `20261026191636`, `sha256:fb6646f9…ecbf843` | The last Floe build with `bootc-1.16.8-storage-digest-v1` (snosi `b3d14e2c`, before the bootc 1.17.1 move in snosi#1157). Installed Floe hosts from earlier media run this assembly, so N → N+1 is the update those hosts take. |
| N+1 | `20261028230726`, `sha256:bb1269a5…95d592` | `bootc-1.17.1-storage-digest-v1`, snosi `a148c664` (main at selection), `latest` at selection. The lane accepts a different assembly for each image (lab #153), as in the Snow 1.16.8 → 1.17.1 run `run-snow-bootc-lifecycle-l6596`. |
| Target | `ghcr.io/frostyard/floe:qa-controlled` | Operator-owned. No snosi workflow writes it. It does not exist yet; Brian creates it at N+1 (step 3). |
| Trust pins | snosi `39cf1988`, index key `977fe2de…0fc97`, cosign key `3e797504…14fc5`, MOK cert `0446e478…545b4`, fingerprint `F37282A3…8216FD68` | The lane's pinned values, the same as for Snow. The three files are byte-identical at `39cf1988`, at `b3d14e2c` (N) and at `a148c664` (N+1). Floe images are signed with the same cosign key, and the shipped signature policy names `ghcr.io/frostyard/floe` with the same `sigstoreSigned` rule as Snow. |

Floe is a headless guest. The lifecycle guest probe has no desktop check for
any product (no GDM, GNOME, `graphical.target`, Flatpak or first-setup), so
Floe runs the same five attested boots as Snow: Secure Boot, kernel lockdown,
a measured Type #2 UKI, a TPM-unlocked LUKS root on btrfs, the shipped
signature policy, the signed pull of N+1, the expected `bootc status`, and the
fs-verity count of each boot. The record travels on `ttyS0` as for Snow. On
Floe `/dev/console` can also be `ttyS0`; the guest then writes the same line
twice, and the host treats identical copies as one record.

## Known causes of a non-PASS result

- **btrfs fs-verity on kernel 7.2 ([snosi#1130](https://github.com/frostyard/snosi/issues/1130)).**
  Debian kernels `7.2.11+deb14-amd64` and `7.2.12+deb14-amd64` log
  `fs-verity … FILE CORRUPTED!` on btrfs when readahead verifies a large folio
  before all of it is read (upstream fix kdave/linux `dcf8811f63af`, not in a
  Debian kernel; forky had `7.2.12-1` at selection). The re-read succeeds;
  no bad bytes reach user space. Floe reproduced it in the snosi#1130 lab on
  both a first boot and two updates. In this run expect counts above 0,
  mostly in the `stage` boot: the stage runs N's updater on bootc 1.16.8,
  which reads every composefs object of the new image in full (bootc 1.17.1
  removed that re-read, but only N+1 carries 1.17.1). On a listed kernel the
  lane records the count (`fsverity_known_defect=<kernel>`) and does not fail.
  If the Floe kernel of any boot is **not** on the list and its count is above
  0, the run ends `FAILED: fsverity_errors` after all five boots: then a human
  checks whether that kernel carries the fix, and either adds it to
  `known_defect_kernels` (with evidence) or removes the list entry when the
  kernel canary says `clean`. Do not edit the list to make a run pass. A PASS
  says nothing about the resolution of #1130.
- **Index rotation.** `iso_index_entry` fails as soon as a newer ISO replaces
  the selected one. Refresh the ISO fields as in step 2; do not change N or
  N+1.
- **Tag drift.** Any move of `ghcr.io/frostyard/floe:qa-controlled` during the
  run fails it (`tag_drift`).

## Phase 1 — The lane accepts Floe (this lab PR)

- `qa.py` takes the repository from `product` (`snow` or `floe`), checks the
  Firn catalog entry and the signature-policy entry of that product, and keeps
  every Snow check as before. The operator preflight selects the catalog
  entry by the same field. `tests/test_floe_bootc_lifecycle.py` covers the
  Floe contract; the Snow test files are unchanged.
- **Done when:** the PR is merged and Argo CD `frostyard-lab` is `Synced` at
  that commit (step 1 below checks the ConfigMap).
  Met: lab#167 merged as `25367a01`; on 2026-10-29 at 14:05Z Argo CD was
  `Synced` and `Healthy` at `5faa2171` (after it), and the cluster ConfigMap
  `qa.py` names `ghcr.io/frostyard/floe`.

## Phase 2 — Preflight (done 2026-10-29, read-only)

[`scripts/snow-lifecycle-preflight/run.sh`](../../scripts/snow-lifecycle-preflight/run.sh)
runs the same checks as for Snow, with the catalog entry of the manifest
product. While `qa-controlled` does not resolve to N+1, only that one resolve
is deferred. It never writes to a registry, a tag or the cluster.

- **Done when:** the [record](floe-bootc-lifecycle-run-2026-10-28/preflight-record.txt)
  shows every check `PASS`, `target_ref PENDING`, `lane_preflight PASS` and
  `failures=0`. Met at 2026-10-29T01:20:14Z (ISO `20261028230444`, from the
  Phase 1 branch's lane code), and again at 2026-10-29T14:08:03Z (ISO
  `20261029101909`, lane code from lab `main` `5faa2171`, Argo CD `Synced`
  there): both images signed, both assembly labels accepted, `iso_catalog` on
  the `floe` entry. The record holds the second run.

## Phase 3 — Submit (Brian)

Run from a checkout of lab `main` after Phase 1, with the kube context of the
lab cluster. Steps 3 and 5 are publications and stay with Brian. The lane
holds the `snosi-vm-qa` semaphore, so a Snow run and this run never overlap.

1. **Lane is current.**

   ```bash
   kubectl -n argocd get application frostyard-lab \
     -o jsonpath='{.status.sync.status} {.status.sync.revision}{"\n"}'
   kubectl -n argo get configmap snow-bootc-lifecycle-scripts \
     -o jsonpath='{.data.qa\.py}' | grep -c 'ghcr.io/frostyard/floe'   # must be >= 1
   argo list -n argo --running                                         # note queued VM lanes
   ```

2. **Fresh preflight (read-only).**

   ```bash
   MANIFEST_FILE=docs/plans/floe-bootc-lifecycle-run-2026-10-28/manifest.json
   PREFLIGHT_IMAGE=docker.io/library/debian:trixie PREFLIGHT_PULL=never \
     scripts/snow-lifecycle-preflight/run.sh "$MANIFEST_FILE"
   ```

   Expect every check `PASS`, `target_ref PENDING` and `failures=0`. If
   `iso_index_entry` fails, copy the manifest to a local file, set `iso_url`,
   `iso_sha256` and `iso_version` from the single line of
   `https://repository.frostyard.org/isos/native/v1/SHA256SUMS`, point
   `MANIFEST_FILE` at it, and run this step again.

3. **Create the controlled tag at N+1 (publication).** With an account that
   can write `ghcr.io/frostyard/floe`:

   ```bash
   skopeo login ghcr.io
   skopeo copy --all --preserve-digests --remove-signatures \
     docker://ghcr.io/frostyard/floe@sha256:bb1269a59a224e05b429776661b419f909d5d58d92c16048129410082d95d592 \
     docker://ghcr.io/frostyard/floe:qa-controlled
   skopeo inspect --no-tags docker://ghcr.io/frostyard/floe:qa-controlled | jq -r .Digest
   # must print sha256:bb1269a59a224e05b429776661b419f909d5d58d92c16048129410082d95d592
   ```

   `crane tag ghcr.io/frostyard/floe@sha256:bb1269a5… qa-controlled` is an
   equivalent manifest-only tag.

4. **Final preflight.** Run step 2 again. Expect `target_ref PASS`,
   `lane_preflight PASS full qa.py preflight` and `failures=0`.

5. **Submit (publication of a run).** Keep `keep-vm-on-failure` at its default
   `false`, and keep the exact workflow name.

   ```bash
   if [[ -n ${MANIFEST_FILE:-} && -s "$MANIFEST_FILE" ]]; then
     WF=$(argo submit --namespace argo --from workflowtemplate/run-snow-bootc-lifecycle \
       -p "manifest-json=$(<"$MANIFEST_FILE")" -o name)
     printf 'workflow=%s\n' "$WF"
   else
     printf '%s\n' 'Set MANIFEST_FILE to a reviewed nonempty public JSON file' >&2
     false
   fi
   ```

6. **Hold the tag still until the workflow ends** (`tag_drift` otherwise).

7. **Watch and read the result of that workflow only.**

   ```bash
   argo watch -n argo "$WF"
   argo get -n argo "$WF" -o json | jq -r '.status.nodes[] | .outputs.parameters[]? | "\(.name)=\(.value)"'
   ```

   Private evidence is under
   `/var/lib/snosi-lab/snow-bootc-evidence/<workflow-name>/` on the
   control-plane node; `manifest-sha256.txt` there names the Floe manifest.

8. **After the run.** Leave `qa-controlled` in place. Do **not** delete it
   through the GitHub package UI: GHCR deletes a package version by digest,
   which would delete N+1 itself with its version and `latest` tags.

- **Done when:** the workflow ends and its `result` and `checks` outputs are
  recorded in Phase 4.

## Phase 4 — Record the result

- Add the workflow name, `result`, `checks` and the canonical
  `manifest_sha256` to this plan. The result line does not name the product:
  the manifest hash does. A `FAILED` or `BLOCKED` result is evidence too:
  record the code (see the known causes above), then fix forward.
- **Done when:** the result is recorded here and the run data shows the run.

## Open questions

- **A separate `product` workflow parameter:** this plan follows
  [ADR-0012](../adr/0012-lifecycle-lane-product-comes-from-the-hashed-manifest.md)
  (product from the hashed manifest only). Decide before a second product run
  whether the submit command must also name the product.
- **Spec transport after stage:** as in the Snow plan, a `PASS` does not keep
  the observed transport per phase.

## References

- Implements: [README — Manual Snow bootc lifecycle](../../README.md#manual-snow-bootc-lifecycle),
  [quality.md — evidence](../quality.md#manual-snow-bootc-lifecycle-evidence)
- Decisions: [ADR-0012](../adr/0012-lifecycle-lane-product-comes-from-the-hashed-manifest.md),
  [ADR-0003](../adr/0003-unproven-is-distinct-from-failed.md),
  [ADR-0005](../adr/0005-console-marker-protocol-for-agentless-guests.md),
  [ADR-0010](../adr/0010-vacuous-success-is-forbidden.md)
- Snow precedent: [2026-10-26 Snow run plan](2026-10-26-snow-bootc-lifecycle-run.md)
- snosi (read only): [snosi#1130](https://github.com/frostyard/snosi/issues/1130),
  `mkosi.images/base/mkosi.extra/usr/libexec/bootc-update-stage`,
  `shared/bootc-secure/tree/etc/containers/policy.json`,
  `shared/firn-installer/catalog.json`
