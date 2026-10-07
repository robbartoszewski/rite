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

    @property
    def owner(self) -> str:
        """The Manager that holds `route` (the `lead` preset's), which is also the
        plan reviewer in both scenarios."""
        return next(m["name"] for m in self.managers if m.get("preset") == "lead")

    @property
    def planner(self) -> str:
        return next(m["name"] for m in self.managers if m.get("preset") == "planner")

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
        return self.scenario["kill_worker"]

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
