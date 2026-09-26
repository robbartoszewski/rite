"""C6/C26: a sandboxed Manager gets GitHub access, and never holds the keys.

`docs/design/V060_MANAGER_CREDENTIALS.md` has the measurements. These tests
pin the path the credential takes. The App key goes through a pipe to
openssl, and the token into a 0600 file named by a path. Every Manager's
profile denies Unix sockets except the name resolver's.

No real credential is used anywhere: a throwaway RSA key, a fake GitHub, and
a token that is a fixed string.
"""

from __future__ import annotations

import base64
import json
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from rite_ai.managers import github_access as ga

TOKEN = "ghs_THISisAfakeTOKENforTESTS0000000000000"

on_macos = pytest.mark.skipif(sys.platform != "darwin", reason="seatbelt is macOS")


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "proj"
    (root / ".rite").mkdir(parents=True)
    return root


@pytest.fixture
def home():
    # Short, because the agent's socket lives under it and a Unix socket path
    # is capped near 104 bytes; pytest's tmp_path is far too long.
    d = Path(tempfile.mkdtemp(prefix="rga", dir="/tmp")).resolve()
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _rsa_key(tmp_path) -> tuple[str, Path]:
    key = tmp_path / "app.pem"
    subprocess.run(
        ["openssl", "genrsa", "-out", str(key), "2048"], check=True, capture_output=True
    )
    pub = tmp_path / "app.pub.pem"
    subprocess.run(
        ["openssl", "rsa", "-in", str(key), "-pubout", "-out", str(pub)],
        check=True,
        capture_output=True,
    )
    return key.read_text(), pub


