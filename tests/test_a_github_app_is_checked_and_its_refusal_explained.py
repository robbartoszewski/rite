"""`rite doctor` checks the GitHub App, and a refused mint says what to do.

SCRUM-16, SCRUM-19.

🔴 SCRUM-16. `rite doctor` said nothing about `github_app`, so its "ok" covered
an App whose installation had not granted a permission rite asks for. The
first to find out was `rite start`, refusing the Manager. `rite doctor
--network` now mints a token the way `rite start` does, and revokes it.

🔴 SCRUM-19. That refusal was GitHub's words alone: "HTTP 422: The permissions
requested are not granted to this installation", which names no permission
and no remedy. rite asks for `TOKEN_PERMISSIONS` explicitly, so it can read
the installation's grant with the same JWT and name the difference.

The key is a real RSA key (`openssl genrsa`), so `_mint` signs a real JWT;
only GitHub is faked.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from rite_ai.config.models import GithubAppConfig, ProjectConfig, TicketBackendConfig
from rite_ai.managers import github_access as ga

NOT_GRANTED = "The permissions requested are not granted to this installation."


@pytest.fixture(scope="module")
def pem(tmp_path_factory):
    key = tmp_path_factory.mktemp("app") / "app.pem"
    subprocess.run(
        ["openssl", "genrsa", "-out", str(key), "2048"], check=True, capture_output=True
    )
    return key.read_text()


def _config(app_id="1", repo="owner/board"):
    return ProjectConfig(
        ticket_backend=TicketBackendConfig(type="github", repo=repo),
        github_app=GithubAppConfig(app_id=app_id, installation_id="99"),
    )


def _minted(url, headers, body):
    assert url.endswith("/app/installations/99/access_tokens")
    assert body["permissions"] == ga.TOKEN_PERMISSIONS
    return 201, json.dumps(
        {"token": "ghs_throwaway", "expires_at": "2030-01-01T00:00:00Z"}
    )


def _refused(url, headers, body):
    return 422, json.dumps({"message": NOT_GRANTED})


def _grant(issues="read", account="User", login="rob", status=200):
    calls = []

    def get(url, headers):
        calls.append(url)
        assert url.endswith("/app/installations/99")
        assert headers["Authorization"].startswith("Bearer ")
        if status != 200:
            return status, json.dumps({"message": "Not Found"})
        return 200, json.dumps(
            {
                "permissions": {
                    "contents": "write",
                    "issues": issues,
                    "pull_requests": "write",
                    "metadata": "read",
                },
                "account": {"type": account, "login": login},
            }
        )

    get.calls = calls
    return get


class TestARefusedMintSaysWhatToDo:
    def _mint(self, pem, get):
        with pytest.raises(ga._MintError) as e:
            ga._mint("1", "99", pem, ["owner/board"], post=_refused, get=get)
        return str(e.value)

    def test_the_missing_permission_is_named_from_the_installations_grant(self, pem):
        said = self._mint(pem, _grant(issues="read"))
        assert "HTTP 422" in said and NOT_GRANTED.rstrip(".") in said
        assert "does not grant issues: write (it has read)" in said
        assert "pending permission update" in said
        assert "https://github.com/settings/installations/99" in said
        assert "contents: write" not in said.split("does not grant")[1]

    def test_an_organisations_installation_gets_its_own_settings_link(self, pem):
        said = self._mint(
            pem, _grant(issues="read", account="Organization", login="acme")
        )
        assert "https://github.com/organizations/acme/settings/installations/99" in said

    def test_a_grant_that_cannot_be_read_still_names_the_usual_one(self, pem):
        said = self._mint(pem, _grant(status=404))
        assert "could not read what the installation grants" in said
        assert "usually missing is issues: write" in said
        assert "pending permission update" in said

    def test_every_permission_granted_points_at_the_repositories(self, pem):
        said = self._mint(pem, _grant(issues="write"))
        assert "grants every permission rite asks for" in said
        assert "repositor" in said

    def test_control_a_refusal_that_is_not_about_permissions_is_left_as_it_is(
        self, pem
    ):
        """A 401 is a bad key or a clock, not a missing grant; the grant is
        not even asked for."""
        get = _grant()
        with pytest.raises(ga._MintError) as e:
            ga._mint(
                "1",
                "99",
                pem,
                ["owner/board"],
                post=lambda u, h, b: (401, json.dumps({"message": "bad jwt"})),
                get=get,
            )
        assert "bad jwt" in str(e.value) and "pending" not in str(e.value)
        assert get.calls == []

    def test_rite_start_carries_the_diagnosis(self, pem, tmp_path):
        """The same words reach the refusal `rite start` prints."""
        project = tmp_path / "p"
        (project / ".rite").mkdir(parents=True)
        home = tmp_path / "h"
        access, refusal = ga.open_access(
            project,
            "lead",
            _config(),
            get_secret=lambda k: pem if k == "github_app_key" else None,
            home=home,
            post=_refused,
            get=_grant(issues="read"),
        )
        assert access is None
        assert "does not grant issues: write" in refusal


class TestTheDoctorMint:
    def test_a_working_app_is_minted_and_revoked(self, pem):
        revoked = []

        def delete(url, headers):
            revoked.append((url, headers["Authorization"]))
            return 204, ""

        got = ga.check_app(
            _config(), get_secret=lambda k: pem, post=_minted, delete=delete
        )
        assert got.kind == "ok" and "revoked it" in got.detail
        assert revoked == [
            (f"{ga.GITHUB_API}/installation/token", "token ghs_throwaway")
        ]

    def test_a_token_that_cannot_be_revoked_is_said(self, pem):
        got = ga.check_app(
            _config(),
            get_secret=lambda k: pem,
            post=_minted,
            delete=lambda u, h: (401, ""),
        )
        assert got.kind == "ok" and "could not be revoked (HTTP 401)" in got.detail

    def test_a_refused_mint_is_refused_with_the_diagnosis(self, pem):
        got = ga.check_app(
            _config(),
            get_secret=lambda k: pem,
            post=_refused,
            get=_grant(issues="read"),
        )
        assert got.kind == "refused" and "does not grant issues: write" in got.detail

    def test_no_network_is_unknown_not_refused(self, pem):
        def down(u, h, b):
            raise ConnectionError("no route to host")

        got = ga.check_app(_config(), get_secret=lambda k: pem, post=down)
        assert got.kind == "unknown" and "no route to host" in got.detail

    def test_no_key_is_refused_by_name(self):
        got = ga.check_app(_config(), get_secret=lambda k: None)
        assert got.kind == "refused" and "github_app_key" in got.detail

    def test_no_app_is_no_check(self):
        assert ga.check_app(_config(app_id="")) is None


class TestDoctorSaysIt:
    def _rite(self, tmp_path):
        rite = tmp_path / ".rite"
        rite.mkdir()
        (rite / "config.yaml").write_text(
            "ticket_backend:\n  type: github\n  repo: owner/board\n"
            "github_app:\n  app_id: '1'\n  installation_id: '99'\n"
        )
        return rite

    def test_without_network_it_says_not_verified_and_counts_nothing(
        self, tmp_path, capsys, monkeypatch
    ):
        from rite_ai.cli import main

        called = []
        monkeypatch.setattr(ga, "check_app", lambda *a, **k: called.append(1))
        problems: list[str] = []
        main._doctor_github_app(self._rite(tmp_path), problems, network=False)
        out = capsys.readouterr().out
        assert "NOT verified" in out and "rite doctor --network" in out
        assert problems == [] and called == []

    @pytest.mark.parametrize(
        "kind, counted",
        [("ok", False), ("refused", True), ("unknown", False)],
    )
    def test_with_network_only_a_refusal_is_a_problem(
        self, tmp_path, capsys, monkeypatch, kind, counted
    ):
        from rite_ai.cli import main

        monkeypatch.setattr(ga, "check_app", lambda c: ga.AppCheck(kind, "detail"))
        monkeypatch.setattr("rite_ai.credentials.store.store_is_readable", lambda: True)
        problems: list[str] = []
        main._doctor_github_app(self._rite(tmp_path), problems, network=True)
        assert bool(problems) is counted, capsys.readouterr().out
