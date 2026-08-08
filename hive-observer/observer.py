#!/usr/bin/env python3
"""Expose a small, sanitized read-only view of the Hive dashboard API."""

from __future__ import annotations

import copy
import hmac
import json
import logging
import math
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

SCHEMA_VERSION = 1
MAX_UPSTREAM_BYTES = 2 * 1024 * 1024
ESTIMATE_DISCLAIMER = (
    "Estimated from token counts and published list prices; not billed spend."
)
ALLOWED_AGENT_STATES = {
    "created",
    "starting",
    "running",
    "paused",
    "stopped",
    "exited",
    "failed",
    "unknown",
}
ALLOWED_AGENT_ACTIVITY = {"idle", "working", "unknown"}
ALLOWED_GOVERNOR_MODES = {"idle", "quiet", "busy", "surge", "unknown"}
ALLOWED_HEALTH_STATES = {"ok", "degraded", "unhealthy", "unknown"}
ALLOWED_CHECK_STATES = {"pass", "warn", "fail", "unknown"}
CHECK_NAME_RE = re.compile(r"^[a-z0-9_-]{1,40}$")

LOGGER = logging.getLogger("hive-observer")


class ObserverError(RuntimeError):
    """Base class for errors safe to map to a public status code."""


class UpstreamUnavailable(ObserverError):
    """Hive did not return a usable required response."""


class Unauthorized(ObserverError):
    """The Caddy-to-observer credential was missing or invalid."""


