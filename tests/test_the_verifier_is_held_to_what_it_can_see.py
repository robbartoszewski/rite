"""The verifier never CONTRADICTS a claim about a file it cannot read (V1/V2).

**Observed** in the v0.6.0 dogfood (2026-09-28, macOS): `helper` reported,
truthfully, that it had journaled an observation at
`.rite/managers/helper/journal/20260928T004948089732Z-observation.md` (788
bytes, there). rite's verifier answered CONTRADICTED: "there is no
.rite/managers directory at all". The next reply, citing the OWNER's own
journal, was CONFIRMED.

**Why.** The verifier runs inside the Owner's boundary, and that boundary does
not grant another Manager's state (DF3). It could not see the directory, and
reported the file's absence. On `main` the journal has moved out of the
project (MM8) and the Owner's profile still does not grant it, so the same
claim meets the same wall.

**Pre-registered in the dogfood write-up (#22):** a helper reply citing its
own journal file is verified CONFIRMED (or COULD NOT TELL), never
CONTRADICTED, when the file exists. Plus: a seeded false claim ("I wrote
tests/x.py" when it didn't) is CONTRADICTED.

The verifier is a model, so the end-to-end tests stand a script in for it
that does what the dogfood's did: looks for the cited file from where it
runs, answers contradicted when it cannot read it, and ignores anything rite
tells it. Run through the real `sandbox-exec` and the profile rite writes.
Each has a control showing the same script, where it CAN see, says confirmed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from rite_ai.managers import verifier
from rite_ai.managers.verifier import (
    CONFIRMED,
    CONTRADICTED,
    COULDNT_TELL,
    Sighting,
    Verdict,
    _cited_paths,
)

JOURNAL_NAME = "20260928T004948089732Z-observation.md"


def _answer(verdict: str, evidence: str) -> str:
    return json.dumps(
        {
            "type": "result",
            "is_error": False,
            "result": "",
            "structured_output": {"verdict": verdict, "evidence": evidence},
        }
    )


# --- which paths a claim cites ----------------------------------------------


class TestCitedPaths:
    def test_the_dogfood_claim(self):
        claim = (
            "KAN-9 blocked: no scope. Observation journaled at "
            f".rite/managers/helper/journal/{JOURNAL_NAME}."
        )
        assert _cited_paths(Path("/p"), claim) == [
            Path(f"/p/.rite/managers/helper/journal/{JOURNAL_NAME}")
        ]

    def test_absolute_home_and_bare_file_names(self):
        got = _cited_paths(Path("/p"), "wrote /a/b.md, ~/x/y and README.md")
        assert got == [
            Path("/a/b.md"),
            Path("~/x/y").expanduser(),
            Path("/p/README.md"),
        ]

    def test_a_url_is_not_a_path(self):
        assert _cited_paths(Path("/p"), "PR at https://github.com/o/r/pull/1") == []

    def test_no_path_cites_nothing(self):
        assert _cited_paths(Path("/p"), "KAN-9 is blocked on scope") == []


# --- the guard, which does not rely on the model -----------------------------


def _seen(path="/p/j.md", exists=True, readable=False):
    return [Sighting(Path(path), exists, readable, "788 bytes")]


class TestHeldToWhatItSaw:
    def test_contradicted_about_an_existing_file_it_cannot_read_is_couldnt_tell(self):
        said = Verdict(CONTRADICTED, "no .rite/managers directory at all")
        got = verifier._held_to_what_it_saw(said, _seen())
        assert got.kind == COULDNT_TELL
        assert "/p/j.md" in got.changed_by_rite
        assert "cannot read" in got.changed_by_rite
        assert got.evidence == "no .rite/managers directory at all"  # kept
        line = got.line()
        assert "rite changed that to COULD NOT TELL" in line  # said, not silent
        assert "not a finding that the reply is wrong" in line

    def test_a_probe_that_failed_is_not_readable(self):
        said = Verdict(CONTRADICTED, "not there")
        got = verifier._held_to_what_it_saw(said, _seen(readable=None))
        assert got.kind == COULDNT_TELL

    def test_contradicted_about_a_file_it_can_read_stands(self):
        said = Verdict(CONTRADICTED, "tests/x.py does not exist")
        assert verifier._held_to_what_it_saw(said, _seen(readable=True)) == said

    def test_contradicted_about_a_file_that_does_not_exist_stands(self):
        """rite looked itself: absent is absent, seen or not."""
        said = Verdict(CONTRADICTED, "no such journal")
        assert verifier._held_to_what_it_saw(said, _seen(exists=False)) == said

    @pytest.mark.parametrize("kind", [CONFIRMED, COULDNT_TELL, verifier.UNVERIFIED])
    def test_only_contradicted_is_touched(self, kind):
        said = Verdict(kind, "x")
        assert verifier._held_to_what_it_saw(said, _seen()) == said


class TestTheVerifierIsToldWhatItCannotSee:
    def _verify(self, tmp_path, monkeypatch, claim, probe_answer, verdict):
        from rite_ai.managers import claude_login

        monkeypatch.setattr(
            claude_login,
            "pane_environment",
            lambda r, m: {"CLAUDE_CONFIG_DIR": str(tmp_path / "login")},
        )
        prompts = []

        def runner(argv, **kw):
            prompts.append(kw["input"])
            return subprocess.CompletedProcess(argv, 0, _answer(*verdict), "")

        got = verifier.verify(
            tmp_path,
            "lead",
            claim,
            runner=runner,
            probe=lambda script: probe_answer,
        )
        return got, prompts[0]

    def test_a_hidden_file_that_exists_is_named_with_what_rite_saw(
        self, tmp_path, monkeypatch
    ):
        journal = tmp_path / "state" / JOURNAL_NAME
        journal.parent.mkdir()
        journal.write_text("x" * 788)
        got, prompt = self._verify(
            tmp_path,
            monkeypatch,
            f"journaled at {journal}",
            "0\n",
            ("contradicted", "no such directory"),
        )
        assert f"- {journal}: exists, 788 bytes" in prompt
        assert "NOT evidence" in prompt
        assert prompt.index("rite looked") < prompt.index("<<<")
        assert got.kind == COULDNT_TELL

    def test_a_visible_path_adds_nothing_to_the_prompt(self, tmp_path, monkeypatch):
        _, prompt = self._verify(
            tmp_path,
            monkeypatch,
            "I wrote tests/x.py",
            "1\n",
            ("contradicted", "tests/x.py does not exist"),
        )
        assert "rite looked" not in prompt


# --- end to end, through the real boundary -----------------------------------


FAKE_VERIFIER = r"""#!/bin/sh
# Stands in for the model: an honest verifier that can only report what it
# can read from where it runs, and ignores anything rite tells it.
prompt=$(cat)
path=$(printf '%s\n' "$prompt" | sed -n 's/^CHECK: //p' | head -1)
head='{"type":"result","is_error":false,"result":"","structured_output":'
if head -c 1 -- "$path" >/dev/null 2>&1; then
  printf '%s{"verdict":"confirmed","evidence":"read %s"}}\n' "$head" "$path"
