"""Offline validation for every Kubernetes and Argo manifest in the repository."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml
from kubernetes_validate import validate
from yaml.constructor import ConstructorError
from yaml.resolver import BaseResolver

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_ROOTS = ("argocd", "argo", "manifests")
KUBERNETES_VERSION = "1.36.0"

EXPECTED_API_VERSIONS = {
    "Application": "argoproj.io/v1alpha1",
    "ConfigMap": "v1",
    "CronWorkflow": "argoproj.io/v1alpha1",
    "Namespace": "v1",
    "Role": "rbac.authorization.k8s.io/v1",
    "RoleBinding": "rbac.authorization.k8s.io/v1",
    "ServiceAccount": "v1",
    "Workflow": "argoproj.io/v1alpha1",
    "WorkflowTemplate": "argoproj.io/v1alpha1",
}
CUSTOM_RESOURCES = {"Application", "CronWorkflow", "Workflow", "WorkflowTemplate"}
DEPENDENCY_STATES = (
    "Succeeded",
    "Failed",
    "Errored",
    "Skipped",
    "Omitted",
    "Daemoned",
    "AnySucceeded",
    "AllFailed",
)
DEPENDENCY_REFERENCE = re.compile(
    rf"\b([a-z0-9](?:[-a-z0-9]*[a-z0-9])?)\.(?:{'|'.join(DEPENDENCY_STATES)})\b"
)


class UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate mapping keys."""


