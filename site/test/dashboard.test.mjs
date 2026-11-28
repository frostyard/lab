import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import test from 'node:test';

import * as dashboard from '../src/dashboard.mjs';
const { duration, laneLabel, laneState, legacyProductLanes, productLanes, stampOf, state, when } = dashboard;

const generated = '2026-09-24T23:30:17Z';
const products4 = ['snow', 'floe', 'snowfield', 'sundog'];
const roster = ['latest', 'forky'].flatMap((stream) => products4.map((product) => `image-poll-${product}-${stream}`));

test('dashboard product roster matches image-poll CronWorkflow manifest names', () => {
  const manifestDir = new URL('../../manifests/', import.meta.url);
  const names = readdirSync(manifestDir)
    .filter((file) => /\.ya?ml$/.test(file))
    .flatMap((file) => {
      const yaml = readFileSync(new URL(file, manifestDir), 'utf8');
      return yaml.split(/^---\s*$/m).flatMap((doc) => {
        if (!/^kind: CronWorkflow\s*$/m.test(doc)) return [];
        const metadata = doc.match(/^metadata:\s*\n((?:^[ \t]+.*\n?)*)/m)?.[1];
        const name = metadata?.match(/^  name: (image-poll-[a-z0-9-]+)\s*$/m)?.[1];
        return name ? [name] : [];
      });
    });
  assert.ok(names.length > 0, 'no image-poll CronWorkflow manifests found');
  const actual = [...new Set(names)].sort();
  const parity = (products, streams = dashboard.STREAMS) => {
    const dashboardNames = products.flatMap((p) => streams.map((s) => `image-poll-${p}-${s}`)).sort();
    assert.deepEqual(actual, dashboardNames);
  };
  parity(dashboard.PRODUCTS);
  // Mutation check: a wrong roster must fail the same scanned-manifest comparison.
  assert.throws(() => parity([...dashboard.PRODUCTS, 'fourth']), assert.AssertionError);
  assert.throws(() => parity(dashboard.PRODUCTS, ['latest']), assert.AssertionError);
});
const poll = (product, phase, qaOutcome, started = '2026-09-24T21:20:00Z') => ({
  laneKey: `image-poll-${product}-latest`, kind: 'poll', label: 'Registry digest poll',
  everGreen: qaOutcome === 'passed', runs: 1,
  latest: { name: `image-poll-${product}-latest-123`, phase, qaOutcome, started, finished: started },
});

test('fresh product QA success and failure stay separate, even without a prior pass', () => {
  const lanes = productLanes([
    poll('snow', 'Failed', 'failed', '2026-09-24T21:00:00Z'),
    poll('floe', 'Succeeded', 'passed'),
  ]);
  assert.deepEqual(lanes.filter((lane) => roster.includes(lane.laneKey)).map((lane) => lane.laneKey), roster);
  assert.equal(laneState(lanes[0], generated), 'danger');
  assert.equal(laneLabel(lanes[0], generated), 'QA failed');
  assert.equal(laneState(lanes[1], generated), 'ok');
  assert.equal(laneLabel(lanes[1], generated), 'QA passed');
});

test('successful unchanged digest poll is unknown image QA', () => {
  const lane = poll('floe', 'Succeeded', 'not-run');
  lane.latest.result = null;
  assert.equal(laneState(lane, generated), 'unknown');
  assert.equal(laneLabel(lane, generated), 'QA not run');
});

test('missing roster entry is unknown, with no invented run', () => {
  const lanes = productLanes([]);
  assert.deepEqual(lanes.map((lane) => lane.laneKey), roster);
  assert.equal(lanes[2].runs, 0);
  assert.equal(lanes[2].latest, null);
  assert.equal(laneState(lanes[2], generated), 'unknown');
});