class RateLimited(ObserverError):
    """The public request rate exceeded the configured limit."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Do not forward Hive's internal credential across redirects."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_secret(path: str) -> str:
    value = Path(path).read_text(encoding="utf-8").strip()
    if not value:
        raise ValueError(f"secret file is empty: {path}")
    return value


def safe_string(value: Any, limit: int = 80) -> str:
    if not isinstance(value, str):
        return ""
    printable = "".join(ch for ch in value if ch >= " " and ch != "\x7f")
    return printable[:limit]


def safe_nonnegative_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(value, 0)


def safe_nonnegative_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    if not math.isfinite(result) or result < 0:
        return None
    return result


def safe_timestamp(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return value


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def enum_value(value: Any, allowed: set[str]) -> str:
    normalized = safe_string(value, 40).lower()
    return normalized if normalized in allowed else "unknown"


def sanitize_health(raw: Any) -> dict[str, Any]:
    payload = as_dict(raw)
    checks: dict[str, str] = {}
    for name, value in as_dict(payload.get("checks")).items():
        if not isinstance(name, str) or not CHECK_NAME_RE.fullmatch(name):
            continue
        check = as_dict(value)
        state = enum_value(check.get("status"), ALLOWED_CHECK_STATES)
        if state == "unknown":
            nested = [
                enum_value(as_dict(child).get("status"), ALLOWED_CHECK_STATES)
                for child in check.values()
                if isinstance(child, dict)
            ]
            if "fail" in nested:
                state = "fail"
            elif "warn" in nested:
                state = "warn"
            elif "pass" in nested:
                state = "pass"
        checks[name] = state

    return {
        "status": enum_value(payload.get("status"), ALLOWED_HEALTH_STATES),
        "checks": dict(sorted(checks.items())),
    }


def sanitize_agents(raw: Any) -> list[dict[str, Any]]:
    agents = []
    for item in as_list(raw):
        source = as_dict(item)
        name = safe_string(source.get("name"))
        if not name:
            continue
        agents.append(
            {
                "name": name,
                "displayName": safe_string(source.get("displayName")) or name,
                "state": enum_value(source.get("state"), ALLOWED_AGENT_STATES),
                "activity": enum_value(source.get("busy"), ALLOWED_AGENT_ACTIVITY),
                "paused": source.get("paused") is True,
                "offByCadence": source.get("offByCadence") is True,
            }
        )
    return agents


def sanitize_repositories(raw: Any) -> list[dict[str, Any]]:
    repositories = []
    for item in as_list(raw):
        source = as_dict(item)
        name = safe_string(source.get("name"))
        if not name:
            continue
        repositories.append(
            {
                "name": name,
                "issues": safe_nonnegative_int(source.get("issues")),
                "pullRequests": safe_nonnegative_int(source.get("prs")),
            }
        )
    return repositories


def sanitize_overview(
    status_raw: Any,
    health_raw: Any,
    cost_raw: Any,
    unavailable_sections: list[str] | None = None,
    fetched_at: str | None = None,
) -> dict[str, Any]:
    status = as_dict(status_raw)
    governor = as_dict(status.get("governor"))
    tokens = as_dict(status.get("tokens"))
    totals = as_dict(tokens.get("totals"))
    cost = as_dict(as_dict(cost_raw).get("estimated"))
    fetched = fetched_at or utc_now()

    return {
        "schemaVersion": SCHEMA_VERSION,
        "fetchedAt": fetched,
        "lastAttemptAt": fetched,
        "sourceTimestamp": safe_timestamp(status.get("timestamp")),
        "stale": False,
        "unavailableSections": sorted(set(unavailable_sections or [])),
        "health": sanitize_health(health_raw),
        "governor": {
            "mode": enum_value(governor.get("mode"), ALLOWED_GOVERNOR_MODES),
            "issues": safe_nonnegative_int(governor.get("issues")),
            "pullRequests": safe_nonnegative_int(governor.get("prs")),
        },
        "agents": sanitize_agents(status.get("agents")),
        "repositories": sanitize_repositories(status.get("repos")),
        "usage": {
            "lookbackHours": safe_nonnegative_int(tokens.get("lookbackHours")),
            "sessions": safe_nonnegative_int(totals.get("sessions")),
            "inputTokens": safe_nonnegative_int(totals.get("input")),
            "outputTokens": safe_nonnegative_int(totals.get("output")),
            "cacheReadTokens": safe_nonnegative_int(totals.get("cacheRead")),
            "cacheCreateTokens": safe_nonnegative_int(totals.get("cacheCreate")),
            "estimatedCostUsd": safe_nonnegative_float(cost.get("total_usd")),
            "disclaimer": ESTIMATE_DISCLAIMER,
        },
    }


class HiveClient:
    """Bounded client for the three exact Hive endpoints the observer needs."""

    ENDPOINTS = {
        "status": "/api/status",
        "health": "/api/health/deep",
        "cost": "/api/cost",
    }

    def __init__(self, base_url: str, token: str, timeout_seconds: float = 5.0):
        parsed = urllib.parse.urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("HIVE_BASE_URL must be an absolute HTTP(S) URL")
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout_seconds = timeout_seconds
        self.opener = urllib.request.build_opener(NoRedirect)

    def fetch_json(self, path: str) -> dict[str, Any]:
        request = urllib.request.Request(
            self.base_url + path,
            headers={
                "Accept": "application/json",
                "X-Hive-Internal": self.token,
                "User-Agent": "frostyard-hive-observer/1",
            },
            method="GET",
        )
        try:
            with self.opener.open(request, timeout=self.timeout_seconds) as response:
                body = response.read(MAX_UPSTREAM_BYTES + 1)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            raise UpstreamUnavailable("upstream request failed") from exc

        if len(body) > MAX_UPSTREAM_BYTES:
            raise UpstreamUnavailable("upstream response exceeded size limit")
        try:
            parsed = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise UpstreamUnavailable("upstream returned invalid JSON") from exc
        if not isinstance(parsed, dict):
            raise UpstreamUnavailable("upstream returned a non-object response")
        return parsed

    def overview(self) -> dict[str, Any]:
        results: dict[str, dict[str, Any]] = {}
        unavailable: list[str] = []
        with ThreadPoolExecutor(max_workers=len(self.ENDPOINTS)) as executor:
            futures = {
                executor.submit(self.fetch_json, path): name
                for name, path in self.ENDPOINTS.items()
            }
            for future in as_completed(futures):
                name = futures[future]
                try:
                    results[name] = future.result()
                except UpstreamUnavailable:
                    unavailable.append(name)
                    LOGGER.warning("Hive upstream section unavailable: %s", name)

        if "status" not in results:
            raise UpstreamUnavailable("required Hive status is unavailable")
        status = results["status"]
        if (
            safe_timestamp(status.get("timestamp")) is None
            or not isinstance(status.get("governor"), dict)
            or not isinstance(status.get("agents"), list)
            or not isinstance(status.get("tokens"), dict)
            or not isinstance(status.get("repos"), list)
        ):
            raise UpstreamUnavailable("Hive status is not initialized")

        return sanitize_overview(
            status,
            results.get("health", {}),
            results.get("cost", {}),
            unavailable_sections=unavailable,
            fetched_at=utc_now(),
        )


@dataclass
class CacheEntry:
    payload: dict[str, Any]
    stored_at: float


class OverviewCache:
    def __init__(
        self,
        fetcher: Callable[[], dict[str, Any]],
        ttl_seconds: float = 30.0,
        stale_seconds: float = 600.0,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], str] = utc_now,
    ):
        self.fetcher = fetcher
        self.ttl_seconds = ttl_seconds
        self.stale_seconds = stale_seconds
        self.clock = clock
        self.wall_clock = wall_clock
        self.entry: CacheEntry | None = None
        self.lock = threading.Lock()
        self.refresh_lock = threading.Lock()

    def get(self) -> dict[str, Any]:
        now = self.clock()
        with self.lock:
            if self.entry and now - self.entry.stored_at <= self.ttl_seconds:
                return copy.deepcopy(self.entry.payload)

        with self.refresh_lock:
            now = self.clock()
            with self.lock:
                if self.entry and now - self.entry.stored_at <= self.ttl_seconds:
                    return copy.deepcopy(self.entry.payload)

            attempt_at = self.wall_clock()
            try:
                payload = self.fetcher()
            except UpstreamUnavailable:
                with self.lock:
                    entry = self.entry
                    if entry and now - entry.stored_at <= self.stale_seconds:
                        stale = copy.deepcopy(entry.payload)
                        stale["stale"] = True
                        stale["lastAttemptAt"] = attempt_at
                        return stale
                raise

            payload["stale"] = False
            payload["lastAttemptAt"] = attempt_at
            with self.lock:
                self.entry = CacheEntry(copy.deepcopy(payload), now)
            return payload


class FixedWindowRateLimiter:
    def __init__(
        self,
        limit: int,
        window_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.limit = max(limit, 1)
        self.window_seconds = window_seconds
        self.clock = clock
        self.requests: deque[float] = deque()
        self.lock = threading.Lock()

    def allow(self) -> bool:
        now = self.clock()
        cutoff = now - self.window_seconds
        with self.lock:
            while self.requests and self.requests[0] <= cutoff:
                self.requests.popleft()
            if len(self.requests) >= self.limit:
                return False
            self.requests.append(now)
            return True


class ObserverHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        address,
        cache: OverviewCache,
        proxy_token: str,
        rate_limiter: FixedWindowRateLimiter,
    ):
        super().__init__(address, ObserverHandler)
        self.cache = cache
        self.proxy_token = proxy_token
        self.rate_limiter = rate_limiter


class ObserverHandler(BaseHTTPRequestHandler):
    server: ObserverHTTPServer
    server_version = "hive-observer"
    sys_version = ""

    def log_message(self, format_string: str, *args: Any) -> None:
        path = urllib.parse.urlsplit(self.path).path
        LOGGER.info("%s %s %s", self.client_address[0], self.command, path)

    def send_json(
        self,
        status: int,
        payload: dict[str, Any],
        *,
        head_only: bool = False,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def authenticate(self) -> None:
        supplied = self.headers.get("X-Hive-Observer-Token", "")
        if not supplied or not hmac.compare_digest(supplied, self.server.proxy_token):
            raise Unauthorized

    def handle_overview(self, *, head_only: bool = False) -> None:
        self.authenticate()
        if not self.server.rate_limiter.allow():
            raise RateLimited
        payload = self.server.cache.get()
        self.send_json(
            200,
            payload,
            head_only=head_only,
            extra_headers={"Cache-Control": "public, max-age=15, stale-if-error=600"},
        )

    def route(self, *, head_only: bool = False) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.query:
            self.send_json(404, {"error": "not_found"}, head_only=head_only)
            return
        if parsed.path == "/healthz":
            self.send_json(200, {"status": "ok"}, head_only=head_only)
            return
        if parsed.path == "/readyz":
            self.send_json(200, {"status": "ready"}, head_only=head_only)
            return
        if parsed.path != "/v1/hive/overview":
            self.send_json(404, {"error": "not_found"}, head_only=head_only)
            return
        self.handle_overview(head_only=head_only)

    def do_GET(self) -> None:
        try:
            self.route()
        except Unauthorized:
            self.send_json(401, {"error": "unauthorized"})
        except RateLimited:
            self.send_json(
                429, {"error": "rate_limited"}, extra_headers={"Retry-After": "60"}
            )
        except UpstreamUnavailable:
            self.send_json(503, {"error": "upstream_unavailable"})
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception:
            LOGGER.exception("Unhandled observer request failure")
            self.send_json(500, {"error": "internal_error"})

    def do_HEAD(self) -> None:
        try:
            self.route(head_only=True)
        except Unauthorized:
            self.send_json(401, {"error": "unauthorized"}, head_only=True)
        except RateLimited:
            self.send_json(
                429,
                {"error": "rate_limited"},
                head_only=True,
                extra_headers={"Retry-After": "60"},
            )
        except UpstreamUnavailable:
            self.send_json(503, {"error": "upstream_unavailable"}, head_only=True)
        except Exception:
            LOGGER.exception("Unhandled observer HEAD failure")
            self.send_json(500, {"error": "internal_error"}, head_only=True)

    def unsupported_method(self) -> None:
        self.send_json(
            405,
            {"error": "method_not_allowed"},
            extra_headers={"Allow": "GET, HEAD"},
        )

    do_POST = unsupported_method
    do_PUT = unsupported_method
    do_PATCH = unsupported_method
    do_DELETE = unsupported_method
    do_OPTIONS = unsupported_method


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    hive_token = read_secret(
        os.getenv(
            "HIVE_TOKEN_FILE",
            "/var/run/secrets/hive-observer/hive-dashboard-token",
        )
    )
    proxy_token = read_secret(
        os.getenv(
            "PROXY_TOKEN_FILE",
            "/var/run/secrets/hive-observer/proxy-token",
        )
    )
    client = HiveClient(
        os.getenv("HIVE_BASE_URL", "http://hive:3002"),
        hive_token,
        timeout_seconds=float(os.getenv("UPSTREAM_TIMEOUT_SECONDS", "5")),
    )
    cache = OverviewCache(
        client.overview,
        ttl_seconds=float(os.getenv("CACHE_TTL_SECONDS", "30")),
        stale_seconds=float(os.getenv("STALE_MAX_SECONDS", "600")),
    )
    limiter = FixedWindowRateLimiter(
        int(os.getenv("MAX_REQUESTS_PER_MINUTE", "120"))
    )
    host = os.getenv("LISTEN_HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8080"))
    server = ObserverHTTPServer((host, port), cache, proxy_token, limiter)
    LOGGER.info("Hive observer listening on %s:%d", host, port)
    server.serve_forever()


if __name__ == "__main__":
    main()
