# The make gate (frostyard core ADR-0043, ADR-0044, ADR-0071 row B4).
# Operator recipes stay in the Justfile (`just --list`); they talk to the
# cluster. These targets need no cluster, no credentials and no root.
#
#   make verify          the baseline gate: verify-template, verify-python and
#                        verify-site. It does not change the tracked files.
#   make verify-template the template gate (agent symlinks, docs index,
#                        risk-path list, corrections, vendored core copies)
#                        and its Node tests.
#   make verify-python   the agent governance policy check and the pytest
#                        suite (with its coverage gate, pytest.ini). Install
#                        requirements-test.txt first (CONTRIBUTING.md).
#   make verify-site     the dashboard unit tests (npm ci, then npm test, in
#                        site/).
#   make check           the developer gate; the same as verify.
#   make ci              verify, plus the append-only corrections check
#                        against BASE when BASE is set (CI passes the pull
#                        request base commit).
#
# The Playwright suite (`just site-e2e`, e2e.yml) needs a browser and is not
# part of this gate.
NODE_MAJOR := $(shell node -p "process.versions.node.split('.')[0]" 2>/dev/null)
PYTHON ?= python3

.PHONY: verify verify-template verify-python verify-site check ci

verify: verify-template verify-python verify-site

# Node.js exits 0 when its test glob matches nothing (ADR-0010), so each
# Node test recipe first requires at least 1 test file.
verify-template:
	@if ! ls test/*.test.mjs >/dev/null 2>&1; then echo "FAIL: no files match test/*.test.mjs; node --test would run zero tests"; exit 1; fi
	@if ! command -v node >/dev/null 2>&1; then echo "FAIL: make verify-template requires Node.js 22 or later (mise install)"; exit 1; fi
	@if [ "$(NODE_MAJOR)" = "" ] || [ "$(NODE_MAJOR)" -lt 22 ]; then echo "FAIL: make verify-template requires Node.js 22 or later (found: $$(node --version 2>/dev/null || echo missing))"; exit 1; fi
	node scripts/check-template.mjs
	node --test test/*.test.mjs

verify-python:
	$(PYTHON) policies/check_agent_governance.py
	$(PYTHON) -m pytest -q

verify-site:
	@if ! ls site/test/*.test.mjs >/dev/null 2>&1; then echo "FAIL: no files match site/test/*.test.mjs; npm test would run zero tests"; exit 1; fi
	cd site && npm ci && npm test

check: verify

ci: verify
	@if [ -n "$(BASE)" ]; then node scripts/check-corrections.mjs --base "$(BASE)"; fi
