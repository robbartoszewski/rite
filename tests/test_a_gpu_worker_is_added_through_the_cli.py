"""`rite add worker` declares an engine (SCRUM-55, OL3).

`worker.yml` has carried the five engine keys since OL3 and
`_write_worker_manifest` has written them — and nothing could SET them. So the
only way to declare a GPU Worker was to hand-edit a manifest, which SPEC §8.5
counts as a UX defect rather than a shortcut: a person editing YAML has no way
to learn that a window is mandatory, or that `goose` is the only agent whose
window rite can enforce, until a scheduled run has already gone wrong.

⚠ **The declaration is checked with the PARSER's own rule and before anything
is created.** `engine_shape_problem` is shared with `parse_worker` on purpose —
"a second copy of 'a local engine must say all three' is a second copy that
drifts" — so a `rite add worker` that succeeds cannot leave behind a
`worker.yml` the next command refuses to read.
"""

from __future__ import annotations

import pytest

from rite_ai.config.models import WorkerManifest
from rite_ai.config.parse import parse_worker
from rite_ai.workspace.manage import add_worker

LOCAL = {
    "engine": "local:small",
    "endpoint": "http://localhost:11434",
    "model": "qwen3.8:latest",
    "agent": "goose",
    "context_window": 32768,
}


@pytest.fixture
def project(tmp_path):
    (tmp_path / ".rite").mkdir()
    (tmp_path / ".rite" / "modules.yaml").write_text(
        "modules:\n  app:\n    path: app\n"
    )
    (tmp_path / ".rite" / "brief.yaml").write_text("project:\n  name: p\n")
    (tmp_path / ".rite" / "config.yaml").write_text("ticket_backend:\n  type: none\n")
    app = tmp_path / "app"
    app.mkdir()
    (app / "README.md").write_text("x\n")
    return tmp_path


def test_a_gpu_worker_is_declared_by_flags(project):
    result = add_worker(project, "gpu1", manager="lead", follow_docs=[], **LOCAL)
    assert result.ok, result.message
    assert result.worker is not None
    assert result.worker.is_local is True
    assert result.worker.model == "qwen3.8:latest"
    assert result.worker.context_window == 32768


def test_what_was_written_parses_back(project):
    # ⚠ The property the shared rule exists for: a declaration the CLI accepted
    # must be one `parse_worker` reads. `context_window` is the trap — a `0`
    # written on a `claude` engine is REFUSED on the next parse.
    add_worker(project, "gpu1", manager="lead", follow_docs=[], **LOCAL)
    manifest = parse_worker(project / "workers" / "gpu1" / "worker.yml")
    assert isinstance(manifest, WorkerManifest), manifest
    assert (manifest.engine, manifest.model, manifest.agent) == (
        "local:small",
        "qwen3.8:latest",
        "goose",
    )
    assert manifest.context_window == 32768
    assert manifest.is_local is True


def test_a_claude_worker_is_added_exactly_as_before(project):
    # Empty `engine` keeps every pre-OL3 default, so the call that added a
    # Claude Worker before adds one now.
    result = add_worker(project, "alpha", manager="lead", follow_docs=[])
    assert result.ok, result.message
    assert result.worker.engine == "claude"
    assert result.worker.is_local is False
    manifest = parse_worker(project / "workers" / "alpha" / "worker.yml")
    assert isinstance(manifest, WorkerManifest), manifest
    assert manifest.context_window == 0


