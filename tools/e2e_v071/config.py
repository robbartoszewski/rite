"""fleet.yaml + tickets.yaml, loaded once into plain dataclasses."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent


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
    tickets: tuple[Ticket, ...] = field(default=())

    @property
    def owner(self) -> str:
        """The Manager that holds `route` — the `lead` preset's, by construction."""
        return next(m["name"] for m in self.managers if m.get("preset") == "lead")

    def worker(self, name: str) -> dict:
        return next(w for w in self.workers if w["name"] == name)

    def ticket(self, key: str) -> Ticket:
        return next(t for t in self.tickets if t.key == key)

    @property
    def work_tickets(self) -> tuple[Ticket, ...]:
        return tuple(t for t in self.tickets if not t.decoy_done)


def load(fleet_path: Path | None = None, tickets_path: Path | None = None) -> Fleet:
    f = yaml.safe_load((fleet_path or HERE / "fleet.yaml").read_text())
    t = yaml.safe_load((tickets_path or HERE / "tickets.yaml").read_text())
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
        tickets=tickets,
    )
