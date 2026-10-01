"""One Slack app, one project: refused, not only advised.

SPEC §9.16.6 (D-101) says each project uses its own Slack app, and until
this nothing checked. What sharing one does, read from `slack.py` and
observed in the v0.6.0 dogfood run, where a second project reused the first
project's bot token:

- **Both projects' Managers take the Owner's DM as an INSTRUCTION.** The DM's
  channel id comes from posting to `slack.owner_user`, so two projects on one
  app with one Owner get the SAME DM. Each relay delivers every top-level
  message in it with the `INSTRUCTION` header (`Listener._relay`), so an
  instruction meant for one project is acted on by both.
- **Each project's Manager posts into a conversation the other project
  reads.** That is the cross-project confidentiality failure rite fixed once
  for mail (DF3), reintroduced through configuration.
- **Stopping one project does not help.** A message sent while a project's
  Manager is not running stays in Slack and is delivered at its next start
  (A5), so the other project picks up instructions meant for the stopped one.

So an app is BOUND to one project, persistently, and a second project is
refused before its listener exists: nothing read, nothing delivered, nothing
posted. It is keyed on the app's IDENTITY, the workspace and bot user that
`auth.test` reports, not on which credential supplied the token. The token
can come from a project's namespace, the machine-global entry or the
environment (`credentials.store.get_scoped`), and all three reach the same
bot.

WHY A BINDING AND NOT A LOCK. A lock held while a relay runs would stop two
projects at once and miss the third bullet. The binding stays until someone
removes it.

WHY IT CANNOT BE RACED. The binding is published with `os.link` from a file
already written, which either creates the name with its full content or
fails because the name exists. Two projects binding one app at the same
instant get one winner. The loser reads the winner's record, which is
complete by construction.

WHERE IT CANNOT KNOW, IT REFUSES. If `auth.test` fails, or returns no
workspace or bot user, there is no identity to check, so the relay does not
open, and it says why. An unreadable binding file refuses too. Guessing
either way would be the confidentiality failure again.

⚠ **One machine only.** The bindings live in this machine's data directory.
Two machines running projects on one app are not seen (v0.8.0, multi-machine).
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

APPS_DIR_ENV = "RITE_SLACK_APPS_DIR"

_ID = re.compile(r"^[A-Z0-9]{2,32}$")


@dataclass(frozen=True)
class Identity:
    team: str
    """The workspace id, `T…`."""
    user: str
    """The bot's user id, `U…`. Distinct per app in a workspace."""

    @property
    def name(self) -> str:
        return f"{self.team}-{self.user}"


@dataclass
class Bound:
    identity: Identity
    newly: bool = False


@dataclass
class Refused:
    reason: str


def _apps_dir() -> Path:
    """Beside the Manager mailboxes, under no path any Manager's profile
    grants, so no Manager can edit or remove a binding."""
    override = os.environ.get(APPS_DIR_ENV)
    if override:
        return Path(override)
    from rite_ai.managers import github_access

    return github_access._credential_root().parent / "slack-apps"  # noqa: SLF001


def identity_of(token: str, *, call=None) -> Identity | Refused:
    """Which app this token belongs to, from `auth.test`, or why not."""
    from rite_ai.managers.slack import _call

    caller = call or _call
    try:
        who = caller("auth.test", token, {})
    except Exception as e:  # noqa: BLE001 - any failure means "cannot tell"
        return Refused(
            f"cannot tell which Slack app this token belongs to (auth.test "
            f"failed: {type(e).__name__}), so cannot tell whether another "
            f"project already uses it"
        )
    team = str((who or {}).get("team_id") or "")
    user = str((who or {}).get("user_id") or "")
    if not (who or {}).get("ok", True) or not _ID.match(team) or not _ID.match(user):
        return Refused(
            "cannot tell which Slack app this token belongs to (auth.test did "
            "not name a workspace and bot user), so cannot tell whether "
            "another project already uses it"
        )
    return Identity(team=team, user=user)


@dataclass(frozen=True)
class Sharing:
    """Whether this app is already another project's, as far as rite can tell.

    ⚠ **Three answers, not two.** "Could not ask" is not "shared" and is not
    "clear": reported as either, a person is told something rite does not
    know. `bind` collapses the unknown into a refusal, which is right where
    the choice is whether to OPEN A LISTENER — refusing costs a run, guessing
    costs the confidentiality failure §9.16.6 exists to prevent. It is wrong
    in a report, where nothing is at stake but what the reader is told.
    """

    kind: str
    """`ok`, `shared`, or `unknown`."""
    message: str = ""
    """What to tell the reader; empty for `ok`."""

    @property
    def is_a_problem(self) -> bool:
        """Only a KNOWN collision is a problem. An unknown is a gap in the
        report, and counting it would make `rite doctor` fail on an
        unreachable network."""
        return self.kind == "shared"


DEDICATED_APP = (
    "Give this project its own Slack app: create one at api.slack.com/apps, "
    "install it to the workspace, and store its bot token with `rite "
    "credential set slack`. It needs the scopes rite posts and reads with, "
    "and `reactions:read` — without that one, rite cannot see a reaction to a "
    "question, so only a reply in the question's thread confirms it reached "
    "you (add it under OAuth & Permissions)."
)


