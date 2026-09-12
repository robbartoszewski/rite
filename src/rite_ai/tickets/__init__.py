"""Ticket backend — abstract interface + concrete implementations (SPEC §6)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .github import GitHubBackend
from .interface import (
    PAGE_LIMIT,
    BackendError,
    Ticket,
    TicketBackend,
    TicketFilter,
    TicketPage,
    paginate,
)
from .jira import JiraBackend, JiraConfig

if TYPE_CHECKING:
    from rite_ai.config.models import TicketBackendConfig

__all__ = [
    "PAGE_LIMIT",
    "BackendError",
    "GitHubBackend",
    "JiraBackend",
    "JiraConfig",
    "Ticket",
    "TicketBackend",
    "TicketFilter",
    "TicketPage",
    "create_backend",
    "create_backend_from_config",
    "paginate",
]


def _missing_credential(key: str, credentials: object | None) -> str:
    """Why a credential could not be found, in enough detail to act on.

    Written against a real failure: a session finished five findings, could
    not file any of them, and recorded "no Jira connector, CLI or
    credentials" — then wrote the findings to a file nobody reads. The
    credentials existed. Nothing in the message it saw named WHICH
    credential, under WHAT keychain account rite had looked, or what to
    type. "Not found" that does not say those three things sends a
    correct session to a text file.

    Names all of: the key, every account actually consulted (which with
    §10.2 is no longer just the key), the env var, and the two commands —
    `rite credential list` first, because the reason the accounts differ
    from the key is a thing that command explains and a one-line error
    cannot.
    """
    from rite_ai.credentials.store import project_account

    env = f"RITE_{key.upper()}"
    looked = [f"${env}"]
    account = project_account(key, credentials)
    if account != key:
        looked.append(f"keychain '{account}' (this project)")
        looked.append(f"keychain '{key}' (machine-global)")
    else:
        looked.append(f"keychain '{key}'")

    return (
        f"{key} not found — looked in: {', '.join(looked)}.\n"
        f"  Set it for this project:  rite credential set {key}\n"
        f"  Or machine-wide:          rite credential set {key} --global\n"
        f"  Or in the environment:    export {env}=...\n"
        f"  What this project needs:  rite credential list"
    )


def create_backend(
    backend_type: str,
    site: str = "",
    project_key: str = "",
    credential_name: str = "",
    repo: str = "",
    projects: dict[str, str] | None = None,
    board_role: str = "workers",
    credentials: object | None = None,
) -> TicketBackend | BackendError:
    """Instantiate a ticket backend from config values.

    SPEC §8.3's `config.yaml` example (and `TicketBackendConfig`) carry a
    three-key `projects: {board, workers, testing}` dict, not a single
    project — a board with worker tickets on one JIRA project and business
    tickets on another (SPEC §6.2) needs to address both. `board_role`
    selects which of the three keys `projects` supplies; `project_key` is
    still accepted directly for a single-project setup or a test that
    doesn't care about the distinction (`projects` takes priority over
    `project_key` when both are given).
    """
    if backend_type == "github":
        if not repo:
            return BackendError("GitHub backend requires a repo (owner/name)")
        return GitHubBackend(repo)

    if backend_type == "jira":
        if not site:
            return BackendError("JIRA backend requires a site URL")
        # Scoped to THIS project (§10.2), falling back to the
        # machine-wide entry. The fallback is what keeps a pre-scoping
        # install working: `jira_email`/`jira_token` set before this
        # project had a scope are still found.
        from rite_ai.credentials.store import get_scoped, warn_if_global

        # ⚠ The fallback must never be silent. A project resolving through
        # the machine-global entry while its owner believes it has its own
        # is the flat namespace surviving under a new name — the exact
        # defect §10.2 exists to remove.
        for _key in ("jira_email", credential_name or "jira_token"):
            warn_if_global(_key, credentials)

        email = get_scoped("jira_email", credentials)
        if not email:
            return BackendError(_missing_credential("jira_email", credentials))
        token_key = credential_name or "jira_token"
        token = get_scoped(token_key, credentials)
        if not token:
            return BackendError(_missing_credential(token_key, credentials))
        resolved_key = (projects or {}).get(board_role) or project_key
        if not resolved_key:
            configured = sorted(k for k, v in (projects or {}).items() if v)
            return BackendError(
                f"no project configured for role '{board_role}' — "
                f"config.yaml's ticket_backend.projects has {configured or 'nothing'} "
                f"configured, and no bare project_key was given either"
            )
        config = JiraConfig(
            site=site,
            email=email,
            token=token,
            project_key=resolved_key or None,
        )
        return JiraBackend(config)

    if backend_type == "none":
        return BackendError("no ticket backend configured")

    return BackendError(f"unknown ticket backend type: {backend_type}")


def create_backend_from_config(
    tb: TicketBackendConfig,
    board_role: str = "workers",
    credentials: object | None = None,
) -> TicketBackend | BackendError:
    """The single config→backend builder — call this, not `create_backend`
    directly, from anything that has a `TicketBackendConfig` (the CLI's
    `_ticket_backend` and lifecycle's `_build_ticket_backend` used to each
    call `create_backend` with their own hand-copied field list, which is
    exactly the kind of divergence this package's own pre-push-hook fix
    just closed elsewhere — one builder here instead, so it can't happen
    again in this module)."""
    return create_backend(
        tb.type,
        site=tb.site,
        repo=tb.repo,
        projects=tb.projects,
        board_role=board_role,
        credential_name=tb.credential,
        credentials=credentials,
    )
