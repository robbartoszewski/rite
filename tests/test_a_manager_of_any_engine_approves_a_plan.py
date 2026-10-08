"""SCRUM-72 §3.3b — plan approval is a Manager responsibility, and it is
ENGINE-AGNOSTIC.

🔴 **The principle Robert set.** SCRUM-72's root cause was an engine gate —
the local tier ran only `if role.is_local`. The approval path must not
reintroduce one: whether a Manager may approve a plan depends only on its
duty (`plan-review`), its independence from the author (DD-3.5, RL-6) and the
author being placeable (RL-67). **Never on what kind of engine it runs.**

🔴 **And approval was a STAMP.** `loop.advance_ticket` picked an independent
reviewer and immediately called `approve_plan` in that reviewer's name. No
Manager was ever asked, nothing could ever write REJECTED, and RL-6's gate
checked the rules about who COULD have reviewed against a review that did not
happen. The engine gate on approval was therefore indirect: approval ran only
inside the local-tier driver, which ran only for a local Manager.

Every test here drives the REAL path end to end — the request to the
reviewer's inbox, the verdict written into the reviewer's own directory
exactly as `rite plan approve` writes it, and the supervisor's own `honour`.
Nothing stubs the approval.
"""

from __future__ import annotations

import pytest

from rite_ai.local import decomposition as dec
from rite_ai.local import plan_review, plan_state
from rite_ai.local import stage as st
from rite_ai.local.loop import advance_ticket

TICKET = "T-1"

# Every engine kind config accepts for a Manager (`config.managers`:
# `claude`, `human`, `local:<class>`). `cursor` joins when CU4 lets config
# declare it, and when it does it needs no change here — which is the point.
REVIEWER_ENGINES = [
    ("claude", "", ""),
    ("human", "", ""),
    (
        "local:large",
        "qwen3:32b",
        "    endpoint: http://localhost:11434\n"
        "    agent: goose\n    context_window: 32768\n",
    ),
]


def _project(
    tmp_path,
    reviewer_engine="claude",
    reviewer_model="",
    reviewer_extra="",
    *,
    author_model="qwen3:8b",
):
    rite = tmp_path / ".rite"
    rite.mkdir(parents=True, exist_ok=True)
    (rite / "brief.yaml").write_text("project:\n  name: acme\n  role: owner\n")
    roles = (
        "  - name: planner\n    engine: local:small\n"
        f"    model: {author_model}\n"
        "    endpoint: http://localhost:11434\n    agent: goose\n"
        "    context_window: 32768\n"
        "    duties: [decompose, execute]\n"
    )
    roles += f"  - name: lead\n    engine: {reviewer_engine}\n"
    if reviewer_model:
        roles += f"    model: {reviewer_model}\n"
    roles += reviewer_extra
    roles += "    duties: [plan-review, decide, board, route, integrate]\n"
    (rite / "config.yaml").write_text(
        "ticket_backend:\n  type: none\ncoordination:\n  manager_roles:\n" + roles
    )
    from rite_ai.spec.digest_files import unit_filename, units_dir

    where = units_dir(tmp_path)
    where.mkdir(parents=True, exist_ok=True)
    for unit in ("5.1", "5.2"):
        (where / unit_filename(unit)).write_text(f"Section {unit}: the rule.\n")
    _started_worker(tmp_path)
    return tmp_path


def _started_worker(root, worker="alpha", manager="planner"):
    from rite_ai.config.parse import parse_config, parse_modules
    from rite_ai.publishing import record
    from rite_ai.refinement import record as rec

    d = root / "workers" / worker
    d.mkdir(parents=True, exist_ok=True)
    (d / "worker.yml").write_text(
        f"worker:\n  name: {worker}\n  manager: {manager}\n  modules: []\n"
    )
    modules = root / ".rite" / "modules.yaml"
    if not modules.exists():
        modules.write_text("modules: {}\n")
    record.write(
        root,
        worker,
        TICKET,
        parse_config(root / ".rite" / "config.yaml"),
        parse_modules(modules),
        refinement=rec.build(
            ticket=TICKET,
            board={"type": "none", "project": "acme"},
            title=f"{TICKET}: the thing",
            description="Do the thing.",
            definition_of_done=["a.txt says the thing"],
            verify=["pytest -q"],
            provenance={"kind": rec.ACCEPTED, "answered_by": {"owner_user": "robert"}},
            supersedes=None,
            key=b"k" * 32,
            scope_in=["a.txt"],
        ).payload(),
    )


def _state(root):
    return plan_state.layer(root)