test('empty published snapshot shows eight unknown product lanes with no retained evidence', () => {
  const lanes = dashboard.displayLanes({ generated, lanes: [], runs: [] });
  assert.deepEqual(lanes.map((lane) => lane.laneKey), roster);
  assert.deepEqual(lanes.map((lane) => lane.runs), Array(8).fill(0));
  assert.deepEqual(lanes.map((lane) => lane.latest), Array(8).fill(null));
  assert.deepEqual(lanes.map((lane) => laneState(lane, generated)), Array(8).fill('unknown'));
  assert.deepEqual(lanes.map((lane) => laneLabel(lane, generated)), Array(8).fill('no poll record'));
  assert.deepEqual(dashboard.productEvidenceCounts(lanes, generated), {
    passed: 0, failed: 0, unknown: 8, stale: 0,
  });
});

test('16-day-old Running record is stale, not live', () => {
  const lane = poll('snowfield', 'Running', 'unknown', '2026-09-08T18:40:00Z');
  lane.latest.finished = null;
  assert.equal(laneState(lane, generated), 'stale');
  assert.equal(laneLabel(lane, generated), 'QA unknown (stale evidence)');
  assert.deepEqual(dashboard.productEvidenceCounts([lane], generated), {
    passed: 0, failed: 0, unknown: 1, stale: 1,
  });
});

test('old legacy, null-result and not-run polls retain unverified QA meaning alongside stale age', () => {
  const legacy = poll('snow', 'Succeeded', undefined, '2026-09-08T21:00:00Z');
  legacy.latest.result = '14 passed';
  const nullResult = poll('floe', 'Succeeded', undefined, '2026-09-08T21:00:00Z');
  nullResult.latest.result = null;
  const notRun = poll('snowfield', 'Succeeded', 'not-run', '2026-09-08T21:00:00Z');
  for (const lane of [legacy, nullResult, notRun]) {
    assert.equal(laneState(lane, generated), 'stale');
  }
  assert.equal(laneLabel(legacy, generated), 'QA unknown (legacy; stale evidence)');
  assert.equal(laneLabel(nullResult, generated), 'QA unknown (legacy; stale evidence)');
  assert.equal(laneLabel(notRun, generated), 'QA not run (stale evidence)');
  assert.deepEqual(dashboard.productEvidenceCounts([legacy, nullResult, notRun], generated), {
    passed: 0, failed: 0, unknown: 3, stale: 3,
  });
});

test('old confirmed failed QA stays red, explicitly qualified as stale evidence', () => {
  const lane = poll('snow', 'Failed', 'failed', '2026-09-08T21:00:00Z');
  assert.equal(laneState(lane, generated), 'danger');
  assert.equal(laneLabel(lane, generated), 'QA failed (stale evidence)');
  assert.equal(dashboard.staleEvidence(lane, generated), true);
  assert.equal(dashboard.staleEvidence(poll('floe', 'Succeeded', 'passed'), generated), false);
});

test('product evidence counts include old failure as failed and stale, not current success', () => {
  const lanes = productLanes([
    poll('snow', 'Failed', 'failed', '2026-09-08T21:00:00Z'),
    poll('floe', 'Succeeded', 'passed', '2026-09-08T21:20:00Z'),
  ]);
  assert.deepEqual(dashboard.productEvidenceCounts(lanes, generated), {
    passed: 0, failed: 1, unknown: 6, stale: 2,
  });
  assert.equal(laneState(lanes[1], generated), 'stale');
  assert.equal(laneLabel(lanes[1], generated), 'stale');
});

test('freshness boundary is six hours (two poll intervals); invalid times are unknown', () => {
  assert.equal(laneState(poll('snow', 'Succeeded', 'passed', '2026-09-24T17:30:17Z'), generated), 'ok');
  assert.equal(laneState(poll('snow', 'Succeeded', 'passed', '2026-09-24T17:30:16Z'), generated), 'stale');
  assert.equal(laneState(poll('snow', 'Succeeded', 'passed', 'not-a-date'), generated), 'unknown');
  assert.equal(laneState(poll('snow', 'Succeeded', 'passed', null), generated), 'unknown');
  assert.equal(laneState(poll('snow', 'Succeeded', 'passed'), null), 'unknown');
  assert.equal(laneState(poll('snow', 'Succeeded', 'passed', '2026-09-25T00:00:00Z'), generated), 'unknown');
});

