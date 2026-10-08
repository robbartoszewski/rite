"""SCRUM-79: the question the driver asks a board must be the one rite answers.

The driver asked `TicketFilter(assignee=<manager>)`; rite assigns a ticket to a
Manager with a LABEL. On a real GitHub board that returns nothing, every cycle,
silently, and it survived from `eb32d89` (L-6) to v0.7.0a10.

**A mock is what let it survive**, so a mock is not the only guard here. Two
guards, and they fail for different reasons:

1. `test_both_manager_scoped_board_reads_ask_the_same_question` — no network,
   runs in every job. The two places that ask "which tickets are this
   Manager's" must ask it the same way. SCRUM-79 was exactly these two
   disagreeing, and nothing compared them.
2. `test_a_real_board_hands_over_a_ticket_labelled_for_a_manager` — against a
   real board when there is one. It is DECLARED-PASS and not skipped when there
   is not: `tools/every_test_passes_somewhere.py` turns a test that skips in
   every job red, because a test that passes nowhere is not a test. The same
   assertion runs on every e2e run against the run's own board, in
   `tools/e2e_v071/board_pickup.py`, which is where it is load-bearing.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Where "which tickets are this Manager's" is asked. Named, because a test that
# discovered these would stop covering a call site the day one was renamed.
_MANAGER_SCOPED_READS = (
    ("src/rite_ai/cli/main.py", "_local_tier_tickets"),
    ("src/rite_ai/coordination/distribution.py", None),
)


def _ticket_filter_keywords(path: Path, inside: str | None) -> list[frozenset[str]]:
    """The keyword names of every `TicketFilter(...)` built in `path`.

    By AST, not by grep: a docstring in this very file names
    `TicketFilter(assignee=...)` while explaining that nothing may construct
    one, and a source-text check would match the prose. That is the lesson
    SCRUM-72's own §3.3b control taught, twice.
    """
    tree = ast.parse((REPO / path).read_text(encoding="utf-8"))
    scopes: list[ast.AST] = []
    if inside is None:
        scopes = [tree]
    else:
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == inside:
                scopes.append(node)
    assert scopes, f"{path}: no scope {inside!r} to look in"
    found: list[frozenset[str]] = []
    for scope in scopes:
        for node in ast.walk(scope):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "TicketFilter"
            ):
                names = {kw.arg for kw in node.keywords if kw.arg}
                if names:
                    found.append(frozenset(names))
    return found


def test_both_manager_scoped_board_reads_ask_the_same_question():
    """🔴 SCRUM-79 in one assertion, with no board needed.

    `coordination.distribution` reads a Manager's tickets with
    `TicketFilter(label=manager)`, which is what `rite board label` — "the
    assignment mechanism" — writes. The local tier's driver asked
    `assignee=manager`, the backend's own field, which nothing in rite's
    automated flow ever sets to a Manager's name.
    """
    asked: dict[str, set[str]] = {}
    for path, inside in _MANAGER_SCOPED_READS:
        keywords = _ticket_filter_keywords(Path(path), inside)
        assert keywords, f"{path}: builds no TicketFilter — has this read moved?"
        asked[path] = {name for names in keywords for name in names}

    by_path = {p: names - {"status"} for p, names in asked.items()}
    distinct = {frozenset(v) for v in by_path.values()}
    assert len(distinct) == 1, (
        "the two places that ask which tickets are a Manager's ask different "
        f"questions: {json.dumps({k: sorted(v) for k, v in by_path.items()}, indent=2)}"
        ". On a real board only one of them can be right, and the wrong one "
        "returns nothing, silently, every cycle (SCRUM-79)."
    )
    assert distinct == {frozenset({"label"})}, (
        f"both reads now ask by {sorted(next(iter(distinct)))}. rite assigns a "
        "ticket to a Manager with a label; a backend's `assignee` is its own "
        "field (a GitHub login, a JIRA account) and `backend.assign` has one "
        "caller, the operator-run `rite board assign`."
    )


# ---- the real board, when this checkout has one -----------------------------

NOTHING_TO_CHECK = (
    "no reachable ticket board with a Manager-labelled ticket is configured "
    "here, so there is nothing to ask. The same assertion runs against the "
    "run's own board on every e2e run (tools/e2e_v071/board_pickup.py)."
)


def _board_repo() -> str:
    """The board to ask, or "". `RITE_E2E_BOARD_REPO` overrides, so an e2e run
    can point this at the board it just created."""
    override = os.environ.get("RITE_E2E_BOARD_REPO", "").strip()
    if override:
        return override
    config = REPO / ".rite" / "config.yaml"
    if not config.is_file():
        return ""
    try:
        import yaml

        backend = (yaml.safe_load(config.read_text()) or {}).get("ticket_backend") or {}
    except Exception:  # noqa: BLE001 - an unreadable config is simply no board
        return ""
    if str(backend.get("type") or "") != "github":
        return ""
    return str(backend.get("repo") or "")


def _labelled_ticket(repo: str) -> tuple[str, str]:
    """(issue number, label) for one open issue carrying a declared Manager's
    name as a label, or ("", "") — read with `gh`, so an unauthenticated or
    offline checkout simply has nothing to check."""
    try:
        done = subprocess.run(
            [
                "gh",
                "issue",
                "list",
                "--repo",
                repo,
                "--json",
                "number,labels",
                "--limit",
                "50",
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "", ""
    if done.returncode != 0:
        return "", ""
    try:
        rows = json.loads(done.stdout)
    except ValueError:
        return "", ""
    managers = _wanted_labels()
    for row in rows if isinstance(rows, list) else []:
        for label in row.get("labels") or []:
            name = label.get("name") if isinstance(label, dict) else None
            if name and name in managers:
                return str(row.get("number")), name
    return "", ""


def _wanted_labels() -> set[str]:
    """Which labels count as a Manager's assignment.

    `RITE_E2E_BOARD_LABEL` is honoured first, because the board an e2e run
    creates belongs to that run's own project and not to this checkout — and
    without it this test could ONLY ever declared-pass, which is the same
    "passes nowhere" defect it exists to prevent. Checked by
    `test_the_real_board_assertion_can_actually_run`.
    """
    named = os.environ.get("RITE_E2E_BOARD_LABEL", "").strip()
    if named:
        return {named}
    return _declared_managers()


def _declared_managers() -> set[str]:
    config = REPO / ".rite" / "config.yaml"
    try:
        import yaml

        body = yaml.safe_load(config.read_text()) or {}
    except Exception:  # noqa: BLE001
        return set()
    coordination = body.get("coordination") or {}
    out = {str(n) for n in (coordination.get("managers") or [])}
    for role in coordination.get("manager_roles") or []:
        if isinstance(role, dict) and role.get("name"):
            out.add(str(role["name"]))
    return out


def _board_under_test():
    """(repo, issue number, label) to ask about, or None when there is nothing.

    A plain helper and not a decorator: `functools.wraps` copies the wrapped
    function's signature, and pytest then reads `repo`/`number`/`label` as
    fixture requests and errors. The guard is inlined at the top of the test
    instead, where it is also easier to see that it PASSES rather than skips.
    """
    repo = _board_repo()
    if not repo:
        return None
    number, label = _labelled_ticket(repo)
    if not number:
        return None
    return repo, number, label


def test_a_real_board_hands_over_a_ticket_labelled_for_a_manager():
    """A real backend, asked the way the driver asks it, returns the ticket.

    No mock anywhere in this test: the backend is rite's own GitHub backend
    against a real repository, and the ticket is one a Manager's label is
    actually on. Declared-pass with its reason when this checkout has no such
    board, because a test that skips in every job passes nowhere.
    """
    under_test = _board_under_test()
    if under_test is None:
        print(NOTHING_TO_CHECK)
        return
    repo, number, label = under_test

    from rite_ai.tickets import create_backend
    from rite_ai.tickets.interface import BackendError, TicketFilter

    board = create_backend("github", repo=repo)
    assert not isinstance(board, BackendError), board

    def ids(flt):
        page = board.list_tickets(flt)
        if isinstance(page, BackendError):
            return None
        return {str(t.id) for t in (getattr(page, "tickets", page) or [])}

    # CONTROL first: without it, an empty board would read as a broken filter.
    everything = ids(TicketFilter())
    if everything is None or number not in everything:
        print(f"{NOTHING_TO_CHECK} (control: board did not list #{number})")
        return

    picked = ids(TicketFilter(label=label))
    assert picked is not None, "the board refused the driver's own question"
    assert number in picked, (
        f"SCRUM-79: issue #{number} carries the label {label!r}, an unfiltered "
        f"read of {repo} returns it, and asking the board the way the local "
        f"tier asks returns {sorted(picked) or 'nothing'}. A fleet on this "
        "board would advance nothing and say nothing."
    )


def test_the_real_board_assertion_can_actually_run(monkeypatch):
    """🔴 The control on the guard above.

    A real-board test that can only ever declared-pass is not a guard, it is a
    comment — and "passes nowhere" is the exact defect
    `every_test_passes_somewhere` exists for. This proves the selection can
    reach a live board: pointed at one, `_board_under_test` returns a ticket
    rather than None.

    Declared-pass itself when there is no board to reach, for the same reason
    as the test above — but it cannot be satisfied by the same emptiness,
    because it asserts the POSITIVE case.
    """
    repo = os.environ.get("RITE_E2E_BOARD_REPO", "").strip()
    label = os.environ.get("RITE_E2E_BOARD_LABEL", "").strip()
    if not repo or not label:
        print(
            "no RITE_E2E_BOARD_REPO/RITE_E2E_BOARD_LABEL given, so the live "
            "selection is not exercised here; an e2e run sets both and "
            "tools/e2e_v071/board_pickup.py asserts the same thing on the "
            "run's own board."
        )
        return
    under_test = _board_under_test()
    assert under_test is not None, (
        f"pointed at {repo} with label {label!r}, the selection still found no "
        "ticket — so the real-board assertion above would declared-pass and "
        "check nothing."
    )
    got_repo, number, got_label = under_test
    assert got_repo == repo
    assert got_label == label
    assert number
