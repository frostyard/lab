import assert from 'node:assert/strict';
import test from 'node:test';

import { duration, laneLabel, laneState, stampOf, state, when } from '../src/dashboard.mjs';

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
