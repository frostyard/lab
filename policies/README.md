# Repository policies

`agent-governance.json` is Lab's machine-readable, deny-by-default policy for
automated contributors. It mirrors the human-readable boundaries in
[`AGENTS.md`](../AGENTS.md), [`docs/SECURITY-AI.md`](../docs/SECURITY-AI.md),
and [`docs/risk-tiers.md`](../docs/risk-tiers.md); those documents provide
context, while this file provides a deterministic regression boundary.

The policy denies autonomous cluster mutation, Argo submission or forced
reconciliation, pull-request merging, protected-environment changes, result
publication, credential rotation, and self-approved exceptions. It also
requires GitOps, pull requests, human review, risk classification, validation
evidence, and explicit controls for exceptions. Protected boundaries cover
Argo CD ownership, credentials, publication, immutable image selection, RBAC,
quality gates, secrets, and workflow permissions.

Validate it from the repository root with only Python's standard library:

```bash
python3 policies/check_agent_governance.py
```

The checker returns zero for a valid policy, one for policy violations, and two
when the policy cannot be read or parsed. `tests/test_agent_governance_policy.py`
runs positive and negative checks in the normal Python CI matrix. It changes
each denied action and required control independently and removes each protected
boundary, so weakening one cell cannot hide behind the rest of the document.

Policy changes require a pull request and human review. They must not be used to
bypass GitOps, suppress failed evidence, or authorize an agent to approve its
own exception. An authorized operator remains responsible for emergency cluster
or credential action and for the auditable restoration follow-up.
