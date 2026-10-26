# Plan: First Snow bootc N → N+1 update and rollback run

This plan prepares one manual
[`run-snow-bootc-lifecycle`](../../argo/workflow-templates/run-snow-bootc-lifecycle.yaml)
run: a Firn-installed Snow image N, staged to N+1 by the shipped updater,
booted, rolled back and booted again as N. It holds the selected public
inputs, a [preflight record](snow-bootc-lifecycle-run-2026-10-26/preflight-record.txt)
with each check shown, the exact submit steps and, in
[Phase 4](#result-2026-10-26-pass), the result: the first manual main-lane
`PASS: verified_five_fresh_boots` on 2026-10-26. The lane stays advisory and
not a release gate
([ADR-0003](../adr/0003-unproven-is-distinct-from-failed.md),
[ADR-0010](../adr/0010-vacuous-success-is-forbidden.md)). The field contract is in the
[README](../../README.md#manual-snow-bootc-lifecycle); evidence limits are in
[quality.md](../quality.md#manual-snow-bootc-lifecycle-evidence).

## Selected inputs

The manifest is [`manifest.json`](snow-bootc-lifecycle-run-2026-10-26/manifest.json)
(file SHA-256 `bdb86a01…368409`; canonical lane `manifest_sha256`
`43df99b1…a4f335`). Every value is public.

| Input | Value | Why |
| --- | --- | --- |
| ISO | `snosi-installer_20261026003253_x86-64.iso`, SHA-256 `e70df1a4…cc2a2a` | The only entry in the signed `SHA256SUMS` index at 01:03Z on 2026-10-26 (built from snosi `0a7c519c`). The index had rotated away from `20261025233920` within 40 minutes, so expect step 2 to refresh it again. Firn 0.9.0, hash `a37d9d07…6013f`. The initramfs has no `bootc`, so Firn runs `bootc install` from image N through podman. |
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
`docs/bootc-secure-assembly-compatibility.md`. snosi#1150 (2026-10-26 00:50 UTC)
then pinned the known-good bootc/libostree pair, so new Snow builds stay on
1.16.8. No 1.17.1 Snow image exists.
When one exists it carries `bootc-1.17.1-storage-digest-v1`, and this lane
rejects it in preflight (`oci_assembly`), because `qa.py` pins
`bootc-1.16.8-storage-digest-v1`. So waiting gains nothing, and N+1 =
`20261025201457` (the newest 1.16.8 image at selection) stays. A newer 1.16.8
build from snosi `37b042bb` (#1144, #1150) is also a valid N+1; keep this one
unless Brian wants those changes in the update. An N (1.16.8) → N+1 (1.17.1) run is the more
valuable later proof; it needs a lab change to pin the assembly per image.

This paragraph records the selection rationale on 2026-10-26. Since lab #153,
preflight accepts `bootc-1.16.8-storage-digest-v1` or
`bootc-1.17.1-storage-digest-v1` for each image independently and records
both, so the N (1.16.8) → N+1 (1.17.1) run Brian approved can follow this one.

## Phase 1 — Correct the lane's guest contract (lab PR [#146](https://github.com/frostyard/lab/pull/146))

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

- **Done when:** lab PR [#146](https://github.com/frostyard/lab/pull/146) is merged and Argo CD `frostyard-lab` is
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
  `failures=0`. Met at 2026-10-26T00:25:46Z (ISO `20261025233920`) and again
  at 2026-10-26T01:03:41Z (ISO `20261026003253`).

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

5. **Submit (publication of a run).** Use the README interface, with
   `keep-vm-on-failure` at its default `false`, and keep the exact workflow
   name: `@latest` can name another workflow in the namespace.

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

6. **Hold the tag still until the workflow ends.** The lane rechecks
   `qa-controlled` before install, before each phase, and after stage, N+1
   boot and rollback. Any move fails the run (`tag_drift`).

7. **Watch and read the result of that workflow only.**

   ```bash
   argo watch -n argo "$WF"
   argo get -n argo "$WF" -o json | jq -r '.status.nodes[] | .outputs.parameters[]? | "\(.name)=\(.value)"'
   ```

   Private evidence is under
   `/var/lib/snosi-lab/snow-bootc-evidence/<workflow-name>/` on the
   control-plane node. The run itself is bounded at 17 h, 18 h with the
   semaphore queue (after #146).

8. **After the run.** Leave `qa-controlled` in place; it names a signed
   image. Do **not** delete it through the GitHub package UI: GHCR deletes a
   package version by digest, which would delete N+1 itself with its
   `20261025201457` and `latest` tags. A later run moves the tag to its own
   N+1.

- **Done when:** the workflow ends and its `result` and `checks` outputs are
  recorded in Phase 4.

## Phase 4 — Record the result

- Add the workflow name (`$WF`), `result` and `checks` to this plan. The
  retained `checks` hold boot IDs, kernels and fs-verity counts per phase, not
  the observed `spec_transport`: that value travels only in the transient
  serial record. A `FAILED` or `BLOCKED` result is
  evidence too: record the code, then fix forward.
- **Done when:** the result is recorded here and the run data shows the run.

### Result (2026-10-26): PASS

`run-snow-bootc-lifecycle-tbjnp` (main lane at lab `4dcde2af`, submitted
17:10:49Z, ended 17:24:34Z):

```
PASS: verified_five_fresh_boots;fsverity_known_defect=7.2.11+deb14-amd64
```

| Phase | Boot ID | Kernel | fs-verity errors |
| --- | --- | --- | --- |
| `installed-n` | `e852717e-2ac2-43de-a64f-fbe4702f602c` | `7.2.11+deb14-amd64` | 4 |
| `stage` | `2cbf2dde-a846-4487-9f17-9ed84335857c` | `7.2.11+deb14-amd64` | 6 |
| `boot-n-plus-1` | `d1723327-e13b-4dbf-81d1-a982a23e20a2` | `7.2.11+deb14-amd64` | 1 |
| `rollback` | `cbd6e82c-8f1f-475f-94a8-8a717d31f329` | `7.2.11+deb14-amd64` | 0 |
| `boot-n` | `5c0b35a0-972b-493f-a3c4-c404d2a0f7e9` | `7.2.11+deb14-amd64` | 0 |

Inputs: ISO `snosi-installer_20261026164045` (Firn 0.9.1, hash `bc6a19ad…`;
the signed index had rotated 4 times since the first selection, each refresh
re-verified by the preflight tool), N `20261025113924`, N+1 `20261025201457`,
`qa-controlled` at N+1, manifest `manifest_sha256` `fb8f065f…91da`. Every
phase attested Secure Boot, kernel lockdown, a measured Type #2 UKI, a
TPM-unlocked LUKS root on btrfs, the shipped signature policy, the signed
pull of N+1 and the expected `bootc status`; fs-verity counts are on the
known-defect kernel (snosi#1130), recorded, not failed. Before it, the dev run
`dev-snow-bootc-lifecycle-xm98m` of the same lane code had passed at
16:36:33Z; a dev run validates lane code and is not evidence about images.

This is the first manual main-lane PASS: Firn install of N, signed update to
N+1 by the shipped updater, N+1 boot, `bootc rollback`, N boot, for this one
same-version (bootc 1.16.8) pair. Each earlier live run stopped on one lane
defect that the offline tests could not show; the fixes were lab #146
(guest contract), #148 (Firn schema 2), #149 (by-id partition names), #150
(`RLIMIT_FSIZE` on podman), #151 (Incus 7 `config show` and `qemu.nvram`),
#152 and #154 (console log restarts), #155 (the record on tty0, plus 4 probe
defects found by an audit against a booted Snow), #156 (poweroff before the
host read the record) and #157 (the getty prompt). #147 added this plan and
the preflight tool, #153 per-image assembly support for a later cross-version
run, and #158 dev submits so later lane fixes are proven before a merge.
It proves nothing about recovery, key rotation, Snowfield hardware or release
readiness.

## Open questions

- **Spec transport after stage:** bootc reports the staged or booted
  deployment's spec. #146 requires `registry` at `installed-n` and before
  stage, and accepts `registry` or `containers-storage` after it. A `PASS`
  does not retain which value each phase saw, so this run cannot settle it.
  Decide by the next lifecycle change: persist the validated transport in
  `checks.txt`, then tighten the rule from a run's evidence.
- **fs-verity noise during stage:** [snosi#1130](https://github.com/frostyard/snosi/issues/1130)
  (btrfs kernel 7.2 defect) can log `FILE CORRUPTED!` during staging on a
  Firn btrfs install. The lane counts fs-verity errors in each boot's kernel
  log. On a listed known-defect kernel (`7.2.11+deb14-amd64`,
  `7.2.12+deb14-amd64`) it records them (`fsverity_known_defect=`) and does
  not fail; on any other kernel a count above 0 fails the run
  (`fsverity_errors`). A pass says nothing about the resolution of #1130.
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
