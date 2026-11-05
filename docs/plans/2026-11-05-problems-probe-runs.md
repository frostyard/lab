# Plan: Problems probe on the snosi-health images (T857 t7)

This plan proves phase 1 of plan T857 in the lab: the snosi-health collector
and systemd-coredump that [snosi#1226](https://github.com/frostyard/snosi/pull/1226)
(merge `b6e5e076`, snosi ADR-0029) ship on every product. It uses the
report-only [problems probe](../adr/0017-problems-probe-is-a-report-only-lane-mode.md)
of the [bootc lifecycle lane](../../README.md#manual-snow-bootc-lifecycle) on
Snow, Sundog and Floe, on a fresh install and on the update path from the
previous image. Snow covers Snowfield (there is no separate Snowfield check in
plan T857). The probe records; it gates nothing until 3 runs with
`problems=ok` on each product and path, and then a new decision promotes it.

## Selected inputs

One manifest per product and path in
[`problems-probe-2026-11-05/`](problems-probe-2026-11-05/). Every image below was
read from the registry before its run; the t5 images carry the source label
`frostyard/snosi@770f9a3c`, whose parent is the t5 merge `b6e5e076`. The ISO is
`snosi-installer_20261104213621_x86-64.iso`, the only signed index entry. Trust
pins are the lane's (snosi `39cf1988`).

| Path | Product | N | N+1 | Target |
| --- | --- | --- | --- | --- |
| fresh | Snow | `20261105072915` `sha256:638b9af8…` (t5) | `20261105001236` `sha256:82f06d9f…` | `snow:qa-controlled` (never staged) |
| fresh | Sundog | `20261105073038` `sha256:e91b7000…` (t5) | `20261030194311` `sha256:199ca989…` | `sundog:qa-controlled` (never staged) |
| fresh | Floe | `20261105072939` `sha256:b2aea38e…` (t5) | `20261028230726` `sha256:bb1269a5…` | `floe:qa-controlled` (never staged) |
| baseline | Snow | `20261104190452` `sha256:f2f103b4…` (T843 image) | as fresh | as fresh |
| baseline | Sundog | `20261104190733` `sha256:f2b2c9dd…` (T843 image) | as fresh | as fresh |
| baseline | Floe | `20261104190417` `sha256:c671e8d9…` (T843 image) | as fresh | as fresh |
| update | Snow | `20261105001236` `sha256:82f06d9f…` | `20261105072915` `sha256:638b9af8…` (t5) | `snow:qa-controlled` must point at N+1 |
| update | Sundog | `20261105001226` `sha256:a32c0c27…` | `20261105073038` `sha256:e91b7000…` (t5) | `sundog:qa-controlled` must point at N+1 |
| update | Floe | `20261105001249` `sha256:35cb5400…` | `20261105072939` `sha256:b2aea38e…` (t5) | `floe:qa-controlled` must point at N+1 |

The baselines are the images of the T843 first-login runs
([2026-11-04](2026-11-04-first-login-probe-runs.md)), probed in the same mode on
the same host, so their boot timing is the comparison point. They have no
collector and end `BLOCKED: problems_unproven-no-collector` by design.

The operator preflight (`scripts/snow-lifecycle-preflight`) passes every check
except `order_distinct` on the fresh and baseline manifests: that operator check
knows only the five-boot order, and a fresh probe uses the `version_distinct`
rule (the lane's own `lane_preflight` passes).

## Phase 1 — Lane mode (this pull request)

- `problems_probe` (`fresh` | `update`), the guest `problems` mode, the host
  `problems.py`, a pinned copy of core's `problem-report/v1` schema, the
  `problems` workflow output, [ADR-0017](../adr/0017-problems-probe-is-a-report-only-lane-mode.md).
- **Done when:** the full test suite passes, CI is green and the cross-review is
  Ready.

## Phase 2 — Fresh-install dev runs

Submitted with
[`scripts/snow-lifecycle-dev-submit.sh`](../../scripts/snow-lifecycle-dev-submit.sh)
(`dev-snow-bootc-lifecycle-*`, label `snosi.io/trigger=dev`,
`keep-vm-on-failure=false`) under the `lab-dev` lease (thread T857-t7). No
publish, no tag move.

- **Done when:** each product has 3 fresh runs with `problems=ok` (the
  promotion bar of ADR-0017 for this path). Met on 2026-11-05 (results below).

### Result 2026-11-05: Floe `ttncz` at `aa44ad3c`, missing-coredump (a probe defect)

`PASS: verified_problems_probe;problems=missing-coredump`. All 4 injected
problems were recorded and listed, the schema check passed, and the crash
record proves that systemd-coredump caught the core. Only the probe's own
`core_pattern` check failed: it accepted only the
`|/usr/lib/systemd/systemd-coredump` pipe, and this kernel and systemd use the
coredump socket. Fixed in `61dbab44` (the pipe or `@`/`@@/run/systemd/coredump`;
test `test_guest_problems_accepts_the_coredump_pipe_and_socket`).

### Result 2026-11-05: Snow, Sundog and Floe PASS, problems=ok (3 runs each)

Every run below ended `PASS: verified_problems_probe;problems=ok`. Runs 1 and
2 ran the lane at `61dbab44` and `341d6e16` (the probe code of both is the code
the cross-review read before its fixes); run 3 ran the reviewed fixes at
`3e7e2645`. The `snosi-vm-qa` semaphore ran them one after the other, so the
times include queue time.

| Product | Run 1 | Run 2 | Run 3 |
| --- | --- | --- | --- |
| Snow | `5nx9h` 08:26 to 08:47 | `m7q4x` 09:07 to 09:15 | `p8td6` 09:34 to 09:42 |
| Sundog | `v2n2h` 08:26 to 08:56 | `8mqtk` 09:07 to 09:24 | `mqfbp` 09:34 to 09:51 |
| Floe | `527ht` 08:26 to 09:03 | `285qd` 09:07 to 09:31 | `87grt` 09:34 to 09:58 |

Each `problems.txt` (Snow shown; Sundog and Floe have the same lines except the
boot timing):

```
problems=ok
path=fresh collector=yes timer=enabled/active preset_reconcile=success coredump_user=yes core_pattern=yes journal_dir=yes
file=0644 owner=0:0 schema=valid records=5 reportable=3 details=0640 group=sudo
injected unit=yes stage=failed/network crash=yes app=yes collector_runs=0,0
recorded unit=yes stage=yes crash=yes app=yes leak=no
list rc=0 unit=yes stage=yes crash=yes app=yes
```

The 5 records on every product: the local unit (`{"result": "exit-code"}`, not
reportable, its name only in `details.json`), the crash (`sleep`, `SIGSEGV`,
reportable), the app problem (reportable), the stage failure
(`error_class=network`, not reportable because a network failure has no target
version), and 1 `integrity_error` (`fs-verity`, reportable) that nobody
injected. It is most likely the known btrfs fs-verity kernel defect
([snosi#1130](https://github.com/frostyard/snosi/issues/1130)): the same
day's [lockdown runs](2026-11-05-lockdown-1214-lifecycle-runs.md) saw it on
`7.2.12+deb14-amd64`, a known-defect kernel. The lane passes it as a known
defect, but the collector records it as a reportable problem; that is a snosi
question for phase 2, not a lab check.

## Boot time against the T843 baseline

`target_ms` is the time from kernel start to the default target
(`graphical.target` on all 3 products), as `systemd-analyze` computes it; the
firmware and loader stamps are 0 in these VMs. Fresh first boot on the shared
lab host; 1 baseline sample per product:

| Product | Baseline (T843 image) | t5 image samples | t5 median | Median − baseline |
| --- | --- | --- | --- | --- |
| Snow | 24110 ms (`h5gz4`) | 23667, 22820, 22954 ms | 22954 ms | −1156 ms |
| Sundog | 21485 ms (`dh8jg`) | 23451, 21277, 21492 ms | 21492 ms | +7 ms |
| Floe | 18411 ms (`zlpm4`) | 25671, 17654, 17528, 17531 ms | 17593 ms | −818 ms |

Floe's 25671 ms (`ttncz`, the first run) is the one outlier; the other 3 Floe
samples of the same image are within 130 ms of each other. snosi-health starts
only from its timer, 3 minutes after boot, and is ordered before no boot
target, so it cannot delay the default target; systemd-coredump adds a socket
and an account. No boot-time regression is visible against the T843 baseline.

## Phase 3 — Update-path dev runs

- Needs each product's `qa-controlled` tag on its t5 image (the N+1 above). The
  tag is operator-owned (Brian); a dev run never moves it.
- **Done when:** each product has 1 update run with `problems=ok`, which also
  proves that `preset-reconcile` enables `snosi-health.timer` and that sysusers
  creates `systemd-coredump` on an updated host. Not met on 2026-11-05: a snosi
  defect (results below).

Brian moved the 3 `qa-controlled` tags to the t5 images (T893); each target
was read from the registry before the runs. The runs ran the merged lane
([lab#187](https://github.com/frostyard/lab/pull/187)) at `99f0b272`.

### Result 2026-11-05: update runs refused, the signed ISO index moved

The first 3 update runs (`tnhwj` Snow, `7vqgs` Floe, `zfvqz` Sundog, 11:44 EST)
ended `FAILED: preflight_mismatch:iso_index` before any boot. The signed
`SHA256SUMS` then listed only `snosi-installer_20261105145811_x86-64.iso`
(`sha256:366872a2…`), so the lane refused the pinned `20261104213621` ISO, as
it must. The new index is signed by the pinned key (`F37282A3…`, snosi
`39cf1988`) and the ISO matches it; the 3 update manifests now name it. The
ISO only installs N; N and N+1 are unchanged.

### Result 2026-11-05: Snow, Sundog and Floe PASS, problems=missing-timer

Every run ended `PASS: verified_problems_probe;problems=missing-timer`. The
`snosi-vm-qa` semaphore ran them one after the other (times include queue time).

| Product | Run | Time (EST) | `target_ms` |
| --- | --- | --- | --- |
| Snow | `x9htr` | 14:52 to 15:04 | 21338 |
| Floe | `mfg6x` | 14:52 to 15:14 | 17591 |
| Sundog | `w7l84` | 14:52 to 15:27 | 21057 |

Each `problems.txt` (Snow shown; Floe and Sundog have the same lines except the
boot timing):

```
problems=missing-timer
path=update collector=yes timer=enabled/inactive preset_reconcile=success coredump_user=yes core_pattern=yes journal_dir=yes
file=0644 owner=0:0 schema=valid records=4 reportable=2 details=0640 group=sudo
injected unit=yes stage=failed/network crash=yes app=yes collector_runs=0,0
recorded unit=yes stage=yes crash=yes app=yes leak=no
list rc=0 unit=yes stage=yes crash=yes app=yes
```

On the first boot of N+1, sysusers created `systemd-coredump`,
`preset-reconcile` succeeded and enabled `snosi-health.timer`, and the collector
recorded and listed all 4 injected problems. Only the timer is not active:
`preset-reconcile` runs `systemctl preset`, which enables a unit but does not
start it, so a timer that an update adds runs first on the next boot. That is a
snosi defect, not a probe defect:
[snosi#1229](https://github.com/frostyard/snosi/issues/1229). Phase 3 is done
when the fix ships in an image and 1 update run per product says `problems=ok`.

The update runs have 4 records, not 5: no run recorded an `integrity_error`
(the unexplained fs-verity record of the fresh runs). The boot timing of the
first N+1 boot is in the range of the fresh samples above.

## Later / ideas

- Promote the probe to a gate after 3 runs with `problems=ok` on each product
  and path (a new decision).
- The guest unit is at 53908 of the 54000 test bound; the next guest mode needs
  a smaller payload first.

## References

- Implements: plan T857 section 9 (lab proof), task t7;
  [ADR-0017](../adr/0017-problems-probe-is-a-report-only-lane-mode.md)
- System under test: snosi ADR-0029, [snosi#1226](https://github.com/frostyard/snosi/pull/1226)
- Decisions: [ADR-0003](../adr/0003-unproven-is-distinct-from-failed.md),
  [ADR-0016](../adr/0016-first-login-probe-is-a-report-only-lane-mode.md)