def _plan(root, **kw):
    state = _state(root)
    read = dec.read(state, TICKET)
    plan = dec.Decomposition(
        ticket=TICKET,
        # Two subtasks, because RL-66 refuses a one-subtask plan: a ticket
        # that needs one subtask did not need decomposing.
        subtasks=kw.pop(
            "subtasks",
            (
                dec.Subtask(
                    id="s1",
                    intent="first",
                    scope=("a.txt",),
                    verify="pytest -q",
                    cites=("5.1",),
                ),
                dec.Subtask(
                    id="s2",
                    intent="second",
                    scope=("b.txt",),
                    verify="pytest -q",
                    cites=("5.2",),
                ),
            ),
        ),
        decomposed_by=kw.pop("decomposed_by", "planner"),
        approval=kw.pop("approval", dec.PENDING),
        **kw,
    )
    dec.write(state, plan, read.version)
    from rite_ai.local.gates import gate_for

    st.adopt(
        state, TICKET, st.DECOMPOSED, gate=gate_for(root, "planner", TICKET, state)
    )
    return plan


def _stored(root):
    return dec.read(_state(root), TICKET).plan


def _never(*_a, **_kw):
    """🔴 **A guard, not convenience.** `advance_ticket`'s production
    `author_plan` runs a REAL decomposition against a REAL endpoint — measured
    once at four and a half minutes. Every `_ask` below is made at
    `decomposed`, where the decomposer is not reached; so if a change ever
    puts the ticket back at `defined` (a state layer read from the wrong
    place does exactly that), this fails in a millisecond instead of dialling
    Ollama until somebody notices."""
    raise AssertionError(
        "this test must never reach the decomposer: the ticket was not at "
        "`decomposed` when the pass ran, which usually means the plan was "
        "written somewhere the driver does not read"
    )


def _ask(root, now=1000.0):
    """One driver pass at `decomposed`: it ASKS and does not approve."""
    return advance_ticket(root, "planner", TICKET, now=now, author_plan=_never)


def _answer(root, reviewer="lead", op=None, reason=""):
    plan_review.write_verdict(root, reviewer, op or plan_review.APPROVE, TICKET, reason)
    said = []
    plan_review.honour_verdicts(root, reviewer, said.append)
    return said


# --- 1. the acceptance test: every engine kind approves, the same way ------------


@pytest.mark.parametrize("engine, model, extra", REVIEWER_ENGINES, ids=lambda v: str(v))
def test_a_manager_of_any_engine_approves_through_the_same_path(
    tmp_path, engine, model, extra
):
    """§3.3b acceptance test 1. `claude`, `local:<class>` on a different model,
    and `human` — which answers through `rite plan approve` from a person's
    shell. One request path, one verdict path, one `approve_plan`."""
    root = _project(tmp_path, engine, model, extra)
    _plan(root)

    asked = _ask(root)
    assert asked.stage == st.DECOMPOSED, asked
    assert "asked of lead" in asked.note
    assert _stored(root).approval == dec.PENDING, "the harness must not approve"
    standing = plan_review.read_asked(root, TICKET)
    assert standing is not None and standing.reviewer == "lead"

    said = _answer(root)
    assert any("is approved by lead" in line for line in said), said
    assert _stored(root).approval == dec.APPROVED
    assert _stored(root).approved_by == "lead"
    assert _stored(root).released
    # The request is finished with, so nothing re-asks.
    assert plan_review.read_asked(root, TICKET) is None

    # And the STAGE follows the artifact on the next pass.
    assert _ask(root, now=2000.0).stage == st.APPROVED


@pytest.mark.parametrize("engine, model, extra", REVIEWER_ENGINES, ids=lambda v: str(v))
def test_a_manager_of_any_engine_rejects_through_the_same_path(
    tmp_path, engine, model, extra
):
    """§3.3b acceptance test 4, first half. Nothing could write REJECTED
    before: plan review only ever wrote APPROVED, so a reviewer that
    disagreed had no way to say so."""
    root = _project(tmp_path, engine, model, extra)
    _plan(root)
    _ask(root)

    said = _answer(root, op=plan_review.REJECT, reason="the slicing crosses modules")
    assert any("is rejected by lead" in line for line in said), said
    stored = _stored(root)
    assert stored.approval == dec.REJECTED
    assert stored.approved_by == ""
    assert stored.returns and "crosses modules" in stored.returns[-1]
    assert "lead" in stored.returns[-1], "the return names who rejected it"

    # The stage follows: it goes back to its planner.
    got = _ask(root, now=2000.0)
    assert got.stage == st.REJECTED, got
    assert st.read(_state(root), TICKET).stage == st.REJECTED


