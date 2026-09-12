"""Workspace management — adding/removing modules and workers (SPEC §5, §9.5–9.7)."""

from __future__ import annotations

from .manage import (
    AddModuleResult,
    AddWorkerResult,
    RemoveModuleResult,
    RemoveWorkerResult,
    UnsavedWork,
    add_module,
    add_worker,
    remove_module,
    remove_worker,
    unsaved_work,
)
from .prepare import (
    WorkspacePrepResult,
    prepare_workspace,
)

__all__ = [
    "AddModuleResult",
    "AddWorkerResult",
    "RemoveModuleResult",
    "RemoveWorkerResult",
    "UnsavedWork",
    "WorkspacePrepResult",
    "add_module",
    "add_worker",
    "prepare_workspace",
    "remove_module",
    "remove_worker",
    "unsaved_work",
]
