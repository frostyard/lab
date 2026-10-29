# Plan: Sundog fresh-install rerun with console capture (snosi#1123)

This plan records the 2026-10-29 rerun of the bootc-only ISO install lane
([manual submit file](../../argo/bootc-only-iso-install-test.yaml),
[`run-firn-install-tests`](../../argo/workflow-templates/run-firn-install-tests.yaml))
for Sundog on minideb, with the serial console of every boot kept. The first
cause of [snosi#1123](https://github.com/frostyard/snosi/issues/1123) (the
serial-getty `TTYVHangup` race, fixed by lab#143) stays fixed. This rerun found
a second, intermittent `boot:no_report` with a different console signature. The
lane change that answers it is
[ADR-0013](../adr/0013-firn-lane-markers-also-travel-through-the-kernel-log.md).
VM evidence only, not hardware qualification.

## Selected inputs

| Input | Value | Why |
| --- | --- | --- |
| ISO | `snosi-installer_20261029022716_x86-64.iso`, SHA-256 `4e996efd…f4cda31` | The single entry of the signed index at submit time. `SHA256SUMS.gpg` verified with fingerprint `F37282A3…8216FD68` (`import-pubring.gpg`, SHA-256 `977fe2de…0fc97`). The ISO carries Firn `0.10.0` (`11133e6b`). |
| Sundog | `sha256:ed793866…3e2195` (`20261029003841`), later `sha256:1668e163…876a47` (`20261029022941`) | `sundog:latest` at each submit. The recipe installs `:latest`, so a run installs the digest current at install time. |
| Snow (control) | `sha256:3e664445…6eaae9` | `snow:latest` at submit time. |
| Cell | bootc / encryption `none` / Secure Boot on | The lane's Sundog cell. |
| Lane | `run-firn-install-tests` at lab `f7c81690` (Argo CD synced) | The main WorkflowTemplate, unchanged. |

## Results

| Run | Image | Result |
| --- | --- | --- |
| `bootc-only-iso-install-ffbw7` (Argo, 02:55Z) | Sundog `ed793866` | **FAILED: `boot:no_report`**, kernel `7.2.12+deb14-amd64` |
| `bootc-only-iso-install-mpt2v` (Argo) | Snow `3e664445` | PASS |
| Hand runs of the lane script on minideb (VMs `murb-sundog-1123*`, deleted): 1 boot right after install, 6 first boots of `1668e163` and 6 of `ed793866` from a snapshot taken after install and MOK enrollment, 7 more with 2 extra console readers (1 s and 3 s) | Sundog | 20 of 20 reached login and printed `FIRN_QA__CHECKS_DONE` within about 70 s |
| `bootc-only-iso-install-gvdvr`, `-xj6kk`, `-j27nv` (Argo, 04:28Z to 04:52Z) | Sundog `1668e163` | PASS, PASS, PASS |

So the failure is intermittent: 1 of 24 Sundog installed boots in this
session, on the same kernel, Firn and ISO as the passing boots.

## What the ffbw7 console shows

Excerpts: [`ffbw7-console-excerpt.txt`](sundog-fresh-install-2026-10-29/ffbw7-console-excerpt.txt).
The full logs are on minideb in `~/lab-consoles/snosi-1123-20261029/`.

- The install, the MOK enrollment and the installed boot under Secure Boot
  work: the UKI starts, the initrd mounts the composefs root, and switch-root
  happens at about 5.6 s.
- At switch-root, PID 1 (systemd `262-1`) resets `/dev/console`
  (`reset_dev_console_fd`): it sends the terminal size queries
  `ESC[18t ESC7 ESC[32766;32766H ESC[6n ESC8`, then always writes the ANSI
  reset `ESC[!p ESC]104 ESC\ ESC[0m ESC[?7h ESC[1G ESC[0J`, with a 333 ms
  non-blocking write. In every passing boot the reset follows the query at
  once, and systemd status and journald console lines follow.
- In ffbw7 the query is the last userspace output on the serial console. The
  reset never appears. After it the log holds only kernel printk lines (the
  zram swap, AppArmor profile loads and the RPC modules at 9.8 to 10.8 s, so
  the boot goes on), then nothing for 900 s: no systemd status, no getty
  prompt, no `FIRN_QA__CHECK` line.
- This is not the snosi#1123 getty race: there the check lines stopped at the
  getty's `vhangup`, and `firn-qa login:` was on the console.

Conclusion: in ffbw7 the guest's userspace path to ttyS0 stopped transmitting
at PID 1's console reset, while the kernel console (polled) went on. The
evidence does not tell whether the guest then completed its checks. The VM was
deleted by the lane's teardown, so its journal is lost. The likely class is a
guest serial TTY transmit stall (8250 driver, triggered at the console reset);
it is not proven, because no hand run reproduced it.

## Phase 1 — Make the lane survive and name a stalled TTY

- Every marker also goes to the kernel log (`/dev/kmsg`), every ttyS0 write is
  bounded, and a `serial_tty` probe (`tcdrain` under a timeout) reports a
  stalled TTY as `boot:serial_tty_stalled`, with `FIRN_QA__DIAG` lines
  (output-queue bytes and `stty` flags) as evidence
  ([ADR-0013](../adr/0013-firn-lane-markers-also-travel-through-the-kernel-log.md)).
- **Done when:** the lane change is merged and a Sundog cell passes with it.
  The next hit of this fault then reports `boot:serial_tty_stalled` with the
  checks, or `boot:no_report` with kernel-log markers that show how far the
  check got.

## Open questions

- **Is a stalled serial TTY a product defect?** The lane now fails it as
  `boot:serial_tty_stalled`, because the serial getty is a user's login path
  on a headless system. If it reproduces, the cause goes to snosi (kernel or
  systemd console setup) as an image issue.

## References

- Implements: [ADR-0013](../adr/0013-firn-lane-markers-also-travel-through-the-kernel-log.md)
- Related: [snosi#1123](https://github.com/frostyard/snosi/issues/1123),
  [docs/quality.md — Bootc-only ISO install evidence](../quality.md#bootc-only-iso-install-evidence),
  [ADR-0005](../adr/0005-console-marker-protocol-for-agentless-guests.md)