def _b64d(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


class TestTheProfile:
    def test_every_manager_loses_unix_sockets_except_the_resolver(self, project, home):
        lines = ga.profile_lines(project, "lead", home)
        assert "(deny network-outbound (remote unix-socket))" in lines
        assert any(ga.RESOLVER_SOCKET in line for line in lines)
        # Nothing else allowed back when this Manager has no credentials.
        allows = [line for line in lines if line.startswith("(allow")]
        assert len(allows) == 1

    def test_only_the_two_gh_files_are_granted_by_exact_path(self, project, home):
        ga._write_token(project, "lead", TOKEN, home)
        lines = ga.profile_lines(project, "lead", home)
        g = ga._gh_dir(project, "lead", home)
        assert f'(allow file-read* (literal "{g / "hosts.yml"}"))' in lines
        assert f'(allow file-read* (literal "{g / "config.yml"}"))' in lines
        cdir = ga._credential_dir(project, "lead", home)
        assert not any(f'(subpath "{cdir}")' in line for line in lines)
        assert not any("file-write" in line and str(g) in line for line in lines)

    def test_the_socket_denial_is_the_LAST_word_in_the_composed_profile(self, project):
        from rite_ai.managers.enclosure import compose

        text = compose(project, "lead")
        assert text.index("(deny network-outbound (remote unix-socket))") > text.index(
            "(allow network*)"
        ), "seatbelt takes the LAST match; a denial before the grant is void"


class TestTheToken:
    def test_the_jwt_verifies_against_the_apps_public_key(self, tmp_path):
        pem, pub = _rsa_key(tmp_path)
        jwt = ga._app_jwt("12345", pem, now=1_800_000_000)
        header, payload, sig = jwt.split(".")
        assert json.loads(_b64d(header)) == {"alg": "RS256", "typ": "JWT"}
        claims = json.loads(_b64d(payload))
        assert claims["iss"] == "12345"
        assert claims["exp"] - claims["iat"] <= 600, "GitHub caps a JWT at 10 minutes"
        (tmp_path / "sig").write_bytes(_b64d(sig))
        ok = subprocess.run(
            [
                "openssl",
                "dgst",
                "-sha256",
                "-verify",
                str(pub),
                "-signature",
                str(tmp_path / "sig"),
            ],
            input=f"{header}.{payload}".encode(),
            capture_output=True,
        )
        assert ok.returncode == 0, ok.stdout + ok.stderr

    def test_the_key_never_reaches_openssls_argv(self, tmp_path, monkeypatch):
        pem, _ = _rsa_key(tmp_path)
        seen = {}
        real = subprocess.run

        def spy(args, **kw):
            seen["argv"] = list(args)
            return real(args, **kw)

        monkeypatch.setattr(ga.subprocess, "run", spy)
        ga._app_jwt("1", pem)
        assert not any("PRIVATE KEY" in a for a in seen["argv"])
        assert any(a.startswith("/dev/fd/") for a in seen["argv"])

    def test_mint_asks_for_ONE_repository_and_named_permissions(self, tmp_path):
        pem, _ = _rsa_key(tmp_path)
        sent = {}

        def post(url, headers, body):
            sent.update(url=url, headers=headers, body=body)
            return 201, json.dumps(
                {"token": TOKEN, "expires_at": "2030-01-01T00:00:00Z"}
            )

        got = ga._mint("1", "99", pem, ["owner/board"], post=post)
        assert got.token == TOKEN
        assert sent["url"].endswith("/app/installations/99/access_tokens")
        assert sent["body"]["repositories"] == ["board"]
        assert sent["body"]["permissions"] == ga.TOKEN_PERMISSIONS
        assert "workflows" not in sent["body"]["permissions"]

    def test_a_refusal_carries_githubs_words(self, tmp_path):
        pem, _ = _rsa_key(tmp_path)

        def post(url, headers, body):
            return 404, json.dumps({"message": "Not Found"})

        with pytest.raises(ga._MintError, match="HTTP 404.*Not Found"):
            ga._mint("1", "99", pem, ["owner/board"], post=post)

    def test_the_token_file_is_0600_in_a_0700_directory(self, project, home):
        path = ga._write_token(project, "lead", TOKEN, home)
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
        assert ga.live_secrets(path.parent) == [TOKEN]

    def test_gh_gets_its_CURRENT_layout_so_it_never_migrates(self, project, home):
        """An old-layout file makes gh rewrite it on first read, which is a
        write the read-only grant refuses (measured)."""
        path = ga._write_token(project, "lead", TOKEN, home)
        assert "    users:\n        x-access-token:\n" in path.read_text()
        config = path.parent / "config.yml"
        assert config.read_text() == 'version: "1"\n'
        assert stat.S_IMODE(config.stat().st_mode) == 0o600


class TestTheLaunch:
    def test_the_pane_is_told_WHERE_the_token_is_never_the_token(self, project, home):
        ga._write_token(project, "lead", TOKEN, home)
        env = ga.pane_environment(project, "lead", home)
        assert env["GH_CONFIG_DIR"].endswith("/gh")
        assert all(TOKEN not in v for v in env.values())
        # git asks gh, after the helper list is reset past osxkeychain.
        assert env["GIT_CONFIG_VALUE_0"] == ""
        assert env["GIT_CONFIG_VALUE_1"] == "!gh auth git-credential"

    def test_every_pane_variable_is_cleared_for_tmux_argv(self, project, home):
        from rite_ai.managers.session import ALLOWED_ON_TMUX_ARGV

        ga._write_token(project, "lead", TOKEN, home)
        assert set(ga.pane_environment(project, "lead", home)) <= ALLOWED_ON_TMUX_ARGV

    def test_nothing_set_up_hands_the_pane_nothing(self, project, home):
        assert ga.pane_environment(project, "lead", home) == {}

    def test_no_app_still_gets_its_OWN_gh_config_never_the_operators(
        self, project, home
    ):
        """W8: without its own GH_CONFIG_DIR, gh reads `~/.config/gh`, the
        operator's login, in plain text wherever gh has no keyring."""
        g = ga._own_gh_config(project, "lead", home)
        env = ga.pane_environment(project, "lead", home)
        assert env["GH_CONFIG_DIR"] == str(g)
        assert not (g / "hosts.yml").exists(), "no App, no token"
        assert env["GIT_CONFIG_VALUE_1"] == "!gh auth git-credential"


class TestRefresh:
    def test_a_failed_refresh_KEEPS_the_file_and_says_so(self, project, home, tmp_path):
        pem, _ = _rsa_key(tmp_path)
        access = ga.Access(
            root=project,
            manager="lead",
            home=home,
            app=("1", "99", ["owner/board"]),
            app_key=pem,
            token=TOKEN,
            expires_at=1_000.0,
            post=lambda u, h, b: (500, json.dumps({"message": "boom"})),
        )
        ga._write_token(project, "lead", TOKEN, home)
        said = access.refresh(now=2_000.0)
        assert said and "could not be refreshed" in said[0] and "boom" in said[0]
        assert "Bad credentials" in said[0], "the Manager's symptom is named"
        # The file stays: removing it would make gh refuse (exit 4), or with
        # the operator's own config shape go ANONYMOUS.
        assert ga.live_secrets(ga._gh_dir(project, "lead", home)) == [TOKEN]

    def test_a_token_with_time_left_is_not_reminted(self, project, home):
        calls = []
        access = ga.Access(
            root=project,
            manager="lead",
            home=home,
            app=("1", "99", ["owner/board"]),
            app_key="unused",
            token=TOKEN,
            expires_at=10_000.0,
            post=lambda *a: calls.append(a) or (201, "{}"),
        )
        assert access.refresh(now=10_000.0 - ga.REFRESH_MARGIN_SECONDS - 1) == []
        assert calls == []


class TestOpening:
    def _config(self, app_id="", repo="owner/board"):
        from rite_ai.config.models import (
            GithubAppConfig,
            ProjectConfig,
            TicketBackendConfig,
        )

        return ProjectConfig(
            ticket_backend=TicketBackendConfig(type="github", repo=repo),
            github_app=GithubAppConfig(app_id=app_id, installation_id="99"),
        )

    def test_nothing_configured_is_not_an_error_and_clears_leftovers(
        self, project, home
    ):
        ga._write_token(project, "lead", TOKEN, home)
        access, refusal = ga.open_access(
            project,
            "lead",
            self._config(),
            get_secret=lambda k: None,
            home=home,
        )
        assert (access, refusal) == (None, "")
        g = ga._gh_dir(project, "lead", home)
        assert not (g / "hosts.yml").exists(), "the leftover token is gone"
        assert ga.pane_environment(project, "lead", home)["GH_CONFIG_DIR"] == str(g)

    def test_an_app_without_its_key_is_REFUSED_by_name(self, project, home):
        access, refusal = ga.open_access(
            project,
            "lead",
            self._config(app_id="1"),
            get_secret=lambda k: None,
            home=home,
        )
        assert access is None and "github_app_key" in refusal and "--stdin" in refusal

    def test_a_mint_github_refuses_is_a_refusal_to_start(self, project, home, tmp_path):
        pem, _ = _rsa_key(tmp_path)
        access, refusal = ga.open_access(
            project,
            "lead",
            self._config(app_id="1"),
            get_secret=lambda k: pem if k == "github_app_key" else None,
            home=home,
            post=lambda u, h, b: (401, json.dumps({"message": "bad jwt"})),
        )
        assert access is None and "bad jwt" in refusal


@on_macos
def test_inside_the_profile_no_unix_socket_is_reachable(project, tmp_path):
    """The operator's SSH agent is a Unix socket, and so is anything else a
    user runs. Measured before this: the shipped profile reached them all."""
    from rite_ai.managers.enclosure import compose

    other = Path(tempfile.mkdtemp(dir="/tmp")) / "o.sock"
    listener = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import socket,time;s=socket.socket(socket.AF_UNIX);"
            f"s.bind({str(other)!r});s.listen(1);time.sleep(30)",
        ]
    )
    try:
        import time as _t

        _t.sleep(0.5)
        profile = tmp_path / "p.sb"
        profile.write_text(compose(project, "lead"))
        probe = (
            "import socket;s=socket.socket(socket.AF_UNIX);"
            f"s.connect({str(other)!r});print('CONNECTED')"
        )
        outside = subprocess.run(
            [sys.executable, "-c", probe], capture_output=True, text=True
        )
        assert "CONNECTED" in outside.stdout, "the control: the socket is live"
        inside = subprocess.run(
            ["sandbox-exec", "-f", str(profile), sys.executable, "-c", probe],
            capture_output=True,
            text=True,
        )
        assert inside.returncode != 0 and "Operation not permitted" in inside.stderr
    finally:
        listener.kill()