else
  printf '%s{"verdict":"contradicted","evidence":"%s does not exist"}}\n' \
    "$head" "$path"
fi
"""


@pytest.fixture
def machine(tmp_path, monkeypatch):
    """A project with `lead` (the Owner) and `helper`, laid out as production
    lays it out: rite's home, the data directory the journals and mail live
    in, and the profile `verify` writes for itself. Under pytest's temp
    directory, which no Manager profile grants (unlike /tmp)."""
    if sys.platform != "darwin":
        pytest.skip("seatbelt is macOS only")
    import rite_ai.managers.github_access as ga
    from rite_ai.managers import claude_login

    home = (tmp_path / "home").resolve()
    (home / ".rite").mkdir(parents=True)
    monkeypatch.setenv("RITE_HOME_DIR", str(home / ".rite"))
    monkeypatch.delenv("RITE_MAIL_DIR", raising=False)
    isolated = ga._credential_root
    monkeypatch.setattr(
        ga, "_credential_root", lambda h=None: isolated(h if h is not None else home)
    )
    root = (tmp_path / "pingr").resolve()
    (root / ".rite").mkdir(parents=True)
    (root / "tests").mkdir()
    login = home / "owner-login"
    login.mkdir()
    monkeypatch.setattr(
        claude_login, "pane_environment", lambda r, m: {"CLAUDE_CONFIG_DIR": str(login)}
    )
    fake = root / "fake-verifier.sh"
    fake.write_text(FAKE_VERIFIER)
    fake.chmod(0o755)
    return {"root": root, "home": home, "fake": fake}


def _through_the_real_boundary(fake: Path, raw: list):
    """A runner that keeps rite's `sandbox-exec -f <profile>` and replaces
    only the engine with the stand-in. `raw` collects the verifier's own
    answer, before rite's guard, for the controls."""

    def runner(argv, **kw):
        wrapped = argv[-1].replace(verifier._command(), f"/bin/sh {fake}")
        assert wrapped.startswith("sandbox-exec -f "), wrapped
        got = subprocess.run(["sh", "-c", wrapped], **kw)
        raw.append(verifier.parse(got.stdout))
        return got

    return runner


