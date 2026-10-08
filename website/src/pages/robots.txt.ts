import type { APIContext } from 'astro';
import { url } from '../lib/site';

/** Generated, not a static public/ file, so the Sitemap line carries the
 *  deploy base and origin. A hard-coded URL here was the one thing that made
 *  the documented "two-line switch" to getrite.ai untrue.
 *
 *  ⚠ On a GitHub Pages PROJECT page this file is served at /rite/robots.txt,
 *  and crawlers only read robots.txt at the ORIGIN root — so it is inert until
 *  the custom domain is live. See DEPLOY.md §2. */
export function GET(context: APIContext) {
  const sitemap = new URL(url('/sitemap-index.xml'), context.site).href;
  return new Response(`User-agent: *\nAllow: /\n\nSitemap: ${sitemap}\n`, {
    headers: { 'Content-Type': 'text/plain; charset=utf-8' },
  });
}
