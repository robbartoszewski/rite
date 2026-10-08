import rss from '@astrojs/rss';
import { getCollection } from 'astro:content';
import { url } from '../../lib/site';

/** RSS of published posts: title, dek and link (not the full article body).
 *
 *  ⚠ `context.site` is the ORIGIN with no base path, so every link here has to
 *  go through `url()` — otherwise item links point at
 *  `<origin>/blog/<slug>` instead of `<origin>/rite/blog/<slug>` and every one
 *  of them 404s on the project page. */
export async function GET(context) {
  const origin = context.site;
  const posts = (await getCollection('blog', (p) => !p.data.draft)).sort(
    (a, b) => (b.data.date?.getTime() ?? 0) - (a.data.date?.getTime() ?? 0),
  );
  return rss({
    title: 'rite — blog',
    description:
      'Changelog notes, design decisions and measurements about rite. Every post links to its sources.',
    /* The feed's own home page, base included. */
    site: new URL(url('/'), origin).href,
    items: posts.map((p) => ({
      title: p.data.title,
      description: p.data.description,
      pubDate: p.data.date,
      link: new URL(url(`/blog/${p.id}/`), origin).href,
    })),
    xmlns: { atom: 'http://www.w3.org/2005/Atom' },
    customData: [
      '<language>en-gb</language>',
      `<atom:link href="${new URL(url('/blog/rss.xml'), origin).href}" rel="self" type="application/rss+xml"/>`,
      `<lastBuildDate>${(posts[0]?.data.date ?? new Date()).toUTCString()}</lastBuildDate>`,
    ].join(''),
  });
}
