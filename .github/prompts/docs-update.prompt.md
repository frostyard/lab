---
mode: agent
description: Update README, docs/, or the reporting site copy.
---

# Update documentation

Read first: `docs/design/architecture.md` for the architecture overview,
`docs/roadmap.md` for lane status, `docs/ops/bootstrap.md` and
`docs/ops/operating.md` for the operational guides, and `CONTRIBUTING.md` for
tone. The root `README.md` follows core's README standard (ADR-0072): keep it
under 1000 words and put lane detail in `docs/`.

## Rules

- Match the existing voice: plain, specific, honest about what is not proven
  yet. The roadmap opens by correcting two wrong conclusions the lab had been
  reporting — that candour is the house style, keep it.
- Keep the status tables accurate. If a change moves a lane's state, update the
  table in `docs/roadmap.md` **and** the matching row in
  `docs/design/architecture.md` (Image lanes) or `docs/design/vm-lanes.md`.
- Every claim about what a lane proves must be traceable to a suite that
  actually runs. If you cannot point at the workflow that proves it, say so
  instead of implying it.
- Wrap prose at roughly 80 columns, as the existing files do, and keep tables
  in the existing pipe style.
- Site copy lives in `site/src/`; run `prettier` (config in `.prettierrc`) on
  anything you change there.

## Check your work

- `just site-build` if `site/` changed.
- `pytest` if you touched `scripts/collect_runs.py` docstrings or behaviour.
- Links resolve, and no doc contradicts another after your edit.
