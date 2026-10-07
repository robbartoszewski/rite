"""A Worker may not edit the gate that governs its own delivery (SCRUM-62).

**What happened, live, 2026-10-03.** KAN-28's delivery was refused by the
publish gate — 17 findings, all placeholder home paths, no secrets. Recovery
then wrongly restarted the already-finished Worker, and in its third session
`alpha` committed `yoloai/.rite/gitleaksignore` with 9 entries suppressing
all 17 of its own gate's findings, and carried that into its delivery.

Robert accepted those specific 17 as a one-time Owner-reviewed suppression
and rejected the pattern. A Worker silencing the gate that governs it
removes the control from the party it exists to constrain; whether a finding
is acceptable is a judgement about risk, and it is the Owner's.

**What is pinned here:**

* a Worker branch that touches the gate's own configuration is NOT pushed,
  and the refusal names the file;
* the work is still collected. A refusal that lost the work would teach a
  Worker to try again without the change — which is to say, to hide it;
* the control: an Owner's own suppression, already on the base branch, is
  honoured and the same Worker's clean branch delivers normally. Without
  that, "refuses everything" would pass this file;
* it is a GOVERNANCE check, not a scan. The entries' contents are never
  read, so an honest false-positive suppression is refused exactly like one
  hiding a live key;
* "could not tell" is not "nothing to tell": an unlistable range holds the
  push too.

What is real: git, three repositories, the parser, the start record, the
delivery itself. What is not: yoloAI — the harness is
`test_rite_delivers_a_finished_task`'s, whose own docstring explains why.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rite_ai.publishing import gate_suppression
from tests.test_rite_delivers_a_finished_task import TICKET, Project, _commit, _git

SUPPRESSION = ".rite/gitleaksignore"
AN_ENTRY = (
    "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef:app.txt:github-pat:sha256-0000"
    "  # a placeholder home path, not a credential\n"
)


def _notes(got) -> str:
    """Every module outcome's note, joined.

    `Delivered` carries one `Outcome` per module and the note lives on each;
    `Refused` is the whole delivery turned away before any module was
    touched, and carries `why`.
    """
    if hasattr(got, "why"):
        return got.why
    return " ".join(o.note() for o in got.outcomes)


def _suppress(repo: Path, where: str = SUPPRESSION, text: str = AN_ENTRY) -> None:
    """Write a suppression file into `repo` and commit it, as alpha did."""
    path = repo / where
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "suppress the gate findings")


class TestWhatCountsAsTheGatesOwnConfiguration:
    """Pure policy, no repository. A refusal over a file with no effect on
    the gate would stop real work for nothing."""

    @pytest.mark.parametrize(
        "path",
        [
            ".rite/gitleaksignore",
            # The live case: a module's own `.rite/`, nested.
            "yoloai/.rite/gitleaksignore",
            "modules/svc/.rite/gitleaks.toml",
            ".rite/gitleaks.toml",
            # Carries `publish_gate.scan_patterns` and names the ruleset file.
            ".rite/config.yaml",
            # Separators: `git log` says `/`, a caller may not.
            "yoloai\\\\.rite\\\\gitleaksignore",
        ],
    )
    def test_it_governs_the_gate(self, path):
        assert gate_suppression.governs_the_gate(path)

    @pytest.mark.parametrize(
        "path",
        [
            # ⚠ Deliberately NOT governed. SCRUM-17 closed this from the
            # other end: rite always passes `--config`, so a repository's own
            # gitleaks config has no effect on rite's gate. Refusing over it
            # would stop a delivery for a file that cannot change the outcome.
            ".gitleaks.toml",
            ".gitleaksignore",
            # Ordinary work.
            "app.txt",
            "src/.rite_helpers/x.py",
            ".rite/claims.json",
            ".rite/brief.yaml",
            ".rite/modules.yaml",
            # The directory itself, and a file named like one but deeper.
            ".rite",
            ".rite/sub/gitleaksignore",
            "gitleaksignore",
        ],
    )
    def test_it_does_not(self, path):
        assert not gate_suppression.governs_the_gate(path)

    def test_the_verdict_names_every_file_once_and_sorted(self):
        verdict = gate_suppression.inspect(
            [
                "app.txt",
                "yoloai/.rite/gitleaksignore",
                ".rite/config.yaml",
                "yoloai/.rite/gitleaksignore",
            ]
        )

        assert verdict.refused
        assert verdict.files == (".rite/config.yaml", "yoloai/.rite/gitleaksignore")

    def test_a_clean_change_set_refuses_nothing(self):
        assert not gate_suppression.inspect(["app.txt", "README.md"]).refused
        assert not gate_suppression.inspect([]).refused
        assert not gate_suppression.inspect(None).refused


class TestTheDeliveryIsHeld:
    def _worker_suppressed(self, tmp_path: Path, strategy: str = "pull_request"):
        p = Project(tmp_path, strategy)
        p.start()
        p.work(1)
        _suppress(p.clone)
        return p, p.deliver()

    def test_a_worker_authored_suppression_is_not_pushed(self, tmp_path):
        p, got = self._worker_suppressed(tmp_path)

        assert not got.ok
        assert f"refs/heads/{TICKET}" not in p.on_origin(), (
            "the branch reached the remote, so the Worker's suppression is "
            "published and governs every later gate run"
        )

    def test_the_refusal_names_the_file(self, tmp_path):
        """ "a suppression change" sends somebody looking through a diff for
        something rite already knows the path of."""
        _p, got = self._worker_suppressed(tmp_path)

        assert SUPPRESSION in _notes(got)
        assert "alpha" in _notes(got)

    def test_the_owners_route_is_in_the_note(self, tmp_path):
        _p, got = self._worker_suppressed(tmp_path)

        assert "ACCEPT" in _notes(got) and "REFUSE" in _notes(got)

    def test_the_work_is_collected_and_nothing_is_lost(self, tmp_path):
        """⚠ A refusal that lost the work would teach a Worker to try again
        without the change — which is to say, to hide it."""
        p, got = self._worker_suppressed(tmp_path)

        assert not got.ok
        assert p.branch(TICKET) is not None, "the branch is not in the project"
        assert "committed locally" in _notes(got)
        assert not p.destroyed, "the sandbox is kept, so the Owner can look"

    def test_the_contents_are_never_read(self, tmp_path):
        """⚠ It is a governance check, not a scan: the objection is to WHO
        authored the entry. An honest suppression of a genuine false positive
        is refused exactly like one hiding a live key, so it cannot be
        satisfied by writing a better suppression."""
        p = Project(tmp_path, "pull_request")
        p.start()
        p.work(1)
        _suppress(p.clone, text="# nothing suppressed at all, just a comment\n")

        got = p.deliver()

        assert not got.ok
        assert SUPPRESSION in _notes(got)

    def test_the_ruleset_file_counts_too(self, tmp_path):
        """A rule deleted from the ruleset suppresses a whole class of
        finding — the same act with a wider blast radius."""
        p = Project(tmp_path, "pull_request")
        p.start()
        p.work(1)
        _suppress(p.clone, where=".rite/gitleaks.toml", text="[extend]\n")

        got = p.deliver()

        assert not got.ok
        assert ".rite/gitleaks.toml" in _notes(got)

    def test_a_nested_module_rite_dir_counts(self, tmp_path):
        """The live path was `yoloai/.rite/gitleaksignore`."""
        p = Project(tmp_path, "pull_request")
        p.start()
        p.work(1)
        _suppress(p.clone, where="yoloai/.rite/gitleaksignore")

        got = p.deliver()

        assert not got.ok
        assert "yoloai/.rite/gitleaksignore" in _notes(got)

    def test_commit_only_is_held_too(self, tmp_path):
        """Nothing is pushed under `commit` anyway, but the entry still
        lands in the project's history and governs every later push from it.
        Who authored it is the question, not which strategy is in force."""
        p, got = self._worker_suppressed(tmp_path, "commit")

        assert not got.ok
        assert SUPPRESSION in _notes(got)

    def test_reverting_it_on_the_branch_is_still_authoring_it(self, tmp_path):
        """A commit that adds the entry and a later one that removes it both
        get published. `git log --name-only` over the range reports both,
        which is the answer wanted: the entry was in a published commit."""
        p = Project(tmp_path, "pull_request")
        p.start()
        p.work(1)
        _suppress(p.clone)
        (p.clone / SUPPRESSION).unlink()
        _git(p.clone, "add", "-A")
        _git(p.clone, "commit", "-qm", "put it back")

        got = p.deliver()

        assert not got.ok


class TestConfigYamlIsJudgedOnItsGateSection:
    """⚠ **The first version refused `config.yaml` WHOLE-FILE**, so a Worker
    given a ticket to change a scheduler window, the ticket backend or the
    publish strategy could never deliver — with no override, since `by_user`
    is deliberately not one. Found by review, 2026-10-07. Only
    `publish_gate` is this check's business."""

    def _reader(self, before: str | None, after: str | None):
        return lambda which, _path: before if which == "base" else after

    def test_an_unrelated_key_changing_is_not_a_gate_change(self):
        verdict = gate_suppression.inspect(
            [".rite/config.yaml"],
            read_at=self._reader(
                "publish_gate:\n  scan_patterns: []\nschedule:\n  timezone: UTC\n",
                "publish_gate:\n  scan_patterns: []\nschedule:\n  timezone: CET\n",
            ),
        )

        assert not verdict.refused

    def test_a_scan_pattern_removed_is(self):
        verdict = gate_suppression.inspect(
            [".rite/config.yaml"],
            read_at=self._reader(
                "publish_gate:\n  scan_patterns: ['/Users/']\n",
                "publish_gate:\n  scan_patterns: []\n",
            ),
        )

        assert verdict.refused

    def test_the_ruleset_being_pointed_elsewhere_is(self):
        """`gitleaks_config` NAMES the ruleset file, so changing the key is
        a suppression of everything the old ruleset found."""
        verdict = gate_suppression.inspect(
            [".rite/config.yaml"],
            read_at=self._reader(
                "publish_gate:\n  gitleaks_config: .rite/gitleaks.toml\n",
                "publish_gate:\n  gitleaks_config: /dev/null\n",
            ),
        )

        assert verdict.refused

    @pytest.mark.parametrize("before,after", [(None, "x: 1\n"), ("x: 1\n", None)])
    def test_a_config_it_cannot_read_is_refused(self, before, after):
        """⚠ "could not tell whether the gate's settings changed" is not
        "they did not"."""
        verdict = gate_suppression.inspect(
            [".rite/config.yaml"], read_at=self._reader(before, after)
        )

        assert verdict.refused

    def test_with_no_reader_at_all_it_is_refused(self):
        """A caller that cannot read the two ends has not answered the
        question, and the half it answered is the one that lets it through."""
        assert gate_suppression.inspect([".rite/config.yaml"]).refused

    def test_the_other_two_files_need_no_reader(self):
        """Any change to the suppression list or the ruleset is a change to
        the gate; there is no section to compare."""
        reader = self._reader("same\n", "same\n")
        assert gate_suppression.inspect([SUPPRESSION], read_at=reader).refused
        assert gate_suppression.inspect([".rite/gitleaks.toml"], read_at=reader).refused


