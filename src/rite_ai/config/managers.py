"""What a Manager is for, and what does its work (RL-T3, design §3).

Two independent axes, never one `tier` field (RL-1): an **engine** — what does
the work — and a set of **duties** — what the Manager is for. A tier conflates
them, and the project manager is the proof: it has a purpose and no model.

**The duty vocabulary is closed** (RL-3). rite enforces gates keyed on duties,
and a gate cannot key on a string a project invented: a misspelt `plan-reveiw`
in an open set is a Manager that silently skips plan review.

## Two keys, and the disagreement they allow is reported rather than prevented

`coordination.managers` shipped in v0.4.0 as a list of names in priority order,
and thirty-nine places read it that way. Roles live beside it under
`manager_roles:`, parsed independently.

One key was tried first, on the reasoning that two keys can disagree — a role
naming a manager that is not listed, or a listed manager with no role. The
round-trip gate refused it, and it was right to: a schema where one key must
produce two fields that always agree is a schema with an invariant nothing
checks. So the disagreement is allowed to exist on disk and named by
`configuration_problems` below, which is where a configuration that parses and
cannot work belongs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

CLAUDE = "claude"
HUMAN = "human"
_LOCAL = re.compile(r"^local:([a-z0-9][a-z0-9-]*)$")

# Closed, and rite-defined (RL-3). Order is the pipeline's order, because that
# is how a reader makes sense of the list.
DECIDE = "decide"
BOARD = "board"
# Held by the Owner (RL-52). A named duty rather than an emergent behaviour, so
# the single-router property is one rite enforces: a question relayed by the
# Owner is not relayed onward, and that rule now has a holder.
ROUTE = "route"
SPEC = "spec"
PLAN_REVIEW = "plan-review"
DECOMPOSE = "decompose"
STEP_REVIEW = "step-review"
INTEGRATE = "integrate"
EXECUTE = "execute"

DUTIES: tuple[str, ...] = (
    DECIDE,
    BOARD,
    ROUTE,
    SPEC,
    PLAN_REVIEW,
    DECOMPOSE,
    STEP_REVIEW,
    INTEGRATE,
    EXECUTE,
)

# Presets are named defaults, not types (RL-2). A project may declare any
# combination, and a new combination needs no code here.
PRESETS: dict[str, tuple[str, ...]] = {
    "lead": (DECIDE, BOARD, ROUTE, SPEC, PLAN_REVIEW, INTEGRATE, EXECUTE),
    "planner": (DECOMPOSE, STEP_REVIEW, EXECUTE),
    "executor": (EXECUTE,),
    "pm": (DECIDE, BOARD, ROUTE),
}

# Only a `local:*` engine may carry these, and it must carry all three: the
# class label is a name, not a configuration (design §3.1).
_LOCAL_ONLY = ("endpoint", "model", "agent")
_ENTRY_KEYS = frozenset(
    {
        "name",
        "engine",
        "duties",
        "preset",
        "credential",
        "context_window",
        "decomposer",
        *_LOCAL_ONLY,
    }
)

# A Claude Manager may name its model (`claude --model <name>`). A CLAUDE
# model, not merely a well-formed word: `qwen3:8b` fits any safe pattern and
# would reach `claude`, which cannot run it. So an alias, or a `claude-` id
# (with Claude Code's `[1m]` long-context suffix). It also travels on tmux's
# argv, which this closed spelling keeps safe.
_CLAUDE_ALIASES = frozenset({"sonnet", "opus", "haiku", "fable", "opusplan", "default"})
_CLAUDE_MODEL = re.compile(r"^claude-[a-z0-9][a-z0-9.-]{0,60}(\[1m\])?$")

# The Level-2 decomposition model a Claude executing unit plans with, when it
# names none (RL-61, DECOMPOSER_DESIGN 0/1.7). An alias, so it travels safely
# on tmux's argv like any other Claude model name.
CLAUDE_DECOMPOSER_DEFAULT = "opus"


@dataclass(frozen=True)
class DecomposerConfig:
    """The ONE new attribute the per-worker (Level-2) placement forces
    (DECOMPOSER_DESIGN 1.7, RL-61). It names the model an executing unit plans
    its own APPROACH with — separate from the model it implements with.

    Empty `model` means "use this unit's type default": Opus for a Claude unit,
    the unit's own model for a local one (`decomposition_model_for`). The common
    case stays empty, so nobody maintains a model name in two places — the S35
    shape this is shaped to avoid."""

    model: str = ""


@dataclass(frozen=True)
class ManagerRole:
    """One Manager's declared role. `duties` empty means UNDECLARED, which is
    not the same as holding none — see `effective_duties`."""

    name: str
    engine: str = CLAUDE
    duties: tuple[str, ...] = ()
    preset: str = ""
    endpoint: str = ""
    model: str = ""
    agent: str = ""
    context_window: int = 0
    """Tokens a local Manager's model is served with, pinned into the model
    rite runs (`local.context_window.pin_window`). 0 means undeclared, and an
    undeclared window REFUSES to start a Goose Manager: Ollama's own default
    is server-wide and cannot be read without loading the model, and "could
    not tell" read as "enough" is how a Manager ran out of context mid-cycle
    and stopped replying."""
    # A KEY NAME, never a secret (SPEC §10). A local endpoint usually needs
    # none; one that does names a credential the keychain holds.
    credential: str = ""
    # Level 2 (DECOMPOSER_DESIGN 1.7): the model this unit plans its own
    # approach with, separate from `model` which it implements with. Default
    # empty — resolved by `decomposition_model_for`.
    decomposer: DecomposerConfig = field(default_factory=DecomposerConfig)

    @property
    def is_local(self) -> bool:
        return is_local_engine(self.engine)

    @property
    def local_class(self) -> str:
        m = _LOCAL.match(self.engine)
        return m.group(1) if m else ""


@dataclass
class ParsedManagers:
    names: list[str] = field(default_factory=list)
    roles: list[ManagerRole] = field(default_factory=list)
    error: str = ""


def effective_duties(role: ManagerRole, declared: int) -> frozenset[str]:
    """What this Manager actually holds.

    A lone Manager that declares nothing holds every duty — that is today's
    single session doing everything, and it keeps working unchanged (design
    §3.3). Once a project declares a second Manager, an undeclared one is a
    parse error rather than a guess, so this is never asked of one.
    """
    if role.duties:
        return frozenset(role.duties)
    if role.preset:
        return frozenset(PRESETS.get(role.preset, ()))
    return frozenset(DUTIES) if declared <= 1 else frozenset()


def decomposition_model_for(role: ManagerRole) -> str:
    """The model this executing unit plans its APPROACH with (Level 2, RL-61).

    The unit's own `decomposer.model` when it names one; otherwise the type
    default — the unit's own model for a local one (one GPU, nothing to gain
    from a second model), Opus for a Claude one (plan with Opus, implement with
    its Claude model). Computed, never stored, so the common case stays empty
    and no model name lives in two files (DECOMPOSER_DESIGN 1.7 reason 3)."""
    if role.decomposer.model:
        return role.decomposer.model
    if role.is_local:
        return role.model
    return CLAUDE_DECOMPOSER_DEFAULT


def model_identity(model: str) -> str:
    """The model two units SHARE, ignoring rite's own window pin (RL-6).

    ⚠ **`rite-ctx32768-qwen3-32b` and `qwen3:32b` are the same model.** rite
    derives a pinned-window copy of a model and runs that
    (`local.context_window.derived_name`), so the same weights appear under two
    names, and a check comparing names would read them as two engines. They are
    not: the derived model is the base model with a bigger window.

    Compared in the DERIVED name's own normalised space, because the derivation
    is lossy the other way — `qwen3:32b` becomes `qwen3-32b`, and `-` cannot be
    turned back into `:` without guessing which it was.
    """
    import re

    base = model.strip().lower()
    pinned = re.match(r"^rite-ctx\d+-(.+)$", base)
    if pinned:
        base = pinned.group(1)
    return re.sub(r"[^a-z0-9._-]+", "-", base).strip("-")


def engine_identity(role: ManagerRole) -> tuple[str, str]:
    """What makes two Managers the same engine for RL-6's independence check.

    ⚠ **The loophole this closes.** The check was `r.engine != decomposer.engine`
    — a comparison of CLASS LABELS. `local:large` and `local:small` are two
    strings and may be one model, so a project could satisfy plan review with a
    reviewer that shares every blind spot of the plan's author, which is the one
    thing plan review exists to prevent. Robert ruled the fix in on 2026-10-02,
    config-breaking accepted.

    **The model, not the endpoint.** What shares a blind spot is the weights. Two
    local Managers both running `qwen3.8:latest` are not independent because they
    are served on different ports.

    **Claude stays model-independent**, so two `claude` Managers remain
    non-independent exactly as before — that was already the behaviour and
    nothing here is an argument for changing it.
    """
    if role.is_local:
        return ("local", model_identity(role.model))
    return (role.engine, "")


def is_local_engine(engine: str) -> bool:
    """Whether `engine` is a `local:<class>` one — the single spelling of the
    BOOLEAN test. A Worker asks this too now (OL3), and `local:` matched by a
    second regex somewhere else is how the two would come to disagree.

    `ManagerRole.local_class` still matches directly, because it needs the
    capture group rather than the answer; that is the one remaining use and it
    is not a copy of this."""
    return bool(_LOCAL.match(engine))


def _engine_error(engine: str) -> str:
    if engine in (CLAUDE, HUMAN) or is_local_engine(engine):
        return ""
    return (
        f"engine {engine!r} is not one rite knows — use 'claude', 'human', or "
        "'local:<class>' where <class> is a label such as 'large' or 'small'"
    )


def _entry_error(raw: dict, name: str) -> str:
    # BEFORE the unknown-key check, which would otherwise answer a secret with
    # "unknown key api_key" — true, unhelpful, and it leaves someone looking
    # for the right spelling of the wrong idea.
    for secret in ("api_key", "apikey", "token", "password", "secret"):
        if secret in raw:
            return (
                f"manager {name}: no secret goes in config (SPEC §10) — name a "
                "credential with 'credential:' and keep the value in the keychain"
            )
    unknown = sorted(set(raw) - _ENTRY_KEYS)
    if unknown:
        return (
            f"manager {name or '?'}: unknown key(s) {', '.join(unknown)} — "
            f"known keys are {', '.join(sorted(_ENTRY_KEYS))}"
        )
    for key in _ENTRY_KEYS - {"context_window", "decomposer"}:
        if key in raw and not isinstance(raw[key], (str, list)):
            return f"manager {name or '?'}: {key} must be text"
    bad_window = window_problem(raw, f"manager {name}")
    if bad_window:
        return bad_window
    preset = raw.get("preset", "")
    if preset and preset not in PRESETS:
        return (
            f"manager {name}: preset {preset!r} is not one rite ships — "
            f"{', '.join(sorted(PRESETS))}"
        )
    duties = raw.get("duties", [])
    if duties and not isinstance(duties, list):
        return f"manager {name}: duties must be a list"
    for duty in duties or ():
        if duty not in DUTIES:
            # A misspelt duty in a closed vocabulary is a Manager that skips a
            # gate, so it is refused rather than dropped.
            return (
                f"manager {name}: {duty!r} is not a duty rite enforces — "
                f"{', '.join(DUTIES)}"
            )
    shape = engine_shape_problem(raw, f"manager {name}")
    if shape:
        return shape
    return ""


def _decomposer_error(raw: dict, subject: str, engine: str) -> str:
    """Why this entry's `decomposer:` cannot be read, or "" (RL-61).

    The ONE Level-2 key. A mapping with just `model`: a Claude unit's model is
    validated as a Claude name (it reaches `claude --model`), a local unit's is
    free (it names an Ollama tag like `qwen3.8:latest`). Absent is the common
    case and means "use the type default"."""
    if "decomposer" not in raw:
        return ""
    body = raw["decomposer"]
    if not isinstance(body, dict):
        return (
            f"{subject}: decomposer must be a mapping with a 'model', e.g. "
            "'decomposer: {model: opus}'"
        )
    unknown = sorted(set(body) - {"model"})
    if unknown:
        return (
            f"{subject}: decomposer knows only 'model', not {', '.join(unknown)}"
        )
    model = body.get("model", "")
    if not isinstance(model, str) or not model.strip():
        return f"{subject}: decomposer.model must be a non-empty model name"
    if engine == CLAUDE:
        problem = claude_model_problem(model)
        if problem:
            return f"{subject}: decomposer.model {model!r} {problem}"
    return ""


def claude_model_problem(model: str) -> str:
    """Why `model` cannot be passed to `claude --model`, or ""."""
    if model in _CLAUDE_ALIASES or _CLAUDE_MODEL.match(model):
        return ""
    return (
        "is not a Claude model name (an alias such as 'sonnet', or an id such "
        "as 'claude-opus-5-5'). A local model goes on a 'local:<class>' unit "
        "with its endpoint"
    )


def engine_shape_problem(raw: dict, subject: str) -> str:
    """Why this unit's engine and its engine-only keys cannot be read, or "".

    ⚠ **Shared by a Manager and a Worker on purpose (OL3).** A Worker gained
    `engine`/`endpoint`/`model`/`agent`/`context_window` so a project can run
    an Ollama Worker beside a Claude one under one Manager, and the rule those
    keys obey must not come to differ by which file they were written in: a
    second copy of "a local engine must say all three" is a second copy that
    drifts. `subject` is the whole prefix a message starts with — `"manager
    lead"` or `"worker w1"` — so only the noun changes.

    The window is deliberately NOT checked here. `_entry_error` checks it
    early, before `preset` and `duties`, and folding it in would reorder which
    problem a half-wrong entry reports first. Callers run `window_problem`
    themselves, in whatever order suits them.
    """
    engine = raw.get("engine", CLAUDE)
    bad = _engine_error(engine)
    if bad:
        return f"{subject}: {bad}"
    local = is_local_engine(engine)
    missing = [k for k in _LOCAL_ONLY if local and not raw.get(k)]
    if missing:
        return (
            f"{subject}: a {engine} engine must also say "
            f"{', '.join(missing)} — the class is a label, not a configuration"
        )
    claude = engine == CLAUDE
    stray = [
        k
        for k in _LOCAL_ONLY
        if not local and raw.get(k) and not (claude and k == "model")
    ]
    if claude and raw.get("model"):
        problem = claude_model_problem(str(raw["model"]))
        if problem:
            return f"{subject}: model {raw['model']!r} {problem}"
    if stray:
        return (
            f"{subject}: {', '.join(stray)} means nothing on a "
            f"{engine!r} engine — only 'local:<class>' runs a model rite drives"
        )
    return _decomposer_error(raw, subject, engine)


def window_problem(raw: dict, subject: str) -> str:
    """Why this unit's `context_window` cannot be read, or "". Public because a
    Worker declares one too (OL3) and must refuse it on the same terms."""
    if "context_window" not in raw:
        return ""
    window = raw["context_window"]
    engine = str(raw.get("engine", CLAUDE))
    if not is_local_engine(engine):
        return (
            f"{subject}: context_window means nothing on a {engine!r} "
            "engine — only 'local:<class>' runs a model whose window rite sets"
        )
    if isinstance(window, bool) or not isinstance(window, int):
        return f"{subject}: context_window must be a whole number of tokens"
    from rite_ai.local.engine_probe import MINIMUM_CONTEXT_WINDOW

    if window < MINIMUM_CONTEXT_WINDOW:
        return (
            f"{subject}: context_window {window} is below "
            f"{MINIMUM_CONTEXT_WINDOW}, the smallest measured to work — an "
            "agent's own system prompt and tool schemas do not fit below it"
        )
    return ""


def parse_managers(raw: object) -> ParsedManagers:
    """`coordination.managers`, in either shape, or an error naming the entry.

    Bare names and mappings may be mixed: a project that adds one local tier
    should not have to rewrite the managers it already had.
    """
    out = ParsedManagers()
    if raw in (None, []):
        return out
    if not isinstance(raw, list):
        out.error = "coordination.managers must be a list"
        return out
    seen: set[str] = set()
    for item in raw:
        if isinstance(item, str):
            name = item.strip()
            if not name:
                out.error = "coordination.managers has an entry with no name"
                return out
            role = ManagerRole(name=name)
        elif isinstance(item, dict):
            name = str(item.get("name", "")).strip()
            if not name:
                out.error = "coordination.managers has an entry with no name"
                return out
            problem = _entry_error(item, name)
            if problem:
                out.error = problem
                return out
            decomposer_raw = item.get("decomposer") or {}
            role = ManagerRole(
                name=name,
                engine=str(item.get("engine", CLAUDE)),
                duties=tuple(item.get("duties", ()) or ()),
                preset=str(item.get("preset", "")),
                endpoint=str(item.get("endpoint", "")),
                model=str(item.get("model", "")),
                agent=str(item.get("agent", "")),
                credential=str(item.get("credential", "")),
                context_window=int(item.get("context_window", 0) or 0),
                decomposer=DecomposerConfig(model=str(decomposer_raw.get("model", ""))),
            )
        else:
            out.error = (
                "coordination.managers takes a name or a mapping, not "
                f"{type(item).__name__}"
            )
            return out
        from rite_ai.managers import manager_name_problem

        # Refused where a Manager is DECLARED, so `rite doctor` and every
        # command that reads the config say so, rather than the first write
        # into `.rite/user/` finding out (C30).
        bad_name = manager_name_problem(role.name)
        if bad_name:
            out.error = f"coordination.managers: {bad_name}"
            return out
        if role.name in seen:
            # Priority is the order of this list, so a duplicate name makes
            # priority ambiguous — the same reason Phase 2 already refuses one.
            out.error = f"coordination.managers lists {role.name!r} twice"
            return out
        folded = role.name.casefold()
        clash = next((n for n in seen if n.casefold() == folded), "")
        if clash:
            # ⚠ Two names differing only in case are ONE directory on a
            # case-insensitive volume, macOS's default, so these two
            # Managers would share every file under .rite/ (C30's class).
            out.error = (
                f"coordination.managers lists {clash!r} and {role.name!r}, "
                "which differ only in case — on a case-insensitive disk "
                "(macOS's default) they are the same directory, so the two "
                "Managers would overwrite each other's state. Rename one."
            )
            return out
        seen.add(role.name)
        out.names.append(role.name)
        out.roles.append(role)
    # RL-34 as written says every Manager must declare once a project has two.
    # Taken literally that breaks every project already on v0.4.0, which shipped
    # `managers:` as bare names and could perfectly well list three.
    #
    # The rule's REASON is that routing by duty cannot guess — and that only
    # bites once there is something to route between. This is the design's own
    # §4 argument ("a project with one Manager keeps today's behaviour, because
    # there is nothing to route between"), applied to the case it missed: a
    # project where NO Manager declares anything has nothing to route between
    # either, whatever the count. So declaration is required from the moment
    # any Manager declares an engine or a duty, and not before.
    tiers_exist = any(r.duties or r.preset or r.engine != CLAUDE for r in out.roles)
    if tiers_exist:
        undeclared = [r.name for r in out.roles if not r.duties and not r.preset]
        if undeclared:
            out.error = (
                f"manager(s) {', '.join(undeclared)} declare no preset and no "
                "duties, and this project has managers that do. Once they "
                "differ, routing cannot guess what a manager is for: give each "
                "one a preset or a duties list"
            )
    return out


@dataclass
class Declaration:
    """The two keys after declaring one Manager, or why it was refused."""

    names: list[str] = field(default_factory=list)
    roles: list[ManagerRole] = field(default_factory=list)
    error: str = ""
    updated: bool = False
    """True when an already-listed Manager's declaration was changed rather
    than a new one added — see `declare_manager`."""


def declare_manager(
    names: list[str],
    roles: list[ManagerRole],
    name: str,
    *,
    preset: str = "",
    duties: tuple[str, ...] = (),
    engine: str = CLAUDE,
    model: str = "",
    endpoint: str = "",
    agent: str = "",
    credential: str = "",
    context_window: int = 0,
) -> Declaration:
    """Declare one Manager, for `rite add manager` (S16).

    ⚠ **BOTH KEYS, because the file has two and they are allowed to
    disagree.** `coordination.managers` is the priority order thirty-nine
    call sites read; `manager_roles` is what each Manager is FOR. A writer
    that touched one would produce exactly the disagreement
    `configuration_problems` exists to report — a role for a Manager nobody
    listed, or a listed Manager with no role — and it would be rite that
    wrote it. So this returns both, and its caller writes both.

    ⚠ **VALIDATION IS THE PARSER'S, not a second copy here.** The entry is
    built, spliced into the list as it stands, and run back through
    `parse_managers`. Whatever that refuses, this refuses, with the same
    words — so `rite add manager` cannot write a config.yaml that the next
    command to read it rejects, and a rule added to the parser later covers
    this writer for free. The alternative, checking the arguments here, is
    the second copy of a security-relevant checklist that falls behind.

    ⚠ **A new Manager goes LAST, and the order is priority** (§2.4: the
    first active Manager is Owner). Appending is the only position that
    cannot change who the Owner is on a project that already runs.

    ⚠ **An already-listed name is UPDATED, not refused**, when something to
    declare is given. That is not "add" being loose: `parse_managers`
    requires every Manager to declare a preset or duties as soon as ONE
    does, so declaring a second Manager on a project whose first is a bare
    name fails on the FIRST one. Without a way to declare the Manager that
    already exists, the only route left is hand-editing config.yaml, which
    is the thing this command is for. Refusing a bare `add` of a name that
    is already there is still right, and it says which flags to give.
    """
    entry: dict[str, object] = {"name": name}
    if engine != CLAUDE:
        entry["engine"] = engine
    if preset:
        entry["preset"] = preset
    if duties:
        entry["duties"] = list(duties)
    for key, value in (
        ("endpoint", endpoint),
        ("model", model),
        ("agent", agent),
        ("credential", credential),
        ("context_window", context_window),
    ):
        if value:
            entry[key] = value

    at = next((i for i, r in enumerate(roles) if r.name == name), None)
    declared_something = len(entry) > 1
    if at is not None and not declared_something:
        return Declaration(
            error=(
                f"manager {name!r} is already declared. To change what it is "
                "for, give --preset or --duties; to add a different Manager, "
                "give a different name"
            )
        )

    raw: list[object] = [to_yaml_entry(r) for r in roles]
    if at is None:
        raw.append(entry)
    else:
        raw[at] = entry

    parsed = parse_managers(raw)
    if parsed.error:
        return Declaration(error=parsed.error)

    # The name list is its own key and may already hold names no role does
    # (and the other way round) — so it is extended, never rebuilt from the
    # roles. Rebuilding it would silently drop a listed Manager that has no
    # role, which is a configuration this file is allowed to hold.
    out_names = list(names) if name in names else [*names, name]
    return Declaration(
        names=out_names, roles=parsed.roles, updated=at is not None or name in names
    )


def to_yaml_entry(role: ManagerRole) -> str | dict:
    """A role that says nothing but its name is written back as a bare name, so
    a file written before roles existed round-trips byte-identically.

    Derived from the role being empty rather than remembered from the parse: a
    "this came from a string" flag is serialisation state living in config, and
    the round-trip gate is right to refuse it — it cannot survive a rewrite
    that did not start from a file.
    """
    if role == ManagerRole(name=role.name):
        return role.name
    entry: dict[str, object] = {"name": role.name}
    if role.engine != CLAUDE:
        entry["engine"] = role.engine
    if role.preset:
        entry["preset"] = role.preset
    if role.duties:
        entry["duties"] = list(role.duties)
    for key in (*_LOCAL_ONLY, "credential", "context_window"):
        value = getattr(role, key)
        if value:
            entry[key] = value
    if role.decomposer.model:
        # The one Level-2 attribute (RL-61). Absent when the type default is
        # taken, so the common case stays a bare name or a plain entry.
        entry["decomposer"] = {"model": role.decomposer.model}
    return entry


def routing_owner(roles: list[ManagerRole]) -> str:
    """The Manager that talks to the User and routes to the others, or "".

    ⚠ **The one Manager holding `route` in this root, and nothing else.**
    `route` is the Owner's duty (RL-52), and the multi-machine election already
    requires it of any Manager standing for Owner (`owner_capable`). On one
    machine with no `coordination.remote` no election can run, so the answer
    has to be deterministic, and a config-declared duty is: local, committed,
    and readable by every process without a lease.

    **"" when it is not exactly one**, never a guess. With two holders nothing
    here picks between them — the election would, and it is not running — and
    choosing by list order would hand Slack and routing to whichever Manager a
    reorder put first. Every caller treats "" as "nobody routes and nobody
    reads Slack", which fails closed. A lone Manager holds every duty
    (`effective_duties`), so a one-Manager project is its own Owner unchanged.
    """
    holders = [r.name for r in roles if ROUTE in effective_duties(r, len(roles))]
    return holders[0] if len(holders) == 1 else ""


def shares_one_root(remote: str) -> bool:
    """Whether a project's Managers must all be in this root: no `remote`.

    `coordination.managers` doubles as the multi-machine election's priority
    list. Without a remote there is nothing to coordinate through, so every
    listed Manager runs here. With one, they may be on other machines and the
    election, not `routing_owner`, decides who the Owner is.
    """
    return not remote


def configuration_problems(
    roles: list[ManagerRole],
    names: list[str] | None = None,
    *,
    one_root: bool = False,
) -> list[str]:
    """What `rite doctor` reports about a set of Managers (RL-T3).

    Each is a configuration that parses and cannot work, which is exactly the
    class doctor exists for: a parse error stops a command, and these stop a
    pipeline at the moment it is first needed, which may be days later.
    """
    problems_first: list[str] = []
    if names is not None:
        # `managers` and `manager_roles` are separate keys, so they can
        # disagree. Parse cannot refuse that without making the two fields one,
        # so it is reported here — a role for a manager nobody listed will
        # never be routed to, and a listed manager with no role, once tiers
        # exist, is a routing decision nobody made.
        listed = set(names)
        for role in roles:
            if role.name not in listed:
                problems_first.append(
                    f"manager_roles names {role.name!r}, which is not in "
                    "coordination.managers — nothing will ever route to it"
                )
        if any(r.duties or r.preset or r.engine != CLAUDE for r in roles):
            declared = {r.name for r in roles if r.duties or r.preset}
            missing = [n for n in names if n not in declared]
            if missing:
                problems_first.append(
                    f"manager(s) {', '.join(missing)} have no role, and this "
                    "project has managers that do — routing cannot guess what "
                    "they are for"
                )

    if len(roles) <= 1:
        # A lone Manager holds every duty and has nobody to gate against. Every
        # rule below is about a division of labour that does not exist yet.
        return problems_first
    problems: list[str] = list(problems_first)
    held = {r.name: effective_duties(r, len(roles)) for r in roles}

    routers = [r.name for r in roles if ROUTE in held[r.name]]
    # ⚠ ONLY WHEN THEY SHARE THIS ROOT. `coordination.managers` is also the
    # multi-machine election's priority list, where each name may run on a
    # different machine and the lease picks the Owner. With no `remote` there
    # is nowhere else for them to be, so they share this root and nothing
    # elects — which is the case this rule is for. The caller says which.
    if one_root and len(routers) != 1:
        # MULTI-MANAGER, one root: exactly one Manager talks to the User and
        # routes to the rest (`routing_owner`). Zero or several means nobody
        # does, and that is said here rather than discovered as silence.
        who = ", ".join(routers) if routers else "none of them"
        problems.append(
            f"{len(roles)} Managers share this root and {len(routers)} hold "
            f"'route' ({who}) — exactly one must, because the one holding it "
            "is the only Manager that reads Slack and the only one that may "
            "route work to the others. Until then none of them does either"
        )

    decomposers = [r for r in roles if DECOMPOSE in held[r.name]]
    reviewers = [r for r in roles if PLAN_REVIEW in held[r.name]]
    for decomposer in decomposers:
        mine = engine_identity(decomposer)
        independent = [
            r
            for r in reviewers
            if r.name != decomposer.name and engine_identity(r) != mine
        ]
        if not independent:
            # RL-6: the decomposer reviewing its own plan's output shares every
            # blind spot of the plan, so a wrong slicing passes every check it
            # wrote. A different MODEL is what makes the gate a gate — the class
            # label is not, which is the loophole `engine_identity` closes.
            problems.append(
                f"manager {decomposer.name} decomposes, and no other manager on "
                "a different model holds plan-review — its decompositions would "
                "be approved by the model that wrote them, which is the one "
                "thing plan review exists to prevent. Two 'local:' classes "
                "serving the same model are the same model, however they are "
                "labelled"
            )

    # ⚠ **A local `integrate` holder is NOT refused any more** (Robert,
    # 2026-10-02, OL8). RL-11 refused it because "SPEC §5.1.1 forbids rite's
    # code a push; the harness is rite's code" — written 2026-09-19, while PB1
    # gave `rite deliver` a push on 2026-09-29 (`4242d48`), which §5.1.1 now
    # states and `tests/test_blast_radius.py` allows by name (`ALLOWED_GIT`).
    # The premise expired ten days before anyone looked at it.
    #
    # **No engine pushes by itself, and that is still true.** The holder — on
    # any engine — posts the same two-value request a Claude Manager posts
    # (`publishing/requests.py`: it "cannot even commit, so it ASKS"), and rite
    # validates it and performs the push on the host (`publishing/deliver.py`:
    # "done here by rite, not by a model"). Both files are engine-agnostic;
    # neither contains an engine check. §5.1.1's bounds therefore apply
    # unchanged, because they live in the file that does the pushing: draft
    # only, a repository the operator owns, its default branch, the publish
    # gate passed on exactly those commits, never `--force`.
    #
    # ⚠ **Deliberately NOT the other route.** A sandboxed Manager's own
    # repo-scoped token can push (C6/C26), and that path is gated only by the
    # `pre-push` hook, which a global `core.hooksPath` stops git reading. The
    # request path gates by construction, which is why it is the one.
    #
    # RL-11's SECOND reason is untouched and is what the request path satisfies:
    # the terminating check belongs before anything leaves the machine.
    #
    # What the harness cannot check is whether the work is finished — §5.1.1
    # bounds the damage of a wrong verdict, never its quality. That is why
    # plan-review independence (RL-6, just above) compares the MODEL rather than
    # the engine label: a verdict is worth acting on only if its reviewer was
    # genuinely another model. Analysis:
    # `docs/design/OL_WHY_A_LOCAL_MANAGER_CANNOT_PUSH.md`.

    eligible = [
        r for r in roles if r.engine != HUMAN and {DECIDE, BOARD, ROUTE} <= held[r.name]
    ]
    if not eligible:
        # RL-32 with RL-52: the Owner runs unattended, so it cannot be a
        # person; it decides and owns the board by definition; and routing is
        # a duty it holds rather than a behaviour it falls into.
        problems.append(
            "no manager can be Owner: that needs decide, board and route on an "
            "engine that runs unattended. A 'human' manager can answer a "
            "question routed to it, but cannot hold the lease that makes it "
            "the router"
        )
    return problems


def window_undeclared(role) -> bool:
    """A local Manager that declares no `context_window`, which `rite start`
    refuses and `rite doctor` warns about (S33).

    ⚠ **Every local agent, not Goose alone.** The rule came with `f340103`
    gated on `agent == "goose"`, the only agent then; nothing about it is
    Goose's. A model served by an endpoint takes that server's default window
    unless rite sets one, the default cannot be read before the model loads,
    and a prompt over it is cut from the front with no error (S34: 4096 on
    this Mac, `truncating input prompt limit=2050 prompt=5583`). Gated on the
    agent, the next local agent would have started on exactly that default,
    silently. The one predicate `rite start`, `rite doctor` and
    `effective_model` all ask, so they cannot disagree about it."""
    return bool(getattr(role, "is_local", False)) and not getattr(
        role, "context_window", 0
    )


def effective_model(role: ManagerRole) -> str:
    """What model a Manager runs, and where that comes from, in one line.

    Track MS's rule, "inspectable, never silent": `rite doctor` and `rite
    start` both print this, from this one function, so the two cannot
    disagree about what a Manager runs.
    """
    if role.engine == HUMAN:
        return f"manager {role.name}: a person, no model"
    if role.is_local:
        # ⚠ S35, as a NOTE and not a refusal. The window is pinned into the
        # model, so the server serves it to any client; an agent rite has no
        # env mapping for is simply not TOLD the number. S33 owns the refusal,
        # and it refuses the genuinely unenforceable case: no window declared.
        from rite_ai.local.enforcement import for_agent

        window = (
            "NO context_window declared, so `rite start` refuses it"
            if not role.context_window
            else f"a {role.context_window}-token window pinned into the model"
            if for_agent(role.agent) is not None
            else (
                f"a {role.context_window}-token window pinned into the model, "
                f"which agent {role.agent!r} is not told"
            )
        )
        return (
            f"manager {role.name}: {role.model} at {role.endpoint}, "
            f"{window} (declared in its role)"
        )
    if role.model:
        return f"manager {role.name}: {role.model} (declared in its role)"
    return (
        f"manager {role.name}: Claude Code's default model for this login "
        "(none declared in its role)"
    )