def _helper_journal(root: Path) -> Path:
    from rite_ai.managers.journal import journal_dir

    where = journal_dir(root, "helper")
    where.mkdir(parents=True)
    path = where / JOURNAL_NAME
    path.write_text("# observation\n" + "x" * 773)  # 788 bytes, as observed
    return path


def test_a_helpers_true_journal_claim_is_never_contradicted(machine):
    """The pre-registered test, first half."""
    root, fake = machine["root"], machine["fake"]
    journal = _helper_journal(root)
    raw: list = []
    claim = f"KAN-9 blocked, observation journaled.\nCHECK: {journal}\n"
    got = verifier.verify(
        root, "lead", claim, runner=_through_the_real_boundary(fake, raw)
    )
    # Control: the stand-in, inside the Owner's real boundary, did hit the
    # wall — so this test measures the defect, not a path the profile grants.
    assert raw[0].kind == CONTRADICTED, raw
    assert got.kind in (CONFIRMED, COULDNT_TELL), got
    assert got.kind == COULDNT_TELL
    assert str(journal) in got.changed_by_rite


def test_the_legacy_in_tree_journal_the_dogfood_cited(machine):
    """v0.6.0's layout exactly: `.rite/managers/helper/journal/…`, which the
    Owner's profile denies by name."""
    root, fake = machine["root"], machine["fake"]
    legacy = root / ".rite" / "managers" / "helper" / "journal" / JOURNAL_NAME
    legacy.parent.mkdir(parents=True)
    legacy.write_text("x" * 788)
    raw: list = []
    cited = f".rite/managers/helper/journal/{JOURNAL_NAME}"
    claim = f"journaled at {cited}\nCHECK: {legacy}\n"
    got = verifier.verify(
        root, "lead", claim, runner=_through_the_real_boundary(fake, raw)
    )
    assert raw[0].kind == CONTRADICTED, raw  # control: the wall is real
    assert got.kind == COULDNT_TELL, got


def test_a_seeded_false_claim_is_still_contradicted(machine):
    """The pre-registered test, second half: the guard must not blunt a
    verifier that saw rightly."""
    root, fake = machine["root"], machine["fake"]
    raw: list = []
    claim = f"I wrote tests/x.py\nCHECK: {root / 'tests' / 'x.py'}\n"
    got = verifier.verify(
        root, "lead", claim, runner=_through_the_real_boundary(fake, raw)
    )
    assert got.kind == CONTRADICTED, got


def test_control_the_stand_in_confirms_what_it_can_read(machine):
    """Without this, a stand-in that always said contradicted would pass
    every test above."""
    root, fake = machine["root"], machine["fake"]
    (root / "tests" / "x.py").write_text("print('hi')\n")
    raw: list = []
    claim = f"I wrote tests/x.py\nCHECK: {root / 'tests' / 'x.py'}\n"
    got = verifier.verify(
        root, "lead", claim, runner=_through_the_real_boundary(fake, raw)
    )
    assert got.kind == CONFIRMED, got


