from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from rite_ai.config.managers import (
    CLAUDE,
    CLAUDE_DECOMPOSER_DEFAULT,
    DecomposerConfig,
    ManagerRole,
    is_local_engine,
)


@dataclass
class ProjectBrief:
    name: str
    role: str  # "owner" | "manager"
    root_branch: str = "main"
    kind: str = ""
    features: str = ""
    platform: str = ""
    languages: list[str] = field(default_factory=list)
    frameworks: list[str] = field(default_factory=list)
    architecture: str = ""
    # The spec or code `rite init` was pointed at, and the one open answer
    # about what in it is stale or should change (SPEC §9.3). Recorded for
    # whatever reads the source later; empty on a project started from scratch.
    source_path: str = ""
    source_changes: str = ""


@dataclass
class Module:
    name: str
    path: str
    url: str | None = None
    branch: str = "main"
    description: str = ""
    # A lambda so `RecordedCommands` can be defined after this class.
    commands: RecordedCommands = field(default_factory=lambda: RecordedCommands())
    # This module's override of the project's `publish:` block, key by key
    # (PB1). Every key None means "no override".
    publish: ModulePublish = field(default_factory=lambda: ModulePublish())


@dataclass
class RecordedCommands:
    """A module's commands as someone recorded them in `modules.yaml`.

    Detection derives commands from a module's own manifests. It is right for
    most modules and wrong for some — a scheme it cannot see, a test target
    that needs a flag — and this is where the correction lives. A recorded
    command wins over the detected one for its own key only; an unrecorded
    key falls back to detection. `None` means "not recorded", never "there is
    no command".
    """

    install: str | None = None
    build: str | None = None
    test: str | None = None
    lint: str | None = None
    format: str | None = None


STRATEGIES = ("commit", "push", "pull_request", "push_to_shared")
"""What rite does with a finished task's commits (PB1). Named verbs, never
numbers. `push_to_shared` parses and is refused at start until v0.8.0 (PB2)."""


@dataclass
class ScopeConfig:
    """The `scope:` block: the diff budget at delivery (SCRUM-65)."""

    lines_per_item: int = 150
    factor: float = 2.0
    exclude: list[str] = field(
        default_factory=lambda: [
            "*.lock",
            "*.sum",
            "uv.lock",
            "**/vendor/**",
            "**/node_modules/**",
            "**/*_generated.*",
            "**/*.pb.go",
        ]
    )


@dataclass
class PublishConfig:
    """The project's `publish:` block (PB1, `V070_RELEASE_PLAN.md` Track PB).

    `pull_request` is the default by Robert's decision: with `auto_merge` off
    it is strictly safer than `push`, and the team that inspects every line is
    the team that will not have changed it."""

    strategy: str = "pull_request"
    squash: bool = False
    auto_merge: bool = False


@dataclass
class ModulePublish:
    """A module's `publish:` override. `None` means "not overridden", never
    "off": resolution falls back to the project's value for that key only,
    the way `RecordedCommands` does."""

    strategy: str | None = None
    squash: bool | None = None
    auto_merge: bool | None = None
    # PB2 (v0.8.0). Parsed so a config written for it is refused at start by
    # name rather than as an unknown key; nothing reads it for publishing.
    shared_repo: str | None = None


@dataclass
class ScanPattern:
    type: str  # "regex" | "path"
    pattern: str
    description: str = ""


@dataclass
class ExpertiseEntry:
    name: str
    tags: list[str] = field(default_factory=list)


@dataclass
class TicketBackendConfig:
    type: str = "none"  # "jira" | "github" | "none"
    site: str = ""  # JIRA site, e.g. "myteam.atlassian.net" — unused for github
    repo: str = ""  # GitHub "owner/name" — unused for jira
    projects: dict[str, str] = field(default_factory=dict)
    credential: str = ""
    scope_label: str = ""
    """The label that marks a ticket as THIS project's (`tickets.scope`): ANDed
    into every list rite makes of the board and stamped on every ticket rite
    creates. Empty reads the whole board, as before it existed."""


