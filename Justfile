# frostyard lab — operator convenience wrappers.
#
# Nothing here is required: every recipe is a thin wrapper over kubectl or argo.
# Cluster state comes from git via ArgoCD, so no recipe applies a
# WorkflowTemplate or a manifest by hand.

set shell := ["bash", "-eu", "-o", "pipefail", "-c"]

_default:
    @just --list

# One-time cluster bootstrap. See docs/ops/bootstrap.md for the full guide.
setup-argocd:
    kubectl apply -f argocd/argo-workflows-app.yaml -n argocd
    kubectl apply -f argocd/application.yaml -n argocd
    kubectl apply -f argocd/infra-application.yaml -n argocd

# Are the Applications reconciled and healthy?
status:
    @kubectl get applications -n argocd \
        -o custom-columns='NAME:.metadata.name,SYNC:.status.sync.status,HEALTH:.status.health.status'
    @echo
    @kubectl get cronworkflows -n argo \
        -o custom-columns='NAME:.metadata.name,SUSPENDED:.spec.suspend,SCHEDULE:.spec.schedules[0]'

# Force ArgoCD to re-read git now instead of waiting for the poll interval.
refresh:
    kubectl annotate application frostyard-lab -n argocd \
        argocd.argoproj.io/refresh=hard --overwrite
    kubectl annotate application frostyard-lab-infra -n argocd \
        argocd.argoproj.io/refresh=hard --overwrite

# Submit a one-off smoke run against snow:latest.
smoke:
    kubectl create -f argo/snosi-smoke-test.yaml

# Submit a one-off run for any image/suite combination.
#   just qa ghcr.io/frostyard/cayo latest smoke cayo
qa image tag suites variant:
    argo submit --from workflowtemplate/snosi-qa-pipeline -n argo \
        -p image={{image}} -p image-tag={{tag}} \
        -p suites={{suites}} -p variant={{variant}}

# Watch the most recently submitted workflow.
watch:
    argo watch -n argo @latest

# Logs for the most recent workflow.
logs:
    argo logs -n argo @latest --follow

# Recent run history, newest first.
runs:
    @kubectl get workflows -n argo \
        --sort-by=.metadata.creationTimestamp \
        -o custom-columns='NAME:.metadata.name,STATUS:.status.phase,STARTED:.status.startedAt,FINISHED:.status.finishedAt'

# Validate every YAML file against the live cluster without applying anything.
# Catches schema errors before a push turns into a failed ArgoCD sync.
validate:
    #!/usr/bin/env bash
    set -euo pipefail
    rc=0
    for f in manifests/*.yaml argo/workflow-templates/*.yaml argo/*.yaml argocd/*.yaml; do
        [[ -e "$f" ]] || continue
        if kubectl apply --dry-run=server -f "$f" >/dev/null 2>/tmp/validate-err; then
            echo "ok    $f"
        else
            echo "FAIL  $f"
            sed 's/^/        /' /tmp/validate-err
            rc=1
        fi
    done
    exit $rc

# ── reporting ────────────────────────────────────────────────────────────────

# Regenerate the site's run data from the live cluster.
collect:
    python3 scripts/collect_runs.py site/src/data/runs.json

# Build the reporting site locally.
site-build:
    cd site && npm install && npm run build

# Serve the reporting site with live reload.
site-dev:
    cd site && npm install && npm run dev

# End-to-end tests against a real build of the reporting site.
site-e2e:
    npm install
    npx playwright install --with-deps chromium
    npm run test:e2e