def test_control_the_owners_own_journal_is_readable(machine):
    """V2's CONFIRMED half: the Owner's own state is granted, so the probe
    must say readable there, or the guard would blunt every journal claim."""
    from rite_ai.managers.journal import journal_dir

    root, fake = machine["root"], machine["fake"]
    own = journal_dir(root, "lead")
    own.mkdir(parents=True)
    (own / JOURNAL_NAME).write_text("x")
    raw: list = []
    claim = f"I journaled it\nCHECK: {own / JOURNAL_NAME}\n"
    got = verifier.verify(
        root, "lead", claim, runner=_through_the_real_boundary(fake, raw)
    )
    assert got.kind == CONFIRMED, got
    assert os.path.exists(own / JOURNAL_NAME)


# --- a CONTRADICTED resting on the claimant's own state (option B) ----------


def _answering(readable: str):
    """A probe that answers `readable` ("1" or "0") for every area asked."""
    return lambda script: "\n".join([readable] * script.count("echo 1")) + "\n"


@pytest.fixture
def helper_state(tmp_path, monkeypatch):
    from rite_ai.managers import manager_dir

    monkeypatch.setenv("RITE_MAIL_DIR", str(tmp_path / "data"))
    root = tmp_path / "proj"
    (root / ".rite").mkdir(parents=True)
    state = manager_dir(root, "helper")
    state.mkdir(parents=True)
    return {"root": root, "state": state}


def _state(helper_state, readable="0"):
    return verifier._claimant_state(
        helper_state["root"], "helper", _answering(readable)
    )


def _contradicted(*rested):
    return Verdict(
        CONTRADICTED,
        "no such journal entry",
        rested_on=tuple(rested) if rested != (None,) else None,
    )


class TestTheClaimantStateGuard:
    def test_resting_on_the_claimants_unreadable_state_is_couldnt_tell(
        self, helper_state
    ):
        rested = str(helper_state["state"] / "journal")
        got = verifier._held_to_the_claimants_state(
            _contradicted(rested), _state(helper_state), helper_state["root"]
        )
        assert got.kind == COULDNT_TELL
        assert rested in got.changed_by_rite
        assert "nothing that could see it checked the claim" in got.changed_by_rite
        assert got.evidence == "no such journal entry"  # its words, unaltered
        line = got.line()
        assert "rite changed that to COULD NOT TELL" in line
        assert "not a finding that the reply is wrong" in line

    def test_the_old_in_tree_managers_folder_counts_too(self, helper_state):
        legacy = f".rite/managers/helper/journal/{JOURNAL_NAME}"
        got = verifier._held_to_the_claimants_state(
            _contradicted(legacy), _state(helper_state), helper_state["root"]
        )
        assert got.kind == COULDNT_TELL

    def test_a_seeded_false_claim_it_could_see_stays_contradicted(self, helper_state):
        """V1's pre-registered second half."""
        said = _contradicted("tests/x.py")
        got = verifier._held_to_the_claimants_state(
            said, _state(helper_state), helper_state["root"]
        )
        assert got == said

    def test_naming_nothing_stands(self, helper_state):
        for said in (_contradicted(), _contradicted(None)):
            got = verifier._held_to_the_claimants_state(
                said, _state(helper_state), helper_state["root"]
            )
            assert got == said

    def test_state_the_verifier_can_open_stands(self, helper_state):
        """Readability is what the verifier's boundary answered, not a rule."""
        said = _contradicted(str(helper_state["state"]))
        got = verifier._held_to_the_claimants_state(
            said, _state(helper_state, readable="1"), helper_state["root"]
        )
        assert got == said

    def test_the_prompt_says_where_absence_is_not_evidence(self, helper_state):
        prompt = verifier._prompt(helper_state["root"], "x", [], _state(helper_state))
        assert str(helper_state["state"]) in prompt
        assert "NOT evidence" in prompt
        assert "rested_on" in prompt


def test_rite_never_reads_the_journal():
    """§9.15.5, the reason option A was rejected: the verifier module must not
    locate or list any Manager's journal."""
    import inspect

    src = inspect.getsource(verifier)
    assert "journal_dir(" not in src
    assert "scandir" not in src and "iterdir" not in src


