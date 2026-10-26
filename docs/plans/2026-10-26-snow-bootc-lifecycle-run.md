# Plan: First Snow bootc N → N+1 update and rollback run

This plan prepares one manual
[`run-snow-bootc-lifecycle`](../../argo/workflow-templates/run-snow-bootc-lifecycle.yaml)
run: a Firn-installed Snow image N, staged to N+1 by the shipped updater,
booted, rolled back and booted again as N. It holds the selected public
inputs, a [preflight record](snow-bootc-lifecycle-run-2026-10-26/preflight-record.txt)
with each check shown, and the exact submit steps. Nothing here is a run or a
pass: the lane stays advisory and unproven
([ADR-0003](../adr/0003-unproven-is-distinct-from-failed.md),
[ADR-0010](../adr/0010-vacuous-success-is-forbidden.md)) until a submitted
run reaches `PASS: verified_five_fresh_boots`. The field contract is in the
[README](../../README.md#manual-snow-bootc-lifecycle); evidence limits are in
[quality.md](../quality.md#manual-snow-bootc-lifecycle-evidence).

## Selected inputs

The manifest is [`manifest.json`](snow-bootc-lifecycle-run-2026-10-26/manifest.json)
(file SHA-256 `7298395e…c829a`; canonical lane `manifest_sha256`
`a68f5c54…fdf4dd`). Every value is public.

| Input | Value | Why |
| --- | --- | --- |
| ISO | `snosi-installer_20261025233920_x86-64.iso`, SHA-256 `664047bc…9e3a7e` | The only entry in the signed `SHA256SUMS` index on 2026-10-26. Firn 0.9.0, hash `a37d9d07…6013f`. The initramfs has no `bootc`, so Firn runs `bootc install` from image N through podman. |
| N | `20261025113924`, `sha256:01a5af1b…f78cb2b` | Signed, `bootc-1.16.8-storage-digest-v1`, version annotation present (snosi#1126). Built after snosi main `6b0e980e` (2026-10-25 07:58 UTC). |
| N+1 | `20261025201457`, `sha256:2a51013c…143b57e5` | Signed, same assembly. The newest Snow image and `:latest` at selection time. Same snosi source as N, so the update changes packages, not the updater. |
| Target | `ghcr.io/frostyard/snow:qa-controlled` | Operator-owned. No snosi workflow writes it. It does not exist yet; Brian creates it at N+1 (step 3). |
| Trust pins | snosi `39cf1988`, index key `977fe2de…0fc97`, cosign key `3e797504…14fc5`, MOK cert `0446e478…545b4`, fingerprint `F37282A3…8216FD68` | The lane's pinned values. The three files are byte-identical at snosi main `6b0e980e`, and the ISO embeds the same bytes. |

The stale tag `ghcr.io/frostyard/snow:lab-snow-n1` (build `20260925030819`)
is from the 2026-09-26 attempt `run-snow-bootc-lifecycle-6hvn6`
(`install_failed:firn_install`). This run does not use it.

### bootc 1.17.1: do not wait for it

[bootc-debian#10](https://github.com/frostyard/bootc-debian/pull/10) merged at
2026-10-25 21:18 UTC. The next snosi image build (run `40245723511`,
23:39 UTC) failed all four secure builds with `expected bootc 1.16.8, observed
bootc 1.17.1`: the secure assembly pins bootc 1.16.8
(`shared/bootc-secure/tree/usr/lib/snosi/bootc-secure.json`), and changing it
requires the revalidation in snosi's
`docs/bootc-secure-assembly-compatibility.md`. No 1.17.1 Snow image exists.
When one exists it carries `bootc-1.17.1-storage-digest-v1`, and this lane
rejects it in preflight (`oci_assembly`), because `qa.py` pins
`bootc-1.16.8-storage-digest-v1`. So N+1 = `20261025201457` (the last 1.16.8
image) is the right choice now. An N (1.16.8) → N+1 (1.17.1) run is the more
valuable later proof; it needs a lab change to pin the assembly per image.

## Phase 1 — Correct the lane's guest contract (lab PR #PR_A)

The lane never got past install, so its guest expectations were never met by
real software. Read against Firn and snosi source, a run with the current
`main` lane fails before it proves update or rollback:

- Firn passes `--target-imgref` to `bootc install`, so the installed spec is
  `target_ref`, not `image_n`. The lane expects `image_n` (`target_spec` at
  `installed-n`).
- The shipped updater stages with `bootc switch --transport
  containers-storage`, so the spec transport after stage is not `registry`.
- `/run/snosi/update-staged` holds `image=`, `digest=` and `staged_at=`
  lines; the lane compares the whole file to `sha256:<N+1>` (`stage_marker`).
- The lane force-stops the VM after each phase. That skips
  `bootc composefs-finalize-staged`, so N+1 never deploys.
- The hourly `bootc-update-stage.timer` can stage N+1 during the
  `installed-n` boot and break the sequence.

- **Done when:** lab PR #PR_A is merged and Argo CD `frostyard-lab` is
  `Synced` at that commit (step 1 below checks the ConfigMap).

## Phase 2 — Preflight (done 2026-10-26, read-only)

[`scripts/snow-lifecycle-preflight/run.sh`](../../scripts/snow-lifecycle-preflight/run.sh)
runs, in a disposable Debian trixie container with the lane's apt tools:
key hashes at the pinned commit, the index key fingerprint, the `SHA256SUMS`
signature and entry, the ISO bytes, the ISO ESP, initramfs, embedded keys,
catalog, release and Firn hash, `cosign verify` of both digests, their labels,
version annotations and immutable version tags, and the target tag. It then
runs the lane's own ConfigMap `qa.py validate` and `qa.py preflight`. While
the target tag does not resolve to N+1, only that one resolve is deferred.
It never writes to a registry, a tag or the cluster.

- **Done when:** the [record](snow-bootc-lifecycle-run-2026-10-26/preflight-record.txt)
  shows every check `PASS`, `target_ref PENDING`, `lane_preflight PASS` and
  `failures=0`. Met at 2026-10-26T00:25:46Z.

## Phase 3 — Submit (Brian)

Run from a checkout of lab `main` after Phase 1, with the kube context of the
lab cluster. Steps 3 and 5 are publications and stay with Brian.

1. **Lane is current.** Check that Argo CD synced the fixed scripts:

   ```bash
   kubectl -n argocd get application frostyard-lab \
     -o jsonpath='{.status.sync.status} {.status.sync.revision}{"\n"}'
   kubectl -n argo get configmap snow-bootc-lifecycle-scripts \
     -o jsonpath='{.data.run\.sh}' | grep -c guest_shutdown   # must be >= 1
   argo list -n argo --running                              # note queued VM lanes
   ```

2. **Fresh preflight (read-only).**

   ```bash
   MANIFEST_FILE=docs/plans/snow-bootc-lifecycle-run-2026-10-26/manifest.json
   PREFLIGHT_IMAGE=docker.io/library/debian:trixie PREFLIGHT_PULL=never \
     scripts/snow-lifecycle-preflight/run.sh "$MANIFEST_FILE"
   ```

   Expect every check `PASS`, `target_ref PENDING` and `failures=0`. The
   ISO index holds one ISO and rotates with each ISO build. If
   `iso_index_entry` fails, copy the manifest to a local file, set
   `iso_url`, `iso_sha256` and `iso_version` from the single line of
   `https://repository.frostyard.org/isos/native/v1/SHA256SUMS`, point
   `MANIFEST_FILE` at it, and run this step again: it re-verifies the
   signature, the bytes, the embedded keys and Firn. Do not change N or N+1.

3. **Create the controlled tag at N+1 (publication).** With an account that
   can write `ghcr.io/frostyard/snow`:

   ```bash
   skopeo login ghcr.io
   skopeo copy --all --preserve-digests --remove-signatures \
     docker://ghcr.io/frostyard/snow@sha256:2a51013c012d0c1def2e8fee8c532ace3d8e3f320404df14af384fa2143b57e5 \
     docker://ghcr.io/frostyard/snow:qa-controlled
   skopeo inspect --no-tags docker://ghcr.io/frostyard/snow:qa-controlled | jq -r .Digest
   # must print sha256:2a51013c012d0c1def2e8fee8c532ace3d8e3f320404df14af384fa2143b57e5
   ```

   `--preserve-digests` refuses any copy that would change the digest.
   `--remove-signatures` keeps skopeo from writing signature attachments; the
   existing cosign signature binds the digest, not the tag. On a Snow host the
   signature policy verifies the source during the copy. `crane tag
   ghcr.io/frostyard/snow@sha256:2a51… qa-controlled` is an equivalent
   manifest-only tag.

4. **Final preflight.** Run step 2 again. Expect `target_ref PASS`,
   `lane_preflight PASS full qa.py preflight` and `failures=0`.

5. **Submit (publication of a run).** Use the README interface unchanged,
   with `keep-vm-on-failure` at its default `false`:

   ```bash
   if [[ -n ${MANIFEST_FILE:-} && -s "$MANIFEST_FILE" ]]; then
     argo submit --namespace argo --from workflowtemplate/run-snow-bootc-lifecycle \
       -p "manifest-json=$(<"$MANIFEST_FILE")"
   else
     printf '%s\n' 'Set MANIFEST_FILE to a reviewed nonempty public JSON file' >&2
     false
   fi
   ```

6. **Hold the tag still until the workflow ends.** The lane rechecks
   `qa-controlled` before install, before each phase, and after stage, N+1
   boot and rollback. Any move fails the run (`tag_drift`).

7. **Watch and read the result.**

   ```bash
   argo watch -n argo @latest
   argo get -n argo @latest -o json | jq -r '.status.nodes[] | .outputs.parameters[]? | "\(.name)=\(.value)"'
   ```

   Private evidence is under
   `/var/lib/snosi-lab/snow-bootc-evidence/<workflow-name>/` on the
   control-plane node. The run can take up to 16 h (queue included).

8. **After the run.** Leave `qa-controlled` in place; it names a signed
   image. Do **not** delete it through the GitHub package UI: GHCR deletes a
   package version by digest, which would delete N+1 itself with its
   `20261025201457` and `latest` tags. A later run moves the tag to its own
   N+1.

- **Done when:** the workflow ends and its `result` and `checks` outputs are
  recorded in Phase 4.

## Phase 4 — Record the result

- Add the workflow name, `result`, `checks` and the observed
  `spec_transport` per phase to this plan. A `FAILED` or `BLOCKED` result is
  evidence too: record the code, then fix forward.
- **Done when:** the result is recorded here and the run data shows the run.

## Open questions

- **Spec transport after stage:** bootc reports the staged or booted
  deployment's spec. #PR_A requires `registry` at `installed-n` and before
  stage, and accepts `registry` or `containers-storage` after it. Decide the
  exact per-phase value from this run's records, by the next lifecycle change.
- **fs-verity noise during stage:** [snosi#1130](https://github.com/frostyard/snosi/issues/1130)
  (btrfs kernel 7.2 defect) can log `FILE CORRUPTED!` during staging on a
  Firn btrfs install. The lane does not judge kernel logs; a pass here says
  nothing about #1130.
- **Assembly pin per image:** needed before an N → N+1 run that crosses a
  bootc version (see the bootc 1.17.1 section). Decide when snosi moves to
  1.17.1.

## References

- Implements: [README — Manual Snow bootc lifecycle](../../README.md#manual-snow-bootc-lifecycle),
  [quality.md — evidence](../quality.md#manual-snow-bootc-lifecycle-evidence)
- Decisions: [ADR-0003](../adr/0003-unproven-is-distinct-from-failed.md),
  [ADR-0005](../adr/0005-console-marker-protocol-for-agentless-guests.md),
  [ADR-0010](../adr/0010-vacuous-success-is-forbidden.md)
- snosi: `docs/specs/bootc-secure-lab-handoff.md`,
  `mkosi.images/base/mkosi.extra/usr/libexec/bootc-update-stage`
