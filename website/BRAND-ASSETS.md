# Brand assets

The site uses the **real `rite-brand.zip` pack** throughout. Nothing here is a
placeholder, and nothing is redrawn or recoloured.

The complete pack is committed unflattened at **`design/brand-pack/`**, with its
own `README.md`, `head-snippet.html` and `site.webmanifest`. That directory is
*not* published — it is the canonical source, so provenance stays obvious and a
future regeneration can be dropped in and re-mapped. What the site serves lives
in `public/`.

## Palette — straight from the pack's README

| Token | Hex | Pack's name |
|---|---|---|
| `--crimson` / `--accent` (light) | `#B82D22` | Crimson (primary) |
| `--ember` / `--accent` (dark) | `#E8743B` | Ember / amber |
| `--text` (light) / `--surface` (dark) | `#211E1B` | Ink (wordmark) |
| `--surface` (light) / `--text` (dark) | `#FAF7F2` | Off-white (bg) |

`src/styles/tokens.css` carries these verbatim. Dark mode shifts the accent
crimson → ember because crimson fails contrast on ink; that is the only
deviation, and it is a contrast decision, not a palette change.

## Pack file → served path

| Pack path | Served as | Used by |
|---|---|---|
| `favicon/favicon.ico` | `/favicon.ico` | Tab icon, and the no-`media` fallback |
| `favicon/favicon-16.png` | `/favicon-16.png` | Tab icon, light scheme |
| `favicon/favicon-32.png` | `/favicon-32.png` | Tab icon, light scheme |
| `favicon/favicon-48.png` | `/favicon-48.png` | Tab icon, light scheme |
| `favicon/favicon-64.png` | `/favicon-64.png` | Tab icon, light scheme |
| `favicon/favicon-tile-16.png` | `/favicon-tile-16.png` | Tab icon, **dark** scheme |
| `favicon/favicon-tile-32.png` | `/favicon-tile-32.png` | Tab icon, **dark** scheme |
| `favicon/favicon-tile-48.png` | `/favicon-tile-48.png` | Tab icon, **dark** scheme |
| `app-icon/apple-touch-icon.png` | `/apple-touch-icon.png` | iOS home screen |
| `app-icon/icon-192.png` | `/icon-192.png` | Web manifest, `purpose: any` |
| `app-icon/icon-512.png` | `/icon-512.png` | Web manifest, `purpose: any` |
| `app-icon/maskable-512.png` | `/maskable-512.png` | Web manifest, `purpose: maskable` |
| `app-icon/tile-192.png` | `/tile-192.png` | Available; rounded app tile |
| `app-icon/tile-512.png` | `/tile-512.png` | Available; rounded app tile |
| `logo/rite-logo-horizontal.png` | `/brand/rite-logo-horizontal.png` | **Nav lockup, light theme** |
| `logo/rite-logo.png` | `/brand/rite-logo.png` | Available; stacked lockup |
| `mark/rite-mark.png` | `/brand/rite-mark.png` | **Nav mark (dark), footer mark, the flame on the final CTA** |
| `monochrome/rite-logo-white.png` | `/brand/rite-logo-white.png` | Available; 1-colour stacked |
| `monochrome/rite-logo-black.png` | `/brand/rite-logo-black.png` | Available; 1-colour stacked |
| `monochrome/rite-mark-white.png` | `/brand/rite-mark-white.png` | Available; 1-colour mark |
| `monochrome/rite-mark-black.png` | `/brand/rite-mark-black.png` | Available; 1-colour mark |
| `social/og-banner-1200x630.png` | `/og.png` | **`og:image` and `twitter:image` on every page** |
| `social/avatar-512.png` | `/brand/avatar-512.png` | Available; square profile tile for GitHub/LinkedIn/X — not referenced by the site |

Eight of those "Available" files ship to the deploy without any page
referencing them (the four monochrome variants, the stacked colour lockup, the
avatar and the two app tiles — about 215 KB in total). That is deliberate, not
an oversight: no visitor ever requests them, and it gives the brand set stable
URLs for a README badge, a slide or a profile picture. Delete them from
`public/brand/` if you would rather keep the deploy to exactly what the site
renders; `design/brand-pack/` still holds every one.

The pack's 1024px masters (`logo/rite-logo-1024.png`,
`logo/rite-logo-light-1024.png`, `mark/rite-mark-1024.png`) stay in
`design/brand-pack/` only. They are 180–185 KB each and the site has no slot
that needs them; publishing them would ship half a megabyte nobody requests.

### Favicon wiring

`src/layouts/Site.astro` follows the pack's `head-snippet.html` and extends it
two ways:

1. **More sizes** (48 and 64) for crisper rendering on high-DPI tabs.
2. **The dark-tab variants**, which the pack's README recommends: *"Use
   favicon-tile-* if the flame looks muddy on dark browser tabs."* The plain
   PNGs carry `media="(prefers-color-scheme: light)"` and the tiles
   `media="(prefers-color-scheme: dark)"`. Verified in a browser: exactly the
   light set matches in a light scheme and exactly the tile set in a dark one.
   `favicon.ico` is listed first with **no** `media`, so a browser that ignores
   `media` on `rel=icon` still gets the real mark rather than nothing.

`theme-color` differs from the snippet on purpose: the pack sets a single
crimson, the site sets off-white for light and ink for dark so the browser
chrome matches the page. Crimson stays in `site.webmanifest` for installed-app
chrome, exactly as the pack has it. The manifest is a generated route
(`src/pages/site.webmanifest.js`) rather than the pack's static file, so its
icon paths carry the deploy base — a static one with `/icon-192.png` would 404
at `/rite/`.

## The one real gap — and it needs you

**There is no horizontal lockup for dark backgrounds, so dark mode sets the
wordmark as text.**

I checked every lockup in the pack:

- `logo/rite-logo-horizontal.png` (590×320) — the only horizontal lockup, and
  its wordmark is **ink**, which is invisible on `#211E1B`.
- `monochrome/rite-logo-white.png` (394×642) — white, but the **stacked**
  lockup. At the nav's 30px height it would be ~18px wide. Unusable there.
- `logo/rite-logo-light-1024.png` (1024×1024) — the stacked lockup on an
  off-white *background*, so it cannot sit on a dark page at all.

So in dark mode the nav shows the real colour flame mark plus **"rite" set in
IBM Plex Sans 600**. It is legible and themeable, and it is better than the
alternative (the 13px flame alone read as a broken image). But the letterforms
are not the logo's — the pack's wordmark is a geometric sans and Plex is not.

**What would fix it properly, in order of preference:**

1. **An SVG of the flame and the lockup.** The pack's own README offers this:
   *"If you later get an SVG of the flame, send it and I'll regenerate crisp
   vector + exact-match set."* That also fixes the two PNG logos being 141 KB
   for marks drawn at 55×30 and 13×30, and makes them sharp on high-DPI.
2. **A horizontal lockup with an off-white wordmark** — flame plus `#FAF7F2`
   wordmark, transparent background, same proportions as
   `rite-logo-horizontal.png`.

Either one drops in like this:

```
place at:   website/public/brand/rite-logo-horizontal-dark.png   (or .svg)
edit:       website/src/components/SiteNav.astro
            — point the .rt-logo-dark <img> at the new file
            — set width/height to its aspect ratio
            — delete the <span class="rt-wordmark">rite</span>
then:       website/src/styles/components.css
            — delete the .rt-wordmark block in the SITE OVERRIDES section
```

Nothing else depends on it, and it does not block launch.
