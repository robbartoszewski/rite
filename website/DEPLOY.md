# Deploying getrite.ai

The site is a static Astro build. Today it goes to GitHub Pages as a **project
page** for the `rite` repository; later it moves to **getrite.ai** with a
two-line change.

---

## 1. One-time setup (needed before the first deploy)

GitHub Pages is **not yet enabled** on this repository. Enable it by hand:

> **Settings → Pages → Build and deployment → Source: `GitHub Actions`**

Until that is set, `.github/workflows/astro.yml` builds fine and then
fails at the deploy step with a Pages error. Nothing else needs configuring —
no branch, no `gh-pages`, no `/docs` folder.

## 2. How a deploy happens

`.github/workflows/astro.yml` (at the **repository root**, not in
`website/`) runs on a push to `main` that touches `website/**`, and can also be
run by hand from the Actions tab. It:

1. installs with `npm ci` on Node 22.12,
2. `npm run build`,
3. `npm run check` — `astro check` plus `copy-lint --strict`, which fails the
   build on a hype word, a forbidden claim, an exclamation mark or an unfilled
   `TODO(`,
4. uploads `website/dist` and deploys it to Pages.

It started life as GitHub's "Deploy Astro site to Pages" starter (PR #211) and
needed three changes to work here: the Astro project is in `website/` rather
than at the repository root, where the starter's package-manager detection
exits 1; Node is pinned to 22.12 because Astro 7 refuses Node 20; and
`npm run check` runs after the build, so `copy-lint` actually gates the
content. The starter's `BUILD_PATH` indirection and package-manager detection
are gone — this project uses npm and has a committed lockfile.

It also runs on a **pull request** touching `website/**`, building and linting
without deploying — so a copy edit that trips `copy-lint` fails on the PR
rather than after the merge.

It is scoped to `website/**` so a Python change does not redeploy the site.
⚠ It does **not** stop rite's own CI running on a website-only change:
`ci.yml` has no `paths:` filter and `publish-gate.yml` is deliberately
unfiltered. If that becomes annoying, add `paths-ignore: ['website/**']` to
`ci.yml` — and leave `publish-gate.yml` alone, it documents why it must stay.

**`robots.txt` is inert until the custom domain is live.** It is served at
`/rite/robots.txt`, and crawlers read robots.txt only at the origin root
(`robbartoszewski.github.io/robots.txt`), which belongs to a different repo.
Until then, submit `https://robbartoszewski.github.io/rite/sitemap-index.xml`
to Search Console directly.

**Live URL after the first deploy:** `https://robbartoszewski.github.io/rite/`

## 3. Switching to getrite.ai

Three steps, in this order.

**a. Point DNS at GitHub.** For the apex domain, four `A` records:

```
185.199.108.153
185.199.109.153
185.199.110.153
185.199.111.153
```

(and `AAAA` records for IPv6 if you want them — GitHub's docs list the
current set). For `www`, a `CNAME` to `robbartoszewski.github.io`.

**b. Change two lines** at the top of `website/astro.config.mjs`:

```js
const SITE = 'https://getrite.ai';   // was https://robbartoszewski.github.io
const BASE = '/';                    // was /rite
```

Nothing else needs touching, and that is tested rather than hoped for: every
internal link and asset path goes through `url()` in `src/lib/site.ts`, which
reads `BASE`. `robots.txt`, `site.webmanifest`, the sitemap and the RSS feed
are all **generated routes** for exactly this reason — there is no absolute URL
anywhere outside `astro.config.mjs`.

Consider also bumping `PROOF_REF` in `src/lib/site.ts` if a newer release tag
exists by then; the proof links are pinned to a tag so their line anchors
cannot rot.

**c. Add the custom domain.** Either set it in **Settings → Pages → Custom
domain** (GitHub writes the `CNAME` file for you), or commit it yourself:

```bash
echo 'getrite.ai' > website/public/CNAME
```

Then tick **Enforce HTTPS** once GitHub has issued the certificate.

> ⚠ If you set the domain in Settings *and* the repo has no `CNAME` file, the
> next Pages deploy from Actions can drop the domain. Committing
> `website/public/CNAME` is the version-controlled way, and it survives every
> deploy.

## 4. Local development

This machine's default `node` on `PATH` is 20.x, and Astro 7 needs ≥ 22.12.
Node 25 is already installed via Homebrew, so either:

```bash
export PATH="/opt/homebrew/opt/node/bin:$PATH"   # per shell
```

or `nvm use` (there is an `.nvmrc` pinning 22.12).

```bash
cd website
npm install
npm run dev       # http://localhost:4321/rite/  ← note the /rite base path
npm run build     # static output in website/dist/
npm run preview   # serves the built site
npm run check     # astro check + copy-lint --strict
```

**The dev URL includes `/rite`** because `base` is set for the project page. On
`getrite.ai` it will be `/`.

Drafts (`draft: true` in a post's front matter) are visible in `npm run dev`
and excluded from the build, the listings, the RSS feed and the sitemap.

## 5. Quality gates that are NOT wired up

The design handover asks for more than the build currently enforces. These are
deliberate omissions, not oversights — each needs a browser in CI, which is a
much heavier dependency than the rest of this site:

- **axe accessibility sweep** on every route in both colour schemes;
- **Playwright layout assertions** (`scrollWidth <= innerWidth` at 375 / 768 /
  1440);
- **external link check** (e.g. `lychee`) over `dist`, including the proof
  links into GitHub;
- **Lighthouse budgets** (95+ performance and accessibility on mobile).

`copy-lint` and `astro check` run on every deploy; the four above are worth
adding if the site grows past a handful of pages.
