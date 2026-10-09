"""A scenario's fleet.yaml + tickets.yaml, loaded into plain dataclasses.

Each scenario is a directory under `scenarios/`: its fleet, its board, the roles
its tickets play (`scenario:` in fleet.yaml), and the checks that judge it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
SCENARIOS = HERE / "scenarios"


@dataclass(frozen=True)
class Ticket:
    key: str
    title: str
    body: str
    definition_of_done: tuple[str, ...]
    verify: str
    for_worker: str = ""
    needs_owner_answer: bool = False
    decoy_done: bool = False


@dataclass(frozen=True)
class Fleet:
    project_name: str
    github_owner: str
    repo_prefix: str
    managers: tuple[dict, ...]
    workers: tuple[dict, ...]
    heartbeat: dict
    run: dict
    yoloai: dict
    fixed_build: dict
    github: dict = field(default_factory=dict)
    scenario: dict = field(default_factory=dict)
    tickets: tuple[Ticket, ...] = field(default=())

    @property
    def name(self) -> str:
        return self.scenario["name"]

    @property
    def title(self) -> str:
        return self.scenario["title"]

    def _holder(self, duty: str, preset: str) -> str:
        """The Manager holding `duty`, by its explicit duties or by its preset.

        ⚠ **By DUTY, not by preset name.** A fleet may give duties directly —
        the smokes must, because the Worker's starter, owner and plan author
        have to be one Manager and the shipped presets split `decompose` from
        `board`/`route`. Resolving a role by the string "lead" stopped working
        the moment a fleet said what it meant instead.
        """
        from rite_ai.config.managers import PRESETS, effective_duties

        del effective_duties  # imported to assert it exists; duties are read raw
        for m in self.managers:
            declared = m.get("duties")
            if declared:
                if duty in [d.strip() for d in str(declared).split(",")]:
                    return m["name"]
            elif m.get("preset") and duty in PRESETS.get(m["preset"], ()):
                return m["name"]
        # Named rather than silent: a fleet with nobody holding a duty the
        # checks read is a fleet that cannot pass, and the error should say so.
        raise KeyError(f"no Manager in this fleet holds the {duty!r} duty")

    @property
    def owner(self) -> str:
        """The Manager that holds `route`: it picks tickets up and asks for
        Workers, so it is also the Worker's owner."""
        return self._holder("route", "lead")

    @property
    def planner(self) -> str:
        """The Manager that AUTHORS plans — the `decompose` holder. Under the
        shipped presets that is a separate `planner`; the smokes give it to the
        Worker's owner, because only its own Manager's cycle drives its
        ticket."""
        return self._holder("decompose", "planner")

    @property
    def approver(self) -> str:
        """The Manager that APPROVES a plan (`plan-review`). RL-6/DD-3.5 make
        this the one that must differ from the author, which is why the author
        being the Owner is not a problem."""
        return self._holder("plan-review", "lead")

    @property
    def gpu_workers(self) -> tuple[str, ...]:
        return tuple(w["name"] for w in self.workers if w.get("engine"))

    @property
    def pipeline_keys(self) -> tuple[str, ...]:
        return tuple(self.scenario.get("pipeline_tickets") or ())

    @property
    def owner_key(self) -> str | None:
        return self.scenario.get("owner_ticket")

    @property
    def decoy_key(self) -> str | None:
        return self.scenario.get("decoy_ticket")

    @property
    def kill_worker(self) -> str:
        """Whose sandbox the run kills, or "" for a scenario that induces no
        failure. Optional because the happy-path smoke induces none, and a
        KeyError here would have made that unexpressible."""
        return self.scenario.get("kill_worker") or ""

    @property
    def smoke(self) -> bool:
        """A reduced run: one ticket, no inductions, four checks. It finishes as
        soon as that ticket is delivered rather than waiting for inductions that
        will never happen."""
        return bool(self.scenario.get("smoke"))

    @property
    def board_only_repo(self) -> bool:
        """Create the run's repo for the BOARD alone — do not make it the app's
        git origin. A remote-less app is the only shape whose Worker sandbox
        starts without a GitHub token of its own."""
        return bool(self.scenario.get("board_only_repo"))

    @property
    def publish_strategy(self) -> str:
        """Override for `publish.strategy`, or "" to let the repo decide."""
        return str(self.scenario.get("publish_strategy") or "")

    @property
    def spec_paths(self) -> tuple[str, ...]:
        """The documents this project registers as its SPEC.

        🔴 **Nothing registered one, and that silently made every ticket
        undecomposable.** RL-63 requires each subtask to cite a resolvable spec
        unit, so with `spec.paths: []` no acceptable plan exists — and the
        local planner said exactly that, in a structured refusal, twice:
        "This project registers no spec units, so no cite can resolve."

        The run that appeared to clear RL-6 only did so because a spec document
        was written into the run directory BY HAND, mid-run, and `spec.paths`
        hand-edited to match. A gate whose fixture is assembled by hand is not
        a gate. The app ships `docs/SPEC.md` now and this registers it, so a
        clean `setup` produces a decomposable project with no intervention.
        """
        declared = self.scenario.get("spec_paths")
        if declared:
            return tuple(str(x) for x in declared)
        return ("docs/SPEC.md",)

    @property
    def check_names(self) -> tuple[str, ...]:
        return tuple(self.scenario["checks"])

    def worker(self, name: str) -> dict:
        return next(w for w in self.workers if w["name"] == name)

    def ticket(self, key: str) -> Ticket:
        return next(t for t in self.tickets if t.key == key)

    @property
    def work_tickets(self) -> tuple[Ticket, ...]:
        return tuple(t for t in self.tickets if not t.decoy_done)


def scenario_names() -> list[str]:
    return sorted(p.name for p in SCENARIOS.iterdir() if (p / "fleet.yaml").exists())


def load(scenario: str = "mixed") -> Fleet:
    d = SCENARIOS / scenario
    if not (d / "fleet.yaml").exists():
        raise SystemExit(f"no scenario {scenario!r}; have {scenario_names()}")
    f = yaml.safe_load((d / "fleet.yaml").read_text())
    t = yaml.safe_load((d / "tickets.yaml").read_text())
    tickets = tuple(
        Ticket(
            key=x["key"],
            title=x["title"],
            body=x["body"],
            definition_of_done=tuple(x["definition_of_done"]),
            verify=x["verify"],
            for_worker=x.get("for_worker", ""),
            needs_owner_answer=bool(x.get("needs_owner_answer")),
            decoy_done=bool(x.get("decoy_done")),
        )
        for x in t["tickets"]
    )
    return Fleet(
        project_name=f["project_name"],
        github_owner=f["github"]["owner"],
        repo_prefix=f["github"]["repo_prefix"],
        managers=tuple(f["managers"]),
        workers=tuple(f["workers"]),
        heartbeat=f["heartbeat"],
        run=f["run"],
        yoloai=f["yoloai"],
        fixed_build=f["fixed_build"],
        github=f["github"],
        scenario=f["scenario"],
        tickets=tickets,
    )
