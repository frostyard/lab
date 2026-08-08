# Change risk tiers

Classify each proposed change before implementation and review. Record the
highest applicable tier, a short rationale, and the validation performed in the
pull request. A small diff is not necessarily low risk.

| Tier | Typical changes | Required review and evidence |
| --- | --- | --- |
| Low | Documentation, comments, or formatting with no operational effect | Author review plus relevant link or formatting checks |
| Medium | Tests, reporting UI, observer logic, dependencies, or non-privileged tooling | Targeted tests, relevant CI, and maintainer review |
| High | Argo or Kubernetes desired state, Argo CD ownership, RBAC, workflow permissions, secret references, image selection, result publication, destructive operations, or safety-gate changes | Maintainer approval, threat and failure analysis, least-privilege review, positive and negative validation, and a rollback plan |

## Classification rules

- Use the highest tier that applies to any part of the change.
- Classify uncertain changes as high risk until their impact is understood.
- Reclassify the change if its scope expands or validation reveals a
  security-sensitive condition.
- Treat changes under `argo/workflow-templates/`, `manifests/`, and `argocd/`
  as high risk because they can alter reconciled cluster state.
- Treat changes to workflow permissions, credentials, image digests, or quality
  gates as high risk regardless of file location or diff size.

## Pull request requirements

Every pull request should identify its risk tier and explain why that tier
applies. Medium- and high-risk changes should also name the affected component
or QA lane and describe the relevant validation.

High-risk pull requests must document:

- the expected behavior and realistic failure modes;
- affected Argo CD Applications, workflows, QA lanes, and shared resources;
- credential, permission, and untrusted-input impact;
- rollback or safe-disable steps; and
- validation performed, including any checks that could not be run.

Reviewers should apply the [pull request review rubric](review-rubric.md) and
raise the tier when the stated classification does not cover the actual impact.
Emergency operational action remains the responsibility of an authorized
operator and requires an auditable follow-up through Git.
