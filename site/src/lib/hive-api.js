export class HiveAPIError extends Error {
  constructor(message, code) {
    super(message);
    this.name = 'HiveAPIError';
    this.code = code;
  }
}

function object(value, label) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new HiveAPIError(`${label} is not an object`, 'invalid_response');
  }
  return value;
}

function string(value, label, nullable = false) {
  if (nullable && value === null) return null;
  if (typeof value !== 'string') {
    throw new HiveAPIError(`${label} is not a string`, 'invalid_response');
  }
  return value;
}

function number(value, label, nullable = false) {
  if (nullable && value === null) return null;
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) {
    throw new HiveAPIError(`${label} is not a non-negative number`, 'invalid_response');
  }
  return value;
}

function boolean(value, label) {
  if (typeof value !== 'boolean') {
    throw new HiveAPIError(`${label} is not a boolean`, 'invalid_response');
  }
  return value;
}

function array(value, label) {
  if (!Array.isArray(value)) {
    throw new HiveAPIError(`${label} is not an array`, 'invalid_response');
  }
  return value;
}

export function validateOverview(value) {
  const root = object(value, 'overview');
  if (root.schemaVersion !== 1) {
    throw new HiveAPIError('unsupported Hive API schema', 'unsupported_schema');
  }

  string(root.fetchedAt, 'fetchedAt');
  string(root.lastAttemptAt, 'lastAttemptAt');
  string(root.sourceTimestamp, 'sourceTimestamp', true);
  boolean(root.stale, 'stale');
  array(root.unavailableSections, 'unavailableSections').forEach((item, index) =>
    string(item, `unavailableSections[${index}]`),
  );

  const health = object(root.health, 'health');
  string(health.status, 'health.status');
  const checks = object(health.checks, 'health.checks');
  Object.entries(checks).forEach(([key, state]) =>
    string(state, `health.checks.${key}`),
  );

  const governor = object(root.governor, 'governor');
  string(governor.mode, 'governor.mode');
  number(governor.issues, 'governor.issues');
  number(governor.pullRequests, 'governor.pullRequests');

  array(root.agents, 'agents').forEach((item, index) => {
    const agent = object(item, `agents[${index}]`);
    string(agent.name, `agents[${index}].name`);
    string(agent.displayName, `agents[${index}].displayName`);
    string(agent.state, `agents[${index}].state`);
    string(agent.activity, `agents[${index}].activity`);
    boolean(agent.paused, `agents[${index}].paused`);
    boolean(agent.offByCadence, `agents[${index}].offByCadence`);
  });

  array(root.repositories, 'repositories').forEach((item, index) => {
    const repository = object(item, `repositories[${index}]`);
    string(repository.name, `repositories[${index}].name`);
    number(repository.issues, `repositories[${index}].issues`);
    number(repository.pullRequests, `repositories[${index}].pullRequests`);
  });

  const usage = object(root.usage, 'usage');
  number(usage.lookbackHours, 'usage.lookbackHours');
  number(usage.sessions, 'usage.sessions');
  number(usage.inputTokens, 'usage.inputTokens');
  number(usage.outputTokens, 'usage.outputTokens');
  number(usage.cacheReadTokens, 'usage.cacheReadTokens');
  number(usage.cacheCreateTokens, 'usage.cacheCreateTokens');
  number(usage.estimatedCostUsd, 'usage.estimatedCostUsd', true);
  string(usage.disclaimer, 'usage.disclaimer');

  return root;
}

export async function fetchOverview(
  endpoint,
  { timeoutMs = 5000, fetchImpl = globalThis.fetch } = {},
) {
  if (!endpoint) {
    throw new HiveAPIError('Hive API URL is not configured', 'not_configured');
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetchImpl(endpoint, {
      method: 'GET',
      headers: { Accept: 'application/json' },
      signal: controller.signal,
    });
    if (!response.ok) {
      throw new HiveAPIError(
        `Hive API returned HTTP ${response.status}`,
        'http_error',
      );
    }
    return validateOverview(await response.json());
  } catch (error) {
    if (error instanceof HiveAPIError) throw error;
    if (error?.name === 'AbortError') {
      throw new HiveAPIError('Hive API request timed out', 'timeout');
    }
    throw new HiveAPIError('Hive API request failed', 'network_error');
  } finally {
    clearTimeout(timer);
  }
}
