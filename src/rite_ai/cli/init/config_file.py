"""Parse the `--config <file>` preset for non-interactive `rite init`.

The file uses the same top-level shape as `brief.yaml` (SPEC.md §9.2), extended
with optional `modules`, `operations`, and `knowledge` sections so a single
file can drive the whole questionnaire without prompting.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class ConfigFileError:
    file: str
    message: str


class Preset:
    """Thin wrapper over the parsed config-file dict with dotted lookups.

    A missing key returns None, which callers treat as "no preset — prompt or
    default" — never as an invented value.
    """

    def __init__(self, data: dict):
        self._data = data if isinstance(data, dict) else {}

    def get(self, dotted_path: str, default=None):
        node = self._data
        for part in dotted_path.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node if node is not None else default

    def raw_modules(self) -> dict:
        modules = self._data.get("modules", {})
        return modules if isinstance(modules, dict) else {}

    def has_modules(self) -> bool:
        return "modules" in self._data


def load_preset(path: Path | None) -> Preset | ConfigFileError:
    if path is None:
        return Preset({})
    if not path.exists():
        return ConfigFileError(str(path), "file not found")
    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        return ConfigFileError(str(path), f"invalid YAML: {e}")
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        return ConfigFileError(str(path), "expected a YAML mapping at top level")
    return Preset(raw)
