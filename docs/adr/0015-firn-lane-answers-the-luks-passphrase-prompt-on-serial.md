# 0015 — The firn lane answers the LUKS passphrase prompt on the serial console

- **Status:** Accepted
- **Date:** 2026-11-03

## Context

Firn's `luks-passphrase` encryption mode prompts for the LUKS passphrase at
every boot by design; there is no TPM to unseal a key. The Firn roadmap's
Floe hardware row is `luks-passphrase`, so this mode is what a Floe user
meets on a real machine.

The firn install lane had no input path. It drains `incus console "${VM}"
--show-log` for console markers ([ADR-0005](0005-console-marker-protocol-for-agentless-guests.md))
and never writes to the guest, so a `luks-passphrase` cell stopped at
`Please enter passphrase for disk ...` and failed `boot:no_report` after the
installed-boot timeout. That failure proved nothing about the image, so
`argo/firn-install-test.yaml` held the cell out and the lane had no evidence
for the mode. The install itself already writes a fixed public test value,
`firn-qa-passphrase`, to `/run/firn-qa-pass.txt`
(`passphrase_file` in the generated recipe), so the lane knows the value.

## Decision

- **Answer on the serial console.** When `ENC` is `luks-passphrase`, the pod
  watches the console log for `Please enter passphrase for disk`. When the
  count of prompt lines rises above the count taken before `incus start`, it
  runs `incus console "${VM}"` with the passphrase on stdin, bounded by
  `timeout -k 5 30`, and keeps the attach's output in
  `/tmp/results/unlock-console.txt`. The pipe stays open for 5 s after the
  bytes, because `incus console` disconnects at EOF and the write must land
  first. The attach output never feeds the judge; the console log stays the
  judge's surface (`console_log`, `--show-log`).
- **1 public passphrase value.** The shell variable `QA_PASSPHRASE` holds
  `firn-qa-passphrase`. The install script heredoc writes it to
  `/run/firn-qa-pass.txt` (expanded pod-side), and the unlock block types the
  same variable. It is a public test value, never a secret.
- **At most 2 prompts.** `MAX_PROMPTS=2`. One prompt is answered, and a boot
  that reports `FIRN_QA__CHECKS_DONE` passes. A third prompt fails the cell as
  `boot:passphrase_rejected`: the passphrase did not unlock the disk.
- **A missing prompt is a failure.** The new input
  `passphrase-prompt-timeout-seconds` (default 600) bounds how long the lane
  waits for the first prompt with no answer yet. When that budget is used up,
  the cell fails `boot:passphrase_prompt_missing` and prints the console tail:
  the prompt never reached ttyS0, or the guest did not boot at all.
- **Overall deadline.** The loop obeys `installed-boot-timeout-seconds`. At
  the deadline the cell fails `boot:passphrase_prompt_missing` when it answered
  nothing, else `boot:no_report`.
- **1 event per pass.** Each pass reads the console log once, so the prompt
  count and the done marker come from 1 sample, 5 s apart.
- **Every other cell is unchanged.** `ENC != luks-passphrase` keeps
  `wait_for "${M_DONE}"` with `boot:no_report` and the same console tail, and
  never attaches with `incus console` without `--show-log`.
- **The judge says so.** A passing `luks-passphrase` cell appends
  `unlock=serial-passphrase prompts=<N>` to the PASS summary, before
  `firn=<version>`. `luks=present` stays required for every cell with
  `ENC != none`, and no other judge rule changes.
- **The cell is in the matrix.** `argo/firn-install-test.yaml` runs 1
  `luks-passphrase` cell: Floe, Secure Boot off. Floe is the roadmap's
  `luks-passphrase` hardware row.
- **Dev runs are allowed.** `scripts/firn-install-dev-submit.sh` renders 1
  Workflow from the branch's templates for an unmerged lane fix, named
  `dev-firn-install-*` with label `snosi.io/trigger=dev`, as for the Snow
  lifecycle lane (AGENTS.md, T237f). `scripts/collect_runs.py` already skips a
  dev trigger, so a dev run is never lane evidence.

## Consequences

- The lane now proves that a user can unlock an installed `luks-passphrase`
  system with the passphrase on the serial console, and that the system then
  reaches multi-user with the normal probe and judge. A green cell is real
  evidence for that path, and a red cell says which part failed.
- It does not prove the graphical prompt on tty0 (Plymouth) or on real
  hardware. The serial console is the only channel the lab has.
- The passphrase is public and printed in the template. It must never become
  a secret: a secret in a Workflow parameter is not a secret either
  ([ADR-0009](0009-no-artifact-store-logs-are-the-surface.md) keeps logs as
  the surface).
- The pod writes to the guest for the first time. The write is 1 short
  bounded attach, and its bytes go to a pod file, not to the console log. A
  noisy attach therefore never changes what the judge reads.
- A prompt that the lane already saw before `incus start` is not answered
  again, so a stale prompt line in the console log cannot cause a wrong
  answer.
- 1 more cell joins the matrix, serialized on the same `snosi-vm-qa`
  semaphore, so the full matrix takes 1 install longer.

## Alternatives considered

- **Keep holding the cell out:** rejected. It left the Firn roadmap's Floe
  hardware row without evidence, and the held-out note was the only record.
- **Pre-seed the passphrase into the guest with a systemd credential:** rejected.
  A credential file on the ESP would prove that the file unlocked the disk,
  not that a user typed the passphrase at the prompt.
- **`incus exec` or the incus agent to type at the console:** rejected, as in
  ADR-0005. Not every image has the agent, and `incus console` is the same
  path a person uses.
- **Wait for the done marker only and tolerate the prompt:** rejected. Then a
  cell that never unlocked would burn the whole installed-boot budget and
  report `no_report`, which hides the cause.

## References

- Shapes: [docs/quality.md — Bootc-only ISO install evidence](../quality.md#bootc-only-iso-install-evidence)
- Builds on: [ADR-0005](0005-console-marker-protocol-for-agentless-guests.md),
  [ADR-0010](0010-vacuous-success-is-forbidden.md),
  [ADR-0013](0013-firn-lane-markers-also-travel-through-the-kernel-log.md)
- Implemented by:
  [argo/workflow-templates/run-firn-install-tests.yaml](../../argo/workflow-templates/run-firn-install-tests.yaml),
  [argo/firn-install-test.yaml](../../argo/firn-install-test.yaml),
  [scripts/firn-install-dev-submit.sh](../../scripts/firn-install-dev-submit.sh),
  [tests/test_firn_install_lane.py](../../tests/test_firn_install_lane.py),
  [tests/test_firn_install_dev_submit.py](../../tests/test_firn_install_dev_submit.py)
- Related: [ADR-0009](0009-no-artifact-store-logs-are-the-surface.md)
