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

  if (lanes.length === 0) {
    await expect(page.locator('.empty')).toBeVisible();
    return;
  }

  await expect(page.locator('.stat').filter({ hasText: 'Lanes' }).locator('strong')).toHaveText(
    String(lanes.length),
  );
  await expect(page.locator('.cards .card')).toHaveCount(lanes.length);
  await expect(page.locator('.card.table-card')).toBeVisible();
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
