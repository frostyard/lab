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
    kubectl annotate application frostyard-hive -n argocd \
        argocd.argoproj.io/refresh=hard --overwrite
    @kubectl get application frostyard-hive-observer -n argocd >/dev/null 2>&1 && \
        kubectl annotate application frostyard-hive-observer -n argocd \
        argocd.argoproj.io/refresh=hard --overwrite || true

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
    for f in manifests/*.yaml argo/workflow-templates/*.yaml argo/*.yaml argocd/*.yaml hive/*.yaml; do
        [[ -e "$f" ]] || continue
        if kubectl apply --dry-run=server -f "$f" >/dev/null 2>/tmp/validate-err; then
            echo "ok    $f"
        else
            echo "FAIL  $f"
            sed 's/^/        /' /tmp/validate-err
            rc=1
        fi
    done
    if kubectl kustomize hive-observer \
        | kubectl apply --dry-run=server -f - >/dev/null 2>/tmp/validate-err; then
        echo "ok    hive-observer"
    else
        echo "FAIL  hive-observer"
        sed 's/^/        /' /tmp/validate-err
        rc=1
    fi
    exit $rc

# ── hive ─────────────────────────────────────────────────────────────────────

# The hive-secrets Secret is created by hand in the namespace before the app
# syncs — see hive/README.md.
# One-time hive bootstrap: namespace, then the Argo CD Application.
hive-bootstrap:
    kubectl apply -f hive/namespace.yaml
    kubectl apply -f argocd/hive-application.yaml -n argocd

# v2-latest is a moving tag with imagePullPolicy: Always, so git can't drive
# an image bump — a restart re-pulls it.
# Update hive to the newest published image.
hive-update:
    kubectl rollout restart deployment/hive -n hive
    kubectl rollout status deployment/hive -n hive

# Deployment health and the image digest actually running.
hive-status:
    @kubectl get deployment hive -n hive
    @echo
    @kubectl get pods -n hive -l app=hive \
        -o custom-columns='NAME:.metadata.name,STATUS:.status.phase,STARTED:.status.startTime,IMAGE:.status.containerStatuses[0].imageID'

# Follow the hive container logs.
hive-logs:
    kubectl logs -n hive deployment/hive -f

# The observer has its own Argo Application so its health is independent from
# the Hive control plane. Create hive-observer-secrets first; see its README.
hive-observer-bootstrap:
    kubectl apply -f argocd/hive-observer-application.yaml -n argocd

hive-observer-status:
    @kubectl get deployment,service -n hive -l app=hive-observer

hive-observer-logs:
    kubectl logs -n hive deployment/hive-observer -f

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