def construct_unique_mapping(
    loader: UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


UniqueKeyLoader.add_constructor(
    BaseResolver.DEFAULT_MAPPING_TAG, construct_unique_mapping
)


@dataclass(frozen=True)
class Manifest:
    path: Path
    document: int
    body: dict[str, Any]

    @property
    def source(self) -> str:
        return f"{self.path.relative_to(ROOT)} document {self.document}"

    @property
    def kind(self) -> str:
        return self.body["kind"]

    @property
    def name(self) -> str:
        metadata = self.body["metadata"]
        return metadata.get("name") or metadata["generateName"]


@pytest.fixture(scope="module")
def manifests() -> tuple[Manifest, ...]:
    loaded: list[Manifest] = []
    for root_name in MANIFEST_ROOTS:
        root = ROOT / root_name
        paths = sorted({*root.rglob("*.yaml"), *root.rglob("*.yml")})
        assert paths, f"{root_name}/ has no YAML manifests"
        for path in paths:
            try:
                documents = list(
                    yaml.load_all(path.read_text(encoding="utf-8"), Loader=UniqueKeyLoader)
                )
            except yaml.YAMLError as error:
                pytest.fail(f"{path.relative_to(ROOT)} is not valid YAML: {error}")
            assert documents, f"{path.relative_to(ROOT)} contains no YAML documents"
            for index, body in enumerate(documents, start=1):
                assert body is not None, (
                    f"{path.relative_to(ROOT)} document {index} is empty"
                )
                assert isinstance(body, dict), (
                    f"{path.relative_to(ROOT)} document {index} is not a mapping"
                )
                loaded.append(Manifest(path=path, document=index, body=body))
    return tuple(loaded)


def resources_by_kind(
    manifests: tuple[Manifest, ...], kind: str
) -> list[Manifest]:
    return [manifest for manifest in manifests if manifest.body.get("kind") == kind]


def manifest_named(
    manifests: tuple[Manifest, ...], kind: str, name: str
) -> Manifest:
    matches = [
        manifest
        for manifest in manifests
        if manifest.body.get("kind") == kind
        and manifest.body.get("metadata", {}).get("name") == name
    ]
    assert len(matches) == 1, f"expected one {kind}/{name}, found {len(matches)}"
    return matches[0]


def workflow_spec(manifest: Manifest) -> dict[str, Any]:
    if manifest.kind == "CronWorkflow":
        return manifest.body["spec"]["workflowSpec"]
    return manifest.body["spec"]


def parameter_map(parameters: list[dict[str, Any]], source: str) -> dict[str, dict]:
    names = [parameter.get("name") for parameter in parameters]
    assert all(isinstance(name, str) and name for name in names), (
        f"{source} has a parameter without a name"
    )
    assert len(names) == len(set(names)), f"{source} has duplicate parameters: {names}"
    return dict(zip(names, parameters, strict=True))


def assert_arguments_match(
    arguments: dict[str, Any] | None,
    declarations: list[dict[str, Any]],
    source: str,
) -> None:
    declared = parameter_map(declarations, f"{source} target")
    provided = parameter_map(
        (arguments or {}).get("parameters", []), f"{source} arguments"
    )
    required = {
        name
        for name, parameter in declared.items()
        if "default" not in parameter and "value" not in parameter
    }
    assert provided.keys() <= declared.keys(), (
        f"{source} passes undeclared parameters: "
        f"{sorted(provided.keys() - declared.keys())}"
    )
    assert required <= provided.keys(), (
        f"{source} omits required parameters: {sorted(required - provided.keys())}"
    )


def walk_dicts(value: Any, parent_key: str = ""):
    if isinstance(value, dict):
        yield value, parent_key
        for key, child in value.items():
            yield from walk_dicts(child, key)
    elif isinstance(value, list):
        for child in value:
            yield from walk_dicts(child, parent_key)


def test_every_yaml_document_is_a_unique_well_formed_resource(manifests):
    fixed_identities: dict[tuple[str, str, str, str], str] = {}

    for manifest in manifests:
        body = manifest.body
        kind = body.get("kind")
        assert kind in EXPECTED_API_VERSIONS, (
            f"{manifest.source} has unsupported kind {kind!r}"
        )
        assert body.get("apiVersion") == EXPECTED_API_VERSIONS[kind], (
            f"{manifest.source} uses {body.get('apiVersion')!r} for {kind}"
        )

        metadata = body.get("metadata")
        assert isinstance(metadata, dict), f"{manifest.source} has no metadata mapping"
        identifiers = [
            value
            for value in (metadata.get("name"), metadata.get("generateName"))
            if value is not None
        ]
        assert len(identifiers) == 1 and isinstance(identifiers[0], str), (
            f"{manifest.source} must set exactly one string name or generateName"
        )
        assert identifiers[0], f"{manifest.source} has an empty resource identifier"

        if kind == "Namespace":
            assert "namespace" not in metadata, (
                f"{manifest.source} is cluster-scoped but sets metadata.namespace"
            )
        else:
            expected_namespace = "argocd" if kind == "Application" else "argo"
            assert metadata.get("namespace") == expected_namespace, (
                f"{manifest.source} must use namespace {expected_namespace!r}"
            )

        if "name" in metadata:
            identity = (
                body["apiVersion"],
                kind,
                metadata.get("namespace", ""),
                metadata["name"],
            )
            assert identity not in fixed_identities, (
                f"{manifest.source} duplicates resource from "
                f"{fixed_identities.get(identity)}"
            )
            fixed_identities[identity] = manifest.source


def test_yaml_loader_rejects_duplicate_mapping_keys():
    with pytest.raises(ConstructorError, match="found duplicate key 'name'"):
        yaml.load(
            "metadata:\n  name: first\n  name: second\n",
            Loader=UniqueKeyLoader,
        )


def test_builtin_resources_match_strict_kubernetes_1_36_schemas(manifests):
    validated = 0
    for manifest in manifests:
        if manifest.kind in CUSTOM_RESOURCES:
            continue
        validate(
            manifest.body,
            desired_version=KUBERNETES_VERSION,
            strict=True,
        )
        validated += 1

    assert validated > 0, "no built-in Kubernetes resources were schema-validated"


def test_argocd_applications_own_exact_nonoverlapping_sources(manifests):
    applications = {
        manifest.body["metadata"]["name"]: manifest.body
        for manifest in resources_by_kind(manifests, "Application")
    }
    assert applications.keys() == {
        "argo-workflows",
        "frostyard-lab",
        "frostyard-lab-infra",
    }

    local_sources = {
        "frostyard-lab": "argo/workflow-templates",
        "frostyard-lab-infra": "manifests",
    }
    for name, path in local_sources.items():
        application = applications[name]
        source = application["spec"]["source"]
        assert source == {
            "repoURL": "https://github.com/frostyard/lab",
            "targetRevision": "main",
            "path": path,
        }
        assert (ROOT / path).is_dir()

    helm_source = applications["argo-workflows"]["spec"]["source"]
    assert set(helm_source) == {"repoURL", "chart", "targetRevision", "helm"}
    assert helm_source["repoURL"] == "https://argoproj.github.io/argo-helm"
    assert helm_source["chart"] == "argo-workflows"
    assert helm_source["targetRevision"] == "1.0.23"
    assert isinstance(helm_source["helm"].get("values"), str)

    for name, application in applications.items():
        spec = application["spec"]
        assert spec["destination"] == {
            "server": "https://kubernetes.default.svc",
            "namespace": "argo",
        }
        automated = spec["syncPolicy"]["automated"]
        assert automated["prune"] is True, name
        assert automated["selfHeal"] is True, name
        assert "CreateNamespace=false" in spec["syncPolicy"]["syncOptions"], name


def test_argo_workflow_references_and_parameters_resolve(manifests):
    workflow_templates = {
        manifest.body["metadata"]["name"]: manifest
        for manifest in resources_by_kind(manifests, "WorkflowTemplate")
    }
    assert workflow_templates, "no WorkflowTemplate resources found"

    for manifest in manifests:
        if manifest.kind not in {"CronWorkflow", "Workflow", "WorkflowTemplate"}:
            continue

        spec = workflow_spec(manifest)
        templates = spec.get("templates", [])
        local_templates = {template["name"]: template for template in templates}
        assert len(local_templates) == len(templates), (
            f"{manifest.source} has duplicate local template names"
        )

        entrypoint = spec.get("entrypoint")
        if entrypoint is not None:
            assert entrypoint in local_templates, (
                f"{manifest.source} entrypoint {entrypoint!r} does not exist"
            )

        workflow_ref = spec.get("workflowTemplateRef")
        if workflow_ref is not None:
            target_name = workflow_ref["name"]
            assert target_name in workflow_templates, (
                f"{manifest.source} references missing WorkflowTemplate/{target_name}"
            )
            target_spec = workflow_templates[target_name].body["spec"]
            assert_arguments_match(
                spec.get("arguments"),
                target_spec.get("arguments", {}).get("parameters", []),
                f"{manifest.source} workflowTemplateRef {target_name}",
            )

        for node, parent_key in walk_dicts(templates):
            if "templateRef" in node:
                reference = node["templateRef"]
                target_name = reference["name"]
                target_template = reference["template"]
                assert target_name in workflow_templates, (
                    f"{manifest.source} references missing "
                    f"WorkflowTemplate/{target_name}"
                )
                target_templates = {
                    template["name"]: template
                    for template in workflow_templates[target_name]
                    .body["spec"]
                    .get("templates", [])
                }
                assert target_template in target_templates, (
                    f"{manifest.source} references missing template "
                    f"{target_name}/{target_template}"
                )
                assert_arguments_match(
                    node.get("arguments"),
                    target_templates[target_template]
                    .get("inputs", {})
                    .get("parameters", []),
                    f"{manifest.source} templateRef "
                    f"{target_name}/{target_template}",
                )
            elif "template" in node and parent_key != "templateRef":
                target_name = node["template"]
                assert target_name in local_templates, (
                    f"{manifest.source} references missing local template "
                    f"{target_name!r}"
                )
                assert_arguments_match(
                    node.get("arguments"),
                    local_templates[target_name]
                    .get("inputs", {})
                    .get("parameters", []),
                    f"{manifest.source} local template {target_name}",
                )

        for template in templates:
            dag = template.get("dag")
            if dag is None:
                continue
            tasks = dag.get("tasks", [])
            task_names = [task["name"] for task in tasks]
            assert len(task_names) == len(set(task_names)), (
                f"{manifest.source} template {template['name']} has duplicate tasks"
            )
            known_tasks = set(task_names)
            for task in tasks:
                dependencies = set(task.get("dependencies", []))
                dependencies.update(DEPENDENCY_REFERENCE.findall(task.get("depends", "")))
                assert dependencies <= known_tasks, (
                    f"{manifest.source} task {task['name']} depends on missing tasks "
                    f"{sorted(dependencies - known_tasks)}"
                )


def test_workflows_have_service_accounts_and_bounded_defaults(manifests):
    controller = manifest_named(
        manifests, "ConfigMap", "workflow-controller-configmap"
    ).body
    defaults = yaml.load(
        controller["data"]["workflowDefaults"], Loader=UniqueKeyLoader
    )["spec"]
    assert defaults["activeDeadlineSeconds"] == 7200
    assert defaults["parallelism"] == 10

    for manifest in manifests:
        if manifest.kind not in {"CronWorkflow", "Workflow"}:
            continue
        spec = workflow_spec(manifest)
        assert spec.get("serviceAccountName") == "argo", (
            f"{manifest.source} must run as ServiceAccount/argo"
        )
        deadline = spec.get("activeDeadlineSeconds", defaults["activeDeadlineSeconds"])
        assert isinstance(deadline, int) and deadline > 0, (
            f"{manifest.source} has no positive active deadline"
        )

        if manifest.kind == "CronWorkflow":
            cron_spec = manifest.body["spec"]
            assert cron_spec.get("concurrencyPolicy") == "Forbid", manifest.source
            assert cron_spec.get("startingDeadlineSeconds", 0) > 0, manifest.source
            assert cron_spec.get("schedules"), manifest.source


def test_workload_containers_declare_cpu_and_memory_bounds(manifests):
    checked = 0
    for manifest in manifests:
        for node, _ in walk_dicts(manifest.body):
            if "image" not in node:
                continue

            checked += 1
            image = node["image"]
            assert isinstance(image, str) and image, (
                f"{manifest.source} has a container without an image"
            )
            resources = node.get("resources")
            assert isinstance(resources, dict), (
                f"{manifest.source} image {image!r} has no resource bounds"
            )
            for bound in ("requests", "limits"):
                quantities = resources.get(bound)
                assert isinstance(quantities, dict), (
                    f"{manifest.source} image {image!r} has no resource {bound}"
                )
                for resource in ("cpu", "memory"):
                    quantity = quantities.get(resource)
                    assert isinstance(quantity, str) and quantity, (
                        f"{manifest.source} image {image!r} has no "
                        f"{bound}.{resource}"
                    )

    assert checked > 0, "no workload containers were resource-policy validated"


def test_rbac_rules_and_bindings_remain_least_privilege(manifests):
    roles = {
        manifest.body["metadata"]["name"]: manifest.body
        for manifest in resources_by_kind(manifests, "Role")
    }

    def normalize_rules(rules):
        return {
            (
                frozenset(rule["apiGroups"]),
                frozenset(rule["resources"]),
                frozenset(rule["verbs"]),
            )
            for rule in rules
        }

    assert {
        name: normalize_rules(role["rules"]) for name, role in roles.items()
    } == {
        "argo-executor": {
            (
                frozenset({"argoproj.io"}),
                frozenset({"workflowtaskresults"}),
                frozenset({"create", "patch"}),
            )
        },
        "argo-configmap-writer": {
            (
                frozenset({""}),
                frozenset({"configmaps"}),
                frozenset({"get", "list", "watch", "create", "patch", "update"}),
            )
        },
        "argo-workflow-operator": {
            (
                frozenset({"argoproj.io"}),
                frozenset(
                    {
                        "workflows",
                        "workflows/finalizers",
                        "workflowtemplates",
                        "cronworkflows",
                    }
                ),
                frozenset(
                    {"get", "list", "watch", "create", "update", "patch", "delete"}
                ),
            ),
            (
                frozenset({""}),
                frozenset({"pods", "pods/log"}),
                frozenset({"get", "list", "watch", "delete"}),
            ),
        },
    }

    bindings = {
        manifest.body["metadata"]["name"]: manifest.body
        for manifest in resources_by_kind(manifests, "RoleBinding")
    }
    assert bindings.keys() == roles.keys()
    for name, binding in bindings.items():
        assert binding["roleRef"] == {
            "apiGroup": "rbac.authorization.k8s.io",
            "kind": "Role",
            "name": name,
        }
        subjects = {
            (subject["kind"], subject["name"], subject["namespace"])
            for subject in binding["subjects"]
        }
        expected = {("ServiceAccount", "argo", "argo")}
        if name == "argo-executor":
            expected.add(("ServiceAccount", "default", "argo"))
        assert subjects == expected, name


def test_image_pollers_and_digest_state_are_one_to_one(manifests):
    pollers = [
        manifest
        for manifest in resources_by_kind(manifests, "CronWorkflow")
        if manifest.path.parent == ROOT / "manifests"
        and manifest.body["metadata"]["name"].startswith("image-poll-")
    ]
    assert pollers, "no image-poll CronWorkflows found"

    state_keys: set[str] = set()
    schedules: set[str] = set()
    for poller in pollers:
        body = poller.body
        name = body["metadata"]["name"]
        spec = body["spec"]
        workflow = spec["workflowSpec"]
        arguments = {
            parameter["name"]: parameter.get("value")
            for parameter in workflow["arguments"]["parameters"]
        }

        image_name = arguments["image"].rsplit("/", 1)[-1]
        tag = arguments["image-tag"]
        assert name == f"image-poll-{image_name}-{tag}"
        assert arguments["variant"] == image_name
        assert arguments["state-key"] == f"digest-{image_name}-{tag}"
        assert arguments["image-digest"] == ""
        assert workflow["workflowTemplateRef"] == {"name": "image-poller"}
        assert spec["concurrencyPolicy"] == "Forbid"

        schedule = spec["schedules"][0]
        assert schedule not in schedules, f"duplicate image-poll schedule {schedule}"
        schedules.add(schedule)
        state_keys.add(arguments["state-key"])

    digest_state = manifest_named(
        manifests, "ConfigMap", "image-polling-digests"
    ).body["data"]
    assert set(digest_state) == state_keys
    assert all(value == "" for value in digest_state.values())


def test_image_poller_persists_only_after_qa_or_explicit_opt_out(manifests):
    poller = manifest_named(manifests, "WorkflowTemplate", "image-poller").body
    workflow_arguments = parameter_map(
        poller["spec"]["arguments"]["parameters"], "image-poller arguments"
    )
    templates = {
        template["name"]: template for template in poller["spec"]["templates"]
    }
    tasks = {
        task["name"]: task
        for task in templates["check-and-trigger"]["dag"]["tasks"]
    }

    assert workflow_arguments["run-qa"]["value"] == "true"
    assert tasks["run-pipeline"]["depends"] == "check-digest.Succeeded"
    assert tasks["run-pipeline"]["when"] == (
        "{{tasks.check-digest.outputs.parameters.changed}} == true && "
        "{{workflow.parameters.run-qa}} == true"
    )
    assert tasks["update-digest"]["depends"] == (
        "check-digest.Succeeded && "
        "(run-pipeline.Succeeded || run-pipeline.Skipped)"
    )
    assert tasks["update-digest"]["when"] == (
        "{{tasks.check-digest.outputs.parameters.changed}} == true"
    )

    update_source = templates["update-local"]["script"]["source"]
    for required in (
        'if [[ "${STORED}" != "${EXPECTED}" ]]',
        "skipping stale write",
        "kubectl patch configmap image-polling-digests",
    ):
        assert required in update_source
