<!-- Branch from the latest main. Reviews apply AGENTS.md,
docs/review-rubric.md and the docs rules in docs/README.md. -->

## Summary

<!-- What does this change do, and why? Which lanes or Argo CD Applications
(frostyard-lab, frostyard-lab-infra) does it affect? -->

## Related issue

- Closes #

## Risk tier

Declare the highest applicable tier, never lower; the scale is
[docs/risk-tiers.md](../docs/risk-tiers.md) (Low, Medium, High). A path in
`.github/risk-paths.json` gets a `level:L1` to `level:L4` label, and `risk:high`
for L1 and L2 (core ADR-0078). A `risk:high` label means High: name each
matched path and why the change is safe, and give the rollback or safe-disable
step.

- Tier: Low / Medium / High
- Rationale:

## Validation

<!-- List the commands you actually ran and their outcome. -->

- [ ] `make verify` passes (template gate, policy check, pytest, dashboard tests)
- [ ] `just site-e2e` for a change to `site/` or `e2e/`
- [ ] Revert test for a fix: the new test fails with the fix reverted; command: <!-- name it -->
- [ ] Workflows changed: every action is SHA-pinned with a version comment, read-only top-level `permissions`, and `persist-credentials: false`
- [ ] Not run (explain why)

## Docs housekeeping

<!-- Delete rows that do not apply. -->

- [ ] Documentation updated (`AGENTS.md`, `README.md`, `docs/`) where relevant
- [ ] New docs started from their category's `TEMPLATE.md` and indexed in `docs/README.md`
- [ ] Cross-links added in both directions (ADR ↔ design ↔ spec ↔ plan)
- [ ] New significant decision recorded as an ADR first, in this PR

## Checklist

- [ ] No secrets, kubeconfigs or credentials are committed
- [ ] No WorkflowTemplate or manifest was applied to the cluster by hand
- [ ] Blocking review point fixed: guard added and 1 `.memory/corrections.jsonl` line added