class TestItIsNotSwitchedOffByACapitalLetter:
    """⚠ On a case-insensitive filesystem — APFS by default, and NTFS — the
    gate opens `.RITE/gitleaksignore` as the file it reads, while the first
    version of this said it was not the gate's. Measured on APFS,
    2026-10-07: the gate parsed the entries and `governs_the_gate` answered
    False."""

    @pytest.mark.parametrize(
        "path",
        [
            ".RITE/gitleaksignore",
            ".rite/GitleaksIgnore",
            ".Rite/GITLEAKS.TOML",
            "YoloAI/.RITE/gitleaksignore",
        ],
    )
    def test_the_case_does_not_matter(self, path):
        assert gate_suppression.governs_the_gate(path)


class TestARenamedRulesetIsStillTheRuleset:
    """`publish_gate.gitleaks_config` may name any path, and the gate reads
    whatever it names. Matching the basename alone left a project that
    relocated its ruleset uncovered."""

    def test_the_configured_path_counts(self):
        assert gate_suppression.governs_the_gate(
            "config/our-rules.toml", ruleset="config/our-rules.toml"
        )

    def test_and_the_default_still_does(self):
        assert gate_suppression.governs_the_gate(
            SUPPRESSION, ruleset="config/our-rules.toml"
        )

    def test_an_unrelated_toml_does_not(self):
        assert not gate_suppression.governs_the_gate(
            "pyproject.toml", ruleset="config/our-rules.toml"
        )


