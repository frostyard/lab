#!/usr/bin/env bash
# Load the published runs.json from the `data/runs` branch into the site.
#
#   scripts/fetch-runs-data.sh [OUT]
#
# The publish-results CronWorkflow (scripts/publish-runs.sh) commits the run
# data to the orphan branch `data/runs`, not to main. The Pages workflow runs
# this before it builds the site, and `just site-data` runs it for a local
# build. OUT defaults to site/src/data/runs.json, the tracked fallback copy.
#
# Environment:
#   LAB_DATA_REMOTE  the repository to read (default: the public
#                    https://github.com/frostyard/lab.git, anonymous read).
#
# Exit codes: 0 when OUT holds the branch data, or when the branch does not
# exist (OUT keeps the committed fallback); 1 on any other error, with OUT
# unchanged, so a network error never replaces a fresh deploy with the stale
# fallback.
set -euo pipefail

readonly DATA_BRANCH=data/runs

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(dirname "${script_dir}")
out=${1:-"${repo_root}/site/src/data/runs.json"}
remote=${LAB_DATA_REMOTE:-https://github.com/frostyard/lab.git}

work=""
staged=""
cleanup() {
  if [[ -n "${work}" ]]; then rm -rf "${work}"; fi
  if [[ -n "${staged}" ]]; then rm -f "${staged}"; fi
}
trap cleanup EXIT

# A temporary repo keeps the caller's repo free of fetch side effects (no
# FETCH_HEAD, no shallow file).
work=$(mktemp -d)
git -C "${work}" init -q

rc=0
git -C "${work}" ls-remote --exit-code "${remote}" "refs/heads/${DATA_BRANCH}" >/dev/null || rc=$?
case "${rc}" in
  0) ;;
  2)
    echo "WARNING: ${DATA_BRANCH} branch not found; keeping the committed fallback ${out}" >&2
    exit 0
    ;;
  *)
    echo "ERROR: git ls-remote for ${DATA_BRANCH} failed (exit ${rc}); ${out} is unchanged." >&2
    exit 1
    ;;
esac

if ! git -C "${work}" fetch -q --depth 1 "${remote}" "refs/heads/${DATA_BRANCH}"; then
  echo "ERROR: cannot fetch ${DATA_BRANCH}; ${out} is unchanged." >&2
  exit 1
fi
sha=$(git -C "${work}" rev-parse FETCH_HEAD)
if ! git -C "${work}" show FETCH_HEAD:runs.json >"${work}/runs.json"; then
  echo "ERROR: ${DATA_BRANCH} at ${sha} has no runs.json; ${out} is unchanged." >&2
  exit 1
fi

if ! generated=$(python3 - "${work}/runs.json" <<'PY'
import json
import sys

try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        data = json.load(handle)
except (OSError, ValueError) as error:
    sys.exit(f"runs.json is not valid JSON: {error}")
if not isinstance(data, dict):
    sys.exit("runs.json is not a JSON object")
for key, kind in (("generated", str), ("lanes", list), ("runs", list)):
    if not isinstance(data.get(key), kind):
        sys.exit(f"runs.json: key {key!r} must be a {kind.__name__}")
print(data["generated"])
PY
); then
  echo "ERROR: ${DATA_BRANCH} at ${sha} holds an invalid runs.json; ${out} is unchanged." >&2
  exit 1
fi

# Stage next to OUT, so the final mv is an atomic rename on 1 file system.
out_dir=$(dirname "${out}")
mkdir -p "${out_dir}"
staged=$(mktemp "${out_dir}/.runs.json.XXXXXX")
cp "${work}/runs.json" "${staged}"
chmod 0644 "${staged}"
mv -f "${staged}" "${out}"
staged=""

echo "Loaded ${DATA_BRANCH} at ${sha} (generated ${generated}) into ${out}."