@pytest.mark.parametrize("engine, model, extra", REVIEWER_ENGINES, ids=lambda v: str(v))
def test_a_rejection_with_no_reasons_is_refused_for_every_engine(
    tmp_path, engine, model, extra
):
    """RL-10 counts returns, and a count with no reasons is a number nobody
    can act on — so a rejection the planner cannot re-author against is
    refused at both ends: by `decide` and by `reject_plan`."""
    root = _project(tmp_path, engine, model, extra)
    _plan(root)
    _ask(root)

    said = _answer(root, op=plan_review.REJECT, reason="   ")
    assert any("cannot be re-authored against" in line for line in said), said
    assert _stored(root).approval == dec.PENDING

    from rite_ai.local.approve import Refused, reject_plan

    direct = reject_plan(root, TICKET, "lead", "", state=_state(root))
    assert isinstance(direct, Refused)
    assert "carries its reasons" in direct.why


# --- 2. the refusals hold for every engine kind too ------------------------------


@pytest.mark.parametrize("engine, model, extra", REVIEWER_ENGINES, ids=lambda v: str(v))
def test_a_self_review_is_refused_for_every_engine(tmp_path, engine, model, extra):
    """DD-3.5. The author's own verdict, written in the author's own
    directory, where the identity is beyond doubt."""
    root = _project(tmp_path, engine, model, extra)
    _plan(root, decomposed_by="lead")

    # rite would never ASK lead to review its own plan, so the request is
    # planted to get past the asking and test the gate itself.
    plan_review._write_asked(
        root,
        plan_review.Asked(
            request="deadbeef",
            ticket=TICKET,
            reviewer="lead",
            author="lead",
            plan_version=dec.read(_state(root), TICKET).version,
            at=1.0,
        ),
    )
    said = _answer(root)
    assert any("DD-3.5" in line for line in said), said
    assert _stored(root).approval == dec.PENDING


def test_the_same_model_refusal_holds_whatever_the_labels_say(tmp_path):
    """RL-6 as Robert ruled it: two `local:` classes serving one model are one
    model, however they are labelled."""
    root = _project(
        tmp_path,
        "local:large",
        "qwen3.8:latest",
        "    endpoint: http://localhost:11434\n    agent: goose\n"
        "    context_window: 32768\n",
        author_model="qwen3.8:latest",
    )
    _plan(root)
    got = _ask(root)
    assert not got.moved, got
    assert "same model" in got.blocked
    # Nothing was asked, so nothing can be answered.
    assert plan_review.read_asked(root, TICKET) is None
    said = _answer(root)
    assert any("no record of asking" in line for line in said), said
    assert _stored(root).approval == dec.PENDING


@pytest.mark.parametrize("engine, model, extra", REVIEWER_ENGINES, ids=lambda v: str(v))
def test_an_unplaceable_author_fails_closed_for_every_engine(
    tmp_path, engine, model, extra
):
    """RL-67: rite cannot check an independence claim against an author it
    cannot place."""
    root = _project(tmp_path, engine, model, extra)
    _plan(root, decomposed_by="ghost")
    got = _ask(root)
    assert not got.moved, got
    assert "RL-67" in got.blocked


# --- 3. one request, and the harness changes nothing while it waits -------------


def test_a_pending_plan_with_no_verdict_is_left_byte_unchanged(tmp_path):
    """§3.3b acceptance test 3. The harness does not approve, and it does not
    fidget with the plan either: a ticket waiting for a review is one nothing
    touches."""
    root = _project(tmp_path)
    _plan(root)
    _ask(root)
    stored = plan_state.home(root) / "state.json"
    before = stored.read_bytes()
    assert before, "the plan state must be where this reads it"

    for n in range(5):
        got = _ask(root, now=1000.0 + n)
        assert not got.moved, got
        assert "waiting for lead's verdict" in got.blocked
    assert stored.read_bytes() == before


def test_exactly_one_request_is_sent_then_it_is_re_asked_then_escalated(tmp_path):
    """Told once per reason, `merging`'s rule: a request standing for ever is
    the per-cycle loop SCRUM-64 exists to stop. So: asked once, re-asked on
    the interval, and after `ESCALATE_AFTER_ASKS` reported rather than asked
    again."""
    root = _project(tmp_path)
    _plan(root)
    sent = []
    version = dec.read(_state(root), TICKET).version

    def once(now):
        return plan_review.ask(
            root,
            "planner",
            TICKET,
            "lead",
            "planner",
            version,
            now=now,
            tell=sent.append,
        )

    assert once(1000.0).sent is True
    assert len(sent) == 1
    # Within the interval: nothing is sent.
    for now in (1001.0, 1000.0 + plan_review.REASK_SECONDS - 1):
        out = once(now)
        assert out.sent is False and "waiting for lead" in out.note
    assert len(sent) == 1
    # Past it: asked again.
    assert once(1000.0 + plan_review.REASK_SECONDS).sent is True
    assert len(sent) == 2
    assert once(1000.0 + 2 * plan_review.REASK_SECONDS).sent is True
    assert len(sent) == 3
    # And then reported rather than asked a fourth time.
    out = once(1000.0 + 9 * plan_review.REASK_SECONDS)
    assert out.sent is False and out.escalated is True
    assert "has not answered" in out.note
    assert f"rite plan approve {TICKET}" in out.note
    assert len(sent) == 3


