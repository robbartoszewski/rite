"""The generated template history stays within the line limit as tags accrue.

Measured after tagging v0.7.0a3, the tenth tag: `tools/template_history.py`
wrote its tag list on one docstring line of 91 characters, `ruff check`
(E501, 88) failed, and the post-tag commit (`docs/releasing.md` step 6) could
not go through the gate. `ruff format` does not wrap a docstring, so the
tool's own formatting step never caught it.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "tools" / "template_history.py"


def _tool():
    spec = importlib.util.spec_from_file_location("template_history_tool", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_header_fits_however_many_tags_there_are():
    tags = [f"v0.{minor}.{patch}" for minor in range(12) for patch in range(4)]
    tags += ["v0.7.0a1", "v0.7.0a2", "v0.7.0a3"]
    lines = _tool().header(tags)
    assert max(len(line) for line in lines) <= 88
    # Every tag is still named, in order.
    assert ", ".join(tags) in " ".join(lines[3:-1])


def test_the_committed_history_fits_too():
    written = TOOL.parents[1] / "src" / "rite_ai" / "update" / "template_history.py"
    assert max(len(line) for line in written.read_text().splitlines()) <= 88
