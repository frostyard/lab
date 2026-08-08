import test from 'node:test';
import assert from 'node:assert/strict';

import {
  HiveAPIError,
  fetchOverview,
  validateOverview,
} from '../src/lib/hive-api.js';

function overview() {
  return {
    schemaVersion: 1,
    fetchedAt: '2026-08-07T23:15:00Z',
    lastAttemptAt: '2026-08-07T23:15:00Z',
    sourceTimestamp: '2026-08-07T23:14:52Z',
    stale: false,
    unavailableSections: [],
    health: { status: 'ok', checks: { ready: 'pass' } },
    governor: { mode: 'idle', issues: 0, pullRequests: 0 },
    agents: [
      {
        name: 'scanner',
        displayName: 'Scanner',
        state: 'running',
        activity: 'idle',
        paused: false,
        offByCadence: false,
      },
    ],
    repositories: [{ name: 'snosi', issues: 0, pullRequests: 0 }],
    usage: {
      lookbackHours: 24,
      sessions: 1,
      inputTokens: 10,
      outputTokens: 2,
      cacheReadTokens: 5,
      cacheCreateTokens: 0,
      estimatedCostUsd: 0.01,
      disclaimer: 'Estimate only.',
    },
  };
}

test('validateOverview accepts the v1 contract', () => {
  assert.equal(validateOverview(overview()).schemaVersion, 1);
});

test('validateOverview rejects unsafe shape changes', () => {
  const invalid = overview();
  invalid.agents[0].paused = 'false';
  assert.throws(() => validateOverview(invalid), HiveAPIError);
});

test('fetchOverview validates successful JSON responses', async () => {
  const result = await fetchOverview('https://example.test/v1/hive/overview', {
    fetchImpl: async () => ({ ok: true, json: async () => overview() }),
  });
  assert.equal(result.agents[0].name, 'scanner');
});

test('fetchOverview rejects non-success responses', async () => {
  await assert.rejects(
    fetchOverview('https://example.test/v1/hive/overview', {
      fetchImpl: async () => ({ ok: false, status: 503 }),
    }),
    (error) =>
      error instanceof HiveAPIError && error.code === 'http_error',
  );
});