def test_the_request_carries_the_plan_and_the_agreed_definition_of_done(tmp_path):
    """§3.3b: "the request carries the plan (subtasks, scope, verify, cites)
    and the refinement record's agreed definition of done"."""
    root = _project(tmp_path)
    _plan(root)
    sent = []
    plan_review.ask(
        root,
        "planner",
        TICKET,
        "lead",
        "planner",
        dec.read(_state(root), TICKET).version,
        now=1.0,
        tell=sent.append,
    )
    text = sent[0]
    for expected in (
        "s1",
        "first",
        "a.txt",
        "pytest -q",
        "5.1",
        "a.txt says the thing",
        "The agreed definition of done",
        f"rite plan approve {TICKET}",
        f"rite plan reject {TICKET}",
        "the harness does not approve plans",
    ):
        assert expected in text, expected


def test_a_plan_that_changed_voids_the_standing_request(tmp_path):
    """A different plan is a different question. The old request is dropped
    and the new plan is asked about from scratch, rather than a verdict
    arriving for something nobody read."""
    root = _project(tmp_path)
    _plan(root)
    sent = []
    first = dec.read(_state(root), TICKET).version
    plan_review.ask(
        root, "planner", TICKET, "lead", "planner", first, now=1.0, tell=sent.append
    )

    _plan(
        root,
        subtasks=(
            dec.Subtask(
                id="s9",
                intent="rewritten",
                scope=("b.txt",),
                verify="pytest -q",
                cites=("5.2",),
            ),
            dec.Subtask(
                id="s8",
                intent="also rewritten",
                scope=("c.txt",),
                verify="pytest -q",
                cites=("5.1",),
            ),
        ),
    )
    second = dec.read(_state(root), TICKET).version
    assert second != first
    out = plan_review.ask(
        root, "planner", TICKET, "lead", "planner", second, now=2.0, tell=sent.append
    )
    assert out.sent is True
    assert len(sent) == 2
    assert plan_review.read_asked(root, TICKET).plan_version == second


# --- 4. the identity is the DIRECTORY, never a name in the payload --------------


def test_a_verdict_naming_a_reviewer_is_refused_by_that_reason(tmp_path):
    """§3.3b acceptance test 5. A field read by nothing is a field somebody
    will later read, so it is refused rather than ignored."""
    import json

    from rite_ai.managers.lifecycle import request_name
    from rite_ai.state import write_atomic

    root = _project(tmp_path)
    _plan(root)
    _ask(root)
    write_atomic(
        plan_review.verdict_dir(root, "lead") / request_name(),
        json.dumps({"op": "approve", "ticket": TICKET, "reviewer": "someone-else"}),
    )
    said = []
    plan_review.honour_verdicts(root, "lead", said.append)
    assert any("names a reviewer" in line for line in said), said
    assert _stored(root).approval == dec.PENDING


def test_a_verdict_is_attributed_to_the_directory_it_was_found_in(tmp_path):
    """Two Managers hold plan-review. The verdict in `lead`'s directory is
    `lead`'s, and `approved_by` says `lead` — there is nowhere a name could
    have come from but the directory."""
    root = _project(tmp_path)
    (root / ".rite" / "config.yaml").write_text(
        (root / ".rite" / "config.yaml").read_text()
        + "  - name: second\n    engine: claude\n    duties: [plan-review]\n"
    )
    _plan(root)
    _ask(root)

    # `second` answers in ITS directory for a review asked of `lead`: refused.
    plan_review.write_verdict(root, "second", plan_review.APPROVE, TICKET)
    said = []
    plan_review.honour_verdicts(root, "second", said.append)
    assert any("was asked of lead, not of this Manager" in line for line in said), said
    assert _stored(root).approval == dec.PENDING

    # `lead` answers in its own: approved, in lead's name.
    assert any("is approved by lead" in line for line in _answer(root))
    assert _stored(root).approved_by == "lead"


def test_a_symlinked_verdict_is_set_aside_and_never_read(tmp_path):
    """§3.3b acceptance test 5. `own_dir` opens the directory following no
    link at any depth and reads nothing but a regular file, so a Manager
    cannot point a verdict at a peer's file and have it read as its own."""
    import json

    root = _project(tmp_path)
    _plan(root)
    _ask(root)
    elsewhere = tmp_path / "planted.json"
    elsewhere.write_text(json.dumps({"op": "approve", "ticket": TICKET}))
    where = plan_review.verdict_dir(root, "lead")
    where.mkdir(parents=True, exist_ok=True)
    (where / "00000000000000000001-1-aaaa.json").symlink_to(elsewhere)

    said = []
    plan_review.honour_verdicts(root, "lead", said.append)
    assert any("was not a regular file and was set aside" in line for line in said), (
        said
    )
    assert _stored(root).approval == dec.PENDING


