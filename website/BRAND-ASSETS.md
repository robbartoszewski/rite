# Brand assets

Everything is generated from **one file**: `public/brand/rite-mark.svg`, the
approved flame logo. It is saved verbatim — 5.5 KB, ten paths, no editor
metadata — and every other asset in the set is derived from it by script.

## The palette is the logo's own

| Token | Hex | Where it comes from |
|---|---|---|
| crimson | `#AC1E1C` | the flame's body — light-mode accent |
| yellow | `#F4B12A` | the flame's core |
| ember | `#EC6C2D` | the flame's outer edge and the dot on the "i" — dark-mode accent |
| blush | `#D36D4A` | the flame's highlight |
| ink | `#222221` | the wordmark |
| paper | `#FAF7F2` | the ground, and the wordmark knocked out for dark |

`src/styles/tokens.css` carries these. Light mode uses crimson as the accent and
dark mode ember, because crimson on ink is only 2.24:1 — a contrast decision,
not a palette change. Re-checked against WCAG AA: crimson on paper **6.65**,
paper on crimson **6.65**, ember on ink **5.12**, muted text **5.94** in both
themes.

## What is in the set

### Vector (`public/brand/`)

| File | What it is |
|---|---|
| `rite-mark.svg` | **the master.** Full lockup: flame above the wordmark. Saved verbatim from the approved export. |
| `rite-flame.svg` | flame alone, tight box. Used for the SVG favicon, the footer mark and the decorative flame on the final CTA. |
| `rite-logo-horizontal.svg` | flame + wordmark side by side, **ink** wordmark — the nav lockup on light. |
| `rite-logo-horizontal-dark.svg` | the same lockup with the wordmark in paper, flame still in colour — **the nav lockup on dark**. This is the asset the old pack never had. |
| `rite-mark-ink.svg` | the full mark in solid ink, for one-colour contexts. |
| `rite-mark-white.svg` | the full mark in solid paper, for one-colour contexts. |

The horizontal lockup sets the flame at **1.4×** the wordmark's height with a
gap of 0.34× the flame's width, bottoms flush, so the flame sits on the
wordmark's baseline. Both lockups keep the ember dot on the "i" — only the ink
glyphs flip for the dark variant.

### Raster (`public/`)

| File | Size | Notes |
|---|---|---|
| `favicon.ico` | 16/32/48 | multi-resolution, flame only |
| `favicon-16/32/48/64.png` | — | flame only, transparent |
| `apple-touch-icon.png` | 180 | flame on an opaque paper tile — iOS composites transparency on black |
| `icon-192.png`, `icon-512.png` | — | transparent, manifest `purpose: any` |
| `maskable-512.png` | 512 | full-bleed paper, flame inside the 80%-diameter safe circle (half-diagonal 180 px against a 204.8 px radius) |
| `og.png` | 1200×630 | the horizontal lockup centred on paper — `og:image` and `twitter:image` |
| `brand/avatar-512.png` | 512 | square profile tile for GitHub/LinkedIn/X; not referenced by the site |

**Icons are the flame without the wordmark, deliberately.** A 16px icon
carrying "rite" is illegible, and the SVG favicon is offered first so a browser
that supports it gets the vector.

## Regenerating

Two scripts, in `scripts/`. They need `cairosvg` and `pillow`, and on this Mac
cairo is present but not on the default loader path:

```bash
export DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib
python3 scripts/build-brand-svg.py    .   # the SVG variants and both lockups
python3 scripts/build-brand-raster.py .   # every PNG, the .ico and og.png
```

Edit `rite-mark.svg` and re-run both; nothing else is hand-maintained. The
flame/wordmark split is by path index (0–4 flame, 5–9 wordmark), which is why
the master must stay verbatim rather than being re-exported with paths in a
different order.

## Still worth having

**An SVG with the paths grouped and named** (`<g id="flame">`, `<g id="wordmark">`)
would remove the index-based split above. Not urgent — the split is asserted in
the scripts and fails loudly if the file changes shape.