BOARD_ONLY_FIELDS: dict[str, tuple[str, ...]] = {
    "jira": ("site", "projects"),
    "github": ("repo",),
}
"""Which `TicketBackendConfig` fields belong to one board and nothing else.

`scope_label` is deliberately absent: it marks a ticket as this project's and
is backend-agnostic (§6.1.1), so it survives a change of board.
"""


def leave_the_old_board(backend: TicketBackendConfig, for_board: str) -> list[str]:
    """Clear what described the board being left behind, and name what was
    cleared.

    A retarget re-prompts for everything the INCOMING board needs in the same
    run, so nothing needed is ever stale — only the outgoing board's
    leftovers, and a `config.yaml` describing two boards invites a reader to
    believe a `site:` that nothing uses.

    `credential` goes too: it RENAMES the outgoing board's token key, so it is
    the old board's field by definition. It is cleared rather than replaced —
    an empty one falls back to the new board's default key
    (`credential_name or "jira_token"`), and inventing a name here is a
    separate question.
    """
    cleared: list[str] = []
    for board, fields in BOARD_ONLY_FIELDS.items():
        if board == for_board:
            continue
        for name in fields:
            current = getattr(backend, name)
            if not current:
                continue
            setattr(backend, name, {} if isinstance(current, dict) else "")
            cleared.append(f"ticket_backend.{name}")
    if backend.credential:
        backend.credential = ""
        cleared.append("ticket_backend.credential")
    return cleared


@dataclass
class HeartbeatConfig:
    interval_minutes: int = 10
    stall_threshold: int = 3


DEFAULT_GITLEAKS_CONFIG = ".rite/gitleaks.toml"
"""The `publish_gate.gitleaks_config` a project gets without asking.

ONE spelling. There were three — here, in `parse.py`'s `.get()` default, and
in the gate's own comparison — and the gate now treats "configured but
absent" as a hard error for any path that is NOT this one. A drift between
the copies would therefore turn every default project into EXIT_ERROR, and
the pre-push hook fails closed, so it would refuse every push everywhere.
"""


@dataclass
class PublishGateConfig:
    scan_patterns: list[ScanPattern] = field(default_factory=list)
    gitleaks_config: str = DEFAULT_GITLEAKS_CONFIG


@dataclass
class WatchdogConfig:
    interval_minutes: int = 5


DEFAULT_BROADCAST = "#all-rite"
DEFAULT_STATUS = "#rite-status"


@dataclass
class SlackConfig:
    """Who may instruct a Manager over Slack, and where status is posted
    (A6, SPEC §9.16.2, D-95).

    ⚠ **AUTHORITY COMES FROM THE CHANNEL, SO THE COMMAND CHANNEL IS NOT
    CONFIGURABLE.** It is the Owner's direct message with the rite app, named
    here by the Owner's user id. Only the Owner and the app are in that
    conversation, so "only the Owner can instruct" is a property of where a
    message was posted rather than a check rite has to get right per message.
    An earlier shape took a `command_channel` id, which let a project point
    authority at a channel the whole workspace can post in. It is refused by
    the parser, by name, rather than read.

    `broadcast_channel` is where status is posted for anyone to read. What is
    typed there reaches the Manager as CONTEXT and never carries authority,
    whoever types it. A NAME is fine: `chat.postMessage` accepts one and
    returns the id, which rite then reads by (A3a, measured), so no
    `channels:read` scope is needed.

    ⚠ **Per project, not per Manager.** A Slack DM is one per user and app, so
    two Managers cannot each have "the Owner's DM" without a second app. How
    several Managers share one DM was MMQ2, decided as (c): only the Owner
    opens a relay (SPEC §9.16.7).
    """

    owner_user: str = ""
    """The Owner's Slack user id (`U…`). Its DM with the app is the command
    channel. Empty means there is NO command channel: Slack is broadcast-only
    and nothing typed in Slack is an instruction."""

    broadcast_channel: str = ""
    """A channel name (`#all-rite`) or id (`C…`). Empty means the default,
    `#all-rite`, once Slack is set up at all — see `broadcast`."""

    status_channel: str = ""
    """Where rite says a Manager started or stopped, and other lifecycle
    lines: a channel name or id. Empty means the default, `#rite-status`, once
    Slack is set up. **Output only**: rite never reads it, so nothing typed
    there reaches a Manager, not even as context. Robert, 2026-09-29: those
    lines "must go to a separate #rite-status channel or something. It's a
    spam anywhere else" — so they never go to the DM, which is for what needs
    the person. One channel is shared by every project (D-114), so each line
    names its project; the parser refuses the broadcast channel here, because
    that one is read as context."""

    @property
    def enabled(self) -> bool:
        """Is Slack set up? Either key turns it on; neither leaves it off."""
        return bool(self.owner_user or self.broadcast_channel)

    @property
    def broadcast(self) -> str:
        """The broadcast target in effect, or "" when Slack is off."""
        if not self.enabled:
            return ""
        return self.broadcast_channel or DEFAULT_BROADCAST

    @property
    def status(self) -> str:
        """The status channel in effect, or "" when Slack is off."""
        if not self.enabled:
            return ""
        return self.status_channel or DEFAULT_STATUS