def test_a_verdict_for_a_stale_plan_is_refused_and_the_review_re_asked(tmp_path):
    """§3.3b acceptance test 5. No approving a plan nobody read: what was
    asked about is recorded where no Manager can write it, and the verdict is
    compared against THAT."""
    root = _project(tmp_path)
    _plan(root)
    _ask(root)
    # The plan is rewritten after the ask and before the answer.
    _plan(
        root,
        subtasks=(
            dec.Subtask(
                id="s9",
                intent="rewritten",
                scope=("b.txt",),
                verify="pytest -q",
                cites=("5.2",),
            ),
            dec.Subtask(
                id="s8",
                intent="also rewritten",
                scope=("c.txt",),
                verify="pytest -q",
                cites=("5.1",),
            ),
        ),
    )

    said = _answer(root)
    assert any(
        "the plan has changed since it was asked about" in line for line in said
    ), said
    assert _stored(root).approval == dec.PENDING
    # The standing request is dropped, so the next pass asks about what is
    # there now rather than waiting for an answer to the old question.
    assert plan_review.read_asked(root, TICKET) is None
    assert _ask(root, now=3000.0).note.startswith(
        f"{TICKET}: plan review asked of lead"
    )


def test_a_verdict_nobody_was_asked_for_is_refused(tmp_path):
    root = _project(tmp_path)
    _plan(root)
    said = _answer(root)
    assert any("no record of asking anyone" in line for line in said), said
    assert _stored(root).approval == dec.PENDING


# --- 5. the harness has no door into APPROVED ------------------------------------


def _keyword_writers(path, attr: str, value: str) -> list[str]:
    """Where `path` passes `<attr>=<value>` as a keyword, by AST — so this
    file's own prose about the thing is not read as a use of it."""
    import ast

    found = []
    for node in ast.walk(ast.parse(path.read_text())):
        if not isinstance(node, ast.keyword) or node.arg != attr:
            continue
        got = node.value
        name = (
            got.attr
            if isinstance(got, ast.Attribute)
            else got.id
            if isinstance(got, ast.Name)
            else ""
        )
        if name == value:
            found.append(f"{path}:{got.lineno}")
    return found


def _src(*names):
    import pathlib

    return [pathlib.Path("src/rite_ai/local") / n for n in names]


def test_approve_plan_is_the_only_writer_of_approved():
    """Pinned on the source, by AST. `plan.released` is the gate three places
    enforce, so a second writer of APPROVED would be a second gate nobody
    checks."""
    import pathlib

    writers = []
    for path in sorted(pathlib.Path("src/rite_ai").rglob("*.py")):
        writers += _keyword_writers(path, "approval", "APPROVED")
    assert len(writers) == 1, f"APPROVED is written in more than one place: {writers}"
    assert writers[0].startswith("src/rite_ai/local/approve.py:"), writers


def test_reject_plan_is_the_only_writer_of_rejected():
    """The same property for the verdict SCRUM-72 added: a rejection spends
    one of RL-10's returns, so it has one writer too.

    ⚠ `returned_to_plan_review` is not a second writer: it writes PENDING.
    """
    import pathlib

    writers = []
    for path in sorted(pathlib.Path("src/rite_ai").rglob("*.py")):
        writers += _keyword_writers(path, "approval", "REJECTED")
    assert len(writers) == 1, f"REJECTED is written in more than one place: {writers}"
    assert writers[0].startswith("src/rite_ai/local/approve.py:"), writers


def test_the_driver_never_reaches_approve_plan():
    """🔴 The stamp, pinned gone, by AST. `loop.advance_ticket` used to call
    `approve_plan` itself with a reviewer it chose; a pass must now be unable
    to produce an approval at all. By AST, because the comment that records
    what it used to do names the function."""
    import ast
    import pathlib

    tree = ast.parse(pathlib.Path("src/rite_ai/local/loop.py").read_text())
    reached = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert "approve_plan" not in reached, (
        "the driver reaches approve_plan again — approval is asked for, never "
        "taken (§3.3b)"
    )


