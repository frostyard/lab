import { expect, test } from '@playwright/test';

import data from '../site/src/data/runs.json' with { type: 'json' };

// The dashboard is a read-only view of site/src/data/runs.json, published by
// the in-cluster collector. These specs assert the contract between that data
// file and the rendered page, so a regression in either shows up here.

test('renders the dashboard shell', async ({ page }) => {
  await page.goto('/lab/');

  await expect(page).toHaveTitle('frostyard lab');
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Pipeline status');
  await expect(page.getByRole('link', { name: 'Source ↗' })).toHaveAttribute(
    'href',
    'https://github.com/frostyard/lab',
  );
});

test('summarises the lanes from runs.json', async ({ page }) => {
  await page.goto('/lab/');

  const lanes = data.lanes ?? [];

  if ((data.runs ?? []).length === 0) {
    await expect(page.locator('.empty')).toHaveCount(0);
    await expect(page.locator('.table-card')).toHaveCount(0);
  }

  const expectedLanes = lanes.filter((lane) => lane.template !== 'image-poller').length + 3;
  await expect(page.locator('.stat').filter({ hasText: 'Lanes' }).locator('strong')).toHaveText(
    String(expectedLanes),
  );
  await expect(page.locator('.cards .card')).toHaveCount(expectedLanes);
  const cards = page.locator('.cards .card');
  for (const product of ['snow', 'floe', 'snowfield']) {
    const card = cards.filter({ has: page.locator('.card-head .name', { hasText: `${product} · Registry digest poll` }) });
    await expect(card).toHaveCount(1);
    if ((data.runs ?? []).length === 0) {
      await expect(card.locator('.card-head .badge')).toHaveText('no poll record');
      await expect(card).toContainText('0 runs retained');
    }
  }
  if ((data.runs ?? []).length > 0) {
    await expect(page.locator('.card.table-card')).toBeVisible();
  } else {
    await expect(page.locator('.stat').filter({ hasText: 'Runs' }).locator('strong')).toHaveText('0');
    await expect(page.locator('.stat').filter({ hasText: 'Unknown / stale evidence' }).locator('strong')).toHaveText('3 / 0');
  }
});

test('rendered product runs distinguish workflow phase from verified QA', async ({ page }) => {
  const productRows = (data.runs ?? []).slice(0, 40)
    .map((run, index) => ({ run, index }))
    .filter(({ run }) => /^image-poll-(snow|floe|snowfield)-latest-/.test(run.name));
  test.skip(productRows.length === 0, 'no product runs among the 40 rendered rows; roster checked separately');
  await page.goto('/lab/');
  const rows = page.locator('.table-card tbody tr');
  for (const { run, index } of productRows) {
    const cells = rows.nth(index).locator('td');
    await expect(cells.first()).toHaveText(run.name);
    await expect(cells.nth(2).locator('.badge')).toHaveText(run.phase);
    // A Succeeded poll with a legacy summary is not a verified QA pass.
    const expectedQA = run.qaOutcome ?? 'legacy unknown';
    await expect(cells.nth(3).locator('.badge')).toHaveText(expectedQA);
    if (!run.qaOutcome) {
      await expect(cells.nth(3).locator('.badge')).not.toHaveClass(/\bok\b/);
    }
  }
});

test('lists recent runs, newest first', async ({ page }) => {
  const runs = data.runs ?? [];
  test.skip(runs.length === 0, 'no run data collected yet');

  await page.goto('/lab/');

  const rows = page.locator('.table-card tbody tr');
  await expect(rows).toHaveCount(Math.min(runs.length, 40));
  await expect(rows.first().locator('td').first()).toHaveText(runs[0].name);
});

test('serves assets under the GitHub Pages base path', async ({ page }) => {
  await page.goto('/lab/');

  await expect(page.locator('link[rel="icon"]')).toHaveAttribute('href', '/lab/favicon.svg');

  const favicon = await page.request.get('/lab/favicon.svg');
  expect(favicon.ok()).toBeTruthy();
});