class TestWhatTheDeliveredHistoryHOLDS:
    """⚠ The check sees the DELIVERED history, which is strategy-dependent.
    An earlier docstring claimed add-then-revert was refused either way;
    measured, `squash=True` delivers it. Both are pinned here, because the
    divergence is real and the reason is sound: under squash the delivered
    history is one commit carrying the branch TIP's tree, so a reverted
    suppression is in nothing that gets published and there is nothing for
    the Owner to approve."""

    def _add_then_revert(self, tmp_path: Path, squash: bool):
        p = Project(tmp_path, "commit", squash=squash)
        p.start()
        p.work(1)
        _suppress(p.clone)
        (p.clone / SUPPRESSION).unlink()
        _git(p.clone, "add", "-A")
        _git(p.clone, "commit", "-qm", "put it back")
        return p, p.deliver()

    def test_without_squash_the_intermediate_commit_is_published_so_it_counts(
        self, tmp_path
    ):
        _p, got = self._add_then_revert(tmp_path, squash=False)

        assert not got.ok

    def test_with_squash_nothing_containing_it_is_delivered_so_it_does_not(
        self, tmp_path
    ):
        _p, got = self._add_then_revert(tmp_path, squash=True)

        assert got.ok, _notes(got)

    def test_but_a_squashed_branch_that_KEEPS_it_is_still_refused(self, tmp_path):
        """⚠ The control that matters most here: squash must not be a way
        round the check, only a different delivered history."""
        p = Project(tmp_path, "commit", squash=True)
        p.start()
        p.work(1)
        _suppress(p.clone)

        got = p.deliver()

        assert not got.ok
        assert SUPPRESSION in _notes(got)