def test_no_engine_kind_condition_is_on_the_approval_path():
    """🔴 **The control for this whole file, as source, by AST.** SCRUM-72's
    root cause was `if role.is_local`. `approve.py` and `plan_review.py` are
    the approval path, and neither may ask what KIND of engine a Manager runs,
    nor read `.engine` at all.

    `engine_identity` is the one engine read that stays, and it lives in
    `config.managers`: it decides INDEPENDENCE — the model for a local engine,
    the engine kind otherwise — and never who may approve.
    """
    import ast

    forbidden = {"is_local", "local_class", "engine", "CLAUDE", "HUMAN"}
    for path in _src("approve.py", "plan_review.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            read = (
                node.attr
                if isinstance(node, ast.Attribute)
                else node.id
                if isinstance(node, ast.Name)
                else ""
            )
            assert read not in forbidden, (
                f"{path.name}:{node.lineno} reads an engine kind ({read!r}) on "
                "the approval path"
            )


# --- 6. a fleet that cannot approve is refused before it runs -------------------


@pytest.mark.parametrize(
    "duties, expected",
    [
        # Nobody can author the plan a local Worker's pipeline starts from.
        ("[decide, board, route, integrate]", "no manager holds 'decompose'"),
        # Somebody authors it and nobody can review it.
        (
            "[decide, board, route, integrate, decompose]",
            "no manager holds 'plan-review'",
        ),
    ],
)
def test_doctor_refuses_a_local_worker_no_manager_can_plan_for(
    tmp_path, duties, expected
):
    """§3.3b's last bullet. A local Worker is driven only by the staged
    pipeline, so a fleet that cannot author or cannot review its plans starts
    the Worker, claims its paths and never gives it a subtask — exactly the
    silent shape `configuration_problems` exists to report."""
    from rite_ai.config.managers import configuration_problems
    from rite_ai.config.parse import parse_config

    rite = tmp_path / ".rite"
    rite.mkdir(parents=True)
    (rite / "config.yaml").write_text(
        "ticket_backend:\n  type: none\n"
        "coordination:\n  manager_roles:\n"
        f"  - name: lead\n    engine: claude\n    duties: {duties}\n"
    )
    roles = list(parse_config(rite / "config.yaml").coordination.manager_roles)

    # With no local Worker there is nothing to report: a Claude-only fleet is
    # not obliged to hold `decompose` at all.
    assert not [p for p in configuration_problems(roles) if "local engine" in p]
    # With one, it is.
    problems = configuration_problems(roles, local_workers=("gpu1",))
    assert any(expected in p and "gpu1" in p for p in problems), problems


def test_a_fleet_that_can_plan_and_review_is_not_reported(tmp_path):
    """The control: the §4.2 fleet shape — a local `planner` that authors and
    a Claude `lead` that approves — is reported about nothing."""
    from rite_ai.config.managers import configuration_problems
    from rite_ai.config.parse import parse_config

    rite = tmp_path / ".rite"
    rite.mkdir(parents=True)
    (rite / "config.yaml").write_text(
        "ticket_backend:\n  type: none\n"
        "coordination:\n  manager_roles:\n"
        "  - name: planner\n    engine: local:small\n    model: qwen3:8b\n"
        "    endpoint: http://localhost:11434\n    agent: goose\n"
        "    context_window: 32768\n    duties: [decompose, execute]\n"
        "  - name: lead\n    engine: claude\n"
        "    duties: [plan-review, decide, board, route, integrate]\n"
    )
    roles = list(parse_config(rite / "config.yaml").coordination.manager_roles)
    problems = configuration_problems(roles, local_workers=("gpu1",))
    assert [p for p in problems if "local engine" in p] == [], problems


def test_doctor_refuses_a_lone_manager_that_would_approve_its_own_plan(tmp_path):
    """🔴 The sharpest case, and the one the lone-Manager early return used to
    hide. A single Manager would author its local Worker's plan and then be
    the only plan-review holder — and DD-3.5 forbids approving your own, so
    the plan stays PENDING for ever. That is a division of labour the
    configuration REQUIRES and does not have, not one it has not reached."""
    from rite_ai.config.managers import configuration_problems
    from rite_ai.config.parse import parse_config

    rite = tmp_path / ".rite"
    rite.mkdir(parents=True)
    (rite / "config.yaml").write_text(
        "ticket_backend:\n  type: none\n"
        "coordination:\n  manager_roles:\n"
        "  - name: solo\n    engine: local:small\n    model: qwen3:8b\n"
        "    endpoint: http://localhost:11434\n    agent: goose\n"
        "    context_window: 32768\n"
        "    duties: [decompose, plan-review, decide, board, route, integrate]\n"
    )
    roles = list(parse_config(rite / "config.yaml").coordination.manager_roles)
    assert len(roles) == 1

    # Nothing is said while it has no local Worker to drive.
    assert [p for p in configuration_problems(roles) if "local engine" in p] == []
    # With one, it is said before the run rather than discovered as silence.
    problems = configuration_problems(roles, local_workers=("gpu1",))
    assert any("DD-3.5" in p and "gpu1" in p for p in problems), problems


