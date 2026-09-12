"""What a credential BELONGS TO, rather than what rite calls it (§10.5).

`rite credential set` took a KEY — `jira_email`, `jira_token` — which is
rite's internal vocabulary, and required the user to know both that JIRA
needs two of them and what each is called. The reported failure is the
direct consequence: someone reaching for their JIRA login typed
`rite credential set robbartoszewski@gmail.com`, rite stored the ADDRESS
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


@dataclass(frozen=True)
class Service:
    """A thing a project authenticates to."""

    name: str
    label: str
    fields: tuple[Field, ...]
    note: str = ""

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
                "JIRA project key for the board (e.g. BEN)",
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
            "One token per worker, scoped to the project's repos — "
            "`rite add worker` walks through it (§5.3.4)."
        ),
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