@dataclass
class PoolConfig:
    coordinator_standby: int = 2
    warn_threshold: float = 0.5
    # How long a slot may go unverified before it drops out of the LIVE
    # COUNT (§2.5.2's readiness lease). Reporting only — nothing is
    # retired or released on this threshold. Was a module-level constant
    # in `rite_ai.pool`; the number is a policy choice about how much
    # probe jitter to tolerate, so it belongs to the project, not to the
    # code.
    lease_expiry_minutes: int = 15
    # How long a slot must be CONTINUOUSLY, PROVABLY UNREACHABLE (the
    # OS-level probe fails, every time, for this long) before `rite pool
    # archive` retires it and releases the claims it died holding.
    # Deliberately a separate, more conservative knob than the lease
    # above: dropping out of the live count is a reporting change, while
    # archiving is destructive and force-releases another session's
    # claims.
    archive_after_minutes: int = 30


@dataclass
class SandboxConfig:
    # Default ON (D-51). The reasoning that made it opt-in — yoloAI is a
    # separate binary, so defaulting on would fail first run for everyone
    # without it — stopped holding once `rite init` asks and `rite doctor`
    # verifies by starting a real sandbox: a machine without yoloAI now
    # gets a question it can answer and a row that says what is missing,
    # not a failure.
    enabled: bool = True
    backend: str = "seatbelt"  # seatbelt | docker | podman | tart — these are
    # yoloAI's literal `--backend` values (verified via `yoloai system
    # backends`); "seatbelt" is yoloAI's name for macOS's `sandbox-exec`
    # mechanism (D-30) — SPEC.md's prose says "sandbox-exec", but that
    # string is not a value this flag accepts.
    token_permissions: list[str] = field(
        default_factory=lambda: ["contents", "pull_requests"]
    )
    max_concurrent_workers: int = 5


@dataclass
class BudgetConfig:
    weekly_quota_pct: int = 95
    # Absolute token figure for "100% of quota" — §2.6.2 leaves the choice
    # between this and `weekly_quota_pct` open ("whichever the account's
    # plan exposes"); rite has no API to read a plan's quota size, so this
    # is the only side of that choice a local script can actually compute
    # a percentage from. None means: report the raw rate only, no
    # percentage, no early-exhaustion warning (nothing to warn against).
    weekly_token_budget: int | None = None
    # Default: ISO week (Monday). Not a discovered fact about any specific
    # Anthropic billing cycle — rite has no way to know that — just a
    # fixed, documented convention so "week-end projection" has a concrete
    # boundary. Override if your plan resets on a different day.
    week_start_day: str = "monday"


