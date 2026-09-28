"""CU8 on Linux: can Landlock keep a Manager from rewriting Cursor's allowlist?

The claim this tests, before it goes in any public document: "Landlock cannot
deny one file inside a directory it grants, and the directory must stay
writable because Cursor keeps its chats there". The first half is true: a
Landlock rule has no deny. The second half assumed the whole directory must be
granted writable. But Landlock grants by PATH, and chats live in a
subdirectory (`<CURSOR_CONFIG_DIR>/chats`, read from Cursor's bundle). So a
policy can grant the config directory READ-only and `chats/` writable, and
simply not grant the config file any write. This checks that the kernel
enforces exactly that.

What it does NOT show: whether Cursor still works when it cannot rewrite its
own config or `statsig-cache.json` at the directory's root during a real turn.
Its config write is a `.tmp` file renamed over the original (bundle); some of
its write paths swallow a failure and one re-throws. That needs an
authenticated turn on Linux, and is held.
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
