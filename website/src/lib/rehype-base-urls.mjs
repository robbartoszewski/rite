/**
 * Rewrites root-relative links and images in Markdown to carry the deploy base.
 *
 * Why this exists: a Markdown link written `[x](/what-works)` does NOT go
 * through `url()` in src/lib/site.ts, so on the GitHub Pages project page it
 * resolved to `<origin>/what-works` and 404'd — while the same link in an
 * .astro file was correct. Found in review on the one published post.
 *
 * Only paths starting with a single `/` are touched: external URLs, anchors,
 * `mailto:` and already-based paths are left alone.
 */
import { visit } from 'unist-util-visit';

export function rehypeBaseUrls() {
  const base = (import.meta.env?.BASE_URL ?? process.env.ASTRO_BASE_URL ?? '/').replace(/\/+$/, '');
  return (tree) => {
    if (!base) return;
    visit(tree, 'element', (node) => {
      for (const attr of ['href', 'src']) {
        const v = node.properties?.[attr];
        if (typeof v !== 'string') continue;
        if (!v.startsWith('/') || v.startsWith('//')) continue;
        if (v.startsWith(`${base}/`)) continue;
        node.properties[attr] = `${base}${v}`;
      }
    });
  };
}

/**
 * Gives Markdown <pre> blocks `tabindex="0"`, so a scrollable code region is
 * reachable by keyboard (axe's `scrollable-region-focusable` rule). The
 * .astro terminals already set it by hand; this covers posts.
 */
export function rehypeCodeTabindex() {
  return (tree) => {
    visit(tree, 'element', (node) => {
      if (node.tagName === 'pre') {
        node.properties = { ...node.properties, tabindex: '0' };
      }
    });
  };
}