@dataclass
class ScheduleWindow:
    hours: str  # "HH:MM-HH:MM", end < start means "through midnight" (§2.7)
    workers: int
    days: str = ""
    """Which weekdays this window applies to. Empty means EVERY day, which
    is what every window written before 0.5.0 meant — so an existing
    schedule keeps its exact meaning and needs no migration.

    "Mon-Fri", "Sat,Sun", "Mon,Wed,Fri". Ranges wrap ("Fri-Mon" is Fri, Sat,
    Sun, Mon) for the same reason `hours` wraps at midnight: a week is a
    cycle and refusing to wrap would make the user write two entries for one
    idea."""


@dataclass
class ScheduleConfig:
    timezone: str = ""  # required once any window exists — no default (D-48)
    windows: list[ScheduleWindow] = field(default_factory=list)


@dataclass
class CheckinWindow:
    """One check-in window (plan § K1): a `ScheduleWindow` without `workers`.

    ⚠ **Not a second time model.** `hours` and `days` are the schedule's own
    grammar, read by the schedule's own `_parse_hours` and `parse_days`, on
    the schedule's clock (`schedule.timezone`, machine-local when unset). A
    second parser would drift from the first, and a second zone rule would put
    a 14:00 check-in at 15:00 for somebody whose schedule is right."""

    hours: str  # "HH:MM-HH:MM", end < start means "through midnight"
    days: str = ""  # empty means every day, as for a schedule window


@dataclass
class GithubAppConfig:
    """The GitHub App that mints a sandboxed Manager's token (C6/C26).

    Neither id is a secret, which is why they live in committed config. The
    App's PRIVATE KEY is a credential (`github_app_key`), stored whole with
    `rite credential set github_app_key --stdin < app.pem` in rite's 0600
    credential file, and it never enters a Manager's sandbox. See
    `managers/github_access.py` for the path each value takes.
    """

    app_id: str = ""
    """The App's numeric id. Empty means no App, so no token is minted."""

    installation_id: str = ""
    """The id of the App's installation on the repository's owner."""

    repository: str = ""
    """`owner/name` the token is scoped to. Empty means
    `ticket_backend.repo`: the board a Manager reads."""


@dataclass
class CheckinsConfig:
    """When the User wants to be asked things (V060_CHECKINS.md).

    **No windows means no check-ins**, and that is stated wherever it matters
    rather than implied: a question deferred to a check-in that never comes
    is a question nobody is asked."""

    windows: list[CheckinWindow] = field(default_factory=list)


ACCEPT_WORDS = ("ok", "yes", "accept", "lgtm", "proceed")
"""TRQ3, decided: the words that accept a proposal. Robert added `proceed`."""

NEVER_ACCEPT = frozenset({"no", "not", "stop", "wait", "cancel", "don't"})
"""Refused in `accept_words`: a mistake in that list turns a refusal into
consent, so the words most likely typed as the opposite of yes cannot be in
it (the note's part 3.11)."""


@dataclass
class RefinementConfig:
    """The limits on refining a ticket with the User (TR2; TRQ2, TRQ3, TRQ8,
    TRQ11, decided; `V070_TICKET_REFINEMENT.md` part 3.11).

    Every key is optional and these defaults are Robert's. **Whether
    refinement is enforced is not here, and never will be (TRQ1).** A value
    out of range is refused by the parser rather than clamped: a limit
    silently changed is a limit the User believes they set."""

    unanswered: int = 3
    """N: consecutive messages about one ticket that reached their deadline
    with no reply, before it is PARKED. **Not a cap on rounds** (Robert's
    correction to TRQ2, 2026-09-29: "This limit should apply to nudging
    without a reply, not to a discussion. A topic may be complex and need
    many rounds to resolve. As long as the User is responsive, the limit
    shouldn't apply"). Any reply resets it to zero."""
    deadline_hours: float = 24
    """How long one message waits for a reply, or until the next check-in
    closes if sooner. A message past it unanswered counts one toward
    `unanswered`, and he is nudged with the same question when he is next
    active."""
    open_max: int = 5
    """K: refinements open at once per Manager (inside their deadline)."""
    start_per_session: int = 3
    """S: new refinements started per Owner session."""
    accept_words: list[str] = field(default_factory=lambda: list(ACCEPT_WORDS))
    chore_after_minutes: int = 60
    """A chat instruction the User has not answered about becomes an
    unrefined chore carrying exactly his words, after this long (TRQ11)."""
    questions_to: str = "dm"
    """`dm`, or `channel` for a private channel rite is invited to (TRQ8)."""
    channel: str = ""
    """That channel's id, when `questions_to` is `channel`."""


