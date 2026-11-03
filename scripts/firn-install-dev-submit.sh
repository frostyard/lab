#!/bin/bash
# Dev submit for the firn install lane (Brian, 2026-10-26, T237f): run ONE cell
# of the lane from an UNMERGED branch on the cluster, without Argo CD and
# without changing the main WorkflowTemplate.
#
# Usage: scripts/firn-install-dev-submit.sh [--repo DIR] [--print] NAME=VALUE...
#
# It renders ONE Workflow from DIR (default: this checkout):
#   - name dev-firn-install-*, labels snosi.io/trigger=dev (scripts/
#     collect_runs.py skips a dev trigger, so a dev run is never lane evidence),
#     the source commit and app.kubernetes.io/part-of=lab;
#   - spec.templates copied unchanged from
#     argo/workflow-templates/run-firn-install-tests.yaml, so the cell runs the
#     branch's script instead of the Argo CD synced template;
#   - spec.arguments.parameters from the NAME=VALUE pairs. Each NAME must be an
#     input parameter of the template, and every input without a default must be
#     given: exit 64 otherwise. image-digest, iso-sha256 and iso-url are
#     validated (an immutable ISO URL never says "latest").
# --print writes the Workflow JSON to stdout and submits nothing. Otherwise it
# pipes the JSON to $SUBMIT (default: kubectl create -f - -o name), for
# example SUBMIT="ssh HOST sudo -n k3s kubectl create -f - -o name".
# A dev run publishes nothing and moves no tag.
set -euo pipefail
usage='usage: firn-install-dev-submit.sh [--repo DIR] [--print] NAME=VALUE...'
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
print=0
while (( $# > 0 )); do
  case "$1" in
    --repo)
      (( $# >= 2 )) || { printf '%s\n' "$usage" >&2; exit 64; }
      repo=$(cd -- "$2" && pwd)
      shift 2
      ;;
    --print) print=1; shift ;;
    --) shift; break ;;
    *) break ;;
  esac
done
(( $# > 0 )) || { printf '%s\n' "$usage" >&2; exit 64; }
python=${PYTHON:-python3}
"$python" -c 'import yaml' 2>/dev/null || { printf '%s\n' "PyYAML is required (set PYTHON)" >&2; exit 69; }
commit=$(git -C "$repo" rev-parse HEAD)
workflow=$("$python" - "$repo" "$commit" "$@" <<'PY'
import json, re, sys
from pathlib import Path

import yaml

repo, commit, pairs = Path(sys.argv[1]), sys.argv[2], sys.argv[3:]


def refuse(message):
    print(message, file=sys.stderr)
    sys.exit(64)


template = yaml.safe_load((repo / "argo/workflow-templates/run-firn-install-tests.yaml").read_text())
if template.get("kind") != "WorkflowTemplate":
    refuse("argo/workflow-templates/run-firn-install-tests.yaml is not a WorkflowTemplate")
spec = template["spec"]
inputs = {p["name"]: p for p in spec["templates"][0]["inputs"]["parameters"]}

values = {}
for pair in pairs:
    name, sep, value = pair.partition("=")
    if not sep:
        refuse("not a NAME=VALUE argument: " + pair)
    if name not in inputs:
        refuse("unknown input parameter: " + name)
    values[name] = value

missing = [name for name, declared in inputs.items() if "value" not in declared and name not in values]
if missing:
    refuse("missing input parameter(s) with no default: " + " ".join(missing))

shapes = (
    ("image-digest", r"sha256:[0-9a-f]{64}", "sha256:<64 lowercase hex>"),
    ("iso-sha256", r"[0-9a-f]{64}", "<64 lowercase hex>"),
)
for name, pattern, shape in shapes:
    if name in values and not re.fullmatch(pattern, values[name]):
        refuse(f"{name} must be {shape}: {values[name]}")
if "iso-url" in values and "latest" in values["iso-url"].lower():
    refuse("iso-url must be an immutable ISO URL, never a latest tag: " + values["iso-url"])

workflow = {
    "apiVersion": "argoproj.io/v1alpha1",
    "kind": "Workflow",
    "metadata": {
        "generateName": "dev-firn-install-",
        "namespace": "argo",
        "labels": {
            "snosi.io/trigger": "dev",
            "snosi.io/dev-commit": commit[:40],
            "app.kubernetes.io/part-of": "lab",
        },
    },
    "spec": {
        "serviceAccountName": "argo",
        "entrypoint": "run-firn-install-tests",
        # The branch's templates, byte for byte: this is what a dev run tests.
        "templates": spec["templates"],
        "activeDeadlineSeconds": 10800,
        "arguments": {"parameters": [{"name": n, "value": v} for n, v in values.items()]},
    },
}
print(json.dumps(workflow))
PY
)
if (( print )); then
  printf '%s\n' "$workflow"
  exit 0
fi
# shellcheck disable=SC2086  # SUBMIT is a command line by design.
printf '%s\n' "$workflow" | ${SUBMIT:-kubectl create -f - -o name}
