from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


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


@dataclass
class HeartbeatConfig:
    interval_minutes: int = 10
    stall_threshold: int = 3


@dataclass
class PublishGateConfig:
    scan_patterns: list[ScanPattern] = field(default_factory=list)
    gitleaks_config: str = ".rite/gitleaks.toml"


@dataclass
class WatchdogConfig:
    interval_minutes: int = 5


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


@dataclass
class ScheduleConfig:
    timezone: str = ""  # required once any window exists — no default (D-48)
    windows: list[ScheduleWindow] = field(default_factory=list)


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


@dataclass
class ProjectConfig:
    ticket_backend: TicketBackendConfig = field(default_factory=TicketBackendConfig)
    credentials: CredentialsConfig = field(default_factory=CredentialsConfig)
    expertise: list[ExpertiseEntry] = field(default_factory=list)
    publish_gate: PublishGateConfig = field(default_factory=PublishGateConfig)
    heartbeat: HeartbeatConfig = field(default_factory=HeartbeatConfig)
    watchdog: WatchdogConfig = field(default_factory=WatchdogConfig)
    pool: PoolConfig = field(default_factory=PoolConfig)
    sandbox: SandboxConfig = field(default_factory=SandboxConfig)
    budget: BudgetConfig = field(default_factory=BudgetConfig)
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
    spec: SpecConfig = field(default_factory=SpecConfig)


@dataclass
class WorkerManifest:
    name: str
    manager: str = ""
    modules: list[str] = field(default_factory=list)
    claude_instructions: str = ""


@dataclass
class RiteProject:
    """The fully resolved project configuration."""

    root: Path
    brief: ProjectBrief
    modules: list[Module] = field(default_factory=list)
    config: ProjectConfig = field(default_factory=ProjectConfig)
    workers: list[WorkerManifest] = field(default_factory=list)
