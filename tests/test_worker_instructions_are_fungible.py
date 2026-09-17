"""Two claims rite's generated files make about themselves.

Both were found auditing the first third-party run of rite (v0.2.0), where a
conscientious Owner read her own generated instructions and pre-assigned
unstarted tickets to named Workers — `6→C, 7→A, 8→B, 9→C` in her handover.
That was not a user error. The Worker file told her Workers had modules.
"""

from __future__ import annotations

from pathlib import Path

from rite_ai.cli.init.claude_gen import _format_commands, generate_claude_md
from rite_ai.cli.init.detect import ModuleCommands, module_commands
from rite_ai.config.models import Module, ProjectBrief, ProjectConfig, SandboxConfig
from rite_ai.workspace import add_worker


def _project(tmp_path: Path) -> Path:
    rite = tmp_path / ".rite"
    rite.mkdir()
    (rite / "brief.yaml").write_text("project:\n  name: t\n  role: owner\n")
    (rite / "modules.yaml").write_text("modules: {}\n")
    return tmp_path


class TestTheWorkerFileDoesNotSayWorkersOwnModules:
    """v0.3.0's changelog says the assignment guidance was fixed. It patched
    `claude_gen.py` — the OWNER's root file — while `workspace/manage.py`,
    the file a Worker session actually loads, still headed its module list
    "## Your modules". The half she was reading was the half left alone."""

    def _worker_claude_md(self, tmp_path: Path) -> str:
        root = _project(tmp_path)
        assert add_worker(root, "A").ok
        return (root / "workers" / "A" / "CLAUDE.md").read_text()

    def test_it_does_not_call_them_the_workers_modules(self, tmp_path):
        assert "## Your modules" not in self._worker_claude_md(tmp_path)

    def test_it_says_a_worker_is_a_workspace_not_a_module(self, tmp_path):
        text = self._worker_claude_md(tmp_path).lower()
        assert "not a module" in text, "the Worker file never states fungibility"
        assert "any worker can take any ticket" in text

    def test_it_says_claims_are_what_separate_workers(self, tmp_path):
        """The operative half. Knowing you are interchangeable is useless
        without knowing what actually keeps two Workers apart."""
        assert "rite claim" in self._worker_claude_md(tmp_path)


class TestCommandsAreNotClaimedAsDetectedWhenNothingWasDetected:
    """`rite init` wrote a module map asserting the commands were "read out
    of each module's own manifest by `rite init`" for two repos that held
    only a README — no pyproject.toml, no .xcodeproj, no Package.swift.
    The commands were authored and recorded in modules.yaml.

    Worse than cosmetic: the Worker file tells a Worker to "run them as
    written", so the provenance claim is what makes an unrunnable command
    trustworthy. Hers included `xcodebuild test -scheme Pulse` against a
    project with no Pulse scheme.
    """

    def test_a_module_with_no_manifest_is_not_reported_as_detected(self, tmp_path):
        """THE REGRESSION. `module_commands` set `detected=True` whenever
        anything was recorded, contradicting the field's own docstring —
        "`detected` is False when no known marker file was found"."""
        empty = tmp_path / "mod"
        empty.mkdir()
        (empty / "README.md").write_text("nothing to detect\n")

        module = Module(name="mod", path="mod", branch="main")
        module.commands.test = "pytest"

        cmds = module_commands(module, tmp_path, SandboxConfig())

        assert cmds.test == "pytest", "the recorded command must survive"
        assert not cmds.detected, (
            "no manifest exists, so nothing was detected — reporting "
            "detected=True is how the module map came to claim rite read a "
            "manifest that was never there"
        )

    def test_recorded_commands_still_render_when_nothing_was_detected(self, tmp_path):
        """The other half: honesty must not cost the commands. Keying the
        placeholder on `detected` would now hide commands that ARE recorded."""
        cmds = ModuleCommands(
            test="pytest",
            detected=False,
            source="modules.yaml",
            configured=frozenset({"test"}),
        )
        rendered = "\n".join(_format_commands(cmds))

        assert "pytest" in rendered
        assert "recorded in modules.yaml" in rendered
        assert "Commands: not detected" not in rendered

    def test_the_module_map_states_provenance_where_the_commands_are(self, tmp_path):
        """Where the correction had to land, and why.

        The blanket claim — "read out of each module's own manifest by `rite
        init`" — lives in the Ticket workflow section, which the
        section-history mechanism requires to render IDENTICALLY for every
        project. That is precisely why it could only ever be a blanket claim:
        a project-independent section cannot state a per-project fact, so it
        asserted the flattering one.

        The Modules section is per-project and is where the commands are
        listed, so provenance belongs there and can be true.

        ⚠ The blanket sentence itself is NOT removed here — editing that
        section makes `test_the_history_covers_this_versions_static_sections`
        fail until a release regenerates `section_history.py` from tags, and
        that path belongs to the release owner.
        """
        root = _project(tmp_path)
        (root / "mod").mkdir()
        (root / "mod" / "README.md").write_text("x\n")
        module = Module(name="mod", path="mod", branch="main")
        module.commands.test = "pytest"

        text = generate_claude_md(
            "owner",
            ProjectBrief(name="t", role="owner"),
            [module],
            ProjectConfig(),
            root,
        )

        modules_section = text.split("## Modules", 1)[1].split("\n## ", 1)[0]
        assert "recorded in modules.yaml" in modules_section
        assert "rite has run neither" in modules_section, (
            "the module map does not say a recorded command is unverified"
        )


class TestAMissingCommandDoesNotPointAtTheFileItWasMissingFrom:
    """Found by running `rite init` cold on a project shaped like the
    tester's: two empty module repos, commands recorded by hand afterwards.

    For a key nobody recorded, the line read:

        Install: not detected in modules.yaml — record it under `commands:`
        in `.rite/modules.yaml`, don't guess.

    It names the file it was not found in as the file to put it in. There is
    no manifest in that module at all, which is the actual thing to say —
    `modules.yaml` is only `source` because it is where the OTHER commands
    came from.
    """

    def _lines(self, *, detected: bool, source: str) -> str:
        from rite_ai.cli.init.claude_gen import _format_commands
        from rite_ai.cli.init.detect import ModuleCommands

        return "\n".join(
            _format_commands(
                ModuleCommands(
                    test="pytest",
                    detected=detected,
                    source=source,
                    configured=frozenset({"test"}),
                )
            )
        )

    def test_it_does_not_say_not_detected_in_modules_yaml(self):
        out = self._lines(detected=False, source="modules.yaml")
        assert "not detected in modules.yaml" not in out, out

    def test_it_says_there_is_no_manifest_to_detect_from(self):
        out = self._lines(detected=False, source="modules.yaml")
        assert "no manifest" in out.lower(), out
        assert "`commands:`" in out, "it must still say where to record one"

    def test_a_real_manifest_is_still_named(self):
        """The other half: when detection DID fire, naming the manifest it
        read is the useful part and must survive."""
        out = self._lines(detected=True, source="modules.yaml + pyproject.toml")
        assert "pyproject.toml" in out
