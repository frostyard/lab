# Lab operations

Use this skill when changing or operating the QA pipeline.

- Treat Git as the source of truth: change manifests in this repository and let
  Argo CD reconcile them. Do not apply `WorkflowTemplate` or manifest changes
  directly to the cluster.
- Keep pipeline definitions in `argo/` and supporting cluster configuration in
  `manifests/`.
- Run `make verify` (the repository gate) and, for manifest changes,
  `just validate` before submitting them.
- Use `just status`, `just runs`, and `just logs` to inspect the current
  pipeline state.
