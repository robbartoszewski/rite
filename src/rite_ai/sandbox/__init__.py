"""Worker process sandboxing via yoloAI (SPEC §5.3, D-26, D-30, D-31).

`sandbox.enabled` in `config.yaml` defaults to `true` (D-51); `rite init`
writes `false` where no verified backend exists (Linux). ⚠ Only `rite doctor`
reads it: `rite sandbox start` sandboxes whatever it says (C34). Wraps
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
2. **Token delivery is `--env`** (D-31, §5.3.3) — rather than a file, which
   would persist inside the sandbox after the run.

   ⚠ **THIS RULE USED TO SAY "never a file or a CLI argument", AND `--env`
   IS A CLI ARGUMENT.** The sentence contradicted the line that implements
   it: `args += ["--env", f"{key}={delivered[key]}"]` puts every project
   credential on the host process table for as long as `yoloai new` runs.

   Measured on this machine rather than assumed, because the severity turns
   entirely on it: a non-root user can read the FULL argument list of
   processes owned by root, `_usbmuxd`, `_distnote` and `_windowserver`.
   argv is not uid-restricted on macOS, so this is not "visible to the user
   who already owns the credentials" — **any local account can read them**
   from `ps -ww` during the seconds a Worker starts.

   It is not fixable inside rite today: `yoloai new --help` offers `--env
   strings (KEY=VAL, repeatable)` and no `--env-file`, so there is no
   off-argv channel for a general secret. The one credential that avoids
   this — `CLAUDE_CODE_OAUTH_TOKEN` — does so because yoloAI reads that
   specific variable from its own environment, which is agent-specific and
   not a mechanism rite can reuse for the rest.

   Recorded as a real exposure with a known cause and no local fix, not as
   a caveat: single-user machines are unaffected in practice, shared and
   multi-user machines are not. The designed fix is credentials fetched
   through a validated channel rather than injected as environment
   variables, at which point there is nothing on argv to read.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
from collections.abc import Iterable
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from rite_ai.config.models import Module, SandboxConfig
from rite_ai.machine import max_sandboxes

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
    '"claimed" (SPEC §5.3)',
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


@contextmanager
def _torn_down_on_sigterm():
    """Make SIGTERM reach the `finally` that destroys the probe sandbox.

    **A `finally` is not the guarantee it reads as.** Ctrl-C is fine —
    SIGINT raises `KeyboardInterrupt`, so the teardown below runs. SIGTERM
    does not raise anything: the interpreter stops where it stands and the
    `finally` never executes. Every harness that stops a background job
    sends SIGTERM, which makes "kill the run" the one way of ending it that
    leaks.

    Measured, and the evidence is in the name: a killed suite left
    `rite-selftest-4751-d0775d81` behind, where 4751 was the pid of the
    process that had just been killed. The orphan counts against
    `machine.max_sandboxes` and holds disk until somebody reads `rite
    doctor`'s litter report.

    `SystemExit` rather than a custom error, because `finally` runs for it
    and the process still exits — with 143, which is what a shell reports
    for a SIGTERM'd child. Nothing here changes what the signal MEANS; it
    only gives the teardown a chance to run first.

    Restores the previous handler, and does nothing at all off the main
    thread, where `signal.signal` cannot be called. A probe running in a
    worker thread keeps exactly the behaviour it had.
    """

    def _raise(signum, frame):  # noqa: ARG001 - the signal API's shape
        raise SystemExit(128 + signal.SIGTERM)

    try:
        previous = signal.signal(signal.SIGTERM, _raise)
    except (ValueError, OSError):
        # Not the main thread, or a platform without SIGTERM. The teardown
        # still covers every ordinary exit and Ctrl-C.
        yield
        return
    try:
        yield
    finally:
        try:
            signal.signal(signal.SIGTERM, previous)
        except (ValueError, OSError):
            pass


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


def _will_not_run(binary: str, error: Exception) -> str:
    """Why a yoloai that IS installed still cannot be used.

    `installed` and `works` are different answers, and this is the gap the
    round-trip check exists to find: a binary on PATH that will not
    execute at all — built for another architecture, or a partial
    download — raises `OSError` rather than exiting non-zero."""
    return (
        f"yoloai is at {binary} but will not run: {error}. Reinstall it "
        "(https://yoloai.dev) — a download for another architecture, or a "
        "partial one, looks exactly like this."
    )


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

    with (
        _torn_down_on_sigterm(),
        tempfile.TemporaryDirectory(prefix="rite-selftest-") as workdir,
    ):
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
            except (OSError, subprocess.SubprocessError) as e:
                return SandboxCheck(False, _will_not_run(binary, e), backend=backend)
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
            # Nothing here may raise. This runs while another return value
            # or exception is in flight, so an exception raised in a
            # `finally` REPLACES it — measured on a tester's machine: a
            # yoloai binary that would not exec (`OSError: [Errno 8] Exec
            # format error`, a wrong-architecture or partial download) blew
            # `rite doctor` up with a traceback from the teardown of a probe
            # that had already failed for the same reason.
            try:
                subprocess.run(
                    [binary, "destroy", name],
                    capture_output=True,
                    text=True,
                    errors="replace",
                    timeout=timeout,
                    env=env,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError):
                pass


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
    except (OSError, subprocess.SubprocessError) as e:
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
    2. `github_token` — THIS PROJECT's, or the machine-wide one. Which of
       those it was is the difference between the configuration §5.3.4
       calls correct and the one worth warning about, so it is reported
       rather than collapsed.

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

    Returns `(token, tier)` where tier is `"worker"`, `"project"`,
    `"global"` or `"none"` — the tier is returned rather than inferred by
    the caller so that "which token did this Worker actually get?" has one
    answer.

    ⚠ **`"project"` used to be reported as `"global"`, and that was the
    bug.** `get_scoped` tries this project's namespaced account BEFORE the
    machine-wide one, so a `github_token` belonging to this project came
    back under the same label as a token belonging to the whole machine —
    a label that measured which NAME matched second rather than whether
    the token is bounded. The caller warned on it, so the loud,
    every-start warning fired for the configuration §5.3.4 calls correct,
    and told the reader to provision a per-Worker token that the same
    section retired. `resolve()` has drawn this distinction all along and
    says why; this asks it instead of guessing.

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
        from rite_ai.credentials.store import PROJECT, resolve

        # ONLY a positively-confirmed PROJECT tier silences the warning.
        #
        # The first cut asked `tier == GLOBAL` and called everything else
        # "project", which quietly widened the silent set to two tiers that
        # are machine-wide by construction: `RITE_GITHUB_TOKEN` and the
        # service's own `GITHUB_TOKEN`, both of which `resolve` reports as
        # ENV. A `GITHUB_TOKEN` exported in a shell profile — the ordinary
        # `gh` setup, and usually account-wide — was then handed to a Worker
        # with nothing printed. That is a warning this commit REMOVED, on
        # the exact case its own docstring says must never become silent.
        #
        # These are two separate lookups: `get_scoped` supplies the value,
        # `resolve` the tier, and they do not walk identically (`resolve`
        # consults `RITE_<account>` where `get_scoped` reads the keychain).
        # They can disagree, and a locked keychain makes `resolve` answer
        # NOT_FOUND while `get_scoped` still returns a value. Asking for
        # PROJECT makes every disagreement fail loud instead of silent,
        # which is the direction a credential warning has to be wrong in.
        where = resolve(GLOBAL_TOKEN_CREDENTIAL, credentials)
        return shared, "project" if where.tier == PROJECT else "global"
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


def count_active_sandboxes(
    root: Path | None = None, workers: list[str] | None = None
) -> int | CountUnavailable:
    """How many of THIS PROJECT's sandboxes yoloAI currently reports active.

    **Scoped to the project, because the cap is.**
    `sandbox.max_concurrent_workers` lives in one project's `config.yaml` and
    SPEC §2.5.9 caps "concurrent Workers **per Manager**"; D-47 puts
    cross-project aggregation somewhere else entirely, in the hub. This
    counted every `rite-` sandbox on the machine, so another project's
    Workers and every leftover test probe filled a project's cap. Measured:
    six sandboxes consuming a cap of five, not one of them belonging to the
    project that hit it — so the run reported "at capacity" while the project
    had nothing running, and the workaround was to raise the number.

    **Matched on the path digest, not the whole slug.** A rename in
    `brief.yaml` changes the readable half of `project_slug` and orphans
    everything named under the old one — `label.project_slug` says so itself.
    Matching `-<digest>-` instead survives it, because the digest is over the
    resolved path. A project that MOVES is genuinely orphaned either way; its
    old sandboxes show up in `list_rite_sandboxes` as litter, which is the
    honest place for them.

    **Legacy names still count.** `rite-<worker>`, from before §8.10, has no
    slug at all; it counts when it matches one of this project's configured
    Workers. Under-counting is the worse failure — the cap exists to bound
    concurrency, and a cap that misses its own Workers bounds nothing.

    Without `root` it counts every `rite-` sandbox, which is what it always
    did — and that is now a deliberate caller rather than a leftover: the
    machine-wide bound (`machine.max_sandboxes`) asks exactly the question
    this project-scoped count stopped answering, and asks it one call later
    at the same site. Scoping the cap gave up an accidental machine-wide
    bound; the rootless call is where it was given back on purpose.

    `--active` "includes idle" per yoloAI's own `--help`, so a stopped-but-not
    destroyed sandbox counts; only `destroy` removes one. ⚠ The `agent` field
    is NOT consulted. It reads `idle` both for a sandbox nobody will return to
    and for one whose agent is between turns — measured on a live Worker with
    unapplied changes whose Owner considered it busy — and yoloAI documents no
    meaning for it. Excluding on it would let the cap be exceeded.

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
    # `isinstance` before `.get`: this called `entry.get(...)` straight on
    # each list element, so one non-dict entry from yoloAI — a bare string, a
    # null — raised `AttributeError` out of the worker cap. `_count_named`
    # two hundred lines up already guards exactly this way.
    names = [
        (entry.get("environment") or {}).get("name", "")
        for entry in data.get("sandboxes", [])
        if isinstance(entry, dict)
    ]
    return sum(1 for name in names if _belongs_to(name, root, workers))


