"""`rite doctor` says which build it is, not just which version.

`install.sh` installs from a git tag, and a tag is a movable pointer — this
project's own `v0.1.0` was re-pointed once, so two machines reporting
`rite 0.1.0` were not running the same code and nothing they printed could
tell them apart. pip, uv and pipx all record a direct install's source in
`direct_url.json` (PEP 610), commit included.

The `uv tool install git+file:///…@v0.3.0` case is the measured one — the
first sample below is that file, byte for byte.
"""

from __future__ import annotations

from click.testing import CliRunner

from rite_ai.cli.main import cli
from rite_ai.update import install_origin

_FROM_A_TAG = (
    '{"url":"file:///work/rite","vcs_info":{"vcs":"git","commit_id":'
    '"05b91d88e83ed54bb23a4b39851b645e97f1a40c","requested_revision":"v0.3.0"}}'
)
_EDITABLE = '{"url":"file:///work/rite","dir_info":{"editable":true}}'


def test_a_git_install_names_the_tag_and_the_commit():
    assert install_origin(_FROM_A_TAG) == "installed from v0.3.0, commit 05b91d88e83e"


def test_a_commit_install_says_the_commit_alone():
    raw = '{"url":"x","vcs_info":{"vcs":"git","commit_id":"abcdef1234567890"}}'
    assert install_origin(raw) == "installed from commit abcdef123456"


def test_a_dev_checkout_says_where_it_is():
    assert install_origin(_EDITABLE) == "dev checkout at /work/rite"


def test_an_install_that_records_nothing_says_nothing():
    assert install_origin("") == ""
    assert install_origin('{"url":"https://pypi/rite_ai-0.3.0.whl"}') == ""
    assert install_origin("not json") == ""
    assert install_origin("[]") == ""


def test_doctor_prints_it_beside_the_version(tmp_path, monkeypatch):
    monkeypatch.setattr("rite_ai.update.install_origin", lambda raw=None: "x")
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(cli, ["doctor"])
    assert "rite " in result.output
    assert "(x)" in result.output


def test_doctor_without_provenance_prints_the_version_alone(tmp_path, monkeypatch):
    monkeypatch.setattr("rite_ai.update.install_origin", lambda raw=None: "")
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(cli, ["doctor"])
    assert "(" not in result.output.splitlines()[0]