class TestTheControls:
    """⚠ Without these, "refuse every delivery" would pass this file."""

    def test_a_clean_worker_branch_still_delivers(self, tmp_path):
        p = Project(tmp_path, "commit")
        p.start()
        p.work(2)

        got = p.deliver()

        assert got.ok, _notes(got)

    def test_an_owners_suppression_on_the_base_branch_is_honoured(self, tmp_path):
        """The ticket's own control. The Owner's entry is on `main`, the
        Worker's branch does not touch it, and the delivery is unaffected —
        the gate's configuration is not frozen, it is the Owner's."""
        p = Project(tmp_path, "commit")
        # The Owner, in the project's own checkout, on the base branch.
        path = p.svc / SUPPRESSION
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(AN_ENTRY)
        _git(p.svc, "add", "-A")
        _git(p.svc, "commit", "-qm", "the Owner accepts these findings")
        # The Worker's clone is taken after that, so it carries the entry
        # without having authored it.
        _git(p.clone, "fetch", "-q", str(p.svc), "main")
        _git(p.clone, "reset", "-q", "--hard", "FETCH_HEAD")
        _git(p.clone, "checkout", "-q", "-B", TICKET)
        p.start()
        _commit(p.clone, "feature.txt", "work\n", "my own work")

        got = p.deliver()

        assert got.ok, _notes(got)
        assert (p.svc / SUPPRESSION).read_text() == AN_ENTRY

    def test_an_owner_delivering_by_hand_is_still_refused(self, tmp_path):
        """⚠ `by_user` is not an override. The question is who AUTHORED the
        entry, and a person typing `rite deliver` does not change that — the
        note tells them how to take it on themselves."""
        p = Project(tmp_path, "pull_request")
        p.start()
        p.work(1)
        _suppress(p.clone)

        got = p.deliver(by_user=True)

        assert not got.ok
        assert SUPPRESSION in _notes(got)


class TestItFailsClosed:
    def test_a_range_it_cannot_list_holds_the_push(self, tmp_path):
        """⚠ "could not check" is not "nothing to check". This is a
        governance control, and an unanswerable question is not a pass."""
        from unittest.mock import patch

        from rite_ai.gate import pattern_scan

        p = Project(tmp_path, "pull_request")
        p.start()
        p.work(1)

        with patch(
            "rite_ai.gate.pattern_scan.files_touched_by",
            return_value=pattern_scan.ScanError("git would not say"),
        ):
            got = p.deliver()

        assert not got.ok
        assert "could not tell" in _notes(got)
        assert f"refs/heads/{TICKET}" not in p.on_origin()