def _schedule_refusal(root: Path | str | None) -> str | None:
    """Why the schedule forbids starting a Worker right now, or None.

    ⚠ **The schedule was ADVISORY before 0.5.0 and this is what makes it
    real.** `workers_at` existed, was correct, and was read only by the
    loop's verdict and the scheduler tick; `start_worker` checked the flat
    `sandbox.max_concurrent_workers` and nothing else. So a project
    configured for zero Workers at the weekend started one anyway whenever
    anything asked, while `rite loop status` reported `closed` — the
    schedule allows nobody — about a Worker that was running. Two true
    sentences that disagreed, which is what a computed-and-never-read value
    always produces eventually.

    Best-effort on an unreadable config, matching `_configured_workers`:
    a project whose config will not parse has a louder problem than this,
    and the flat cap above still applies. **A project with no windows is
    unaffected** — `workers_at` returns 0 for an empty schedule, so the
    check is skipped rather than refusing everything.

    The refusal names when the window next opens. A refusal that tells you
    when to come back is a different thing from one that only says no.
    """
    if root is None:
        return None
    try:
        from rite_ai.config.parse import load_project

        project = load_project(Path(root))
        if isinstance(project, list):
            return None
        schedule = project.config.schedule
    except Exception:  # noqa: BLE001 - an unreadable config is not this check's
        return None

    if not schedule.windows:
        return None

    from rite_ai.schedule import current_moment, next_open, workers_at

    moment = current_moment(schedule.timezone)
    allowed = workers_at(schedule, moment.minute_of_day, moment.weekday)
    if allowed > 0:
        return None

    when = next_open(schedule, moment)
    coming = f" Next open: {when}." if when else ""
    return (
        f"the schedule allows 0 Workers right now "
        f"({moment.zone.describe()}).{coming} "
        f"Refused rather than started — `rite schedule show` lists the "
        f"windows, and raising the count is a config change."
    )