def test_a_lone_manager_with_no_local_worker_is_still_not_reported(tmp_path):
    """The control for the early return: the lone-Manager rules below it must
    stay off. A lone Claude Manager holding every duty is today's single
    session and keeps working unchanged (design §3.3)."""
    from rite_ai.config.managers import configuration_problems
    from rite_ai.config.parse import parse_config

    rite = tmp_path / ".rite"
    rite.mkdir(parents=True)
    (rite / "config.yaml").write_text(
        "ticket_backend:\n  type: none\n"
        "coordination:\n  manager_roles:\n"
        "  - name: lead\n    engine: claude\n"
    )
    roles = list(parse_config(rite / "config.yaml").coordination.manager_roles)
    assert configuration_problems(roles) == []


# --- 7. what the mutation run found missing -------------------------------------


def test_a_request_that_could_not_be_sent_is_not_recorded_as_asked(tmp_path):
    """🔴 The ORDER. The record is written only after the note goes out: the
    other way round records a request nobody was sent, and then rite waits
    `REASK_SECONDS` for an answer to a question nobody was asked."""
    root = _project(tmp_path)
    _plan(root)

    def full_disk(_text):
        raise OSError("no space left on device")

    out = plan_review.ask(
        root,
        "planner",
        TICKET,
        "lead",
        "planner",
        dec.read(_state(root), TICKET).version,
        now=1.0,
        tell=full_disk,
    )
    assert out.sent is False
    assert "could not be asked for" in out.note
    assert plan_review.read_asked(root, TICKET) is None, (
        "a request nobody was sent must not be recorded as standing"
    )

    # And the next attempt is a fresh ask, not a wait.
    sent = []
    assert (
        plan_review.ask(
            root,
            "planner",
            TICKET,
            "lead",
            "planner",
            dec.read(_state(root), TICKET).version,
            now=2.0,
            tell=sent.append,
        ).sent
        is True
    )
    assert len(sent) == 1


@pytest.mark.parametrize(
    "reviewer, config_kw, expected",
    [
        # Holds no plan-review: the duty is what admits a reviewer (RL-6).
        ("planner", {}, "does not hold plan-review"),
        # The author, in its own name: DD-3.5.
        ("lead", {"decomposed_by": "lead"}, "DD-3.5"),
    ],
)
def test_a_rejection_applies_the_SAME_authority_checks_as_an_approval(
    tmp_path, reviewer, config_kw, expected
):
    """🔴 A rejection is as much an act of authority as an approval — it sends
    the plan back and spends one of RL-10's returns — so `reject_plan` must
    apply the same rule, through the same `_may_review`, not a second
    spelling of it that can drift."""
    from rite_ai.local.approve import Refused, reject_plan

    root = _project(tmp_path)
    _plan(root, **config_kw)

    got = reject_plan(
        root, TICKET, reviewer, "the slicing is wrong", state=_state(root)
    )
    assert isinstance(got, Refused), got
    assert expected in got.why
    assert _stored(root).approval == dec.PENDING
    assert _stored(root).returns == (), "a refused rejection writes no return"


def test_a_same_model_reviewer_cannot_reject_either(tmp_path):
    """RL-6 on the rejection side: a reviewer sharing the author's blind spots
    is no more independent when it says no than when it says yes."""
    from rite_ai.local.approve import Refused, reject_plan

    root = _project(
        tmp_path,
        "local:large",
        "qwen3.8:latest",
        "    endpoint: http://localhost:11434\n    agent: goose\n"
        "    context_window: 32768\n",
        author_model="qwen3.8:latest",
    )
    _plan(root)
    got = reject_plan(root, TICKET, "lead", "wrong slicing", state=_state(root))
    assert isinstance(got, Refused)
    assert "same model" in got.why
    assert _stored(root).approval == dec.PENDING


def test_a_rejection_of_a_plan_that_is_not_there_is_refused(tmp_path):
    """The `_may_review` branches that are about WHAT is being decided, not
    who is deciding, hold for a rejection too."""
    from rite_ai.local.approve import Refused, reject_plan

    root = _project(tmp_path)
    got = reject_plan(root, TICKET, "lead", "wrong slicing", state=_state(root))
    assert isinstance(got, Refused)
    assert "no decomposition" in got.why


# --- 8. the plan state is not in the project any more ---------------------------