@dataclass(frozen=True)
class TokenCheck:
    """Whether a bot token is this app's, asked with a READ.

    ⚠ **`auth.test` only, never a post.** Setting a credential must not put a
    message in anyone's channel: a person setting rite up is not announcing it,
    and a setup command that posts is one that spams a workspace every time it
    is re-run. `auth.test` returns the workspace and bot user and writes
    nothing. A live POST to confirm delivery belongs in `rite doctor`, where
    the person asked for a check.

    Three answers, as everywhere else on this path: a token Slack REFUSED is
    not the same as a Slack that could not be reached.
    """

    kind: str
    """`ok`, `bad`, or `unknown`."""
    message: str = ""
    identity: Identity | None = None


def check_token(token: str, *, call=None) -> TokenCheck:
    """Ask Slack whether this token works, and whose app it is (read only)."""
    from rite_ai.managers.slack import _call

    caller = call or _call
    try:
        who = caller("auth.test", token, {})
    except Exception as e:  # noqa: BLE001 - any failure means "could not ask"
        return TokenCheck(
            "unknown",
            f"could not reach Slack to check the token ({type(e).__name__})",
        )
    who = who or {}
    if not who.get("ok", False):
        # Slack ANSWERED and said no. That is a bad token, not a bad network.
        return TokenCheck(
            "bad",
            f"Slack refused this token ({who.get('error') or 'not ok'}). A bot "
            "token starts with `xoxb-` and is the Bot User OAuth Token under "
            "OAuth & Permissions, not the App-Level or User token",
        )
    team, user = str(who.get("team_id") or ""), str(who.get("user_id") or "")
    if not (_ID.match(team) and _ID.match(user)):
        return TokenCheck(
            "unknown",
            "Slack accepted the token but named no workspace and bot user, so "
            "which app it belongs to cannot be told",
        )
    return TokenCheck("ok", identity=Identity(team=team, user=user))


def sharing_for(identity: Identity, project: Path) -> Sharing:
    """`sharing`, for a caller that already asked `auth.test` — so one guided
    setup makes ONE read of Slack, not one per question it answers."""
    holder = _holder_of(identity)
    if not holder or Path(holder) == project.resolve():
        return Sharing("ok")
    return Sharing(
        "shared",
        f"this Slack app (workspace {identity.team}, bot {identity.user}) is "
        f"already used by another project: {holder}. Two projects on one app "
        f"share the Owner's DM, so each takes the other's instructions and "
        f"reads the other's messages (SPEC §9.16.6). {DEDICATED_APP} If "
        f"{holder} no longer uses this one, remove {_binding_path(identity)} "
        f"and start again.",
    )


def _holder_of(identity: Identity) -> str:
    """Which project this app is bound to, or "" — READ ONLY.

    `bind` is the only other way to ask, and it answers by BINDING, which a
    report may not do: `rite doctor` on a project that has never run would
    take the app for itself and make the project that really uses it the
    second one. So the record is read and nothing is written.
    """
    try:
        return str(json.loads(_binding_path(identity).read_text())["project"])
    except (OSError, ValueError, KeyError, TypeError):
        return ""


def sharing(token: str, project: Path, *, call=None) -> Sharing:
    """Whether another project already uses this app (v0.7.0 dogfood S22a).

    Asked where the token is SET and where the project is CHECKED, not only
    where the listener opens. Until this, the collision surfaced at `rite
    start`, after the app was made, the token stored and the project
    configured — and the only guidance was "give this project its own Slack
    app", which is the heaviest step in the setup, offered last.
    """
    who = identity_of(token, call=call)
    if isinstance(who, Refused):
        return Sharing("unknown", who.reason)
    return sharing_for(who, project)


def _binding_path(identity: Identity) -> Path:
    return _apps_dir() / f"{identity.name}.json"


def bind(identity: Identity, project: Path) -> Bound | Refused:
    """Bind `identity` to `project`, or find it already bound. Never raises."""
    project = project.resolve()
    target = _binding_path(identity)
    record = {
        "project": str(project),
        "team": identity.team,
        "bot_user": identity.user,
        "bound_at": time.time(),
    }
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".bind-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(json.dumps(record) + "\n")
                f.flush()
                os.fsync(f.fileno())
            try:
                os.link(tmp, target)
                return Bound(identity=identity, newly=True)
            except FileExistsError:
                pass
        finally:
            Path(tmp).unlink(missing_ok=True)
    except OSError as e:
        return Refused(f"cannot record which project uses this Slack app: {e}")

    try:
        holder = json.loads(target.read_text())["project"]
    except (OSError, ValueError, KeyError, TypeError) as e:
        return Refused(
            f"this Slack app's binding {target} cannot be read ({e}), so "
            f"which project it belongs to is unknown"
        )
    if Path(holder) == project:
        return Bound(identity=identity)
    return Refused(
        f"this Slack app (workspace {identity.team}, bot {identity.user}) "
        f"belongs to another project: {holder}. Two projects on one app "
        f"share the Owner's DM, so each would take the other's instructions "
        f"and read the other's messages (SPEC §9.16.6). Give this project its "
        f"own Slack app. If {holder} no longer uses this one, remove "
        f"{target} and start again"
    )
