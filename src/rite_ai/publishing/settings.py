"""Which publish settings are in force for a module, and which are refused.

**One resolver.** Every reader of a strategy (`rite doctor`, the start
refusals, and the publish step) calls `effective`. An override that silently
does not apply is the failure this exists to prevent, so resolution is one
pure function and it says where each value came from.

**Key by key**, the way `RecordedCommands` falls back: a module's `publish:`
overrides the project's for the keys it sets, and inherits the rest.

**`push_to_shared` is defined and refused** until v0.8.0 (PB2). It parses, so
a config written for it is refused by name at start, not as a typo, and it is
refused again at the publish step. The door is shut, not merely unbuilt.
"""

from __future__ import annotations

from dataclasses import dataclass

from rite_ai.config.models import Module, ProjectConfig, PublishConfig

NOT_UNTIL = "v0.8.0"
"""The release that brings `push_to_shared` and `shared_repo` (PB2)."""

PROJECT = "project default"
MODULE = "module override"


@dataclass(frozen=True)
class Effective:
    """A module's publish settings as they will be applied, and where each
    came from."""

    module: str
    strategy: str
    squash: bool
    auto_merge: bool
    source: dict[str, str]

    @property
    def merges(self) -> bool:
        """Will rite ever merge this module's PRs? Only on a pull request."""
        return self.auto_merge and self.strategy == "pull_request"

    def describe(self) -> str:
        """One line for `rite doctor`, e.g.
        `svc: commit (module override), squash off (project default), ...`."""

        def flag(key: str, on: bool) -> str:
            return f"{key} {'on' if on else 'off'} ({self.source[key]})"

        merge = flag("auto_merge", self.auto_merge)
        if self.auto_merge and not self.merges:
            merge = (
                f"auto_merge does not apply under {self.strategy} "
                f"({self.source['auto_merge']} says on)"
            )
        return (
            f"{self.module}: {self.strategy} ({self.source['strategy']}), "
            f"{flag('squash', self.squash)}, {merge}"
        )


def effective(project: PublishConfig, module: Module) -> Effective:
    """`module`'s settings: its override where it sets one, else the project's."""
    values: dict[str, object] = {}
    source: dict[str, str] = {}
    for key in ("strategy", "squash", "auto_merge"):
        own = getattr(module.publish, key)
        values[key] = getattr(project, key) if own is None else own
        source[key] = PROJECT if own is None else MODULE
    return Effective(
        module=module.name,
        strategy=str(values["strategy"]),
        squash=bool(values["squash"]),
        auto_merge=bool(values["auto_merge"]),
        source=source,
    )


def _unavailable(strategy: str) -> str:
    """The refusal for a strategy this rite does not publish, or "".

    Private until the publish step (PB1's next piece) calls it too, so the
    refusal text is the same wherever it is met; `test_no_dead_wiring`
    would rightly flag a public function with no caller outside this file."""
    if strategy == "push_to_shared":
        return (
            f"publish strategy push_to_shared is not available until rite "
            f"{NOT_UNTIL}. Set publish.strategy to commit, push or pull_request"
        )
    return ""


def refusals(config: ProjectConfig, modules: list[Module]) -> list[str]:
    """Why this project may not start, as far as publishing goes; [] if none.

    ⚠ **At start, not at publish.** A strategy that cannot run is found
    before a Worker spends a session on work that then cannot leave, and it
    is refused at every entry point with the same text (`rite start`, `rite
    sandbox start`, `rite doctor`).
    """
    found: list[str] = []
    project_refusal = _unavailable(config.publish.strategy)
    if project_refusal:
        found.append(f"config.yaml: {project_refusal}")
    for module in modules:
        resolved = effective(config.publish, module)
        refusal = _unavailable(resolved.strategy)
        if refusal and resolved.source["strategy"] == MODULE:
            found.append(f"module {module.name!r}: {refusal}")
        if module.publish.shared_repo is not None:
            found.append(
                f"module {module.name!r}: publish.shared_repo is for "
                f"push_to_shared, which is not available until rite "
                f"{NOT_UNTIL}. Remove shared_repo from modules.yaml"
            )
        # An auto_merge the MODULE wrote, under a strategy that never merges,
        # is a setting its author believes they made. One inherited from the
        # project is not: that is the project's default for its PR modules,
        # and `describe` says it does not apply here.
        if module.publish.auto_merge is True and not resolved.merges:
            found.append(
                f"module {module.name!r}: publish.auto_merge is true, and its "
                f"strategy is {resolved.strategy}: only a pull request is ever "
                "merged. Remove auto_merge, or set strategy: pull_request"
            )
    return found
