# Pull request review rubric

Use this rubric for human and agent reviews. The goal is not to maximize the
number of comments; it is to decide whether the change is safe, supported by
evidence, and honest about what the lab proves.

Review the changed behavior and its direct integration points. Do not block a
focused pull request on unrelated cleanup.

## Review criteria

| Criterion | Accept when | Request changes when |
|---|---|---|
| Correctness | The change implements the stated behavior and handles relevant failure paths. | A realistic input, failure, or integration path produces the wrong result. |
| GitOps ownership | Cluster changes are made in git and remain under the correct Application: `argo/workflow-templates/` for `frostyard-lab`, `manifests/` for `frostyard-lab-infra`. | The change requires a hand-applied resource, duplicates ownership, or puts a resource under the wrong Application. |
| Evidence | Claims about a QA lane are traceable to a workflow and suite that actually run. Limits and unproven behavior are stated plainly. | The PR implies coverage or proof that the pipeline does not provide. |
| Safety and security | Secrets stay out of git and logs; permissions, external inputs, image references, and destructive operations are narrowly scoped. Images under test remain pinned by digest. | Credentials can leak, permissions are unnecessarily broad, untrusted input reaches a dangerous operation, or a tag can change what was tested. |
| Operational reliability | Long-running work is bounded, failures are visible, shared registry work uses the existing semaphore, and bookkeeping happens only after QA succeeds. | Work can hang indefinitely, fail silently, race a shared resource, or record an unverified image as tested. |
| Maintainability | The change follows nearby structure and naming, explains non-obvious operational choices, and avoids unnecessary duplication. | Future maintainers would need to guess at an important invariant or maintain competing implementations. |
| Documentation | User-visible behavior, bootstrap steps, and lane status are updated where affected. Wording distinguishes observation from proof. | The change makes an existing guide or status table inaccurate. |
| Validation | The smallest relevant existing checks passed, or the PR clearly states what could not run and why. | Relevant validation is absent without explanation, or its result contradicts the claimed behavior. |

## Change-specific checks

Apply the rows relevant to the pull request:

| Area | Check |
|---|---|
| Argo workflows | Metadata and labels match neighboring templates; long-running work has `activeDeadlineSeconds`; registry work uses the `selfie-container-qa` semaphore; image inputs are digest-pinned; offline pytest contracts and `just validate` or `argo lint` cover the YAML. |
| Manifests and Argo CD | Resource ownership is not duplicated; bootstrap prerequisites remain distinct from reconciled resources; explanatory header comments still match the configuration; offline pytest contracts and `just validate` cover the YAML. |
| Scripts and observer | Errors are surfaced rather than converted into success-shaped output; external commands and API responses are bounded and checked; relevant `pytest` tests pass. |
| Reporting site | Displayed status agrees with the collected data contract; empty and failure states remain clear; the site build or targeted Playwright tests pass. |
| Documentation | Commands and paths exist, links resolve, prose is specific about limitations, and lane status agrees with both `README.md` and `docs/roadmap.md`. |

## Review findings

Make each finding actionable and assign one of these levels:

- **Blocking:** A correctness, security, data-loss, GitOps ownership, or
  evidence problem that must be fixed before merge.
- **Follow-up:** A real improvement that is safe to defer and should not block
  this focused change.
- **Nit:** Optional wording or style feedback.

A blocking finding should identify the affected file or behavior, describe a
realistic failure or impact, and state the condition needed to resolve it.
Prefer one root-cause finding over several comments on its symptoms.

Approve when all applicable criteria are satisfied and no blocking findings
remain. Request changes only for blocking findings; leave follow-ups and nits
without withholding approval.