def _configured_workers(root: Path | str | None) -> list[str]:
    """This project's Worker names, for the legacy-name match only.

    Best-effort by design: an unreadable roster costs the legacy fallback and
    nothing else, so it returns an empty list rather than becoming a third
    failure mode inside the cap check. Every sandbox started since §8.10
    carries the project digest and is matched without this.
    """
    if root is None:
        return []
    try:
        from rite_ai.config.parse import load_project

        project = load_project(Path(root))
        if isinstance(project, list):
            return []
        return [w.name for w in project.workers]
    except Exception:  # noqa: BLE001 - the cap must not fail on a roster read
        return []


def _belongs_to(name: str, root: Path | None, workers: list[str] | None) -> bool:
    """Whether a sandbox name is this project's. No `root` means every
    `rite-` name, which is the machine-wide question this used to answer."""
    if not name.startswith("rite-"):
        return False
    if root is None:
        return True
    from rite_ai.label import project_digest

    if f"-{project_digest(Path(root))}-" in name:
        return True
    # Pre-§8.10 `rite-<worker>`: no slug to match on, so the Worker's own name
    # is the only evidence. Checked against the configured roster rather than
    # against any string, or one project's `rite-w1` would count for another's.
    return any(name == legacy_sandbox_name(w) for w in (workers or []))