def test_the_plan_state_is_outside_the_project_tree(tmp_path):
    """🔴 SCRUM-72 §3.3b. `LocalStateLayer` kept every decomposition in
    `.rite/state.json`, inside the tree every Manager's profile grants
    writable — so any Manager could set a plan's own `approval` to APPROVED
    with no duty, no independence and no `approve_plan`. The kernel tests
    (seatbelt and Landlock) assert the denial; this asserts the move."""
    from rite_ai.local import plan_state

    root = _project(tmp_path)
    _plan(root)

    where = plan_state.home(root)
    assert (where / "state.json").is_file(), "the plan is stored outside"
    assert not plan_state.legacy_path(root).exists(), (
        "nothing writes the old in-project location any more"
    )
    assert not str(where.resolve()).startswith(str(root.resolve()) + "/")
    # Two checkouts do not share a plan: rite itself has ~19 worktrees on one
    # committed namespace, and `T-1` in each is a different ticket's plan.
    other = _project(tmp_path / "second")
    assert plan_state.home(other) != where


def test_plan_state_left_in_the_project_is_reported_not_adopted(tmp_path):
    """Said once, never migrated: a `.rite/state.json` from before the move is
    content a Manager could have written, so reading it would import on day
    one exactly the forgery the move exists to prevent. A plan silently lost
    is worse than one reported stranded."""
    from rite_ai.local import plan_state

    root = _project(tmp_path)
    plan_state.legacy_path(root).write_text('{"keys": {}, "version": "1"}\n')

    notice = plan_state.stranded(root)
    assert "does not read it any more" in notice
    assert "NOT migrated" in notice
    assert str(plan_state.legacy_path(root)) in notice

    # 🔴 **And it is not adopted.** A legacy file holding an APPROVED plan is
    # the case that matters: migrating it would import on day one exactly the
    # forgery the move prevents, since any Manager could have written it.
    import base64
    import json as _json

    forged = dec.render(
        dec.Decomposition(
            ticket=TICKET,
            subtasks=(
                dec.Subtask(id="x", intent="forged", scope=("a.txt",), verify="true"),
            ),
            decomposed_by="planner",
            approval=dec.APPROVED,
            approved_by="planner",
        )
    )
    plan_state.legacy_path(root).write_text(
        _json.dumps(
            {
                "keys": {
                    dec.key_for(TICKET): base64.b64encode(forged).decode(),
                },
                "version": "1",
            }
        )
        + "\n"
    )
    assert dec.read(plan_state.layer(root), TICKET).plan is None, (
        "the old in-project state must not be adopted: a Manager could have written it"
    )

    # Once this checkout has written a plan outside the project, the old file
    # is history and is not mentioned again.
    _plan(root)
    assert plan_state.stranded(root) == ""


def test_no_plan_state_in_the_project_says_nothing(tmp_path):
    from rite_ai.local import plan_state

    assert plan_state.stranded(_project(tmp_path)) == ""


def test_doctor_reports_stranded_plan_state(tmp_path):
    """Wired, not only written: `test_no_dead_wiring`'s lesson is that a rule
    reporting to nobody is the defect shape, not the rule."""
    import inspect

    from rite_ai.cli import main

    src = inspect.getsource(main)
    assert (
        "        left_behind = stranded(root)\n"
        "        if left_behind:\n"
        '            click.echo(f"plan state: {left_behind}")\n'
        "            problems.append(left_behind)\n" in src
    ), "the notice must be read, said AND counted as a problem"


def test_the_state_layer_is_constructed_in_exactly_one_place():
    """🔴 Measured the hard way during this very commit: a mutation run was
    interrupted and left ONE of the seven call sites reading the old
    in-project path, and because that is the path it used to read, nothing
    looked wrong — the tests that would have caught it instead spent four
    minutes in a live decomposition. Seven spellings is how six get moved and
    one does not; one spelling is why `plan_state.layer` exists."""
    import ast
    import pathlib

    sites = []
    for path in sorted(pathlib.Path("src/rite_ai").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "LocalStateLayer"
            ):
                sites.append(f"{path}:{node.lineno}")
    assert len(sites) == 1, f"LocalStateLayer is constructed more than once: {sites}"
    assert sites[0].startswith("src/rite_ai/local/plan_state.py:"), sites


def test_the_fence_is_the_path_and_never_an_environment_variable():
    """§3.3b: `rite local approve` is fenced "by file permissions rather than
    by an environment variable". There is no env check anywhere in
    `plan_state` — the command simply cannot open the state from inside a
    boundary, and an env var is inherited by anything a session starts and is
    the one thing a compromised session would set."""
    import ast
    import pathlib

    tree = ast.parse(pathlib.Path("src/rite_ai/local/plan_state.py").read_text())
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {
        n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)
    }
    for forbidden in ("environ", "getenv", "environb", "RITE_MANAGER"):
        assert forbidden not in names, (
            f"plan_state reads {forbidden!r}: the fence must be the path the "
            "kernel denies, not a value a session can set"
        )
