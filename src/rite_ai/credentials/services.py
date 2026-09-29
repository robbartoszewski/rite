"""What a credential BELONGS TO, rather than what rite calls it (§10.5).

`rite credential set` took a KEY — `jira_email`, `jira_token` — which is
rite's internal vocabulary, and required the user to know both that JIRA
needs two of them and what each is called. The reported failure is the
direct consequence: someone reaching for their JIRA login typed
`rite credential set you@example.com`, rite stored the ADDRESS
as a key name and printed "stored", and JIRA was still unconfigured.

Taking a SERVICE instead makes that mistake unavailable rather than
merely detectable: `jira` is validated against this registry, and rite
then asks for the fields JIRA actually has, in order, labelled in the
user's vocabulary.

**Each service carries its own field list.** Not one hardcoded
username-then-token pair: a GitHub fine-grained PAT has no username at
all, and a service that is key-plus-secret has two secrets and no
address. A fixed pair of prompts would have made every service that is
not JIRA-shaped either wrong or a `custom`, and `custom` would have
become the dumping ground for everything real.

**The key names are unchanged.** A field's key is `<service>_<field>`,
which yields exactly the `jira_email` / `jira_token` / `github_token`
that already exist, so nothing stored before this existed needs moving
or re-typing.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Field:
    """One thing a service needs from the user."""

    name: str
    """Suffix of the stored key: `<service>_<name>`."""

    prompt: str
    """What the user is asked, in THEIR vocabulary — "JIRA account email",
    not "jira_email"."""

    secret: bool = True
    """Hidden input with a confirmation prompt. False for the parts that
    are not secrets — an account email is not, and hiding it makes it
    impossible to check a typo in the one field most likely to have
    one."""

    config_path: str = ""
    """Dotted path in `config.yaml` when this field is CONFIGURATION
    rather than a credential — `ticket_backend.site`, say.

    A JIRA integration needs a site and a project key as well as a token,
    and those are not secrets: they are the same for everyone on the
    team. Routing them to the committed `config.yaml` rather than the
    keychain is what completes the `.env.example` pattern the namespace
    started — a teammate clones the project, already has the site and the
    board key, and only has to supply their own token. Setting up the
    integration is then one command instead of "set a credential, then
    separately discover you also needed config".

    Empty means the field is a secret and goes to the keychain."""

    env: str = ""
    """The environment variable this field is delivered as when it is
    injected into a Worker's sandbox. Empty means rite has no injection
    story for it yet, which is deliberate for anything the sandbox does
    not need."""

    multiline: bool = False
    """A private key, several lines long. A one-line prompt cannot take it,
    and `--value` would put it on argv, so it is set only with
    `rite credential set <key> --stdin`."""


@dataclass(frozen=True)
class Service:
    """A thing a project authenticates to."""

    name: str
    label: str
    fields: tuple[Field, ...]
    note: str = ""
    board_type: str = ""
    """The `ticket_backend.type` this service is, when it is a ticket board.
    Setting its config fields on a project with no board (`type: none`) makes
    it the board: recording the site and the project key while leaving `none`
    configured a board rite never reads (v0.7.0 dogfood)."""

    @property
    def secrets(self) -> tuple[Field, ...]:
        return tuple(f for f in self.fields if not f.config_path)

    @property
    def config_fields(self) -> tuple[Field, ...]:
        return tuple(f for f in self.fields if f.config_path)


SERVICES: dict[str, Service] = {
    "jira": Service(
        name="jira",
        label="JIRA (Atlassian) — the ticket board",
        board_type="jira",
        fields=(
            Field(
                "site",
                "JIRA site (e.g. yourteam.atlassian.net)",
                secret=False,
                config_path="ticket_backend.site",
            ),
            Field(
                "email",
                "JIRA account email (the address you log in with)",
                secret=False,
                env="JIRA_EMAIL",
            ),
            Field("token", "JIRA API token", secret=True, env="JIRA_API_TOKEN"),
            Field(
                "board",
                "JIRA project key for the board (e.g. XYZ)",
                secret=False,
                config_path="ticket_backend.projects.workers",
            ),
        ),
    ),
    "github": Service(
        name="github",
        label="GitHub — repository access",
        # ONE field, no username. A fine-grained PAT carries its own
        # identity; asking for a username here would be asking for
        # something GitHub does not use, and the user would have to
        # invent an answer.
        fields=(
            Field(
                "token",
                "GitHub personal access token",
                secret=True,
                env="GITHUB_TOKEN",
            ),
        ),
        note=(
            # This said "One token per worker, scoped to the project's repos"
            # and cited §5.3.4 — the section that RETIRED per-Worker scoping.
            # The citation pointed at its own refutation, which is the worst
            # kind of stale reference: it reads as authority for the claim.
            "The project's token, shared by every worker (§5.3.4 — Workers "
            "are fungible). Scoped to the project's repos, not to a worker. "
            "`rite add worker --scoped-token` provisions one worker its own "
            "instead, if you want that."
        ),
    ),
    "slack": Service(
        name="slack",
        label="Slack — the relay that reads and writes a Manager's mailbox",
        # ONE field. A bot token carries the workspace and the identity, so
        # there is nothing else to ask for — the same shape as GitHub's PAT
        # and for the same reason. The channel is configuration rather than a
        # credential and belongs with the Slack settings.
        fields=(
            Field(
                "bot_token",
                "Slack bot token (starts with xoxb-)",
                secret=True,
                env="SLACK_BOT_TOKEN",
            ),
        ),
        note=(
            # Read by the relay inside `rite start`, on the host. Workers do
            # not receive it (§5.3.4, `WORKER_SERVICES`): until 2026-09-29
            # they did, because every Worker got every credential, and this
            # was the first credential where that rule cost without buying.
            "Read by the relay inside `rite start`, on the host. Workers do "
            "not receive it (§5.3.4)."
        ),
    ),
    # ⚠ C6/C26: a credential that exists ONLY outside a Manager's sandbox.
    # No `env`, deliberately, and not in `WORKER_SERVICES`: it is never
    # handed to a Worker. See
    # `managers/github_access.py` for the path each takes.
    "github_app": Service(
        name="github_app",
        label="GitHub App — mints a sandboxed Manager's one-hour, one-repository token",
        fields=(
            Field(
                "key",
                "GitHub App private key (.pem)",
                secret=True,
                multiline=True,
            ),
        ),
        note=(
            "Read only by `rite start`, outside the sandbox. The App's id and "
            "installation id go in config.yaml under github_app."
        ),
    ),
    # ⚠ No `env`, like `github_app`, and for the reason Robert gave: the key
    # is not handed to a pane or put in the environment of anything that
    # does not need it. A field with `env` goes to EVERY Worker through
    # yoloAI's `--env`, which puts it on yoloAI's argv and in the pane's
    # launch line. A Cursor Worker gets it as a read-only file (CW2) and a
    # Cursor Manager as a per-Manager copy (CU4), each read into the agent's
    # own environment at `exec`. Measured in `spikes/CU1b-...`.
    "cursor": Service(
        name="cursor",
        label="Cursor — the API key a Cursor Manager or Worker signs in with",
        fields=(Field("api_key", "Cursor API key", secret=True),),
        note=(
            "From Cursor's dashboard. A stored login does not work inside "
            "rite's sandbox (measured), so the API key is the route. From a "
            "file: `rite credential set cursor_api_key --stdin < <file>`."
        ),
    ),
    "claude": Service(
        name="claude",
        label="Claude Code — the login a sandboxed Worker uses",
        # A sandboxed session cannot read the Claude login in the macOS
        # keychain and reports that its login has expired. The token
        # `claude setup-token` prints is yoloAI's documented answer; storing
        # it here means every sandbox gets it, whichever terminal started it.
        fields=(
            Field(
                "token",
                "Claude Code OAuth token (from `claude setup-token`)",
                secret=True,
                env="CLAUDE_CODE_OAUTH_TOKEN",
            ),
        ),
        note="Run `claude setup-token` first; it prints the token to paste here.",
    ),
}


def service_key(service: str, field: str) -> str:
    """The stored key for one field. `jira` + `email` -> `jira_email`,
    which is the key that already exists."""
    return f"{service}_{field}"


def canonical_service(name: str) -> str | None:
    """The registry key for what the user typed, or None.

    **Case-insensitive on purpose.** `JIRA` is how Atlassian writes it, how
    this project's own docs write it, and therefore what someone types
    from memory. Rejecting it to insist on `jira` would fail the first
    real use of this interface on a capital letter — which is precisely
    the class of "rite's vocabulary, not yours" friction that taking a
    service name was meant to remove."""
    return name.strip().lower() if name.strip().lower() in SERVICES else None


def is_service(name: str) -> bool:
    return canonical_service(name) is not None


def suggest_service(name: str) -> str | None:
    """The closest service to a typo'd one, or None. `jria` -> `jira`."""
    import difflib

    matches = difflib.get_close_matches(
        name.strip().lower(), list(SERVICES), n=1, cutoff=0.6
    )
    return matches[0] if matches else None


def describe_services() -> list[str]:
    lines = []
    for svc in SERVICES.values():
        fields = ", ".join(f.name for f in svc.fields)
        lines.append(f"  {svc.name:<10} {svc.label}  [{fields}]")
    return lines


def how_to_set(key: str) -> str:
    """The argument to `rite credential set` that sets `key`: its service when
    one owns it and a prompt can take it, otherwise the key itself.

    ⚠ **The one answer, for every hint rite prints.** Hints named the key
    (`set github_token`) while the listing named the service (`set github`),
    so one `rite doctor` report told a person two different commands for one
    gap (v0.7.0 dogfood). A multi-line field stays the key: `set <service>`
    refuses it and sends the person to `set <key> --stdin`. A per-Worker
    sandbox token is not a service and never will be."""
    for svc in SERVICES.values():
        for f in svc.fields:
            if service_key(svc.name, f.name) == key:
                return key if f.multiline else svc.name
    return key
