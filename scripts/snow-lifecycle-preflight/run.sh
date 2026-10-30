#!/bin/bash
# Read-only operator preflight for a run-snow-bootc-lifecycle manifest (Snow, Floe or Sundog).
#
# Usage: scripts/snow-lifecycle-preflight/run.sh MANIFEST.json [LOCAL_ISO]
#
# Runs checks.sh in a disposable Debian trixie container (the lane's runner
# distribution) with the lane's apt tools. It prints one CHECK line per
# check, then runs the lane's own qa.py validate and preflight, taken from
# the committed ConfigMap in this checkout. It never writes to a registry,
# a tag, the cluster or the host outside a private temporary directory.
#
# PREFLIGHT_IMAGE overrides the container image. A host whose
# containers policy rejects docker.io can use a local Debian trixie image:
#   PREFLIGHT_IMAGE=docker.io/library/debian:trixie PREFLIGHT_PULL=never run.sh ...
set -euo pipefail
if (( $# < 1 || $# > 2 )) || [[ ! -s $1 ]]; then
  printf '%s\n' 'usage: run.sh MANIFEST.json [LOCAL_ISO]' >&2
  exit 64
fi
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo=$(cd -- "$here/../.." && pwd)
image=${PREFLIGHT_IMAGE:-docker.io/library/debian:trixie-slim}
pull=${PREFLIGHT_PULL:-missing}
work=$(mktemp -d)
trap 'rm -rf -- "$work"' EXIT
cp -- "$1" "$work/manifest.json"
cp -- "$here/checks.sh" "$work/checks.sh"
# Extract the ConfigMap qa.py payload the same way the offline tests do.
python3 - "$repo/argo/workflow-templates/snow-bootc-lifecycle-scripts.yaml" "$work/qa.py" <<'PY'
import re, sys, textwrap
from pathlib import Path
match = re.search(r"(?m)^  qa.py: \|\n((?:^    .*\n|^\n)*)", Path(sys.argv[1]).read_text())
if not match:
    sys.exit("ConfigMap qa.py payload missing")
Path(sys.argv[2]).write_text(textwrap.dedent(match.group(1)))
PY
args=(--rm --pull="$pull" --tmpfs /tmp:size=8g -v "$work:/work:Z")
if (( $# == 2 )); then
  args+=(-v "$(realpath -- "$2"):/iso.iso:ro")
fi
podman run "${args[@]}" "$image" bash /work/checks.sh