@on_macos
def test_inside_the_profile_ONLY_the_two_gh_files_are_readable(project):
    """The directory is under no granted path (not /tmp, which the profile
    grants, and which made an earlier version of this check vacuous)."""
    from rite_ai.managers.enclosure import compose

    path = ga._write_token(project, "lead", TOKEN)
    other = path.parent / "other"
    other.write_text("not granted")
    profile = project.parent / "p.sb"
    profile.write_text(compose(project, "lead"))

    def inside(*argv):
        return subprocess.run(
            ["sandbox-exec", "-f", str(profile), *argv], capture_output=True, text=True
        )

    assert inside("/bin/cat", str(path)).stdout.count(TOKEN) == 2
    assert inside("/bin/cat", str(path.parent / "config.yml")).returncode == 0
    third = inside("/bin/cat", str(other))
    assert third.returncode != 0 and "Operation not permitted" in third.stderr
    write = inside("/bin/sh", "-c", f"echo x >> {path}")
    assert write.returncode != 0 and "Operation not permitted" in write.stderr


def test_the_journal_redacts_the_live_token_by_exact_value(project, home, monkeypatch):
    from rite_ai.managers.journal import _redacted

    ga._write_token(project, "lead", TOKEN, home)
    monkeypatch.setenv("GH_CONFIG_DIR", str(ga._gh_dir(project, "lead", home)))
    # The shape `cat hosts.yml` prints, which the structural rule misses.
    out = _redacted(f"I read my config: oauth_token: {TOKEN}")
    assert TOKEN not in out and "[redacted]" in out
