"""What a Manager is for, and what does its work (RL-T3, design §3).

Two independent axes, never one `tier` field (RL-1): an **engine** — what does
the work — and a set of **duties** — what the Manager is for. A tier conflates
them, and the project manager is the proof: it has a purpose and no model.

**The duty vocabulary is closed** (RL-3). rite enforces gates keyed on duties,
and a gate cannot key on a string a project invented: a misspelt `plan-reveiw`
in an open set is a Manager that silently skips plan review.

## The file keeps one key; the model keeps the old type

`coordination.managers` shipped in v0.4.0 as a list of names in priority order,
and thirty-nine places read it that way. So an entry here may be **either** a
bare name or a mapping, and parsing produces both a `list[str]` of names — the
same list those callers already have — and the roles beside it.

One key rather than a second `manager_roles:` block, because two keys can
disagree: a role naming a manager that is not listed, or a listed manager with
no role. Neither is expressible here.
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
    {"name", "engine", "duties", "preset", "credential", *_LOCAL_ONLY}
)


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
    # A KEY NAME, never a secret (SPEC §10). A local endpoint usually needs
    # none; one that does names a credential the keychain holds.
    credential: str = ""

    @property
    def is_local(self) -> bool:
        return bool(_LOCAL.match(self.engine))

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


def _engine_error(engine: str) -> str:
    if engine in (CLAUDE, HUMAN) or _LOCAL.match(engine):
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
    for key in _ENTRY_KEYS:
        if key in raw and not isinstance(raw[key], (str, list)):
            return f"manager {name or '?'}: {key} must be text"
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
    engine = raw.get("engine", CLAUDE)
    bad = _engine_error(engine)
    if bad:
        return f"manager {name}: {bad}"
    local = bool(_LOCAL.match(engine))
    missing = [k for k in _LOCAL_ONLY if local and not raw.get(k)]
    if missing:
        return (
            f"manager {name}: a {engine} engine must also say "
            f"{', '.join(missing)} — the class is a label, not a configuration"
        )
    stray = [k for k in _LOCAL_ONLY if not local and raw.get(k)]
    if stray:
        return (
            f"manager {name}: {', '.join(stray)} means nothing on a "
            f"{engine!r} engine — only 'local:<class>' runs a model rite drives"
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
            role = ManagerRole(
                name=name,
                engine=str(item.get("engine", CLAUDE)),
                duties=tuple(item.get("duties", ()) or ()),
                preset=str(item.get("preset", "")),
                endpoint=str(item.get("endpoint", "")),
                model=str(item.get("model", "")),
                agent=str(item.get("agent", "")),
                credential=str(item.get("credential", "")),
            )
        else:
            out.error = (
                "coordination.managers takes a name or a mapping, not "
                f"{type(item).__name__}"
            )
            return out
        if role.name in seen:
            # Priority is the order of this list, so a duplicate name makes
            # priority ambiguous — the same reason Phase 2 already refuses one.
            out.error = f"coordination.managers lists {role.name!r} twice"
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
    for key in (*_LOCAL_ONLY, "credential"):
        value = getattr(role, key)
        if value:
            entry[key] = value
    return entry


def configuration_problems(
    roles: list[ManagerRole], names: list[str] | None = None
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

    decomposers = [r for r in roles if DECOMPOSE in held[r.name]]
    reviewers = [r for r in roles if PLAN_REVIEW in held[r.name]]
    for decomposer in decomposers:
        independent = [
            r
            for r in reviewers
            if r.name != decomposer.name and r.engine != decomposer.engine
        ]
        if not independent:
            # RL-6: the decomposer reviewing its own plan's output shares every
            # blind spot of the plan, so a wrong slicing passes every check it
            # wrote. A different engine is what makes the gate a gate.
            problems.append(
                f"manager {decomposer.name} decomposes, and no other manager on "
                "a different engine holds plan-review — its decompositions would "
                "be approved by the engine that wrote them, which is the one "
                "thing plan review exists to prevent"
            )

    for role in roles:
        if INTEGRATE in held[role.name] and role.is_local:
            # RL-11: the harness is rite's own code, and SPEC §5.1.1 forbids
            # rite's code a push. A local integrate holder cannot do the job.
            problems.append(
                f"manager {role.name} holds integrate on a {role.engine} engine. "
                "Local engines commit to a local branch and stop; pushing and "
                "opening the PR needs a claude engine or a person"
            )

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
