// @ts-check
import { defineConfig } from 'astro/config';
import mdx from '@astrojs/mdx';
import sitemap from '@astrojs/sitemap';
import { rehypeBaseUrls, rehypeCodeTabindex } from './src/lib/rehype-base-urls.mjs';

/* ─────────────────────────────────────────────────────────────────────────────
 * DEPLOY TARGET — the one-line switch between GitHub Pages and getrite.ai.
 *
 * Today (GitHub Pages project page for the `rite` repo):
 *     SITE = 'https://robbartoszewski.github.io'
 *     BASE = '/rite'
 *
 * Later (custom domain getrite.ai) — change these two lines, add a CNAME file
 * (see DEPLOY.md §3), and nothing else needs touching. Every internal link and
 * asset path goes through `url()` in src/lib/site.ts, which reads BASE.
 *     SITE = 'https://getrite.ai'
 *     BASE = '/'
 * ──────────────────────────────────────────────────────────────────────────── */
const SITE = 'https://robbartoszewski.github.io';
const BASE = '/rite';

// The Markdown rehype plugin runs outside Astro's import.meta.env, so it reads
// the base from here. Keep in step with BASE above.
process.env.ASTRO_BASE_URL = BASE;

export default defineConfig({
  site: SITE,
  base: BASE,
  trailingSlash: 'always',
  output: 'static',
  integrations: [
    mdx(),
    sitemap({
      // Drafts are filtered out of the collection at build time, but a draft
      // route never exists in production anyway, so this is belt and braces.
      filter: (page) => !page.includes('/draft'),
    }),
  ],
  build: { inlineStylesheets: 'auto' },
  markdown: {
    // defaultColor:false makes Shiki emit --shiki-light/--shiki-dark custom
    // properties instead of baking one theme's colours into an inline style
    // that no stylesheet can beat. components.css consumes them.
    shikiConfig: { themes: { light: 'github-light', dark: 'github-dark' }, defaultColor: false },
    // Root-relative links inside posts need the deploy base; .astro files get
    // it from url(), Markdown does not. See src/lib/rehype-base-urls.mjs.
    rehypePlugins: [rehypeBaseUrls, rehypeCodeTabindex],
  },
  devToolbar: { enabled: false },
});
