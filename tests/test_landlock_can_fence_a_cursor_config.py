"""Kernel evidence: Landlock CAN keep a file unwritable inside a directory
whose subdirectory stays writable, and why that does not help Cursor.

With a Cursor config directory granted read-only and only its `chats/`
writable, the kernel refuses overwriting, deleting, renaming over and creating
a `.tmp` beside `cli-config.json`, while a chat can be written; the control
(the whole directory writable) lets the file be overwritten.

Cursor does not survive that layout: it creates a temp file beside its config
and renames it over the config on every turn, and exits 1 when it cannot
(`spikes/CU1c-cursor-authenticated-measurements.md`). So the allowlist in that
file is detection-only (the CU8 row). This test pins the kernel half so the
reason is not re-derived: the limitation is Cursor's rewrite, not Landlock.
"""

from __future__ import annotations

import os

from rite_ai.managers import landlock
from test_landlock_really_confines import NO_LANDLOCK, _in_child, _policy


def _layout(tmp_path):
    config_dir = tmp_path / "cursor"
    chats = config_dir / "chats"
    chats.mkdir(parents=True)
    config = config_dir / "cli-config.json"
    config.write_text('{"permissions": {"allow": ["Shell(ls)"]}}')
    return config_dir, chats, config


def _attempts(config_dir, chats, config) -> dict[str, bool]:
    """Each attempt, and whether it SUCCEEDED."""
    out = {}

    def tried(name, fn):
        try:
            fn()
            out[name] = True
        except OSError:
            out[name] = False

    tried("write a chat", lambda: (chats / "store.db").write_text("x"))
    tried("read the config", config.read_text)
    tried("overwrite the config", lambda: config.write_text("{}"))
    tried(
        "create the .tmp Cursor renames from",
        lambda: (config_dir / "cli-config.json.tmp").write_text("{}"),
    )
    tried("delete the config", lambda: os.unlink(config))
    (chats / "evil.json").write_text("{}") if out["write a chat"] else None
    tried(
        "rename a file over the config", lambda: os.rename(chats / "evil.json", config)
    )
    return out


@NO_LANDLOCK
def test_a_read_only_directory_with_writable_chats_fences_the_config(tmp_path):
    config_dir, chats, config = _layout(tmp_path)

    def child():
        landlock.apply(_policy(readable=[config_dir], writable=[chats]))
        got = _attempts(config_dir, chats, config)
        wanted = {
            "write a chat": True,
            "read the config": True,
            "overwrite the config": False,
            "create the .tmp Cursor renames from": False,
            "delete the config": False,
            "rename a file over the config": False,
        }
        wrong = {k: v for k, v in got.items() if wanted[k] != v}
        print("WRONG:", wrong) if wrong else None
        return 0 if not wrong else 1

    assert _in_child(child) == 0
    assert config.read_text() == '{"permissions": {"allow": ["Shell(ls)"]}}'


@NO_LANDLOCK
def test_control_todays_whole_directory_grant_lets_it_be_rewritten(tmp_path):
    """CU4 as merged grants the whole directory writable. Here the overwrite
    must SUCCEED, or the test above could pass with a probe that sees nothing."""
    config_dir, chats, config = _layout(tmp_path)

    def child():
        landlock.apply(_policy(writable=[config_dir]))
        return 0 if _attempts(config_dir, chats, config)["overwrite the config"] else 1

    assert _in_child(child) == 0