@dataclass
class CredentialsConfig:
    """Which keychain accounts THIS project's credentials live under
    (SPEC §10.2).

    `SERVICE_NAME = "rite"` keyed by a bare credential name was a FLAT
    namespace: every project on a machine shared one `jira_token`, so two
    projects could not hold two JIRA identities, and a client-privileged
    project's token was the same entry every other project read.
    `namespace` scopes the ACCOUNT per project — `<namespace>/jira_token`
    rather than `jira_token`.

    Only the namespace lives here. The secret stays in the OS keychain,
    so this file is committed on purpose: a fresh clone learns which
    credentials to set, and under what names, without a value ever being
    shared — the `.env.example` pattern. That is also why it is RECORDED
    rather than derived: a name nobody can rederive is a credential
    nobody can find again.

    Deliberately just the namespace, not a map of every composed account
    name. A stored map drifts from what the code composes; `rite
    credential list` prints the composed names instead, which is where
    someone actually reads them.
    """

    namespace: str = ""


@dataclass
class SpecConfig:
    """Where this project's design already lives.

    POINTERS, never copies. A spec is typically thousands of lines and it
    changes; copying it into `.rite/` would duplicate it and go stale
    silently the moment the original moved on. Workers are given the path
    and the citation convention and read what their ticket needs.
    """

    # Repo-relative files and/or directories, in the order a reader should
    # meet them. A directory is handed over whole — rite does not index it,
    # because the shapes people keep specs in are not rite's to define.
    paths: list[str] = field(default_factory=list)
    # How tickets cite the spec, in the project's own words — e.g.
    # "Decisions are cited as D-<number>; the register is in SPEC.md."
    # Free text because the convention already exists in projects that
    # have one, and inventing a rite-specific syntax would defeat the
    # point of pointing at what is already there.
    convention: str = ""

    # --- the spec digest: `rite spec index`, `slice` and `verify` ---
    # Addressable items beyond headings and decision rows: regular expressions
    # matched against each line outside fenced code, whose first capture group
    # is the item's id — `^- (REQ-\d+)\b` for numbered requirements. Opt-in,
    # because guessing at a spec's format is a project of its own and a wrong
    # guess makes a coverage map that quietly omits things.
    extra_units: list[str] = field(default_factory=list)
    # Hubs pinned into every slice. Pinning the top eight took median
    # transitive closure on rite's own spec from 70.5% of it to 10.3%.
    pin_count: int = 8
    # How many hops a slice follows: 1 or 2, never unbounded. Following every
    # reference loads 71.5% of rite's spec at the median. Go to 2 on evidence —
    # a rising insufficiency rate — not on taste.
    slice_depth: int = 1
    # Do not digest a spec whose p90 slice, pinned hubs included, is above this
    # share of it; Workers keep reading the whole spec instead.
    refuse_above: float = 0.25


