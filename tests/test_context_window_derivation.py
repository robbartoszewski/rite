"""Deriving a model with a usable context window (B7's other half).

⚠ **MEASURED 2026-09-24**, deriving from `qwen3:32b`, a 20 GB model:

    delta 4 KB, ZERO new blobs, create took 0.07s
    largest layer sha256:3291abe70f16… 18.8 GB — THE SAME BLOB
    unique to the derived model: 1 layer, 136 bytes

So the cost is 136 bytes, not 20 GB. The reason anyone believed otherwise is
that `ollama list` sums layer sizes and reports the derived model at the base
model's full size.

⚠ And it writes to the operator's SHARED model library, which is global state
outside the project. These pin that rite only writes when it must, names what
it wrote, and says how to remove it.
"""

from __future__ import annotations

import subprocess

from rite_ai.local.context_window import (
    DERIVED_PREFIX,
    derived_name,
    ensure_window,
)


def _ok(seen):
    def run(argv, **kw):
        seen.append((argv, open(argv[argv.index("-f") + 1]).read()))
        return subprocess.CompletedProcess(argv, 0, "success", "")

    return run


def _fails(message="no such model"):
    def run(argv, **kw):
        return subprocess.CompletedProcess(argv, 1, "", message)

    return run


class TestItOnlyWritesWhenItMust:
    def test_a_big_enough_window_creates_nothing(self):
        seen = []
        out = ensure_window("qwen3:32b", 32768, served=40960, run=_ok(seen))
        assert out.model == "qwen3:32b"
        assert not out.created
        assert seen == [], "it wrote to the operator's library for no reason"

    def test_an_exactly_sufficient_window_creates_nothing(self):
        seen = []
        out = ensure_window("qwen3:32b", 32768, served=32768, run=_ok(seen))
        assert not out.created and seen == []

    def test_an_unknown_window_is_not_treated_as_a_small_one(self):
        """⚠ The case that would otherwise write to the library on every
        start: a model that is simply not loaded yet reports no window, and
        `engine_probe` deliberately says unknown rather than guessing."""
        seen = []
        out = ensure_window("qwen3:32b", 32768, served=None, run=_ok(seen))
        assert not out.created
        assert seen == [], "an unloaded model got a derived twin created for it"
        assert "not a small one" in out.detail


class TestWhatItCreates:
    def test_it_derives_when_the_window_is_too_small(self):
        seen = []
        out = ensure_window("qwen3:32b", 32768, served=4096, run=_ok(seen))
        assert out.created
        assert out.model == "rite-ctx32768-qwen3-32b"
        (argv, modelfile) = seen[0]
        assert argv[:3] == ["ollama", "create", "rite-ctx32768-qwen3-32b"]
        assert modelfile == "FROM qwen3:32b\nPARAMETER num_ctx 32768\n"

    def test_the_name_says_rite_made_it_and_what_for(self):
        """An operator must be able to find everything rite put in their
        library without knowing which project asked for it."""
        assert derived_name("qwen3:32b", 32768).startswith(DERIVED_PREFIX)
        assert "32768" in derived_name("qwen3:32b", 32768)

    def test_two_windows_do_not_collide(self):
        assert derived_name("qwen3:32b", 32768) != derived_name("qwen3:32b", 16384)

    def test_a_tag_separator_does_not_become_a_second_tag(self):
        name = derived_name("qwen3:32b", 32768)
        assert ":" not in name, f"{name} would be read as a tagged name"

    def test_it_says_how_to_remove_what_it_made(self):
        out = ensure_window("qwen3:32b", 32768, served=4096, run=_ok([]))
        assert out.removal_command == "ollama rm rite-ctx32768-qwen3-32b"

    def test_it_offers_no_removal_when_it_made_nothing(self):
        out = ensure_window("qwen3:32b", 32768, served=40960, run=_ok([]))
        assert out.removal_command == ""

    def test_the_detail_warns_that_ollama_list_will_overstate_it(self):
        """⚠ The measurement that stops somebody deleting it in a panic over
        disk: the shared weights are counted again by `ollama list`."""
        out = ensure_window("qwen3:32b", 32768, served=4096, run=_ok([]))
        assert "136 bytes" in out.detail
        assert "sums shared layers" in out.detail


class TestItFailsTheSafeWay:
    def test_a_failed_create_returns_the_original_model_and_says_why(self):
        out = ensure_window("qwen3:32b", 32768, served=4096, run=_fails())
        assert out.model == "qwen3:32b", "it pointed at a model that was not created"
        assert not out.created
        assert "no such model" in out.problem

    def test_a_missing_ollama_is_a_problem_not_a_crash(self):
        def missing(*a, **kw):
            raise FileNotFoundError("ollama")

        out = ensure_window("qwen3:32b", 32768, served=4096, run=missing)
        assert not out.created and "not on PATH" in out.problem

    def test_it_refuses_to_derive_from_its_own_derived_model(self):
        """⚠ Stacking would work and would produce a chain nobody can read.
        The first derived model already carries a window; if it is too
        small, the base is what to derive from again."""
        seen = []
        out = ensure_window(
            "rite-ctx16384-qwen3-32b", 32768, served=16384, run=_ok(seen)
        )
        assert not out.created and seen == []
        assert "stacking" in out.problem
        assert "ollama rm rite-ctx16384-qwen3-32b" in out.problem
