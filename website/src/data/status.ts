/** The /what-works rows. One file, so a release updates one list.
 *
 *  `label` is the badge word. 'works' rows carry no badge — the column heading
 *  says it. Every other row carries exactly one of:
 *    'not-proven'  built, but not yet shown end to end
 *    'not-built'   does not exist
 *    'caveat'      a limit you work around
 *    'by-design'   a decision, not a gap
 *
 *  ⚠ "Not yet" is deliberately NOT a label: it implies a roadmap commitment.
 *
 *  Every row traces to docs/GETRITE_HANDOVER.md §4. Do not add a row that does
 *  not. The page's counts are computed from this array, so adding a row
 *  updates the intro automatically.
 */
export type StatusLabel = 'works' | 'not-proven' | 'not-built' | 'caveat' | 'by-design';

export interface StatusRow {
  label: StatusLabel;
  /** Bolded lead sentence. */
  lead: string;
  /** The rest. May contain <code> and <a>. */
  text?: string;
}

export const LABEL_TEXT: Record<Exclude<StatusLabel, 'works'>, string> = {
  'not-proven': 'Not proven',
  'not-built': 'Not built',
  caveat: 'Caveat',
  'by-design': 'By design',
};

export const WORKS: StatusRow[] = [
  {
    label: 'works',
    lead: 'Claim exclusion under concurrency.',
    text: 'A four-minute soak of six concurrent Workers: 132,321 grants, zero lost and zero held twice. The previous lock lost 309 updates in ten seconds on the same harness.',
  },
  {
    label: 'works',
    lead: 'Per-Worker checkouts.',
    text: '<code>rite add worker alpha</code> creates <code>workers/alpha/</code> with that Worker&rsquo;s own clone of each module.',
  },
  {
    label: 'works',
    lead: 'The staged pipeline, enforced by rite&rsquo;s code.',
    text: 'Persisted, gated per stage on its own artifact, append-only. A model cannot skip or reorder a stage.',
  },
  {
    label: 'works',
    lead: 'Independent-model plan review.',
    text: 'The approver is not the author and runs a different model; an author rite can&rsquo;t place fails closed.',
  },
  {
    label: 'works',
    lead: 'Delivery blocked until the composed work passes the ticket&rsquo;s own agreed check.',
    text: 'Each piece passing its own check is not the ticket working.',
  },
  {
    label: 'works',
    lead: 'Sandboxed Workers on macOS',
    text: '&mdash; a guard rail against mistakes, not containment. See the caveats below.',
  },
  {
    label: 'works',
    lead: 'Secret scanning over full history,',
    text: 'on pre-push and in CI. Suppressing a finding takes a written reason.',
  },
  { label: 'works', lead: 'JIRA and GitHub Issues', text: 'as ticket boards.' },
  {
    label: 'works',
    lead: 'Managers on Claude, Goose or Cursor.',
    text: 'A Claude Owner with a local secondary Manager is the setup this release is built for: one machine, one project root.',
  },
  {
    label: 'works',
    lead: 'A local model&rsquo;s context window, pinned rather than hoped for.',
    text: 'A local Manager must declare at least 32,768 tokens; rite pins it on the server so Ollama&rsquo;s 4,096-token default doesn&rsquo;t decide it.',
  },
  {
    label: 'works',
    lead: 'Every local-model report checked in a separate session',
    text: '&mdash; CONFIRMED, CONTRADICTED or COULD NOT TELL. The checker is also a model and can be wrong.',
  },
];

export const GAPS: StatusRow[] = [
  {
    label: 'by-design',
    lead: 'Unattended dispatch of Claude sessions is refused on purpose.',
    text: 'It spends your quota with nobody watching. You start every run, and <code>rite start</code> runs a Manager in your own foreground terminal; the loop reports a stalled queue rather than working it. Perpetual unattended operation is the goal, not the state.',
  },
  {
    label: 'not-proven',
    lead: 'One sandboxed Worker has not yet carried a ticket all the way to a merged PR in one run.',
    text: 'Each part of that path has been run.',
  },
  {
    label: 'not-proven',
    lead: 'A live sandboxed local-model turn is not measured.',
    text: 'The path has been measured with the model replaced by a scripted command, inside a real sandbox; the live run is held behind an open sandbox-isolation issue.',
  },
  {
    label: 'not-proven',
    lead: 'Several machines on one project is implemented and unproven',
    text: '&mdash; not yet run on two physical machines over a real network. Several Managers across machines is planned for 0.8.0.',
  },
  {
    label: 'caveat',
    lead: 'Linux is thinner than macOS.',
    text: 'Built and tested on macOS 26.2; on Linux (tested on Ubuntu 24.04, ARM64) the sandbox is weaker and Workers are not sandboxed by default.',
  },
  { label: 'not-built', lead: 'Windows is not attempted.' },
  {
    label: 'caveat',
    lead: 'On Docker, file locking does not lock.',
    text: 'Two Workers can be granted the same path. Run one Worker there.',
  },
  {
    label: 'caveat',
    lead: 'rite does not meter your quota.',
    text: 'Six Workers burn it about six times as fast as one session.',
  },
  {
    label: 'not-built',
    lead: 'Nothing notices a rejected push of a Worker&rsquo;s work.',
    text: 'Scope branch protection to your default branch, or a Worker&rsquo;s work exists only inside a sandbox that is later destroyed.',
  },
  {
    label: 'not-built',
    lead: 'No gates on your code quality.',
    text: 'Beyond the ticket&rsquo;s own agreed check, rite does not read your test or lint results &mdash; no coverage threshold, no accessibility pass, no opinion on your test strategy.',
  },
  {
    label: 'not-built',
    lead: 'No capability routing.',
    text: 'Workers are interchangeable; assignment picks whichever is free, not whichever can.',
  },
  {
    label: 'not-built',
    lead: 'Questions aren&rsquo;t routed to whoever owns an area.',
    text: 'The table of people and areas is printed into the Owner&rsquo;s instructions. It is reference material, not a mechanism.',
  },
  { label: 'not-built', lead: 'Not on PyPI.', text: 'Install from a release tag.' },
];