@dataclass(frozen=True)
class SandboxEntry:
    """One `rite-` sandbox as yoloAI describes it.

    `agent` is reported and deliberately NOT used to decide anything. It is
    `idle` for a sandbox nobody will return to and also for one whose agent
    is merely between turns — measured: `~/PapugaAI`'s live Worker w1 reports
    `agent: idle`, 15h old, with unapplied changes, while its Owner considers
    it busy. yoloAI documents no meaning for the field (`yoloai help` has no
    topic for it), so it cannot carry a liveness decision. It is shown to a
    human, who can.
    """

    name: str
    status: str = ""
    agent: str = ""
    has_changes: bool = False
    workdir: str = ""

    @property
    def safe_to_destroy(self) -> bool:
        """Only the absence of unapplied changes, never a liveness claim. A
        sandbox is a copy-on-write workspace: `has_changes` means edits exist
        there and nowhere else."""
        return not self.has_changes


def list_rite_sandboxes() -> list[SandboxEntry] | CountUnavailable:
    """Every `rite-` sandbox on this machine, for a human to read.

    Separate from `count_active_sandboxes` on purpose: that one answers a
    question about capacity and this one answers "what is lying around". A
    listing narrowed the way a cap is narrowed would hide the litter it exists
    to show.
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
    except (OSError, subprocess.TimeoutExpired) as e:
        return CountUnavailable(f"`yoloai ls --active --json` could not run: {e}")
    if proc.returncode != 0:
        return CountUnavailable(f"`yoloai ls --active --json` exited {proc.returncode}")
    try:
        data = json.loads(proc.stdout)
        entries = data["sandboxes"]
    except (ValueError, KeyError, TypeError):
        return CountUnavailable("`yoloai ls --active --json` did not return JSON")

    found: list[SandboxEntry] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        environment = entry.get("environment") or {}
        name = str(environment.get("name", ""))
        if not name.startswith("rite-"):
            continue
        dirs = environment.get("dirs") or []
        workdir = ""
        for d in dirs:
            if isinstance(d, dict) and d.get("mode") == "copy":
                workdir = str(d.get("host_path", ""))
                break
        found.append(
            SandboxEntry(
                name=name,
                status=str(entry.get("status", "")),
                agent=str(entry.get("agent", "")),
                # Anything that is not a plain "no" counts as holding work.
                # The conservative direction: a sandbox wrongly called unsafe
                # to destroy costs some disk, and one wrongly called safe
                # costs somebody's uncommitted changes.
                has_changes=str(entry.get("has_changes", "yes")).lower() != "no",
                workdir=workdir,
            )
        )
    return found


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
    ticket: str = "",
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
    # Scoped to this project (SPEC §2.5.9 caps per Manager). Machine-wide
    # counting let another project's Workers and old test probes fill this
    # project's cap, which presents as capacity rather than as litter.
    active = count_active_sandboxes(root, _configured_workers(root))
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

    # The SCHEDULE, which until 0.5.0 nothing enforced at the spawn site.
    # Checked after the flat cap because the cap is the more specific
    # failure, and before anything is started because a ceiling applied
    # afterwards is a report (§9.14.5 makes the same argument about a
    # budget).
    schedule_problem = _schedule_refusal(root)
    if schedule_problem is not None:
        return SandboxResult(False, schedule_problem)

    # The MACHINE's bound, second and deliberately so. The project cap is
    # the specific, actionable failure ("this project is at its limit"); a
    # machine bound is environmental ("this box is full, possibly because
    # of another project"), and someone who trips both should be told the
    # one they can do something about.
    #
    # A different quantity from `max_concurrent_workers`, which §2.5.9 caps
    # per Manager: four projects each correctly capped at five give twenty
    # sessions on one laptop with nobody at fault. It cannot live in
    # `config.yaml` — that file is committed and shared, so the same bytes
    # are right on one machine and wrong on another, which is the argument
    # `.rite/machine` already makes for identity.
    #
    # Nothing is counted when no bound is set, so an upgrade changes
    # nothing for a fleet already running.
    bound = max_sandboxes()
    if bound is not None:
        everywhere = count_active_sandboxes()
        if isinstance(everywhere, CountUnavailable):
            # Refuse, never treat as zero. `count_active_sandboxes` returns
            # a union and the number is the easy half to read; a `yoloai
            # ls` that cannot answer means the sandbox tooling here is
            # broken, and starting anyway is the thing a bound exists to
            # stop.
            return SandboxResult(
                False,
                f"cannot count this machine's sandboxes, so the machine "
                f"bound ({bound}) cannot be enforced — refusing to start "
                f"'{worker}'. {everywhere.reason}",
            )
        if everywhere + 1 > bound:
            return SandboxResult(
                False,
                f"this machine already runs {everywhere} sandbox(es) and its "
                f"bound is {bound} — refusing to start '{worker}'. Some may "
                f"belong to other projects, or be leftovers: `rite sandbox "
                f"status` lists them. Raise it in ~/.rite/machine.json "
                f'("max_sandboxes") or unset it for no bound.',
            )

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
    # Recorded where rite SAW it start: `yoloai new` exited 0. The standup
    # cites this line (plan § K4), so it carries the name a reader checks.
    from rite_ai.reporting import events

    events.record(root, "sandbox-started", worker=worker, sandbox=name, ticket=ticket)
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
    if root is None:
        # A library caller with no project root. It cannot be checked, so it
        # is not cleared — `destroy_worker(worker, root=None)` used to skip
        # the guard entirely and destroy unconditionally.
        return (
            f"cannot check sandbox '{name}' for unpushed work without the project root"
        )

    if not (_yoloai_sandboxes_dir() / name).is_dir():
        # "" — rite has nothing to say — AND THAT IS ONLY SAFE BECAUSE
        # `destroy` no longer passes `--abandon-unapplied` unless the caller
        # asked for it. This test is weak by construction:
        # `_yoloai_sandboxes_dir()` is hard-coded, and the comment on it says
        # the layout "is yoloAI's, not an interface". A yoloAI upgrade or a
        # non-default home makes every sandbox look absent, so this cannot be
        # the only thing between the command and the work.
        #
        # It used to be. `--abandon-unapplied` was passed unconditionally,
        # switching off yoloAI's own refusal, so rite's guess was the sole
        # guard and it guessed "nothing at risk" whenever it could not look.
        #
        # An earlier version of this fix refused here instead. That was
        # wrong in the other direction: on any machine where yoloAI has not
        # created a sandbox yet the directory legitimately does not exist,
        # and `destroy` would have refused forever with a message about
        # rite's own blindness. Two checks that fail independently beats one
        # check that tries to be certain.
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
    from rite_ai.reporting import events

    if root is not None:
        events.record(Path(root), "sandbox-stopped", worker=worker, sandbox=name)
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
            # `--abandon-unapplied` ONLY UNDER --force. It was passed
            # unconditionally, which switched off yoloAI's own refusal to
            # destroy a sandbox holding unapplied work on the ordinary
            # `rite sandbox destroy alpha` path — leaving rite's guard above
            # as the single thing between the command and the work, and that
            # guard used to fail open. Two independent checks that both have
            # to be wrong is the point; one of them being rite's own is not.
            [binary, "destroy", name] + (["--abandon-unapplied"] if force else []),
            capture_output=True,
            text=True,
            errors="replace",
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return SandboxResult(False, "yoloai destroy timed out after 120s")
    if proc.returncode != 0:
        return SandboxResult(False, proc.stderr.strip() or proc.stdout.strip())
    from rite_ai.reporting import events

    if root is not None:
        events.record(Path(root), "sandbox-destroyed", worker=worker, sandbox=name)
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
#
# ⚠ ANSI ESCAPES BREAK BOTH PASSES, AND `rite sandbox pane --ansi` IS A FLAG
# THE GUIDE ADVERTISES. A terminal colours its own output, so a real screen
# carries `\x1b[32mexport\x1b[0m GITHUB_TOKEN='ghp_AAAA\x1b[0mBBBB'` — the
# statement pattern needed `export ` literally adjacent to the name, which a
# colour reset breaks, and the value pattern below allowed a line break
# between characters but not an escape. Either one leaked the token while
# README and guide both promise these values are replaced.
#
# So both passes skip escapes wherever they may fall. `_SKIPPABLE` is a line
# wrap or a CSI/OSC sequence: zero-width as far as the reader is concerned,
# and therefore zero-width as far as matching is concerned.
_SKIPPABLE = r"(?:\r?\n|\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\))*"
_EXPORT_STATEMENT = re.compile(
    r"(export" + _SKIPPABLE + r"\s+" + _SKIPPABLE + r"[A-Za-z_][A-Za-z0-9_]*"
    r"" + _SKIPPABLE + r"=')((?:[^']|'\\'')*)(')"
)


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
    lines AND colours them, so both passes allow a line break or an ANSI
    escape sequence between any two characters — see `_SKIPPABLE`. Values
    shorter than eight characters are not searched for: `false` and `5` are
    not secrets, and replacing every occurrence of them would destroy the
    capture.

    **A truncated value is still a hole**, and it is the known remaining one:
    the second pass needs the whole secret, so a launch line whose start has
    scrolled off the top of the pane leaves a token tail with no
    `export NAME='` prefix for the first pass either. Partial disclosure,
    recorded rather than hidden.

    ⚠ A mitigation, not the fix. It closes rite's own surface, the one that
    routes a screen into Claude sessions by design; `yoloai attach` still
    shows the values, and so does anything else that reads the session's
    screen. The fix is for Workers to fetch credentials through a validated
    channel instead of having them injected as environment variables — the
    MCP entry in `.docs/FUTURE_IMPROVEMENTS.md` ("credentials through the
    MCP server"). When that lands, this should have nothing left to redact.
    """
    text = _EXPORT_STATEMENT.sub(lambda m: m.group(1) + "[redacted]" + m.group(3), text)
    searched = {s for s in secrets if s and len(s) >= 8}
    for value in sorted(searched, key=len, reverse=True):
        pattern = _SKIPPABLE.join(re.escape(ch) for ch in value)
        text = re.sub(pattern, "[redacted]", text)
    return text


_ENV_ASSIGNMENT = re.compile(r"\b([A-Z_][A-Z0-9_]*=)([^\s]{8,})")


def redact_assignments(text: str, secrets: Iterable[str] = ()) -> str:
    """`redact_secrets`, plus any environment-shaped assignment's value.

    BY STRUCTURE, never by a list of token formats (C7): `UPPER_NAME=value`,
    quoted or not, `export` or not, with a value of eight characters or more.
    Upper case is what makes it environment-shaped, so `--sessions=3` and
    `PYTHONPATH=src` survive and the text stays useful.

    Shared by every path that sends text a Manager wrote somewhere a person
    reads it — the journal (C7) and the Slack relay (A4) — so the two cannot
    drift into different rules.

    ⚠ Known hole, stated: a bare token that is neither in an assignment nor
    one of `secrets` (an `Authorization:` header, a token alone on a line) is
    not recognised.
    """
    return _ENV_ASSIGNMENT.sub(
        lambda m: m.group(1) + "[redacted]", redact_secrets(text, secrets)
    )


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
