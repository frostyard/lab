---
mode: agent
description: Update README, docs/, or the reporting site copy.
---

# Update documentation

Read first: `README.md` for the architecture overview, `docs/roadmap.md` for
lane status, `docs/ops/bootstrap.md` for the operational guide, and
`CONTRIBUTING.md` for tone.

## Rules

- Match the existing voice: plain, specific, honest about what is not proven
  yet. The roadmap opens by correcting two wrong conclusions the lab had been
  reporting — that candour is the house style, keep it.
- Keep the status tables accurate. If a change moves a lane's state, update the
  table in `docs/roadmap.md` **and** the matching row in `README.md`.
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
