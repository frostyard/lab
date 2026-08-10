// Argo phases collapse to three states worth colouring. Anything still moving
// is "running" rather than a fourth colour nobody can interpret at a glance.
// The names match the design system's --state-ok/warn/danger tokens.
export function state(phase) {
  if (phase === 'Succeeded') return 'ok';
  if (phase === 'Failed' || phase === 'Error') return 'danger';
  return 'warn';
}

// A lane that has never once succeeded is a different claim from a lane that
// went red. It is not evidence about the thing under test — it is evidence that
// nobody has shown the lane can pass. Two false bug reports against snosi came
// from reading a never-green lane's red as a finding, so the distinction earns
// its own state rather than living in a comment. See docs/roadmap.md.
export function laneState(lane) {
  if (lane.everGreen === false && state(lane.latest.phase) === 'danger') return 'unproven';
  return state(lane.latest.phase);
}

export function laneLabel(lane) {
  return laneState(lane) === 'unproven' ? 'unproven' : lane.latest.phase;
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
