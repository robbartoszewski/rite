"""GitHub Issues backend via `gh` CLI (SPEC §6.3).

Uses `gh` for all operations — avoids token refresh issues that plague
MCP-based GitHub integrations. The `gh` binary must be installed and
authenticated (`gh auth login`).
"""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from .interface import (
    PAGE_LIMIT,
    BackendError,
    Ticket,
    TicketBackend,
    TicketFilter,
    TicketPage,
    paginate,
)

_ISSUE_FIELDS = "number,title,state,body,labels,assignees,url,createdAt,updatedAt"


def _find_gh() -> str | None:
    return shutil.which("gh")


def _run_gh(args: list[str], cwd: Path | None = None) -> str | BackendError:
    binary = _find_gh()
    if not binary:
        return BackendError(
            "`gh` CLI not found — install it from https://cli.github.com/"
        )
    try:
        proc = subprocess.run(
            [binary, *args],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=30,
            cwd=cwd,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return BackendError(f"gh failed: {e}")
    if proc.returncode != 0:
        return BackendError(f"gh exited {proc.returncode}: {proc.stderr.strip()[:300]}")
    return proc.stdout


def _parse_iso(s: str) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _issue_to_ticket(data: dict) -> Ticket:
    labels = [
        lbl.get("name", "") if isinstance(lbl, dict) else str(lbl)
        for lbl in (data.get("labels") or [])
    ]
    assignees = data.get("assignees") or []
    assignee = ""
    if assignees and isinstance(assignees, list):
        first = assignees[0]
        assignee = first.get("login", "") if isinstance(first, dict) else str(first)

    return Ticket(
        id=str(data.get("number", "")),
        title=data.get("title", ""),
        status=data.get("state", ""),
        assignee=assignee,
        labels=labels,
        description=data.get("body") or "",
        url=data.get("url") or data.get("html_url") or "",
        created_at=_parse_iso(data.get("createdAt") or data.get("created_at") or ""),
        updated_at=_parse_iso(data.get("updatedAt") or data.get("updated_at") or ""),
        metadata=data,
    )


class GitHubBackend(TicketBackend):
    def __init__(self, repo: str, cwd: Path | None = None):
        self.repo = repo
        self.cwd = cwd

    def _gh(self, args: list[str]) -> str | BackendError:
        return _run_gh(args, cwd=self.cwd)

    def create(
        self,
        title: str,
        description: str = "",
        labels: list[str] | None = None,
    ) -> Ticket | BackendError:
        # Labels are applied AFTER the issue exists, not via `--label`.
        # `gh issue create --label` validates against the repo's label
        # list and aborts the whole create on an unknown name — measured:
        # `could not add label: 'rite-probe' not found`, and no issue
        # created. Since rite labels with worker names, that turned every
        # first `rite board create -l <worker>` on a fresh repo into a
        # no-op. `label()` creates what is missing (see there).
        args = [
            "issue",
            "create",
            "--repo",
            self.repo,
            "--title",
            title,
            "--body",
            description or "",
        ]
        result = self._gh(args)
        if isinstance(result, BackendError):
            return result
        url = result.strip().splitlines()[-1].strip() if result.strip() else ""
        number = url.rstrip("/").rsplit("/", 1)[-1] if "/" in url else url
        if labels:
            labelled = self.label(number, labels)
            if isinstance(labelled, BackendError):
                # The issue EXISTS. Saying only "labelling failed" would
                # send the caller off to create it again.
                return BackendError(
                    f"created issue {number} ({url}) but labelling it failed: "
                    f"{labelled.message}"
                )
        return Ticket(id=number, title=title, url=url, labels=labels or [])

    def read(self, ticket_id: str) -> Ticket | BackendError:
        result = self._gh(
            [
                "issue",
                "view",
                ticket_id,
                "--repo",
                self.repo,
                "--json",
                _ISSUE_FIELDS,
            ]
        )
        if isinstance(result, BackendError):
            return result
        try:
            data = json.loads(result)
        except json.JSONDecodeError:
            return BackendError(f"could not parse gh output for issue {ticket_id}")
        return _issue_to_ticket(data)

    def update(self, ticket_id: str, **fields: str) -> None | BackendError:
        args = ["issue", "edit", ticket_id, "--repo", self.repo]
        if "title" in fields:
            args.extend(["--title", fields["title"]])
        if "description" in fields or "body" in fields:
            args.extend(["--body", fields.get("body", fields.get("description", ""))])
        result = self._gh(args)
        if isinstance(result, BackendError):
            return result
        return None

    # GitHub issues only have two real states (open/closed) — there is no
    # first-class equivalent of a JIRA workflow column. rite's status
    # vocabulary (§6.2: "In Progress", "Ready for testing", etc.) has to map
    # onto that binary, and any status this mapping doesn't recognise must
    # refuse rather than silently collapse to "close" — closing a ticket
    # that was actually meant to move to "In Progress" is data loss, not a
    # harmless no-op.
    _OPEN_STATUSES = {"open", "reopen", "reopened", "to do", "todo", "in progress"}
    _CLOSE_STATUSES = {"closed", "close", "done", "cancelled", "canceled"}

    def _classify_status(self, status: str) -> str | BackendError:
        """ "open" or "closed" for one of rite's status names, or an error
        naming what IS recognised. Shared by `move` and `list_tickets` so
        the two speak the same vocabulary — a status `move` accepts and
        `list` rejects is the sort of split that made `rite board list
        --status "In Progress"`, the example in its own `--help`, fail."""
        normalised = status.lower()
        if normalised in self._OPEN_STATUSES:
            return "open"
        if normalised in self._CLOSE_STATUSES:
            return "closed"
        return BackendError(
            f"GitHub issues have no '{status}' state — only open/closed "
            f"are real states. Recognised as open: {sorted(self._OPEN_STATUSES)}; "
            f"as closed: {sorted(self._CLOSE_STATUSES)}."
        )

    def _state_flag(self, status: str) -> str | BackendError:
        """`gh issue list --state` value for a rite status name. "all" is
        accepted here and nowhere else — it is meaningful as a filter and
        meaningless as a destination for `move`."""
        if status.lower() == "all":
            return "all"
        return self._classify_status(status)

    def move(self, ticket_id: str, status: str) -> str | None | BackendError:
        """Open or close the issue, and say which — never echo the rite
        status name back as if GitHub held it.

        `rite board move 1 "In Progress"` used to print `1 -> In Progress`
        while the issue sat exactly where it was: GitHub has no such
        column, `_classify_status` maps the name to "open", and an already
        open issue is reopened to no effect (`gh issue reopen` on an open
        issue warns and exits 0). The move reported a board state that
        does not exist and cannot be checked — `rite board list --status
        "In Progress"` returns every open issue, so nothing contradicts
        it. Returning the real state is what makes the claim falsifiable.
        """
        state = self._classify_status(status)
        if isinstance(state, BackendError):
            return state
        action = "reopen" if state == "open" else "close"
        result = self._gh(["issue", action, ticket_id, "--repo", self.repo])
        if isinstance(result, BackendError):
            return result
        return None if status.lower() == state else state

    def link(
        self, ticket_id: str, target_id: str, link_type: str
    ) -> None | BackendError:
        return BackendError(
            "GitHub has no first-class issue-link mechanism (as of gh CLI "
            "2.98.0) — `gh api` against GitHub's REST issue-dependency "
            "endpoints is the escape hatch to evaluate if this becomes worth "
            "building; not implemented here rather than faked as a comment."
        )

    def assign(self, ticket_id: str, worker: str) -> None | BackendError:
        result = self._gh(
            [
                "issue",
                "edit",
                ticket_id,
                "--repo",
                self.repo,
                "--add-assignee",
                worker,
            ]
        )
        if isinstance(result, BackendError):
            return result
        return None

    def label(
        self, ticket_id: str, labels: list[str], remove: list[str] | None = None
    ) -> None | BackendError:
        """Add and remove labels, creating any that the repo lacks.

        `gh issue edit --add-label` VALIDATES against the repo's existing
        labels and refuses an unknown one — measured: `'scheduled' not
        found`. Since rite's labels are worker names and `scheduled`, and
        no repo ships with those, that made §9.10's assignment mechanism
        and the whole handover unusable on a fresh GitHub-backed project,
        with a retry queued forever behind a failure that retrying cannot
        clear.

        `POST /repos/{owner}/{repo}/issues/{n}/labels` has no such
        validation — measured against the real API, it CREATES a missing
        label (default grey) and returns the issue's new label set. That
        is the one call that does what rite's model needs, so the add path
        goes through `gh api` rather than `gh issue edit`. Removal keeps
        `gh issue edit --remove-label`, which tolerates a label the issue
        does not carry.
        """
        if labels:
            args = [
                "api",
                "-X",
                "POST",
                f"repos/{self.repo}/issues/{ticket_id}/labels",
            ]
            for lbl in labels:
                args.extend(["-f", f"labels[]={lbl}"])
            result = self._gh(args)
            if isinstance(result, BackendError):
                return result
        if remove:
            args = ["issue", "edit", ticket_id, "--repo", self.repo]
            for lbl in remove:
                args.extend(["--remove-label", lbl])
            result = self._gh(args)
            if isinstance(result, BackendError):
                return result
        return None

    def list_tickets(
        self, filters: TicketFilter | None = None
    ) -> TicketPage | BackendError:
        args = [
            "issue",
            "list",
            "--repo",
            self.repo,
            "--json",
            _ISSUE_FIELDS,
            # One over the page size: a result longer than the page is how
            # truncation is known rather than guessed.
            "--limit",
            str(PAGE_LIMIT + 1),
        ]
        if filters:
            if filters.status:
                state = self._state_flag(filters.status)
                if isinstance(state, BackendError):
                    return state
                args.extend(["--state", state])
            if filters.assignee:
                args.extend(["--assignee", filters.assignee])
            if filters.label:
                args.extend(["--label", filters.label])
            if filters.labels:
                for lbl in filters.labels:
                    args.extend(["--label", lbl])
        result = self._gh(args)
        if isinstance(result, BackendError):
            return result
        try:
            data = json.loads(result)
        except json.JSONDecodeError:
            return BackendError("could not parse gh issue list output")
        if not isinstance(data, list):
            return BackendError("gh issue list returned non-list JSON")
        return paginate([_issue_to_ticket(entry) for entry in data])

    def comment(self, ticket_id: str, text: str) -> None | BackendError:
        result = self._gh(
            [
                "issue",
                "comment",
                ticket_id,
                "--repo",
                self.repo,
                "--body",
                text,
            ]
        )
        if isinstance(result, BackendError):
            return result
        return None

    def query(self, raw_query: str) -> TicketPage | BackendError:
        """Backend-native search — GitHub qualifiers, one argv token each.

        The whole query used to be joined into a single string and passed
        after `--`, which made `gh` treat it as ONE search TERM and quote
        it: `rite board query "is:open"` reached GitHub as
        `( repo:"owner/name is:issue is:open" ) type:issue` and came back
        `Invalid search query`. Every `rite board query` against GitHub
        failed — the same shape of defect as JIRA's removed `/search`
        endpoint, and equally invisible to a mocked suite, which asserts
        on the argv rite builds rather than on what `gh` does with it.

        Qualifiers are therefore split the way a shell would split them,
        so quoted values (`label:"good first issue"`) survive as one
        token. A malformed quote is the user's typo, not a crash.
        """
        try:
            terms = shlex.split(raw_query)
        except ValueError as e:
            return BackendError(f"could not parse query {raw_query!r}: {e}")
        result = self._gh(
            [
                "search",
                "issues",
                "--json",
                _ISSUE_FIELDS,
                "--limit",
                str(PAGE_LIMIT + 1),
                "--",
                f"repo:{self.repo}",
                "is:issue",
                *terms,
            ]
        )
        if isinstance(result, BackendError):
            return result
        try:
            data = json.loads(result)
        except json.JSONDecodeError:
            return BackendError("could not parse gh search output")
        if not isinstance(data, list):
            return BackendError("gh search returned non-list JSON")
        return paginate([_issue_to_ticket(entry) for entry in data])
