import assert from 'node:assert/strict';
import test from 'node:test';

import * as dashboard from '../src/dashboard.mjs';

// The page and the tests import these names. A refactor keeps the set: an
// added or removed export is an interface change, not a pure refactor.
test('dashboard module exports exactly the known names', () => {
  assert.deepEqual(Object.keys(dashboard).sort(), [
    'PRODUCTS',
    'SNAPSHOT_FUTURE_TOLERANCE_MS',
    'SNAPSHOT_STALE_MS',
    'displayLanes',
    'duration',
    'laneLabel',
    'laneState',
    'legacyProductLanes',
    'productEvidenceCounts',
    'productLanes',
    'productOf',
    'snapshotAgeText',
    'snapshotStale',
    'staleEvidence',
    'stampOf',
    'state',
    'when',
  ]);
});