class TestRestedOnIsParsed:
    def _parse(self, answer: dict):
        out = {"type": "result", "is_error": False, "structured_output": answer}
        return verifier.parse(json.dumps(out))

    def test_a_list_is_kept(self):
        answer = {"verdict": "contradicted", "evidence": "e", "rested_on": ["a"]}
        assert self._parse(answer).rested_on == ("a",)

    def test_missing_is_none_not_empty(self):
        got = self._parse({"verdict": "contradicted", "evidence": "e"})
        assert got.rested_on is None

    def test_the_schema_requires_it(self):
        assert "rested_on" in verifier.SCHEMA["required"]


class TestTheSilentPathIsCounted:
    """The coordinator's condition: a CONTRADICTED that names nothing stands,
    and is counted where a person reads it, so a guard starved of input is
    seen rather than read as the problem being gone."""

    def test_the_event_says_which_it_was(self):
        assert verifier.event_fields(_contradicted())["unanchored"] is True
        assert verifier.event_fields(_contradicted(None))["unanchored"] is True
        assert verifier.event_fields(_contradicted("x"))["unanchored"] is False
        changed = Verdict(COULDNT_TELL, "e", changed_by_rite="because")
        assert verifier.event_fields(changed) == {
            "changed_by_rite": True,
            "unanchored": False,
        }

    def test_the_summary_says_both_counts(self, tmp_path):
        import time

        from rite_ai.managers import checkins, routing

        start = time.time() - 1
        changed = Verdict(COULDNT_TELL, "e", changed_by_rite="because")
        for verdict in (
            _contradicted(),
            _contradicted(None),
            _contradicted("x"),
            changed,
        ):
            checkins.record(
                tmp_path,
                "lead",
                {
                    "event": "verification",
                    "at": time.time(),
                    "verdict": verdict.kind,
                    **verifier.event_fields(verdict),
                },
            )
        said = routing.verification_summary(tmp_path, "lead", start)
        assert "2 CONTRADICTED did not say what it rested on" in said
        assert "rite changed 1 CONTRADICTED to COULD NOT TELL" in said


PATHLESS_VERIFIER = r"""#!/bin/sh
# Stands in for the model on a claim with no path: it looks in the folder it
# was pointed at, answers contradicted when it cannot open it, names where it
# looked, and ignores anything rite said.
cat >/dev/null
where="__WHERE__"
head='{"type":"result","is_error":false,"result":"","structured_output":'
if ls -- "$where" >/dev/null 2>&1; then
  ok='"verdict":"confirmed","evidence":"listed'
  printf '%s{%s %s","rested_on":["%s"]}}\n' "$head" "$ok" "$where" "$where"
else
  no='"verdict":"contradicted","evidence":"no journal at'
  printf '%s{%s %s","rested_on":["%s"]}}\n' "$head" "$no" "$where" "$where"
fi
"""


def _pathless(machine, where: Path) -> Path:
    fake = machine["root"] / "pathless-verifier.sh"
    fake.write_text(PATHLESS_VERIFIER.replace("__WHERE__", str(where)))
    fake.chmod(0o755)
    return fake


def test_a_pathless_true_journal_claim_is_not_contradicted(machine):
    """End to end through the real `sandbox-exec`: "I journaled the
    observation", no path."""
    root = machine["root"]
    entry = _helper_journal(root)
    raw: list = []
    got = verifier.verify(
        root,
        "lead",
        "KAN-9 is blocked on scope; I journaled the observation.",
        runner=_through_the_real_boundary(_pathless(machine, entry.parent), raw),
        claimant="helper",
    )
    assert raw[0].kind == CONTRADICTED, raw  # control: the wall is real
    assert got.kind == COULDNT_TELL, got
    assert str(entry.parent) in got.changed_by_rite


def test_control_the_owners_own_state_is_not_guarded(machine):
    """The same stand-in, pointed at the Owner's own journal, which its
    boundary opens: it confirms, and nothing is changed."""
    from rite_ai.managers.journal import journal_dir

    root = machine["root"]
    own = journal_dir(root, "lead")
    own.mkdir(parents=True)
    raw: list = []
    got = verifier.verify(
        root,
        "lead",
        "I journaled the observation.",
        runner=_through_the_real_boundary(_pathless(machine, own), raw),
        claimant="helper",
    )
    assert got.kind == CONFIRMED, got
    assert not got.changed_by_rite
