"""Worker process sandboxing via yoloAI (SPEC §5.3, D-26, D-30, D-31).

Optional (`sandbox.enabled` in `config.yaml`, default `false`). Wraps
yoloAI's CLI directly (`yoloai new/stop/destroy/ls`) — this is the
scriptable path for launching a sandbox from `rite`'s own commands or a
shell. The MCP path (`yoloai mcp serve`) is a separate thing: a Manager
session calls those tools directly as its own MCP client, with no
approval gate and no rite code in between (§5.3.1) — this module does not
implement or depend on that path.

Two things here are load-bearing and easy to get backwards, both found by
running the real `yoloai` binary rather than trusting its docs or SPEC's
own prose:

1. **The `--backend` value is `"seatbelt"`, not `"sandbox-exec"`** (D-30).
   macOS's sandboxing mechanism is called `sandbox-exec` at the OS level
   and Seatbelt internally at Apple — yoloAI's own name for that backend,
   confirmed against `yoloai system backends` and accepted by `yoloai new
   --backend`, is `seatbelt`. `SandboxConfig.backend` carries this value;
   do not reintroduce `"sandbox-exec"` as a default.
2. **Token delivery is `--env`, never a file or a CLI argument** (D-31,
   §5.3.3) — a file persists inside the sandbox after the run, and a CLI
   argument is visible to `ps`/process listings within the same sandbox
   namespace.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from rite_ai.config.models import Module, SandboxConfig

TOKEN_ENV_VAR = "GITHUB_TOKEN"
CLAUDE_TOKEN_ENV_VAR = "CLAUDE_CODE_OAUTH_TOKEN"


def _yoloai_binary() -> str | None:
    return shutil.which("yoloai")


def is_installed() -> bool:
    """Whether a file named `yoloai` exists on PATH.

    Deliberately NOT named `is_available`. A file under that name is not
    a working sandbox, and the gap between the two is exactly the case
    that bit: yoloAI present but broken returned a fabricated count of
    zero, and the worker cap silently stopped capping. Use this only to
    tell "not installed" apart from "installed and something is wrong" —
    never as the answer to "will a sandbox contain a worker".
    `verify_sandbox` answers that.
    """
    return _yoloai_binary() is not None


# Back-compat alias. `is_available` promised more than a PATH lookup can
# deliver, which is why it is no longer that name.
is_available = is_installed


@dataclass
class BackendAvailability:
    name: str
    available: bool
    note: str = ""


def available_backends(
    timeout: int = 15,
) -> list[BackendAvailability] | CountUnavailable:
    """What yoloAI says its backends can do ON THIS MACHINE.

    `yoloai system backends --json` reports availability per backend with
    a reason when it is not. Nothing in rite consulted it before, so a
    project configured for a backend this platform cannot run — the
    default `seatbelt` on anything that is not macOS — found out at the
    first `rite sandbox start`.
    """
    binary = _yoloai_binary()
    if binary is None:
        return CountUnavailable("yoloai not found on PATH")
    try:
        proc = subprocess.run(
            [binary, "system", "backends", "--json"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            env=sandbox_environment(),
        )
    except subprocess.TimeoutExpired:
        return CountUnavailable(
            f"`yoloai system backends --json` timed out after {timeout}s"
        )
    except OSError as e:
        return CountUnavailable(f"`yoloai system backends --json` could not run: {e}")
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        return CountUnavailable(
            f"`yoloai system backends --json` exited {proc.returncode}: {detail[:200]}"
        )
    try:
        data = json.loads(proc.stdout)
        entries = data["backends"]
    except (ValueError, KeyError, TypeError):
        return CountUnavailable(
            "`yoloai system backends --json` did not return the expected JSON — "
            "this rite may be too old for the installed yoloai"
        )
    return [
        BackendAvailability(
            name=str(e.get("name", "")),
            available=bool(e.get("available")),
            note=str(e.get("note") or ""),
        )
        for e in entries
        if isinstance(e, dict)
    ]


# Backends rite has verified its OWN safety property inside, not merely
# ones yoloAI can start. SPEC §5.3: 88 concurrent claim attempts from four
# sandboxed Workers gave exactly one grant per round on seatbelt.
VERIFIED_BACKENDS = ("seatbelt",)

# Backends measured NOT to work for rite, with why. `flock` is a no-op
# inside a docker sandbox and every claim rests on `flock`, so two Workers
# can hold the same path and both be told "claimed" (SPEC §5.3).
UNSUITABLE_BACKENDS = {
    "docker": "`flock` is a no-op inside a docker sandbox, so claims stop "
    "excluding — two Workers can hold the same path and both be told "
    "\"claimed\" (SPEC §5.3)",
    "docker-desktop": "same docker runtime, same `flock` failure (SPEC §5.3)",
    "orbstack": "same docker runtime, same `flock` failure (SPEC §5.3)",
}


@dataclass
class BackendChoice:
    """Which backend rite would sandbox with here, or why it would not."""

    name: str = ""
    reason: str = ""

    @property
    def usable(self) -> bool:
        return bool(self.name)


def platform_can_sandbox() -> bool:
    """Whether any backend rite has verified could exist on this platform.

    Answerable without yoloAI installed, which is the point: when the
    binary is absent there is nothing to ask about backends, and offering
    to install it on a platform where no verified backend can exist would
    be offering something that cannot help. seatbelt is the only backend
    measured to keep claims excluding (§5.3) and it is macOS-only.
    """
    return sys.platform == "darwin"


def yoloai_install_command() -> list[str] | None:
    """How to install yoloAI here, or None if rite does not know.

    yoloAI ships as a Homebrew cask. Returning None rather than guessing a
    shell pipeline matters: rite offering to run an installer it is not
    sure of is worse than naming the download page.
    """
    if sys.platform != "darwin":
        return None
    if shutil.which("brew") is None:
        return None
    return ["brew", "install", "--cask", "yoloai"]


@dataclass
class InstallOutcome:
    ok: bool
    detail: str
    installed_now: bool = False


def install_yoloai(timeout: int = 900) -> InstallOutcome:
    """Run the installer and then CHECK, rather than trusting its exit code.

    A package manager reporting success is not the same as a working
    `yoloai` on PATH — a cask can install while the binary lands somewhere
    the current shell's PATH does not cover, which looks like success and
    behaves like absence. So this re-resolves the binary afterwards and
    reports what is actually true.
    """
    command = yoloai_install_command()
    if command is None:
        return InstallOutcome(
            False,
            "rite does not know how to install yoloAI here — "
            "get it from https://yoloai.dev",
        )
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return InstallOutcome(
            False, f"`{' '.join(command)}` timed out after {timeout}s"
        )
    except OSError as e:
        return InstallOutcome(False, f"`{' '.join(command)}` could not run: {e}")

    if proc.returncode != 0:
        detail = _why_it_failed(proc)
        return InstallOutcome(False, f"install failed: {detail}")

    # Exit 0 is a claim, not a fact.
    if _yoloai_binary() is None:
        return InstallOutcome(
            False,
            "the installer reported success but `yoloai` is still not on "
            "PATH — open a new shell, or install it from https://yoloai.dev",
        )
    return InstallOutcome(True, "yoloAI installed", installed_now=True)


def choose_backend() -> BackendChoice | CountUnavailable:
    """The backend rite can actually sandbox with on this machine.

    Availability alone is not the bar. yoloAI reporting docker available
    means docker can run a container, not that rite's claims still exclude
    inside one — and they do not. Untested backends are not claimed either
    way: they are simply not offered, because a sandbox that might silently
    break claims is worse than no sandbox.
    """
    backends = available_backends()
    if isinstance(backends, CountUnavailable):
        return backends
    by_name = {b.name: b for b in backends}
    for name in VERIFIED_BACKENDS:
        entry = by_name.get(name)
        if entry is not None and entry.available:
            return BackendChoice(name=name)

    # Nothing verified is available. Say which way it failed.
    seatbelt = by_name.get("seatbelt")
    if seatbelt is not None and not seatbelt.available:
        why = seatbelt.note or "not available on this platform"
        return BackendChoice(reason=f"seatbelt is unavailable here ({why})")
    return BackendChoice(
        reason="seatbelt is macOS-only, and the container backends are not "
        "usable by rite: " + UNSUITABLE_BACKENDS["docker"]
    )


@dataclass
class SandboxCheck:
    """The result of actually round-tripping a sandbox."""

    ok: bool
    detail: str
    backend: str = ""
    installed: bool = True
    elapsed_ms: int = 0


# The agent used for the self-test. yoloAI's own description: "No-op
# container — keeps the sandbox running without an AI agent." The probe
# has to START a sandbox to be worth anything — a created-but-not-started
# one does not appear in `ls --active` at all, so "count it, expect 1"
# would be answered by a sandbox that never ran — and starting one with a
# real agent would spawn a coding assistant, burn a session and need
# credentials, on every `rite doctor`.
SELFTEST_AGENT = "idle"
SELFTEST_PREFIX = "rite-selftest-"


def _why_it_failed(proc: subprocess.CompletedProcess) -> str:
    """The line of yoloAI's output that says what went wrong.

    Its errors are prefixed `yoloai:` and followed by "Run 'yoloai new -h'
    for help" — so taking the last line reports the usage hint and drops
    the reason, which is the half worth reading.
    """
    lines = [
        ln.strip()
        for ln in ((proc.stderr or "") + "\n" + (proc.stdout or "")).splitlines()
        if ln.strip()
    ]
    if not lines:
        return "no output"
    for line in lines:
        if line.startswith("yoloai:"):
            return line[len("yoloai:") :].strip()[:200]
    informative = [ln for ln in lines if not ln.startswith("Run '")]
    return (informative[-1] if informative else lines[-1])[:200]


def verify_sandbox(backend: str = "", timeout: int = 120) -> SandboxCheck:
    """Can this machine actually run a sandbox? Round-trip one and see.

    Creates a throwaway sandbox on a temporary directory, confirms yoloAI
    counts it as active, and destroys it. Measured at ~2.0s on seatbelt,
    which is why this is affordable for `rite init` and `rite doctor`
    rather than a `which` call standing in for it.

    The teardown is in a `finally`: a probe that leaves a sandbox behind
    on failure would add to `max_concurrent_workers` forever after.
    """
    import tempfile
    import time
    import uuid

    binary = _yoloai_binary()
    if binary is None:
        return SandboxCheck(
            False,
            "yoloai is not installed — install it from https://yoloai.dev",
            backend=backend,
            installed=False,
        )

    name = f"{SELFTEST_PREFIX}{os.getpid()}-{uuid.uuid4().hex[:8]}"
    started = time.monotonic()
    env = sandbox_environment()

    def _elapsed() -> int:
        return int((time.monotonic() - started) * 1000)

    with tempfile.TemporaryDirectory(prefix="rite-selftest-") as workdir:
        args = [binary, "new", "--agent", SELFTEST_AGENT]
        if backend:
            args += ["--backend", backend]
        args += [name, workdir]
        try:
            try:
                proc = subprocess.run(
                    args,
                    capture_output=True,
                    text=True,
                    errors="replace",
                    timeout=timeout,
                    env=env,
                )
            except subprocess.TimeoutExpired:
                return SandboxCheck(
                    False,
                    f"`yoloai new` did not finish within {timeout}s",
                    backend=backend,
                    elapsed_ms=_elapsed(),
                )
            except OSError as e:
                return SandboxCheck(
                    False, f"`yoloai new` could not run: {e}", backend=backend
                )
            if proc.returncode != 0:
                return SandboxCheck(
                    False,
                    f"could not start a sandbox: {_why_it_failed(proc)}",
                    backend=backend,
                    elapsed_ms=_elapsed(),
                )

            counted = _count_named(binary, name, env)
            if isinstance(counted, CountUnavailable):
                return SandboxCheck(
                    False,
                    f"started a sandbox but could not confirm it: {counted.reason}",
                    backend=backend,
                    elapsed_ms=_elapsed(),
                )
            if counted != 1:
                return SandboxCheck(
                    False,
                    f"started a sandbox but yoloai reports {counted} active under "
                    f"that name — it did not stay up",
                    backend=backend,
                    elapsed_ms=_elapsed(),
                )
            return SandboxCheck(
                True,
                "started a sandbox, counted it, and tore it down",
                backend=backend,
                elapsed_ms=_elapsed(),
            )
        finally:
            subprocess.run(
                [binary, "destroy", name],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=timeout,
                env=env,
                check=False,
            )


def _count_named(binary: str, name: str, env: dict[str, str]) -> int | CountUnavailable:
    """How many active sandboxes carry exactly this name.

    Scoped to the probe's own name rather than reusing
    `count_active_sandboxes`, whose `rite-` prefix would also count a
    Worker's real sandbox — and a probe that passes because somebody
    else's sandbox is running has verified nothing.
    """
    try:
        proc = subprocess.run(
            [binary, "ls", "--active", "--json"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=30,
            env=env,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        return CountUnavailable(f"`yoloai ls --active --json` could not run: {e}")
    if proc.returncode != 0:
        return CountUnavailable(f"`yoloai ls --active --json` exited {proc.returncode}")
    try:
        entries = json.loads(proc.stdout)["sandboxes"]
    except (ValueError, KeyError, TypeError):
        return CountUnavailable("`yoloai ls --active --json` did not return JSON")
    return sum(
        1
        for e in entries
        if isinstance(e, dict) and (e.get("environment") or {}).get("name") == name
    )


@dataclass
class SandboxResult:
    ok: bool
    message: str


def legacy_sandbox_name(worker: str) -> str:
    """What this was called before names carried the project (§8.10).

    Kept only so `stop`/`destroy`/`pane`/`status` can still find a sandbox
    started by an older rite. Nothing creates these any more."""
    return f"rite-{worker}"


def sandbox_name(worker: str, root: str | os.PathLike[str] | None = None) -> str:
    """The yoloAI sandbox name for a Worker — one sandbox per Worker, so
    `rite sandbox status <worker>` and yoloAI's own `ls` agree on what to
    call it.

    **Carries the project (§8.10).** `rite-acme-3f9a2c-w1`, not
    `rite-w1`. Every project names its workers `w1`, `w2`, `w3`, so on a
    machine running two projects — which is what rite is for — the old
    name collided outright: `yoloai ls` showed `rite-w1` twice and
    `rite sandbox destroy w1` in one project would destroy the other
    project's worker. That is a destructive mistake reachable by typing
    the correct command in the wrong directory.

    `root` is optional only so a caller with no project in hand still
    gets a usable name; omitting it reproduces the ambiguous old form and
    should be treated as a bug in the caller, not a supported mode.
    """
    if root is None:
        return legacy_sandbox_name(worker)
    from rite_ai.label import project_slug

    return f"rite-{project_slug(Path(root))}-{worker}"


def existing_sandbox_name(
    worker: str, root: str | os.PathLike[str] | None = None
) -> str:
    """The name a sandbox for this Worker is ACTUALLY running under.

    Prefers the project-scoped name and falls back to the legacy one only
    when a sandbox is genuinely running under it, so a sandbox started
    before §8.10 is still reachable by `stop`, `destroy` and `pane`
    instead of appearing to have vanished. Falls back to the new name when
    neither exists, so error messages name the form the user should
    expect."""
    scoped = sandbox_name(worker, root)
    if root is None:
        return scoped
    names = _list_sandbox_names()
    if scoped in names:
        return scoped
    legacy = legacy_sandbox_name(worker)
    if legacy in names:
        return legacy
    return scoped


def _list_sandbox_names() -> set[str]:
    """Every sandbox yoloAI currently knows, or an empty set if it cannot
    be asked. Empty means "could not enumerate" and callers treat it as
    "no legacy sandbox to fall back to", which is the safe direction: it
    keeps them on the project-scoped name rather than guessing at the
    ambiguous one."""
    binary = _yoloai_binary()
    if binary is None:
        return set()
    try:
        proc = subprocess.run(
            [binary, "ls", "--json"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=30,
        )
        if proc.returncode != 0:
            return set()
        data = json.loads(proc.stdout or "[]")
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return set()
    entries = data.get("sandboxes", data) if isinstance(data, dict) else data
    if not isinstance(entries, list):
        return set()
    return {e.get("name", "") for e in entries if isinstance(e, dict)}


GLOBAL_TOKEN_CREDENTIAL = "github_token"


def resolve_worker_token(
    worker: str, credentials: object | None = None
) -> tuple[str | None, str]:
    """A Worker's sandbox token, and which tier supplied it.

    Two tiers today, in order:

    1. `sandbox_token_<worker>` — provisioned by `rite add worker`'s
       guided step, scoped to the PROJECT's repos (§5.3.3). Not to that
       Worker's own module subset: §5.3.4 records why workers are
       fungible and what that trade costs.
    2. `github_token` — machine-global.

    ⚠ **Tier 2 exists because tier 2 was already advertised and did
    nothing.** `github_token` is in `credentials.store.KNOWN`, `rite
    doctor` prints its status, and `rite credential set` names it in the
    status block it prints after a successful store — and until this
    function, NOTHING read it. Measured: with only `github_token` set, a
    Worker's sandbox received no token at all while `rite doctor`
    reported the credential configured. A key that stores cleanly and
    does nothing is worse than no key, so it either works or it goes;
    this makes it work.

    ⚠⚠ **The fallback is deliberately NOT silent, and must not become
    silent.** A machine-global GitHub token is over-scoped by
    construction: §5.3.2 already states the sandbox does not bound damage
    to the GitHub account, and a global token widens exactly the half the
    sandbox does not protect. The failure this warning exists to prevent
    is someone setting `github_token` once and every Worker thereafter
    quietly using the broad token while `rite add worker` still appears
    to offer per-Worker scoping.

    Returns `(token, tier)` where tier is `"worker"`, `"global"` or
    `"none"` — the tier is returned rather than inferred by the caller so
    that "which token did this Worker actually get?" has one answer.

    `credentials` is this project's `CredentialsConfig` (§10.2). With one
    given, tier 1 looks under the project's scoped name first and only
    then at the unscoped `sandbox_token_<worker>` — so a token
    provisioned before scoping existed is still found, and still counts
    as tier 1 rather than dropping to the over-scoped global.
    """
    from rite_ai.credentials.store import get_scoped

    scoped = get_scoped(token_credential_name(worker), credentials)
    if scoped:
        return scoped, "worker"
    shared = get_scoped(GLOBAL_TOKEN_CREDENTIAL, credentials)
    if shared:
        return shared, "global"
    return None, "none"


def token_credential_name(worker: str) -> str:
    """The `rite_ai.credentials.store` key a Worker's sandbox token is
    provisioned under — the single naming convention both `rite add
    worker`'s provisioning step and `rite credential rotate` must agree
    on."""
    return f"sandbox_token_{worker}"


@dataclass
class CountUnavailable:
    """yoloAI could not be asked how many sandboxes are running.

    Distinct from a count of zero, and the distinction is the whole point.
    `max_concurrent_workers` (§2.5.9, D-29) is enforced by comparing
    against this count; a failure that reports 0 does not merely lose
    information, it silently switches the cap off while every `rite
    sandbox start` keeps reporting success. An operator who set a cap
    then believes they have one.
    """

    reason: str


def count_active_sandboxes() -> int | CountUnavailable:
    """How many `rite`-managed sandboxes yoloAI currently reports active
    (`yoloai ls --active`, which "includes idle" per its own `--help` —
    delegating that judgement to yoloAI rather than inventing a status-
    string taxonomy of our own). Scoped to names starting with `rite-`
    so an unrelated sandbox from manual `yoloai` use elsewhere on this
    machine doesn't count against the cap.

    Returns `CountUnavailable` — never a plausible-looking 0 — when the
    question cannot be answered. Each caller decides what that means;
    none of them can decide it if the answer arrives disguised as a
    count.
    """
    binary = _yoloai_binary()
    if binary is None:
        return CountUnavailable("yoloai not found on PATH")
    try:
        proc = subprocess.run(
            [binary, "ls", "--active", "--json"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        # A wedged yoloAI must not become an unbounded wait inside an
        # unattended `scheduler-tick`, and must not become a crash either.
        # It is the same answer as any other unanswerable count: unknown,
        # so the caller refuses to start rather than guessing zero.
        return CountUnavailable("`yoloai ls --active --json` timed out after 30s")
    except OSError as e:
        return CountUnavailable(f"`yoloai ls --active --json` could not run: {e}")
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or "no output"
        return CountUnavailable(
            f"`yoloai ls --active --json` exited {proc.returncode}: {detail[:200]}"
        )
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return CountUnavailable(
            "`yoloai ls --active --json` did not return JSON — "
            "this rite may be too old for the installed yoloai"
        )
    if not isinstance(data, dict):
        return CountUnavailable(
            "`yoloai ls --active --json` returned unexpected JSON "
            f"({type(data).__name__}, expected an object)"
        )
    return sum(
        1
        for entry in data.get("sandboxes", [])
        if entry.get("environment", {}).get("name", "").startswith("rite-")
    )


def sandbox_environment(base: dict[str, str] | None = None) -> dict[str, str]:
    """rite's own environment, minus the parts that only make sense
    outside the sandbox.

    A sandbox inherits the environment of whatever spawned it, and rite is
    always spawned from a virtualenv — `uv tool install`, `pipx` and `uv
    run` all put their venv's `bin` first on PATH. Inside a seatbelt
    sandbox that directory is not readable, so the first `python3` the
    agent's startup resolves is rite's own venv interpreter, which cannot
    read its own `pyvenv.cfg` and dies before it can import `site`:

        Fatal Python error: init_import_site: Failed to import the site module
        PermissionError: [Errno 1] Operation not permitted:
          .../rite/.venv/pyvenv.cfg

    yoloAI then reports `wait for tmux session: sandbox-exec exited`. The
    sandbox never starts, and nothing in the message points at rite.

    Isolated by bisecting the environment against the real binary: a clean
    PATH succeeds, `VIRTUAL_ENV` alone succeeds, and the venv's `bin` on
    PATH is what fails. So PATH is what gets cleaned — `VIRTUAL_ENV` goes
    too because it is meaningless once its `bin` is gone, but it is not
    the cause.

    Only entries inside THIS interpreter's venv are dropped. A venv the
    user put on PATH deliberately, for the agent to use, is not rite's to
    remove.
    """
    env = dict(base if base is not None else os.environ)
    if sys.prefix == sys.base_prefix:
        return env  # not running from a venv; nothing to strip

    venv_bin = os.path.realpath(os.path.join(sys.prefix, "bin"))
    entries = env.get("PATH", "").split(os.pathsep)
    kept = [e for e in entries if e and os.path.realpath(e) != venv_bin]
    if kept != [e for e in entries if e]:
        env["PATH"] = os.pathsep.join(kept)
        env.pop("VIRTUAL_ENV", None)
    return env


def start_worker(
    root: str | os.PathLike[str],
    worker: str,
    config: SandboxConfig,
    token: str | None = None,
    agent_args: list[str] | None = None,
    env: dict[str, str] | None = None,
    allow_dirty: bool = False,
    prompt: str | None = None,
) -> SandboxResult:
    """Launch a Worker's session inside a fresh sandbox, mounted on that
    Worker's own workspace directory (`workers/<worker>/`, §2.1's
    per-worker isolation — this extends it to the process). The agent is
    a normal `claude` invocation yoloAI runs inside the sandbox.

    `prompt` is the session's opening instruction — usually which ticket
    to work. Without one the session starts idle, and nothing in rite can
    type into a sandbox afterwards (`rite sandbox pane` only reads), so a
    Worker started without a prompt waits until someone attaches."""
    root = Path(root)
    if prompt is not None and not prompt.strip():
        return SandboxResult(
            False,
            "empty prompt — give the Worker something to do, or leave the "
            "prompt out and attach to the session instead",
        )
    binary = _yoloai_binary()
    if binary is None:
        return SandboxResult(
            False, "yoloai not found — install it from https://yoloai.dev"
        )

    workdir = root / "workers" / worker
    if not workdir.is_dir():
        return SandboxResult(False, f"no such worker workspace: {workdir}")

    from rite_ai.schedule import check_worker_cap

    # Refuse when the cap cannot be evaluated, rather than start anyway.
    #
    # The alternative — warn and proceed — leaves the operator in exactly
    # the state the cap exists to prevent, with one line of evidence that
    # scrolls past in a Manager's tool output while every subsequent start
    # in that session goes uncapped. Refusing is also what this function
    # already does for the adjacent failure two checks up (`yoloai not
    # found`): a `yoloai ls` that does not work is the same class of "the
    # sandbox tooling on this machine is broken", and it breaks `rite
    # sandbox status` and sandbox cleanup too, so it needs fixing either
    # way. The message names the failing command so it can be.
    active = count_active_sandboxes()
    if isinstance(active, CountUnavailable):
        return SandboxResult(
            False,
            f"cannot count running sandboxes, so "
            f"sandbox.max_concurrent_workers ({config.max_concurrent_workers}) "
            f"cannot be enforced — refusing to start '{worker}'. "
            f"{active.reason}",
        )

    cap_problem = check_worker_cap(active + 1, config.max_concurrent_workers)
    if cap_problem is not None:
        return SandboxResult(False, cap_problem)

    args = [binary, "new", "--backend", config.backend, "--agent", "claude"]
    if allow_dirty:
        # yoloAI refuses a workdir with uncommitted changes unless told
        # otherwise, and a Worker part-way through a task is exactly that.
        # Opt-in rather than always-on: its warning ("could be modified or
        # lost") is about the Worker's own unpushed work.
        args.append("--allow-dirty")
    # Every credential the project holds, one `--env` each (§5.3.4).
    # `token` remains for callers that have only the git token; when both
    # are given `env` is authoritative and already carries it, because
    # `worker_environment` puts the Worker's own token in GITHUB_TOKEN
    # ahead of any machine-global one.
    delivered = dict(env or {})
    if token and TOKEN_ENV_VAR not in delivered:
        delivered[TOKEN_ENV_VAR] = token
    # The Claude login goes to yoloAI, not to `--env`. yoloAI treats
    # CLAUDE_CODE_OAUTH_TOKEN as the claude agent's own credential and reads
    # it from the environment `yoloai new` runs in (`yoloai system agents
    # claude`), which is also what `yoloai help security` tells a person to
    # export. Putting the stored one there is what makes it reach every
    # sandbox, whichever terminal started it.
    yoloai_env = sandbox_environment()
    # Git settings from the host shell must not stack on the sandbox's own:
    # GIT_CONFIG_PARAMETERS would add to them, and a host GIT_CONFIG_COUNT
    # would compete with the one passed below. Both start GIT_CONFIG_.
    for inherited in [k for k in yoloai_env if k.startswith("GIT_CONFIG_")]:
        del yoloai_env[inherited]
    claude_login = delivered.pop(CLAUDE_TOKEN_ENV_VAR, None)
    if claude_login:
        yoloai_env[CLAUDE_TOKEN_ENV_VAR] = claude_login
    login_note = (
        []
        if yoloai_env.get(CLAUDE_TOKEN_ENV_VAR) or yoloai_env.get("ANTHROPIC_API_KEY")
        else [
            "  no Claude login for the sandbox, so the session will start and "
            "do nothing — run `claude setup-token`, then `rite credential set "
            "claude`, and start the Worker again"
        ]
    )
    # The project root, named rather than found. rite walks up from cwd for
    # `.rite/`, and inside the sandbox most of that walk is unreadable.
    delivered.setdefault("RITE_PROJECT_ROOT", str(root))
    git_notes = []
    for key, value in sandbox_git_environment(shutil.which("gh")).items():
        delivered.setdefault(key, value)
    if shutil.which("gh") is None:
        git_notes.append(
            "  gh is not installed, so `git push` over HTTPS cannot authenticate "
            "from inside — destroy this sandbox, install GitHub's `gh` CLI, and "
            "start the Worker again"
        )
    for key in sorted(delivered):
        args += ["--env", f"{key}={delivered[key]}"]
    # What the sandbox can reach, and why it is exactly this (SPEC §5.3):
    #
    # - the Worker's own `workers/<worker>/`, as yoloAI's isolated copy.
    #   `rite prepare` runs outside, before the sandbox starts; the Worker's
    #   work leaves by pushing its branch to origin, and the copy is thrown
    #   away with the sandbox. Its cwd is outside the project, so the project
    #   is named with RITE_PROJECT_ROOT rather than found.
    # - the project's `.rite/`, writable: claims, heartbeats and handover
    #   live there. yoloAI's `-d` mounts directories, so this is all of
    #   `.rite/`: a Worker can also rewrite what unsandboxed rite acts on —
    #   config, modules.yaml commands, gate suppressions, the handover outbox
    #   posted with the host's credentials, other Workers' heartbeats.
    # - read-only, each local directory one of the Worker's clones fetches
    #   from, so git inside can fetch from it. Usually that is the root's own
    #   checkout of the module, so those files are readable from inside. Being
    #   read-only, it cannot be pushed to: only a URL origin takes a push. A
    #   URL origin needs no mount.
    #
    # Deliberately NOT the project root. Mounting it writable would let a
    # Worker edit another Worker's checkout, which is the isolation the
    # sandbox exists for; yoloAI also refuses a mount that contains the
    # Worker's directory. Other Workers' directories are neither readable
    # nor writable from inside.
    rite_dir = root / ".rite"
    args += ["-d", f"{rite_dir}:rw"]
    origins, unmountable = _local_origins(workdir, rite_dir)
    for origin in origins:
        args += ["-d", str(origin)]
    local_origin_notes = [
        f"  a clone fetches from {origin}, a local directory mounted read-only, "
        "so that module cannot push from the sandbox — work on it would be "
        "lost with the sandbox; give the module a URL origin to push from"
        for origin in origins
    ]
    # The prompt goes through yoloAI's prompt file, not `-p` or an agent
    # argument: on argv it is readable in `ps` and subject to quoting and
    # length limits. The file is only needed until `yoloai new` has read it.
    prompt_dir: str | None = None
    if prompt is not None:
        prompt_dir = tempfile.mkdtemp(prefix="rite-prompt-")
        prompt_file = Path(prompt_dir) / "prompt.txt"
        prompt_file.write_text(prompt if prompt.endswith("\n") else prompt + "\n")
        args += ["--prompt-file", str(prompt_file)]
    name = sandbox_name(worker, root)
    # `:copy-all`, not yoloAI's default `:copy`. Measured on 0.11.0: when the
    # workdir is inside a git repository — a rite project root is one, and
    # `rite init` gitignores `workers/` — `:copy` leaves out gitignored files
    # and nested repositories, so the sandbox received neither the Worker's
    # CLAUDE.md nor its module clones. `:copy-all` copies the directory as it
    # is, including gitignored files in the clones, such as a module's `.env`.
    args += [name, f"{workdir}:copy-all"]
    if agent_args:
        args += ["--", *agent_args]

    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=300,
            env=yoloai_env,
        )
    except subprocess.TimeoutExpired:
        return SandboxResult(False, "yoloai new timed out after 300s")
    finally:
        if prompt_dir is not None:
            shutil.rmtree(prompt_dir, ignore_errors=True)
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip()
        if "--allow-dirty" in detail and not allow_dirty:
            # yoloAI tells the user to "Re-run with --allow-dirty", which
            # is a flag on `yoloai new` — not on `rite sandbox start`.
            # Passing that advice through unchanged sends someone to a
            # flag this CLI rejects, which is a dead end rather than a
            # fix. Name the one that exists here.
            detail = (
                f"{detail}\n"
                "  (that advice is yoloai's own flag — from rite, re-run as "
                "`rite sandbox start <worker> --allow-dirty`)"
            )
        elif "already exists" in detail:
            # Same shape: yoloAI suggests `--replace`, which `rite sandbox
            # start` does not have. Starting a Worker on its next ticket is
            # destroy, then start.
            detail = (
                f"{detail}\n"
                "  (from rite: `rite sandbox destroy <worker>`, then start it "
                "again)"
            )
        return SandboxResult(False, f"yoloai new failed: {detail}")
    lines = [
        f"sandbox '{name}' started",
        f"  watch or step in: yoloai attach {name}",
        "  its first screen shows the credentials passed in, in plain text: do not "
        "share or record it. `rite sandbox pane` shows the screen with them redacted",
    ]
    lines.extend(login_note)
    lines.extend(local_origin_notes)
    lines.extend(git_notes)
    for clone_name, origin in unmountable:
        lines.append(
            f"  {clone_name}/ fetches from {origin}, which contains the "
            "Worker's own directory or .rite/ and so cannot be mounted — git "
            "inside the sandbox cannot fetch from it or push to it"
        )
    if prompt is None:
        lines.append(
            "  no --ticket or --prompt was given, so unless an --agent-arg "
            "carries an instruction the session is idle — attach and tell it "
            "which ticket to work, or destroy it and start again with --ticket"
        )
    return SandboxResult(True, "\n".join(lines))


def sandbox_git_environment(gh: str | None) -> dict[str, str]:
    """Git settings for inside a sandbox, as environment variables.

    Passed with `--env`, so they apply to that sandbox's session and change
    no config file on the host. Git reads `GIT_CONFIG_COUNT` /
    `GIT_CONFIG_KEY_<n>` / `GIT_CONFIG_VALUE_<n>` after every config file.

    - **Credentials.** The helper git finds on macOS is the keychain
      (`osxkeychain`, from Xcode's system config), which a Seatbelt sandbox
      cannot read, so an HTTPS push stops at "could not read Username".
      The list is reset and github.com is sent to `gh auth git-credential`;
      `gh` takes its token from `GH_TOKEN` or `GITHUB_TOKEN` (`gh help
      environment`), which rite injects. The path is shell-quoted because
      git runs a `!` helper through the shell. Without `gh` only the reset
      is set, and the caller says so.
    - **Signing off.** A Worker's commits are the agent's. Signing them with
      the host user's key would assert that person wrote them, and a
      signing key under `~/.ssh` is unreadable inside anyway, which made
      every commit fail.
    - **The repository's own hooks.** `core.hooksPath` is `.git/hooks`. A
      global hooks directory under `$HOME` is unreadable inside, and
      measured, git then fails every push on the hook it cannot open. The
      choice is between each clone's own hooks running and nothing
      running, so a user's global hooks never run for a sandboxed push.
    """
    settings: list[tuple[str, str]] = [("credential.helper", "")]
    if gh:
        settings.append(
            (
                "credential.https://github.com.helper",
                f"!{shlex.quote(gh)} auth git-credential",
            )
        )
    settings += [
        ("commit.gpgsign", "false"),
        ("tag.gpgsign", "false"),
        ("core.hooksPath", ".git/hooks"),
    ]
    env = {"GIT_CONFIG_COUNT": str(len(settings))}
    for i, (key, value) in enumerate(settings):
        env[f"GIT_CONFIG_KEY_{i}"] = key
        env[f"GIT_CONFIG_VALUE_{i}"] = value
    if gh:
        # Measured: inside a Seatbelt sandbox gh exits before answering,
        # because reading ~/.config/gh/config.yml is denied, and git falls
        # back to a prompt that cannot be shown. Any config directory the
        # sandbox is not denied — it need not exist — lets gh answer from
        # GITHUB_TOKEN.
        env["GH_CONFIG_DIR"] = str(Path(tempfile.gettempdir()) / "rite-sandbox-gh")
    return env


def _local_origins(
    workdir: Path, rite_dir: Path
) -> tuple[list[Path], list[tuple[str, Path]]]:
    """The local directories this Worker's clones fetch from, split into
    those that can be mounted read-only and those that cannot.

    A relative origin is resolved against the clone, as git resolves it, and
    `file://` is local. Nothing that overlaps the Worker's own directory or
    `.rite/` is mounted — yoloAI refuses a mount containing the workdir, and
    an origin inside either is already readable. One that CONTAINS either
    (the project root itself, for a module registered at `.`) is returned
    as unmountable, so the caller can say so rather than start a Worker
    that cannot fetch from it."""
    mounts: list[Path] = []
    unmountable: list[tuple[str, Path]] = []
    if not workdir.is_dir():
        return mounts, unmountable
    work = workdir.resolve()
    rite = rite_dir.resolve()
    for clone in sorted(p for p in workdir.iterdir() if (p / ".git").exists()):
        try:
            proc = subprocess.run(
                ["git", "-C", str(clone), "remote", "get-url", "origin"],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        url = proc.stdout.strip()
        if proc.returncode != 0 or not url:
            continue
        if url.startswith("file://"):
            from urllib.parse import unquote, urlparse

            raw = Path(unquote(urlparse(url).path))
        elif "://" in url or re.match(r"^[^/]+:", url):
            continue  # https, ssh, git@host:path — fetched over the network
        else:
            raw = Path(url).expanduser()
        try:
            origin = (raw if raw.is_absolute() else clone / raw).resolve()
        except (OSError, RuntimeError):
            continue
        if not origin.is_dir():
            continue
        if any(origin == x or x in origin.parents for x in (work, rite)):
            continue
        if any(origin in x.parents for x in (work, rite)):
            unmountable.append((clone.name, origin))
            continue
        if origin not in mounts:
            mounts.append(origin)
    return mounts, unmountable


def _yoloai_sandboxes_dir() -> Path:
    return Path.home() / ".yoloai" / "library" / "sandboxes"


def _sandbox_copy(name: str, workdir: Path) -> Path | None:
    """yoloAI's copy of a Worker's directory, or None when it is not found.

    Measured on yoloAI 0.11.0: a copy-mode workdir lives at
    `~/.yoloai/library/sandboxes/<name>/rw/work/<workdir, "/" as "^s">`.
    That layout is yoloAI's, not an interface, so a copy that is not where
    it was measured falls back to the only directory there, and is
    otherwise reported as not found rather than guessed at.
    """
    work = _yoloai_sandboxes_dir() / name / "rw" / "work"
    measured = work / str(workdir).replace("/", "^s")
    if measured.is_dir():
        return measured
    candidates = [p for p in work.iterdir() if p.is_dir()] if work.is_dir() else []
    return candidates[0] if len(candidates) == 1 else None


def _work_only_in_sandbox(
    name: str, worker: str, root: str | os.PathLike[str] | None
) -> str:
    """What exists only in the sandbox's copy of the Worker's checkout, as
    text naming module, branch and count — or "" when nothing is at risk.

    The Worker works on a copy that `destroy` deletes, so a Worker that
    committed and never pushed loses everything, and until this nothing
    said so. "" also when there is no sandbox on disk under that name:
    yoloAI then reports the missing sandbox itself. A copy that cannot be
    found is reported, not taken as safe.
    """
    if root is None or not (_yoloai_sandboxes_dir() / name).is_dir():
        return ""
    copy = _sandbox_copy(name, Path(root) / "workers" / worker)
    if copy is None:
        return (
            f"could not find the sandbox's copy of workers/{worker}/ to check "
            "it for work that is on no remote"
        )
    from rite_ai.workspace import unsaved_work

    items = unsaved_work(copy)
    if not items:
        return ""
    lines = [
        f"the sandbox's copy of workers/{worker}/ holds work that is on no remote:"
    ]
    lines += [f"  {item.describe()}" for item in items]
    lines.append(f"  the copy is at {copy}")
    return "\n".join(lines)


def stop_worker(
    worker: str, root: str | os.PathLike[str] | None = None
) -> SandboxResult:
    binary = _yoloai_binary()
    if binary is None:
        return SandboxResult(False, "yoloai not found")
    name = existing_sandbox_name(worker, root)
    try:
        proc = subprocess.run(
            [binary, "stop", name],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return SandboxResult(False, "yoloai stop timed out after 120s")
    if proc.returncode != 0:
        return SandboxResult(False, proc.stderr.strip() or proc.stdout.strip())
    message = f"sandbox '{name}' stopped"
    at_risk = _work_only_in_sandbox(name, worker, root)
    if at_risk:
        message += (
            f"\nwarning: {at_risk}\n  `rite sandbox destroy {worker}` deletes "
            "the copy; push that work first"
        )
    return SandboxResult(True, message)


def destroy_worker(
    worker: str,
    root: str | os.PathLike[str] | None = None,
    force: bool = False,
) -> SandboxResult:
    binary = _yoloai_binary()
    if binary is None:
        return SandboxResult(False, "yoloai not found")
    name = existing_sandbox_name(worker, root)
    at_risk = "" if force else _work_only_in_sandbox(name, worker, root)
    if at_risk:
        return SandboxResult(
            False,
            f"refusing to destroy sandbox '{name}': {at_risk}\n"
            "  destroying it deletes the copy; push that work first, or "
            f"`rite sandbox destroy {worker} --force` to discard it",
        )
    try:
        proc = subprocess.run(
            [binary, "destroy", name, "--abandon-unapplied"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return SandboxResult(False, "yoloai destroy timed out after 120s")
    if proc.returncode != 0:
        return SandboxResult(False, proc.stderr.strip() or proc.stdout.strip())
    return SandboxResult(True, f"sandbox '{name}' destroyed")


@dataclass
class PaneCapture:
    """What a Worker's sandboxed session currently has on screen.

    `ok=False` carries the reason in `text` — the same "could not check"
    vs "nothing there" distinction `SandboxStatus` makes, for the same
    reason: a Manager deciding whether to intervene must not read a
    broken yoloAI as a quiet worker.
    """

    ok: bool
    text: str


# yoloAI's own launch line on Seatbelt, as it is typed into the session's
# shell: `export NAME='value'; ` per secret, with a single quote in a value
# written as '\''. The value can wrap across screen lines.
_EXPORT_STATEMENT = re.compile(r"(export [A-Za-z_][A-Za-z0-9_]*=')((?:[^']|'\\'')*)(')")


def redact_secrets(text: str, secrets: Iterable[str] = ()) -> str:
    """`text` with every exported value and every known secret replaced.

    Measured on yoloAI 0.11.0 with Seatbelt: the first thing on a sandboxed
    session's screen is the command yoloAI types to launch the agent, and it
    carries every `--env` value as `export NAME='value'` — the GitHub token,
    the JIRA token, the proxy token for Claude. yoloAI has no other way to
    take them on Seatbelt. `rite sandbox pane` exists to be read by Claude
    sessions, so unredacted it would route live credentials into model
    context and transcripts.

    Two passes: every export statement's value, whatever its name, and then
    each known secret value anywhere else on screen. A screen wraps long
    lines, so a value is matched with a line break allowed between any two
    characters. Values shorter than eight characters are not searched for:
    `false` and `5` are not secrets, and replacing every occurrence of them
    would destroy the capture.

    ⚠ A mitigation, not the fix. It closes rite's own surface, the one that
    routes a screen into Claude sessions by design; `yoloai attach` still
    shows the values, and so does anything else that reads the session's
    screen. The fix is for Workers to fetch credentials through a validated
    channel instead of having them injected as environment variables — the
    MCP entry in `.docs/FUTURE_IMPROVEMENTS.md` ("credentials through the
    MCP server"). When that lands, this should have nothing left to redact.
    """
    text = _EXPORT_STATEMENT.sub(
        lambda m: m.group(1) + "[redacted]" + m.group(3), text
    )
    searched = {s for s in secrets if s and len(s) >= 8}
    for value in sorted(searched, key=len, reverse=True):
        pattern = r"(?:\r?\n)?".join(re.escape(ch) for ch in value)
        text = re.sub(pattern, "[redacted]", text)
    return text


def worker_pane(
    worker: str,
    ansi: bool = False,
    root: str | os.PathLike[str] | None = None,
    secrets: Iterable[str] = (),
) -> PaneCapture:
    """The rendered terminal of a Worker's sandboxed session.

    A sandboxed Worker is invisible to Claude Code's own session tooling
    — verified 2026-09-11: it creates a registry socket and never appears
    in the session list, so it cannot be listed, addressed, or messaged
    the way an ordinary session can. Its screen is the only way to see
    what it is doing, and without this command a Manager has to reach
    into yoloAI's private library directory (`.../rw/tmux.sock`) to get
    it — around rite, into another tool's internals, to look at rite's
    own worker.

    Delegates to `yoloai sandbox <name> terminal-snapshot`, which is
    yoloAI's supported command for exactly this, rather than driving the
    tmux socket directly. The socket works and is what this was first
    built on; the supported command is preferred because the library
    layout is not a promise yoloAI makes.

    Read-only. Nudging a blocked Worker is a separate, consequential act
    (it types into a live session) and is deliberately not bundled in
    here — see the guide's manual `tmux -S ... send-keys` route.
    """
    binary = _yoloai_binary()
    if binary is None:
        return PaneCapture(
            False, "yoloai not found — install it from https://yoloai.dev"
        )
    args = [binary, "sandbox", existing_sandbox_name(worker, root), "terminal-snapshot"]
    if ansi:
        args.append("--ansi")
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return PaneCapture(False, "`yoloai ... terminal-snapshot` timed out after 30s")
    except OSError as e:
        return PaneCapture(False, f"`yoloai ... terminal-snapshot` could not run: {e}")
    if proc.returncode != 0:
        detail = redact_secrets(
            proc.stderr.strip() or proc.stdout.strip() or "no output", secrets
        )
        return PaneCapture(
            False,
            f"no pane for '{worker}' — `yoloai sandbox "
            f"{existing_sandbox_name(worker, root)} "
            f"terminal-snapshot` exited {proc.returncode}: {detail[:200]}",
        )
    return PaneCapture(True, redact_secrets(proc.stdout.rstrip("\n"), secrets))


@dataclass
class SandboxStatus:
    """A Worker's sandbox status, and whether it is an actual answer.

    `known=False` means the question could not be answered at all, which
    is not the same fact as "no sandbox exists" even though both once
    printed the same word.
    """

    value: str
    known: bool = True

    def __str__(self) -> str:
        return self.value


def worker_sandbox_status(
    worker: str, root: str | os.PathLike[str] | None = None
) -> SandboxStatus:
    """One of the `status` values `yoloai ls --json` reports for this
    Worker's sandbox, or `"not found"` if no such sandbox exists —
    verified against real output, not documentation.

    A yoloAI that cannot be asked reports the failure, not `"not found"`.
    The same conflation as the worker cap's: "there is no sandbox" and "I
    could not check" are opposite answers for anyone deciding what to do
    next, and only one of them is safe to act on.
    """
    binary = _yoloai_binary()
    if binary is None:
        return SandboxStatus("yoloai not found", known=False)
    proc = subprocess.run(
        [binary, "ls", "--json"],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=30,
    )
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or "no output"
        return SandboxStatus(
            f"unknown — `yoloai ls --json` exited {proc.returncode}: {detail[:200]}",
            known=False,
        )
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return SandboxStatus(
            "unknown — `yoloai ls --json` did not return JSON", known=False
        )
    if not isinstance(data, dict):
        return SandboxStatus(
            "unknown — `yoloai ls --json` returned unexpected JSON", known=False
        )
    # Both forms: a sandbox started before §8.10 still answers `status`
    # rather than reporting "not found" for something plainly running.
    names = {sandbox_name(worker, root), legacy_sandbox_name(worker)}
    for entry in data.get("sandboxes", []):
        if entry.get("environment", {}).get("name") in names:
            return SandboxStatus(str(entry.get("status", "unknown")))
    return SandboxStatus("not found")


_GITHUB_URL_RE = re.compile(
    r"github\.com[:/]+(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+?)(?:\.git)?/?$"
)


def owner_repo_from_url(url: str) -> tuple[str, str] | None:
    """Parses `owner/repo` from a GitHub HTTPS or SSH remote URL. Returns
    `None` for anything else (a non-GitHub host, a local path) — callers
    skip what they can't parse rather than guessing."""
    m = _GITHUB_URL_RE.search(url)
    if not m:
        return None
    return m.group("owner"), m.group("repo")


def check_token_access(token: str, modules: list[Module]) -> list[str]:
    """Confirms the token can reach every repo the Worker's
    `modules.yaml` lists — problems, empty if none. This is NOT a check
    that the token can reach *only* those repos: GitHub exposes no API to
    enumerate everything a fine-grained PAT can access from the token
    alone, so the actual risk §5.3.2 names (a wide-scoped token inside a
    sandbox) is not detectable this way, and this function does not
    claim otherwise. §5.3.3's Open Questions names GitHub App
    installation tokens as the alternative with better properties here —
    not built in this pass."""
    binary = shutil.which("gh")
    if binary is None:
        return ["`gh` CLI not found — cannot verify token access"]

    problems: list[str] = []
    env = {**os.environ, "GH_TOKEN": token}
    for module in modules:
        if not module.url:
            continue
        pair = owner_repo_from_url(module.url)
        if pair is None:
            continue
        owner, repo = pair
        proc = subprocess.run(
            [binary, "api", f"repos/{owner}/{repo}"],
            capture_output=True,
            text=True,
            errors="replace",
            env=env,
            timeout=15,
        )
        if proc.returncode != 0:
            problems.append(
                f"token cannot access {owner}/{repo}: {proc.stderr.strip()[:200]}"
            )
    return problems
