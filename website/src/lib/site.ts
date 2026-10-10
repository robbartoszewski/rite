/* ═══════════════════════════════════════════════════════════════════════════
 *  getrite.ai — site constants.
 *
 *  Nothing in a page hard-codes a GitHub URL, a version string or a line
 *  anchor. They all live here, so a release is one edit.
 * ═══════════════════════════════════════════════════════════════════════════ */

/* ───────────────────────────────────────────────────────────────────────────
 *  THE THREE OPEN DECISIONS — flip any of these here.
 *  Defaults are the recommendations from docs/GETRITE_HANDOVER.md §1.
 * ─────────────────────────────────────────────────────────────────────────── */

/** DECISION 1 — maturity framing. Default: "Alpha — building in public".
 *  Shows a `v0.7.0` chip in the nav on every page and a maturity
 *  line in the hero. Set `label: 'beta'` to soften, or MATURITY.show = false
 *  to drop the chip entirely (not recommended before 1.0 — the candour is the
 *  positioning). */
export const MATURITY = {
  show: true,
  label: 'alpha',
  /** The hero's one-line maturity statement. */
  heroNote: 'Pre-1.0. Every limit is on one page.',
} as const;

/** DECISION 2 — the commercial story. Default: one honest sentence, no prices.
 *  There is NO pricing page and no tier table anywhere on this site, by
 *  design: nothing about a paid tier exists in the repository yet. If that
 *  changes, edit this sentence and the /licensing page — do not add prices
 *  without the owner's sign-off. */
export const COMMERCIAL_SENTENCE =
  'The core stays MIT. A paid management layer for teams is the direction, and there is nothing to sell yet.';

/** DECISION 3 — email capture. Default: OFF, because there is no backend and a
 *  form that goes nowhere is worse than no form.
 *
 *  To turn it on, set PUBLIC_NOTIFY_ENDPOINT in the build environment (a form
 *  POST endpoint from a list provider). The strip renders only when it is set.
 *  The wording stays "release notifications" — never "early access" or
 *  "waitlist": nothing on this site is gated. */
export const NOTIFY_ENDPOINT: string = import.meta.env.PUBLIC_NOTIFY_ENDPOINT ?? '';
/** Who stores the address and how to unsubscribe. Required reading before the
 *  strip goes live; shown under the field. */
export const NOTIFY_STATUS: string = import.meta.env.PUBLIC_NOTIFY_STATUS ?? '';

/* ───────────────────────────── version ──────────────────────────────────── */

/** Feeds the nav chip and the FAQ. Bump on release. */
export const VERSION = 'v0.7.0';

/** The /what-works page's eyebrow. Updated BY HAND after re-checking that page
 *  against the release — a version bump must never re-stamp it on its own,
 *  because the date is a claim that someone actually looked. */
export const STATUS_CHECKED = { version: 'v0.7.0', date: '9 Oct 2026' } as const;

/** Blog author shown on posts. Already public in the repository's LICENSE. */
export const AUTHOR = 'Rob Bartoszewski';

/* ───────────────────────────── repository ───────────────────────────────── */

const GH = 'https://github.com/robbartoszewski/rite';
const BLOB = `${GH}/blob/main`;

/** Proof links are pinned to a TAG, not to `main`. A line anchor into a moving
 *  branch silently becomes a confident link to the wrong code the moment
 *  anything is inserted above it — and these anchors are the site's whole
 *  credibility argument. Bump this with VERSION, and re-check the line numbers
 *  in PROOF below against the new tag before you do. */
/* Every anchor below was re-read against v0.7.0 itself on 2026-10-10, not
 *  carried over: `soak` and `install` had both moved, because v0.7.0 rewrote
 *  the README. A line anchor into the wrong revision is a confident link to
 *  the wrong code, so re-read each one here whenever this ref changes. */
const PROOF_REF = 'v0.7.0';
const PINNED = `${GH}/blob/${PROOF_REF}`;

export const REPO = GH;
export const SPEC = `${BLOB}/SPEC.md`;
export const CHANGELOG = `${BLOB}/CHANGELOG.md`;
export const GUIDE = `${BLOB}/docs/guide.md`;
export const LICENSE = `${BLOB}/LICENSE`;
export const README = `${BLOB}/README.md`;
export const INSTALL_NOTES = `${BLOB}/docs/install-notes.md`;

/** Proof links land on the LINE, not the file top — this audience clicks, and
 *  a link to a file top is a weak proof. Line numbers verified against
 *  origin/main on 2026-10-08; re-check them when the files move. */
const P_README = `${PINNED}/README.md`;
const P_SPEC = `${PINNED}/SPEC.md`;
const P_NOTES = `${PINNED}/docs/install-notes.md`;

export const PROOF = {
  /** README: "Claim exclusion, measured." + both figures. */
  soak: `${P_README}#L34-L36`,
  /** stage.py: the transition table — what may follow what. */
  stageTransitions: `${PINNED}/src/rite_ai/local/stage.py#L97`,
  /** stage.py: `advance`, the single guard every stage move goes through. */
  stageAdvance: `${PINNED}/src/rite_ai/local/stage.py#L308`,
  /** gates.py: `gate_for` — the artifact each stage must already have. */
  gates: `${PINNED}/src/rite_ai/local/gates.py#L92`,
  /** approve.py: the RL-6 different-model check, and its fail-closed branch. */
  independence: `${PINNED}/src/rite_ai/local/approve.py#L148-L161`,
  /** SPEC §9.12: "Nothing rite runs unattended starts a Claude session." */
  unattended: `${P_SPEC}#L5305`,
  /** README: the install one-liner. */
  install: `${P_README}#L325`,
  /** install-notes.md: download-and-check-then-run. */
  verify: `${P_NOTES}#L80`,
  /** install-notes.md: clone and read everything. */
  clone: `${P_NOTES}#L87`,
} as const;

/** The install command, verbatim from the repository's README (line 325).
 *  install.sh defaults its own VERSION to v0.7.0 from 0.7.0 on (SCRUM-89), so
 *  no RITE_VERSION is needed. Bump BOTH when a newer tag ships an install.sh. */
export const INSTALL_COMMAND =
  'curl -fsSL https://raw.githubusercontent.com/robbartoszewski/rite/v0.7.0/install.sh | sh';

/* ───────────────────────────── link helper ──────────────────────────────── */

/** Prefix an internal path with the deploy base (`/rite` on GitHub Pages, `/`
 *  on getrite.ai). Every internal href and every public/ asset goes through
 *  this, which is what makes the custom-domain switch a two-line change. */
export function url(path: string): string {
  const base = import.meta.env.BASE_URL.replace(/\/+$/, '');
  const rest = path.replace(/^\/+/, '');
  if (!rest) return `${base}/`;
  /* Page routes get a trailing slash so every internal link matches the
   * canonical GitHub Pages serves (it 301s the unslashed form, which would
   * cost a redirect on every nav click and split the link graph). A path whose
   * last segment has an extension is a file — rss.xml, og.png — and must not. */
  const isFile = rest.split('/').pop()!.includes('.');
  return `${base}/${rest}${isFile || rest.endsWith('/') ? '' : '/'}`;
}

/** Dates as "8 Oct 2026". */
export function formatDate(d: Date): string {
  return new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', year: 'numeric' }).format(d);
}

export const CATEGORY_LABEL = {
  'changelog-note': 'Changelog note',
  'design-decision': 'Design decision',
  measurement: 'Measurement',
} as const;
