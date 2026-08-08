#!/usr/bin/env node

import { appendFile } from "node:fs/promises";

const DEFAULT_DAYS = 90;
const PAGE_SIZE = 100;

function parseArguments(argv) {
  const options = {
    days: DEFAULT_DAYS,
    repository: process.env.GITHUB_REPOSITORY,
  };

  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];

    if (argument === "--days") {
      options.days = Number.parseInt(argv[++index], 10);
    } else if (argument === "--repo") {
      options.repository = argv[++index];
    } else if (argument === "--help") {
      options.help = true;
    } else {
      throw new Error(`Unknown argument: ${argument}`);
    }
  }

  if (options.help) {
    return options;
  }

  if (!Number.isInteger(options.days) || options.days < 1) {
    throw new Error("--days must be a positive integer");
  }

  if (
    !options.repository ||
    !/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(options.repository)
  ) {
    throw new Error(
      "--repo must be an owner/repository name (or set GITHUB_REPOSITORY)",
    );
  }

  return options;
}

async function fetchClosedPullRequests(repository, since, token) {
  const pulls = [];

  for (let page = 1; ; page += 1) {
    const url = new URL(
      `https://api.github.com/repos/${repository}/pulls`,
    );
    url.searchParams.set("state", "closed");
    url.searchParams.set("sort", "updated");
    url.searchParams.set("direction", "desc");
    url.searchParams.set("per_page", PAGE_SIZE);
    url.searchParams.set("page", page);

    const headers = {
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "frostyard-lab-pr-metrics",
    };
    if (token) {
      headers.Authorization = `Bearer ${token}`;
    }

    const response = await fetch(url, { headers });
    if (!response.ok) {
      const detail = (await response.text()).slice(0, 500);
      throw new Error(
        `GitHub API request failed (${response.status}): ${detail}`,
      );
    }

    const pagePulls = await response.json();
    if (!Array.isArray(pagePulls)) {
      throw new Error("GitHub API returned an unexpected response");
    }

    pulls.push(
      ...pagePulls.filter(
        (pull) => pull.closed_at && new Date(pull.closed_at) >= since,
      ),
    );

    const oldestUpdate = pagePulls.at(-1)?.updated_at;
    if (
      pagePulls.length < PAGE_SIZE ||
      !oldestUpdate ||
      new Date(oldestUpdate) < since
    ) {
      return pulls;
    }
  }
}

function median(values) {
  if (values.length === 0) {
    return null;
  }

  const sorted = [...values].sort((left, right) => left - right);
  const middle = Math.floor(sorted.length / 2);
  const value =
    sorted.length % 2 === 0
      ? (sorted[middle - 1] + sorted[middle]) / 2
      : sorted[middle];
  return Number(value.toFixed(1));
}

function calculateMetrics(repository, days, since, through, pulls) {
  const mergedPulls = pulls.filter((pull) => pull.merged_at);
  const mergeDurations = mergedPulls.map(
    (pull) =>
      (new Date(pull.merged_at) - new Date(pull.created_at)) /
      (60 * 60 * 1000),
  );

  return {
    repository,
    windowDays: days,
    since: since.toISOString(),
    through: through.toISOString(),
    closedPullRequests: pulls.length,
    mergedPullRequests: mergedPulls.length,
    closedWithoutMerge: pulls.length - mergedPulls.length,
    acceptanceRatePercent:
      pulls.length === 0
        ? null
        : Number(((mergedPulls.length / pulls.length) * 100).toFixed(1)),
    medianTimeToMergeHours: median(mergeDurations),
  };
}

function renderSummary(metrics) {
  const acceptance =
    metrics.acceptanceRatePercent === null
      ? "N/A"
      : `${metrics.acceptanceRatePercent}%`;
  const medianMerge =
    metrics.medianTimeToMergeHours === null
      ? "N/A"
      : `${metrics.medianTimeToMergeHours} hours`;

  return `## PR acceptance: ${metrics.repository}

Window: ${metrics.since.slice(0, 10)} through ${metrics.through.slice(0, 10)}

| Metric | Value |
|---|---:|
| Acceptance rate | ${acceptance} |
| Merged PRs | ${metrics.mergedPullRequests} |
| Closed without merge | ${metrics.closedWithoutMerge} |
| Total closed PRs | ${metrics.closedPullRequests} |
| Median time to merge | ${medianMerge} |
`;
}

function printUsage() {
  console.log(`Usage: node scripts/pr-metrics.mjs [options]

Options:
  --repo OWNER/REPOSITORY  Repository to measure (default: GITHUB_REPOSITORY)
  --days NUMBER            Rolling window in days (default: ${DEFAULT_DAYS})
  --help                   Show this help
`);
}

async function main() {
  const options = parseArguments(process.argv.slice(2));
  if (options.help) {
    printUsage();
    return;
  }

  const through = new Date();
  const since = new Date(
    through.getTime() - options.days * 24 * 60 * 60 * 1000,
  );
  const token = process.env.GH_TOKEN || process.env.GITHUB_TOKEN;
  const pulls = await fetchClosedPullRequests(
    options.repository,
    since,
    token,
  );
  const metrics = calculateMetrics(
    options.repository,
    options.days,
    since,
    through,
    pulls,
  );

  console.log(JSON.stringify(metrics, null, 2));

  if (process.env.GITHUB_STEP_SUMMARY) {
    await appendFile(
      process.env.GITHUB_STEP_SUMMARY,
      `${renderSummary(metrics)}\n`,
      "utf8",
    );
  }
}

main().catch((error) => {
  console.error(`PR metrics failed: ${error.message}`);
  process.exitCode = 1;
});