class TestWhatIsRefusedAndWhyNothingIsCreated:
    """⚠ Half a Worker whose `worker.yml` will not parse is worse than none:
    every later command that walks `workers/` reports it, and the person has to
    know to delete a directory to get out of it."""

    def _refused(self, project, name, **engine):
        result = add_worker(project, name, manager="lead", follow_docs=[], **engine)
        assert result.ok is False, result.message
        # Nothing created, so nothing to clean up by hand.
        assert not (project / "workers" / name).exists()
        return result.message

    def test_a_local_engine_must_say_all_three(self, project):
        said = self._refused(project, "w", engine="local:small")
        assert "must also say endpoint, model, agent" in said
        assert "a label, not a configuration" in said

    def test_a_local_engine_must_say_its_window(self, project):
        said = self._refused(project, "w", **{**LOCAL, "context_window": 0})
        # S33/S34: the server's default cannot be read before the model loads,
        # and a prompt over it is cut from the front with no error.
        assert "must also say context_window" in said
        assert "4,096" in said

    def test_a_window_below_the_measured_minimum_is_refused(self, project):
        said = self._refused(project, "w", **{**LOCAL, "context_window": 4096})
        assert "below 32768" in said

    def test_an_agent_rite_cannot_enforce_a_window_for_is_refused(self, project):
        # The Worker would be created correctly, pass `rite doctor`, and refuse
        # every turn it was given — a failure found at the end of a run instead
        # of at the one moment it costs a word to fix.
        said = self._refused(project, "w", **{**LOCAL, "agent": "aider"})
        assert "S35" in said
        assert "only 'goose'" in said

    def test_an_unknown_engine_is_refused(self, project):
        said = self._refused(project, "w", **{**LOCAL, "engine": "ollama"})
        assert "not one rite knows" in said

    def test_engine_keys_mean_nothing_on_a_claude_worker(self, project):
        said = self._refused(project, "w", endpoint="http://localhost:11434")
        assert "means nothing on a 'claude' engine" in said


def test_the_one_model_note_is_a_note_and_not_a_guard(project):
    """Two local Workers on different models thrash the card — and rite says so
    rather than refusing.

    ⚠ Sharing is the SCHEDULE's job (`local/loop.py`: "the share-one-model rule
    is the schedule's job, not this module's"), and rite gains no budget or
    quota concept from it. A second model the operator means to run at a
    different hour is not a mistake.
    """
    from rite_ai.cli.main import _one_model_per_fleet_note

    first = add_worker(project, "gpu1", manager="lead", follow_docs=[], **LOCAL)
    assert _one_model_per_fleet_note(project, first.worker) == []

    second = add_worker(
        project,
        "gpu2",
        manager="lead",
        follow_docs=[],
        **{**LOCAL, "model": "other:8b"},
    )
    # Not refused.
    assert second.ok, second.message
    note = _one_model_per_fleet_note(project, second.worker)
    assert len(note) == 1
    assert "qwen3.8:latest" in note[0]
    assert "does not refuse" in note[0]
    assert "rite schedule" in note[0]


def test_the_cli_passes_every_engine_flag_through(tmp_path, monkeypatch):
    """The wiring itself: a flag that reached no `add_worker` argument would
    leave the whole feature silently inert, which is the defect class OL3 left
    behind in the first place."""
    import click
    from click.testing import CliRunner

    from rite_ai.cli import main as cli

    seen: dict = {}

    def fake_add_worker(root, name, **kwargs):
        seen.update(kwargs)
        return type(
            "R",
            (),
            {
                "ok": True,
                "message": "ok",
                "worker": None,
                "cloned_modules": [],
                "failed_modules": [],
            },
        )()

    monkeypatch.setattr("rite_ai.workspace.add_worker", fake_add_worker)
    monkeypatch.setattr(
        cli, "_load_config_for_write", lambda: (tmp_path, _config_with_manager())
    )
    monkeypatch.setattr(
        "rite_ai.cli.module_docs.settle_module_docs", lambda r, m, f: []
    )
    result = CliRunner().invoke(
        cli.add_worker_cmd,
        [
            "gpu1",
            "--engine",
            "local:small",
            "--endpoint",
            "http://localhost:11434",
            "--model",
            "qwen3.8:latest",
            "--agent",
            "goose",
            "--context-window",
            "32768",
            "--no-follow-module-docs",
        ],
    )
    assert result.exit_code == 0, result.output
    assert seen["engine"] == "local:small"
    assert seen["endpoint"] == "http://localhost:11434"
    assert seen["model"] == "qwen3.8:latest"
    assert seen["agent"] == "goose"
    assert seen["context_window"] == 32768
    assert isinstance(click.Command, type)


def _config_with_manager():
    from rite_ai.config.models import CoordinationConfig, ProjectConfig

    return ProjectConfig(coordination=CoordinationConfig(managers=["lead"]))
