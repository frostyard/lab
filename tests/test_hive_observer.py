"""Tests for the sanitized Hive observer contract and cache."""

from __future__ import annotations

import importlib.util
import json
import sys
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "hive-observer" / "observer.py"

spec = importlib.util.spec_from_file_location("hive_observer", MODULE_PATH)
hive_observer = importlib.util.module_from_spec(spec)
sys.modules["hive_observer"] = hive_observer
spec.loader.exec_module(hive_observer)  # type: ignore[union-attr]


def status_payload():
    return {
        "timestamp": "2026-08-07T23:14:52Z",
        "hiveId": "frostyard",
        "agents": [
            {
                "name": "scanner",
                "displayName": "Scanner",
                "state": "running",
                "busy": "working",
                "paused": False,
                "offByCadence": False,
                "liveSummary": "SECRET TRANSCRIPT",
                "detailSummary": "PRIVATE OUTPUT",
                "lastError": "private error",
                "model": "private-model",
                "cadence": "4h",
                "authAvailable": True,
            }
        ],
        "governor": {"mode": "idle", "issues": 2, "prs": 1},
        "tokens": {
            "lookbackHours": 24,
            "totals": {
                "sessions": 3,
                "input": 100,
                "output": 20,
                "cacheRead": 40,
                "cacheCreate": 5,
            },
            "sessions": [{"id": "private-session"}],
            "byAgent": {"scanner": {"input": 100}},
            "byModel": {"private-model": {"input": 100}},
        },
        "repos": [
            {
                "name": "snosi",
                "issues": 2,
                "prs": 1,
                "actionableIssues": [
                    {"title": "private issue title", "body": "private body"}
                ],
                "openPrs": [{"title": "private PR title"}],
            }
        ],
        "acmmLevel": 2,
        "systemResources": {"cpuCores": 16},
        "beads": {"workers": 10},
        "unknownFutureField": {"secret": "must not pass through"},
    }


def health_payload():
    return {
        "status": "degraded",
        "checks": {
            "ready": {"status": "pass", "detail": "private detail"},
            "github_auth": {"status": "fail", "detail": "private auth failure"},
            "INVALID CHECK": {"status": "pass"},
        },
        "private": "must not pass through",
    }


def cost_payload():
    return {
        "estimated": {
            "total_usd": 1.25,
            "by_model": [{"name": "private-model", "usd": 1.25}],
            "by_agent": [{"name": "scanner", "usd": 1.25}],
        },
        "gateways": [{"name": "private-gateway"}],
    }


def test_sanitize_overview_emits_only_approved_contract():
    result = hive_observer.sanitize_overview(
        status_payload(),
        health_payload(),
        cost_payload(),
        fetched_at="2026-08-07T23:15:00Z",
    )

    assert result["schemaVersion"] == 1
    assert result["governor"] == {
        "mode": "idle",
        "issues": 2,
        "pullRequests": 1,
    }
    assert result["agents"] == [
        {
            "name": "scanner",
            "displayName": "Scanner",
            "state": "running",
            "activity": "working",
            "paused": False,
            "offByCadence": False,
        }
    ]
    assert result["repositories"] == [
        {"name": "snosi", "issues": 2, "pullRequests": 1}
    ]
    assert result["usage"]["estimatedCostUsd"] == 1.25

    encoded = json.dumps(result)
    for forbidden in [
        "SECRET TRANSCRIPT",
        "PRIVATE OUTPUT",
        "private error",
        "private-model",
        "private-session",
        "private issue title",
        "private body",
        "private PR title",
        "private detail",
        "private auth failure",
        "private-gateway",
        "unknownFutureField",
        "acmmLevel",
        "systemResources",
        "beads",
    ]:
        assert forbidden not in encoded


def test_health_preserves_only_safe_names_and_states():
    result = hive_observer.sanitize_health(health_payload())

    assert result == {
        "status": "degraded",
        "checks": {"github_auth": "fail", "ready": "pass"},
    }