@dataclass
class CoordinationConfig:
    """How this machine reaches the other Managers (SPEC §2.4, Phase 2).

    Nothing here is live in Phase 1: a single-Manager setup is trivially
    Owner and never runs an election (§2.4's own Phase 2 warning). The
    section exists so the consumers built on top of it have something to
    bind to.
    """

    # PRIORITY IS THE ORDER. §2.4: "Managers are listed in priority order";
    # the first active one is Owner. A bare list rather than a list of
    # objects — priority is the only attribute §2.4 gives them, and a
    # machine identity can be added later without breaking the file.
    managers: list[str] = field(default_factory=list)

    # WHAT each Manager is for (rite local, RL-T3), from its own
    # `manager_roles:` key. TWO keys in the file, and they are allowed to
    # disagree: a role for a manager nobody listed, or a listed manager with
    # no role. `rite doctor` names both — an invariant nothing checks is worse
    # than one stated where it is read. Empty on a file that declares nothing,
    # which is every project written before this existed.
    manager_roles: list[ManagerRole] = field(default_factory=list)

    # EXPLICIT, never inferred from `origin`. Inferring would couple
    # coordination to whichever remote happens to be `origin`, which is
    # precisely the implicit link that breaks on a fork — and the failure
    # would be a second machine coordinating through the wrong repo, which
    # looks like nothing at all until two Owners appear.
    remote: str = ""

    # The branch the state layer force-pushes (D-19: one repo, two
    # mechanisms — this branch for ephemeral state, the message log on
    # `main`). Named rather than hardcoded because it must be excludable
    # from a host's CI triggers (§2.4.2).
    state_branch: str = "state"

    # §2.4.1's 15 minutes. NOT `PoolConfig.lease_expiry_minutes`, which is
    # the LOCAL pool readiness lease — a different lease with a different
    # job, and the reason this one carries `owner_` in its name.
    owner_lease_minutes: int = 15

    # D-42's 60-second margin, as a key rather than a constant. The number
    # is a policy choice about how much clock drift a fleet tolerates, not
    # a property of the algorithm — the same reasoning that made the pool's
    # staleness rule configurable. NTP-synced clocks remain a documented
    # precondition; this narrows the split-brain window, it does not close
    # it.
    skew_tolerance_seconds: int = 60

    # Q9: may an UNATTENDED tick hand this Manager's tickets to its Workers?
    # Off by default, and the default is the decision rather than an absence
    # of one — distribution writes labels and comments on a shared board that
    # other people read, and the scheduler runs from cron with nobody
    # watching. Everything else a tick does writes only to the coordination
    # branch, which is rite talking to itself.
    #
    # A project that wants the loop turns it on and gets told what it does;
    # a project that has not decided gets told the arm is off, which is the
    # part that was missing — the switch existed as "nobody ever passed a
    # backend", which reads exactly like "there was nothing to hand out".
    assign_unattended: bool = False

    # How often, in minutes, an Owner's supervisor checks whether a Manager
    # it routed work to has DIED (DF2 decision 3, Robert 2026-09-27: every
    # 30 minutes, configurable). ⚠ A LATENCY bound, not a correctness one:
    # a secondary that FINISHES without replying is noticed at once, by the
    # event of its session ending, and a death is also noticed at once when
    # a wait would end on it. This sweep is the backstop for a death during
    # a long Owner session. Lowering it buys latency on the died case only,
    # and finding nothing costs nothing: it starts an Owner session only
    # when it has something new to say.
    sweep_minutes: int = 30


@dataclass
class ProjectConfig:
    ticket_backend: TicketBackendConfig = field(default_factory=TicketBackendConfig)
    credentials: CredentialsConfig = field(default_factory=CredentialsConfig)
    expertise: list[ExpertiseEntry] = field(default_factory=list)
    publish_gate: PublishGateConfig = field(default_factory=PublishGateConfig)
    scope: ScopeConfig = field(default_factory=ScopeConfig)
    heartbeat: HeartbeatConfig = field(default_factory=HeartbeatConfig)
    watchdog: WatchdogConfig = field(default_factory=WatchdogConfig)
    pool: PoolConfig = field(default_factory=PoolConfig)
    sandbox: SandboxConfig = field(default_factory=SandboxConfig)
    budget: BudgetConfig = field(default_factory=BudgetConfig)
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
    checkins: CheckinsConfig = field(default_factory=CheckinsConfig)
    refinement: RefinementConfig = field(default_factory=RefinementConfig)
    spec: SpecConfig = field(default_factory=SpecConfig)
    coordination: CoordinationConfig = field(default_factory=CoordinationConfig)
    slack: SlackConfig = field(default_factory=SlackConfig)
    github_app: GithubAppConfig = field(default_factory=GithubAppConfig)
    publish: PublishConfig = field(default_factory=PublishConfig)


