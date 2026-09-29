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
        assert "cannot read /p/j.md" in got.evidence
        assert "no .rite/managers directory at all" in got.evidence  # kept

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
    assert str(journal) in got.evidence


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
