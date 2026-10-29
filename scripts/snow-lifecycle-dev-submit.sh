#!/bin/bash
# Dev submit for the manual Snow and Floe bootc lifecycle lane (Brian, 2026-10-26,
# T237f): run the lane from an UNMERGED branch on the cluster, without Argo
# CD and without changing the main WorkflowTemplate or ConfigMap.
#
# Usage: scripts/snow-lifecycle-dev-submit.sh [--repo DIR] [--print] MANIFEST.json
#
# It renders ONE Workflow from DIR (default: this checkout):
#   - the spec of argo/workflow-templates/run-snow-bootc-lifecycle.yaml, inline;
#   - the scripts of argo/workflow-templates/snow-bootc-lifecycle-scripts.yaml
#     as Argo raw input artifacts (gzip+base64, unpacked to /opt/snow-qa by the
#     entry command), instead of the ConfigMap;
#   - name dev-snow-bootc-lifecycle-*, label snosi.io/trigger=dev and the
#     source commit, keep-vm-on-failure=false.
# --print writes the Workflow JSON to stdout and submits nothing. Otherwise it
# pipes the JSON to $SUBMIT (default: kubectl create -f - -o name), for
# example SUBMIT="ssh HOST sudo -n k3s kubectl create -f - -o name".
# A dev run publishes nothing and moves no tag; the manifest's controlled tag
# must already point at N+1.
set -euo pipefail
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
print=0
while (( $# > 1 )); do
  case "$1" in
    --repo) repo=$(cd -- "$2" && pwd); shift 2;;
    --print) print=1; shift;;
    *) break;;
  esac
done
if (( $# != 1 )) || [[ ! -s $1 ]]; then
  printf '%s\n' 'usage: snow-lifecycle-dev-submit.sh [--repo DIR] [--print] MANIFEST.json' >&2
  exit 64
fi
python=${PYTHON:-python3}
"$python" -c 'import yaml' 2>/dev/null || { printf '%s\n' "PyYAML is required (set PYTHON)" >&2; exit 69; }
commit=$(git -C "$repo" rev-parse HEAD)
workflow=$("$python" - "$repo" "$1" "$commit" <<'PY'
import base64, gzip, json, sys
from pathlib import Path
import yaml

repo, manifest, commit = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
base = repo / "argo/workflow-templates"
template = yaml.safe_load((base / "run-snow-bootc-lifecycle.yaml").read_text())
scripts = yaml.safe_load((base / "snow-bootc-lifecycle-scripts.yaml").read_text())
assert template["kind"] == "WorkflowTemplate" and scripts["kind"] == "ConfigMap"
spec = template["spec"]
volume = "scripts"
mounted = [v for v in spec["volumes"] if v["name"] == volume]
assert len(mounted) == 1 and mounted[0]["configMap"]["name"] == scripts["metadata"]["name"]
spec["volumes"] = [v for v in spec["volumes"] if v["name"] != volume]
(entry,) = [t for t in spec["templates"] if t["name"] == spec["entrypoint"]]
mounts = entry["container"]["volumeMounts"]
(mount,) = [m for m in mounts if m["name"] == volume]
entry["container"]["volumeMounts"] = [m for m in mounts if m["name"] != volume]
mode = mounted[0]["configMap"].get("defaultMode", 0o555)
# Argo offloads a pod template above 128 KiB (MaxEnvVarLen) to a ConfigMap the
# controller cannot create on this cluster, so the scripts travel gzip+base64
# under PACKED and the entry command unpacks them to the mount path, byte-equal.
packed = mount["mountPath"] + ".packed"
command = entry["container"]["command"]
assert command == ["/bin/bash", mount["mountPath"] + "/run.sh"], command
entry["inputs"]["artifacts"] = [
    {"name": "script-" + name.replace(".", "-").replace("_", "-"), "path": packed + "/" + name + ".gz.b64",
     "mode": 0o444, "raw": {"data": base64.b64encode(gzip.compress(data.encode(), 9, mtime=0)).decode()}}
    for name, data in sorted(scripts["data"].items())]
entry["container"]["command"] = ["/bin/bash", "-c", (
    "set -euo pipefail; install -d -m 0755 {dest}; for f in {src}/*.gz.b64; do n=${{f##*/}}; n=${{n%.gz.b64}}; "
    "base64 -d \"$f\" | gzip -dc > \"{dest}/$n\"; chmod {mode:o} \"{dest}/$n\"; done; exec \"$@\""
).format(dest=mount["mountPath"], src=packed, mode=mode), "dev-unpack", *command]
spec["arguments"] = {"parameters": [
    {"name": "manifest-json", "value": manifest.read_text()},
    {"name": "keep-vm-on-failure", "value": "false"}]}
workflow = {"apiVersion": "argoproj.io/v1alpha1", "kind": "Workflow",
            "metadata": {"generateName": "dev-snow-bootc-lifecycle-", "namespace": "argo",
                         "labels": {"snosi.io/trigger": "dev", "snosi.io/dev-commit": commit[:40]}},
            "spec": spec}
print(json.dumps(workflow))
PY
)
if (( print )); then
  printf '%s\n' "$workflow"
  exit 0
fi
# shellcheck disable=SC2086  # SUBMIT is a command line by design.
printf '%s\n' "$workflow" | ${SUBMIT:-kubectl create -f - -o name}
