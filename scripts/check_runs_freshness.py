#!/usr/bin/env python3
"""Fail when the published run data is stale.

Usage: scripts/check_runs_freshness.py RUNS_JSON [--max-age-hours N]

Reads a runs.json (the collector's output contract) and exits 1 when its
newest run (the latest `started` or `finished` time of any run) is older than
N hours (default 6), or when the file is missing, invalid or holds no run
time. The Pages workflow runs it on the data from the `data/runs` branch, so
a stalled publish-results CronWorkflow, or a cluster that stopped running
workflows, shows as a red check instead of a quietly frozen dashboard.

It checks the freshness of the published data only, not the health of a
lane: each publish-results run records itself, so the data stays fresh while
the publisher runs, even when every product poll stops. The dashboard's own
per-poll staleness rule (ADR-0011) covers the lanes.

Why 6 hours: the 4 product polls each run every 3 hours, staggered, and every
publish-results run records itself, so a healthy lab publishes a newer run at
least every 30 minutes. 6 hours is 2 missed cycles of a product poll, and it is
the age at which the dashboard already calls poll evidence stale (ADR-0011).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_MAX_AGE_HOURS = 6.0


def parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def newest_run_time(data: object) -> datetime | None:
    if not isinstance(data, dict) or not isinstance(data.get("runs"), list):
        return None
    times = [
        moment
        for run in data["runs"]
        if isinstance(run, dict)
        for moment in (parse_time(run.get("started")), parse_time(run.get("finished")))
        if moment is not None
    ]
    return max(times, default=None)


def check(path: Path, max_age: timedelta, now: datetime) -> tuple[bool, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return False, f"cannot read run data {path}: {error}"
    newest = newest_run_time(data)
    if newest is None:
        return False, f"{path} holds no run with a start or finish time"
    age = now - newest
    hours = age.total_seconds() / 3600
    limit = max_age.total_seconds() / 3600
    if age > max_age:
        return False, (
            f"stale run data: the newest run is from {newest:%Y-%m-%dT%H:%M:%SZ}, "
            f"{hours:.1f} h ago (limit {limit:g} h); check the publish-results "
            "CronWorkflow and the data/runs branch"
        )
    return True, (
        f"fresh run data: the newest run is from {newest:%Y-%m-%dT%H:%M:%SZ}, "
        f"{hours:.1f} h ago (limit {limit:g} h)"
    )


def main(argv: list[str] | None = None, now: datetime | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("runs_json", type=Path)
    parser.add_argument("--max-age-hours", type=float, default=DEFAULT_MAX_AGE_HOURS)
    args = parser.parse_args(argv)
    if args.max_age_hours <= 0:
        parser.error("--max-age-hours must be greater than 0")
    ok, message = check(
        args.runs_json,
        timedelta(hours=args.max_age_hours),
        now or datetime.now(timezone.utc),
    )
    print(message, file=sys.stdout if ok else sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
