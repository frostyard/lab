import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { cpSync, mkdtempSync, readFileSync, rmSync, symlinkSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const site = fileURLToPath(new URL('..', import.meta.url));

test('empty snapshot builds a roster with zero runs but no banner or recent table', () => {
  const sandbox = mkdtempSync(join(tmpdir(), 'lab-empty-'));
  try {
    cpSync(join(site, 'src'), join(sandbox, 'src'), { recursive: true });
    cpSync(join(site, 'astro.config.mjs'), join(sandbox, 'astro.config.mjs'));
    cpSync(join(site, 'package.json'), join(sandbox, 'package.json'));
    cpSync(join(site, 'test/fixtures/empty-runs.json'), join(sandbox, 'src/data/runs.json'));
    symlinkSync(join(site, 'node_modules'), join(sandbox, 'node_modules'), 'dir');

    execFileSync(process.execPath, [join(site, 'node_modules/astro/bin/astro.mjs'), 'build'], {
      cwd: sandbox, encoding: 'utf8', stdio: 'pipe',
    });
    const html = readFileSync(join(sandbox, 'dist/index.html'), 'utf8');
    const cards = [...html.matchAll(/<article class="card unknown"[^>]*>[\s\S]*?<\/article>/g)]
      .map(([card]) => card);
    assert.equal(cards.length, 3);
    for (const product of ['snow', 'floe', 'snowfield']) {
      const card = cards.find((markup) => markup.includes(`${product} · Registry digest poll`));
      assert.ok(card, `${product} roster card missing`);
      assert.match(card, /no poll record/);
      assert.match(card, /0 runs retained/);
    }
    assert.match(html, /<span[^>]*>Lanes<\/span>\s*<strong[^>]*>3<\/strong>/);
    assert.match(html, /<span[^>]*>Runs<\/span>\s*<strong[^>]*>0<\/strong>/);
    assert.match(html, /<span[^>]*>Unknown \/ stale evidence<\/span>\s*<strong[^>]*>3 \/ 0<\/strong>/);
    assert.doesNotMatch(html, /<[^>]+class="empty"/);
    assert.doesNotMatch(html, /No run data yet/);
    assert.doesNotMatch(html, /<section[^>]+class="card table-card"/);
  } finally {
    rmSync(sandbox, { recursive: true, force: true });
  }
});
