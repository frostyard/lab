import { defineConfig } from 'astro/config';

// GitHub Pages serves a project site from /<repo>, so the base must match or
// every asset URL 404s once deployed.
export default defineConfig({
  site: 'https://frostyard.github.io',
  base: '/lab',
  output: 'static',
  build: { format: 'directory' },
});