test('legacy poll records never become verified QA even with succeeded phase or result counts', () => {
  const lane = poll('floe', 'Succeeded', undefined);
  lane.latest.result = '14 passed, 6 skipped';
  assert.equal(laneState(lane, generated), 'unknown');
  assert.equal(laneLabel(lane, generated), 'QA unknown (legacy)');
});

test('legacy mixed image-poller history exposes each product without claiming verified QA', () => {
  const runs = [
    { name: 'image-poll-floe-latest-3', phase: 'Succeeded', result: '14 passed, 6 skipped', started: '2026-09-24T21:20:00Z', template: 'image-poller' },
    { name: 'image-poll-snow-latest-2', phase: 'Failed', result: '19 passed, 1 failed', started: '2026-09-24T21:00:00Z', template: 'image-poller' },
    { name: 'image-poll-snowfield-latest-1', phase: 'Running', started: '2026-09-08T18:40:00Z', template: 'image-poller' },
  ];
  const lanes = productLanes(legacyProductLanes(runs));
  assert.deepEqual(lanes.map((lane) => lane.latest?.name), [runs[1].name, runs[0].name, runs[2].name, ...Array(5).fill(undefined)]);
  assert.deepEqual(lanes.map((lane) => laneState(lane, generated)), ['unknown', 'unknown', 'stale', ...Array(5).fill('unknown')]);
});

test('legacy-shaped snapshot separates product polls without promoting summary text to QA evidence', () => {
  const data = JSON.parse(readFileSync(new URL('./fixtures/legacy-runs.json', import.meta.url), 'utf8'));
  const displayLanes = dashboard.displayLanes(data);
  const products = displayLanes.filter((lane) => roster.includes(lane.laneKey));
  assert.deepEqual(products.map((lane) => lane.laneKey), roster);
  assert.deepEqual(products.map((lane) => lane.latest?.phase), ['Failed', 'Succeeded', 'Running', ...Array(5).fill(undefined)]);
  assert.deepEqual(products.map((lane) => lane.runs), [1, 1, 1, 0, 0, 0, 0, 0]);
  assert.deepEqual(products.map((lane) => laneState(lane, data.generated)), ['unknown', 'unknown', 'stale', ...Array(5).fill('unknown')]);
  assert.deepEqual(products.map((lane) => laneLabel(lane, data.generated)),
    ['QA unknown (legacy)', 'QA unknown (legacy)', 'QA unknown (legacy; stale evidence)', ...Array(5).fill('no poll record')]);
  assert.deepEqual(dashboard.productEvidenceCounts(displayLanes, data.generated), {
    passed: 0, failed: 0, unknown: 8, stale: 1,
  });
});

test('non-product workflow successes and failures remain phase statuses, not QA', () => {
  assert.equal(laneState({ kind: 'maintenance', latest: { phase: 'Succeeded' } }, generated), 'ok');
  assert.equal(laneLabel({ kind: 'maintenance', latest: { phase: 'Succeeded' } }, generated), 'Succeeded');
});

test('state maps Argo phases to dashboard states', () => {
  assert.equal(state('Succeeded'), 'ok');
  assert.equal(state('Failed'), 'danger');
  assert.equal(state('Error'), 'danger');
  assert.equal(state('Running'), 'warn');
  assert.equal(state('Pending'), 'warn');
});

test('never-green failures are shown as unproven', () => {
  const lane = { everGreen: false, latest: { phase: 'Failed' } };

  assert.equal(laneState(lane), 'unproven');
  assert.equal(laneLabel(lane), 'unproven');
});

test('proven lanes retain their latest phase and state', () => {
  const lane = { everGreen: true, latest: { phase: 'Failed' } };

  assert.equal(laneState(lane), 'danger');
  assert.equal(laneLabel(lane), 'Failed');
});