@dataclass
class WorkerManifest:
    name: str
    manager: str = ""
    modules: list[str] = field(default_factory=list)
    claude_instructions: str = ""
    # The module's OWN instructions this Worker was told to follow (S23):
    # repo-relative paths to a module's `CLAUDE.md`, `AGENTS.md` or
    # `CONTRIBUTING.md`. Empty means the question was asked and declined, or
    # there was nothing to ask about — NOT "nobody has looked". A Worker
    # follows what it was told to follow, and that is a decision somebody
    # made rather than a file that happened to exist.
    follow_module_docs: list[str] = field(default_factory=list)
    # Level 2 (DECOMPOSER_DESIGN 1.7, RL-61): the model this Worker plans its
    # own APPROACH with, separate from the Claude model it implements with.
    # Empty means the type default, which depends on this Worker's engine
    # (`worker_decomposition_model`).
    decomposer: DecomposerConfig = field(default_factory=DecomposerConfig)
    # ── The engine a Worker runs (OL3) ───────────────────────────────────────
    # The same five keys a Manager declares, obeying the same rule
    # (`managers.engine_shape_problem`): a `local:<class>` Worker must say
    # endpoint, model and agent, because the class is a label and not a
    # configuration. They are per-WORKER rather than inherited from the Manager
    # so that one Manager can run a Claude Worker beside an Ollama one, which
    # is the v0.7.0 goal; a fleet that wants them all the same says the same
    # thing in each manifest.
    engine: str = CLAUDE
    endpoint: str = ""
    model: str = ""
    agent: str = ""
    context_window: int = 0
    """Tokens this Worker's model is served with, pinned into the model rite
    runs. 0 is undeclared, and undeclared REFUSES to start a local Worker for
    the reason `ManagerRole.context_window` gives: Ollama's own default is
    server-wide, cannot be read without loading the model, and "could not
    tell" read as "enough" is how a unit ran out of context mid-cycle."""

    @property
    def is_local(self) -> bool:
        return is_local_engine(self.engine)

    @property
    def needs_authored_plan(self) -> bool:
        """Whether rite must author this Worker's plan before it can work.

        🔴 **A CAPABILITY, and the one question a caller may ask about this.**
        Robert, 2026-10-08: the Manager/Worker interfaces must be provider- and
        platform-agnostic — gate on duty and capability, never on which engine
        or provider a unit runs. Callers that branched on `is_local` were
        branching on a provider, and every new provider meant another
        special-case in another file.

        It is derived from the engine HERE because the engine is the only
        declaration rite has today. That makes this property the single place a
        new provider touches: one that plans its own work says so and nothing
        else changes. Nothing outside this file should read `is_local` to decide
        how a Worker is driven.
        """
        return self.is_local


def worker_decomposition_model(worker: WorkerManifest) -> str:
    """The model a Worker plans its own approach with (Level 2, RL-61).

    Its own `decomposer.model` when set, else this Worker's TYPE default, which
    is the same split `decomposition_model_for` makes for a Manager: a local
    Worker plans with its own model (one GPU, nothing to gain from loading a
    second), a Claude one plans with Opus and implements with its Claude model.

    ⚠ Until OL3 this returned Opus unconditionally, which was right while a
    Worker was always a Claude sandbox and became wrong the moment one could
    declare `engine: local:small` — an Ollama Worker would have been told to
    plan with a model its endpoint has never heard of."""
    if worker.decomposer.model:
        return worker.decomposer.model
    if worker.is_local:
        return worker.model
    return CLAUDE_DECOMPOSER_DEFAULT


@dataclass
class RiteProject:
    """The fully resolved project configuration."""

    root: Path
    brief: ProjectBrief
    modules: list[Module] = field(default_factory=list)
    config: ProjectConfig = field(default_factory=ProjectConfig)
    workers: list[WorkerManifest] = field(default_factory=list)
