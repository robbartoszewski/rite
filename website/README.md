# getrite.ai

The marketing site and blog for [rite](https://github.com/robbartoszewski/rite),
built with Astro as a static site. Deploys to GitHub Pages; moves to
getrite.ai with a two-line change (see [`DEPLOY.md`](DEPLOY.md)).

```bash
export PATH="/opt/homebrew/opt/node/bin:$PATH"   # Astro 7 needs Node >= 22.12
npm install
npm run dev        # http://localhost:4321/rite/
npm run build
npm run check      # astro check + copy-lint --strict
```

## What governs the content

Every product claim on this site traces to a verified fact in
[`../docs/GETRITE_HANDOVER.md`](../docs/GETRITE_HANDOVER.md) — §4 is the fact
sheet, §7 the voice rules and §12 the list of things the site must never claim.
**Do not add a claim that is not in §4.**

`scripts/copy-lint.mjs` is the tripwire: `npm run check` fails the build on a
hype word, a forbidden claim, an exclamation mark, or an unfilled `TODO(`. It
is not a substitute for reading §12 line by line before launch.

## Layout

```
src/
  lib/site.ts          every URL, version string and proof anchor + the three
                       owner decisions as flippable constants
  data/status.ts       the /what-works rows; the page's counts are computed
  content/blog/*.md    posts (front matter requires at least one source)
  content.config.ts    the blog schema
  layouts/             Site.astro (head, nav, footer), Post.astro
  components/          one per component; classes come from components.css
  styles/              tokens.css + components.css (generated — do not hand-edit)
  pages/               /, /what-works, /licensing, /blog, /blog/[slug], 404
public/                brand pack, favicons, og.png, robots.txt
design/                BRAND.md and tokens.json, for reference
```

## Pages

| Route | Source |
|---|---|
| `/` | `src/pages/index.astro` |
| `/what-works` | `src/pages/what-works.astro` |
| `/licensing` | `src/pages/licensing.astro` |
| `/blog` | `src/pages/blog/index.astro` |
| `/blog/<slug>` | `src/pages/blog/[...slug].astro` |
| `/blog/rss.xml` | `src/pages/blog/rss.xml.js` |
| `/404` | `src/pages/404.astro` |

Deliberately **not** built: a docs site (docs live in the repo), pricing,
customers, a comparison page, or any UI mock. rite is a CLI.

## Adding a blog post

Drop a `.md` or `.mdx` file in `src/content/blog/`:

```yaml
---
title: Short and specific
description: One sentence; also the meta description.
date: 2026-10-20          # required once draft is false
category: changelog-note  # changelog-note | design-decision | measurement
author: Rob Bartoszewski
appliesTo: v0.7.0a10
draft: false
sources:                  # at least one, or the build fails
  - label: CHANGELOG.md — 0.7.0a10
    href: https://github.com/robbartoszewski/rite/blob/main/CHANGELOG.md
---
```

Drafts are visible in `npm run dev` and excluded from the build, every listing,
the RSS feed and the sitemap.

See [`BRAND-ASSETS.md`](BRAND-ASSETS.md) for what the brand pack covers and the
two gaps in it.
