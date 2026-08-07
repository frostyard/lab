---
mode: agent
description: Wire a new QA lane end to end, from suite to reporting site.
---

# Add a QA lane

A "lane" is one suite run against one image in one execution environment
(nested container, Incus VM, disk artifact, ISO under Secure Boot). Adding one
touches several layers — do them in this order and stop at the first that
fails.

1. **Understand what the lane proves.** Read `docs/roadmap.md` §
   "What each lane actually tests" first — several existing lane names are
   misleading, and the roadmap says so explicitly. Write down the claim the new
   lane makes before writing YAML.
2. **Runner template.** Add or extend a `WorkflowTemplate` in
   `argo/workflow-templates/`. Follow
   [`workflow-template.prompt.md`](workflow-template.prompt.md): digest
   pinning, `activeDeadlineSeconds`, and the `selfie-container-qa` semaphore
   for anything that pulls an image.
3. **Pipeline wiring.** Add the lane as a DAG task in
   `snosi-qa-pipeline.yaml` via `templateRef`, and give it a one-off
   submission `Workflow` under `argo/` mirroring the existing
   `snosi-*-test.yaml` files.
4. **Convenience recipe.** If operators will run it by hand, add a `Justfile`
   recipe next to `smoke` / `qa`, with the same one-line comment style.
5. **Reporting.** Check `scripts/collect_runs.py` and its tests in
   `tests/test_collect_runs.py` — if the lane's naming or labels do not match
   what the collector expects, the run will not show on the site.
6. **Docs.** Update the lane table in `README.md` and the status table in
   `docs/roadmap.md`.

## Check your work

- `just validate`
- `pytest` if `scripts/` changed (coverage gates live in
  `.coverage-thresholds.json`).
- Describe in the PR what the lane proves and what it explicitly does *not*
  prove.
