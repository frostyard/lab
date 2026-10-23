// Argo phases collapse to three states worth colouring. Anything still moving
// is "running" rather than a fourth colour nobody can interpret at a glance.
// The names match the design system's --state-ok/warn/danger tokens.
export function state(phase) {
  if (phase === 'Succeeded') return 'ok';
  if (phase === 'Failed' || phase === 'Error') return 'danger';
  return 'warn';
}

export const PRODUCTS = ['snow', 'floe', 'snowfield', 'sundog'];
const productKey = (name) => PRODUCTS.map((p) => `image-poll-${p}-latest`)
  .find((key) => name === key || name?.startsWith(`${key}-`));

export function productOf(run) {
  return productKey(run?.laneKey) || productKey(run?.name);
}

export function legacyProductLanes(runs) {
  const byProduct = new Map();
  for (const run of runs) {
    const key = productOf(run);
    if (!key) continue;
    if (byProduct.has(key)) {
      byProduct.get(key).runs++;
    } else {
      byProduct.set(key, {
        laneKey: key, label: 'Registry digest poll', kind: 'poll', template: 'image-poller',
        latest: run, runs: 1, everGreen: false,
      });
    }
  }
  return [...byProduct.values()];
}

// Supply roster entries even when no workflow has been retained. Old JSON may
// have a single image-poller lane: do not reuse its mixed-product latest run.
export function productLanes(lanes, generated) {
  const existing = lanes.filter((lane) => lane.template !== 'image-poller' || productKey(lane.laneKey));
  return [...existing.filter((lane) => !productKey(lane.laneKey) && !productOf(lane.latest)),
    ...PRODUCTS.map((product) => {
      const key = `image-poll-${product}-latest`;
      return existing.find((lane) => lane.laneKey === key || productOf(lane.latest) === key) || {
        laneKey: key, label: 'Registry digest poll', kind: 'poll', template: 'image-poller',
        latest: null, runs: 0, everGreen: false,
      };
    })];
}

// Use the same snapshot composition for the page and the legacy contract test.
export function displayLanes({ lanes = [], runs = [], generated }) {
  return productLanes([...lanes, ...legacyProductLanes(runs).filter((legacy) =>
    !lanes.some((lane) => lane.laneKey === legacy.laneKey))], generated);
}

function evidenceAge(lane, generated) {
  const at = Date.parse(lane.latest?.finished || lane.latest?.started);
  const now = Date.parse(generated);
  return Number.isFinite(at) && Number.isFinite(now) && at <= now ? now - at : null;
}

export function staleEvidence(lane, generated) {
  const age = evidenceAge(lane, generated);
  return age !== null && age > 6 * 60 * 60 * 1000;
}

// The page shows a snapshot by the viewer's clock, not only the collector's:
// publication can stop while the lab still looks healthy in the stored data.
export const SNAPSHOT_STALE_MS = 2 * 60 * 60 * 1000;
export const SNAPSHOT_FUTURE_TOLERANCE_MS = 5 * 60 * 1000;

function snapshotAge(generated, nowMs) {
  const then = Date.parse(generated);
  if (!Number.isFinite(then) || !Number.isFinite(nowMs)) return null;
  return nowMs - then;
}

export function snapshotStale(generated, nowMs = Date.now()) {
  const age = snapshotAge(generated, nowMs);
  if (age === null) return true;
  if (age < -SNAPSHOT_FUTURE_TOLERANCE_MS) return true;
  return age > SNAPSHOT_STALE_MS;
}

export function snapshotAgeText(generated, nowMs = Date.now()) {
  const age = snapshotAge(generated, nowMs);
  if (age === null) return 'unknown';
  const minutes = Math.max(0, Math.floor(age / 60000));
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  if (hours > 0) return rest > 0 ? `${hours} h ${rest} min` : `${hours} h`;
  if (minutes > 0) return `${minutes} min`;
  return 'less than 1 min';
}

// A non-product lane that has never succeeded is unproven rather than a
// product finding. Product failures with actual QA evidence retain their red.
export function laneState(lane, generated) {
  if (productKey(lane.laneKey) || productOf(lane.latest)) {
    if (!lane.latest) return 'unknown';
    if (evidenceAge(lane, generated) === null) return 'unknown';
    if (lane.latest.qaOutcome === 'failed') return 'danger';
    if (staleEvidence(lane, generated)) return 'stale';
    // An unchanged-digest poll carries the last pass for that digest.
    if (lane.latest.qaOutcome === 'passed' || lane.latest.qaOutcome === 'carried') return 'ok';
    return 'unknown';
  }
  if (lane.everGreen === false && state(lane.latest.phase) === 'danger') return 'unproven';
  return state(lane.latest.phase);
}

export function laneLabel(lane, generated) {
  const status = laneState(lane, generated);
  if (productKey(lane.laneKey) || productOf(lane.latest)) {
    if (status === 'ok') {
      return lane.latest.qaOutcome === 'carried' ? 'QA passed (digest unchanged)' : 'QA passed';
    }
    if (status === 'danger') return staleEvidence(lane, generated) ? 'QA failed (stale evidence)' : 'QA failed';
    if (!lane.latest) return 'no poll record';
    // The stale state keeps old passes from looking green, but age must not
    // erase whether this poll ever provided verifiable QA evidence.
    if (status === 'stale' && (lane.latest.qaOutcome === 'passed' || lane.latest.qaOutcome === 'carried')) return 'stale';
    const qa = !lane.latest.qaOutcome ? 'QA unknown (legacy)'
      : lane.latest.qaOutcome === 'not-run' ? 'QA not run' : 'QA unknown';
    if (status !== 'stale') return qa;
    return qa.endsWith(')') ? `${qa.slice(0, -1)}; stale evidence)` : `${qa} (stale evidence)`;
  }
  return status === 'unproven' ? 'unproven' : lane.latest.phase;
}

// Stale is an overlapping age qualifier, not a mutually exclusive QA outcome:
// a confirmed old failure remains failed while an old pass cannot count as green.
export function productEvidenceCounts(lanes, generated) {
  const counts = { passed: 0, failed: 0, unknown: 0, stale: 0 };
  for (const lane of lanes) {
    if (!productKey(lane.laneKey) && !productOf(lane.latest)) continue;
    const status = laneState(lane, generated);
    if (status === 'ok') counts.passed++;
    if (status === 'danger') counts.failed++;
    if (status === 'unknown' || (status === 'stale' && lane.latest.qaOutcome !== 'passed' && lane.latest.qaOutcome !== 'carried')) counts.unknown++;
    if (staleEvidence(lane, generated)) counts.stale++;
  }
  return counts;
}

export function duration(seconds) {
  if (seconds == null) return '—';
  if (seconds < 90) return `${seconds}s`;
  const m = Math.floor(seconds / 60);
  return m < 90 ? `${m}m` : `${Math.floor(m / 60)}h ${m % 60}m`;
}

export function when(ts) {
  if (!ts) return '—';
  return ts.replace('T', ' ').replace('Z', ' UTC');
}

// The topbar pill is a mono kicker, so it drops seconds to stay one short line.
export function stampOf(ts) {
  if (!ts) return null;
  return ts.replace('T', ' ').replace(/:\d\dZ$/, ' UTC');
}