def test_paused_agent_state_is_preserved_without_pause_reason():
    payload = status_payload()
    payload["agents"][0].update(
        {
            "state": "paused",
            "paused": True,
            "pausedReason": "private pause reason",
        }
    )

    result = hive_observer.sanitize_overview(payload, health_payload(), cost_payload())

    assert result["agents"][0]["state"] == "paused"
    assert result["agents"][0]["paused"] is True
    assert "private pause reason" not in json.dumps(result)


def test_invalid_values_become_bounded_unknown_or_zero():
    payload = status_payload()
    payload["governor"] = {"mode": "malicious", "issues": -4, "prs": "many"}
    payload["agents"][0]["state"] = "arbitrary private state"
    payload["tokens"]["totals"]["input"] = float("inf")

    result = hive_observer.sanitize_overview(payload, {}, {})

    assert result["governor"] == {
        "mode": "unknown",
        "issues": 0,
        "pullRequests": 0,
    }
    assert result["agents"][0]["state"] == "unknown"
    assert result["usage"]["inputTokens"] == 0
    assert result["usage"]["estimatedCostUsd"] is None
    assert result["health"]["status"] == "unknown"


def test_cache_returns_stale_data_only_inside_stale_window():
    now = [100.0]
    attempts = [0]

    def fetch():
        attempts[0] += 1
        if attempts[0] > 1:
            raise hive_observer.UpstreamUnavailable
        return hive_observer.sanitize_overview(
            status_payload(),
            health_payload(),
            cost_payload(),
            fetched_at="2026-08-07T23:15:00Z",
        )

    cache = hive_observer.OverviewCache(
        fetch,
        ttl_seconds=30,
        stale_seconds=600,
        clock=lambda: now[0],
        wall_clock=lambda: "2026-08-07T23:20:00Z",
    )

    fresh = cache.get()
    assert fresh["stale"] is False

    now[0] += 31
    stale = cache.get()
    assert stale["stale"] is True
    assert stale["fetchedAt"] == "2026-08-07T23:15:00Z"
    assert stale["lastAttemptAt"] == "2026-08-07T23:20:00Z"

    now[0] += 600
    with pytest.raises(hive_observer.UpstreamUnavailable):
        cache.get()


def test_rate_limiter_releases_entries_after_window():
    now = [10.0]
    limiter = hive_observer.FixedWindowRateLimiter(
        2, window_seconds=60, clock=lambda: now[0]
    )

    assert limiter.allow()
    assert limiter.allow()
    assert not limiter.allow()
    now[0] += 61
    assert limiter.allow()


def test_hive_client_rejects_uninitialized_status(monkeypatch):
    client = hive_observer.HiveClient("http://hive.test", "secret")

    def fetch(path):
        if path == "/api/status":
            return {"status": "initializing"}
        return {}

    monkeypatch.setattr(client, "fetch_json", fetch)
    with pytest.raises(hive_observer.UpstreamUnavailable):
        client.overview()


@contextmanager
def running_server():
    overview = hive_observer.sanitize_overview(
        status_payload(),
        health_payload(),
        cost_payload(),
        fetched_at="2026-08-07T23:15:00Z",
    )
    cache = hive_observer.OverviewCache(lambda: overview)
    server = hive_observer.ObserverHTTPServer(
        ("127.0.0.1", 0),
        cache,
        "proxy-secret",
        hive_observer.FixedWindowRateLimiter(20),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_http_surface_requires_proxy_token_and_rejects_writes():
    with running_server() as base:
        with pytest.raises(urllib.error.HTTPError) as unauthorized:
            urllib.request.urlopen(f"{base}/v1/hive/overview", timeout=2)
        assert unauthorized.value.code == 401

        request = urllib.request.Request(
            f"{base}/v1/hive/overview",
            headers={"X-Hive-Observer-Token": "proxy-secret"},
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            assert response.status == 200
            assert json.load(response)["schemaVersion"] == 1

        write = urllib.request.Request(
            f"{base}/v1/hive/overview",
            method="POST",
            data=b"{}",
        )
        with pytest.raises(urllib.error.HTTPError) as denied:
            urllib.request.urlopen(write, timeout=2)
        assert denied.value.code == 405