test('carried poll is a fresh pass labelled by its digest', () => {
  const lane = poll('sundog', 'Succeeded', 'carried');
  lane.latest.digest = `sha256:${'a'.repeat(64)}`;
  assert.equal(laneState(lane, generated), 'ok');
  assert.equal(laneLabel(lane, generated), 'QA passed (digest unchanged)');
  assert.deepEqual(dashboard.productEvidenceCounts([lane], generated), {
    passed: 1, failed: 0, unknown: 0, stale: 0,
  });
});

test('stale carried poll is stale, not green', () => {
  const lane = poll('snowfield', 'Succeeded', 'carried', '2026-09-08T21:00:00Z');
  lane.latest.digest = `sha256:${'a'.repeat(64)}`;
  assert.equal(laneState(lane, generated), 'stale');
  assert.equal(laneLabel(lane, generated), 'stale');
  assert.deepEqual(dashboard.productEvidenceCounts([lane], generated), {
    passed: 0, failed: 0, unknown: 0, stale: 1,
  });
});

test('snapshot freshness uses the viewer clock with a two-hour limit', () => {
  const now = Date.parse('2026-09-24T23:30:00Z');
  assert.equal(dashboard.SNAPSHOT_STALE_MS, 2 * 60 * 60 * 1000);
  assert.equal(dashboard.snapshotStale('2026-09-24T22:30:00Z', now), false);
  assert.equal(dashboard.snapshotStale('2026-09-24T21:30:00Z', now), false);
  assert.equal(dashboard.snapshotStale('2026-09-24T21:29:59Z', now), true);
  assert.equal(dashboard.snapshotStale(undefined, now), true);
  assert.equal(dashboard.snapshotStale(null, now), true);
  assert.equal(dashboard.snapshotStale('not-a-date', now), true);
  assert.equal(dashboard.snapshotStale('2026-09-24T23:34:00Z', now), false);
  assert.equal(dashboard.snapshotStale('2026-09-24T23:36:00Z', now), true);
});

test('snapshot age text is short and readable', () => {
  const now = Date.parse('2026-09-24T23:30:00Z');
  assert.equal(dashboard.snapshotAgeText('2026-09-24T20:20:00Z', now), '3 h 10 min');
  assert.equal(dashboard.snapshotAgeText('2026-09-24T21:30:00Z', now), '2 h');
  assert.equal(dashboard.snapshotAgeText('2026-09-24T23:05:00Z', now), '25 min');
  assert.equal(dashboard.snapshotAgeText('not-a-date', now), 'unknown');
});

test('duration formats seconds, minutes, hours, and missing values', () => {
  assert.equal(duration(null), '—');
  assert.equal(duration(89), '89s');
  assert.equal(duration(90), '1m');
  assert.equal(duration(5_400), '1h 30m');
});

test('timestamps use readable UTC labels', () => {
  assert.equal(when(null), '—');
  assert.equal(when('2026-08-10T18:16:11Z'), '2026-08-10 18:16:11 UTC');
  assert.equal(stampOf(null), null);
  assert.equal(stampOf('2026-08-10T18:16:11Z'), '2026-08-10 18:16 UTC');
});

test('product display name keeps latest lanes bare and marks other streams', () => {
  assert.equal(dashboard.productDisplayName('image-poll-snow-latest'), 'snow');
  assert.equal(dashboard.productDisplayName('image-poll-snowfield-forky'), 'snowfield:forky');
  assert.equal(dashboard.productDisplayName(undefined), '');
});

test('forky and latest runs of one product stay on separate lanes', () => {
  const forky = { name: 'image-poll-snow-forky-5', phase: 'Succeeded', started: '2026-09-24T21:00:00Z', template: 'image-poller' };
  assert.equal(dashboard.productOf(forky), 'image-poll-snow-forky');
  assert.equal(dashboard.productOf({ name: 'image-poll-snow-forkyish-1' }), undefined);
  const lanes = productLanes(legacyProductLanes([forky]));
  assert.equal(lanes[4].latest.name, forky.name);
  assert.equal(lanes[0].latest, null);
});
