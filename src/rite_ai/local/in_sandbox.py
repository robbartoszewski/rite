"""Running a local engine's turn INSIDE a Worker sandbox (OL5).

The local tier has always run on the host. A local Worker runs its turn inside
a seatbelt sandbox, and rite drives it: the sandbox holds an `idle` container
(`sandbox.LOCAL_WORKER_AGENT` says why it cannot be `--agent goose`), and each
turn is one `yoloai exec`.

⚠ **This is a LAUNCHER, not a second agent.** `GooseAgent` already takes an
injectable `launch`, so running inside costs a different launcher and no change
to the tool loop — which is what keeps RL-13 ("borrow an existing agent's
loop") true of the sandboxed path as well as the host one.

Measured in `docs/design/spikes/OL1-ollama-inside-a-worker-sandbox.md`: from
inside a real Worker, `localhost:11434` answers, `goose` is on PATH, rite's own
`engine_probe` reports the pinned window, and a full tool loop runs.
"""

from __future__ import annotations

import shutil
import subprocess

# What the launcher forwards into the sandbox, and NOTHING else.
#
# ⚠ `GooseAgent.run` hands its launcher `dict(os.environ)` updated with Goose's
# own keys — the WHOLE host environment, because on the host that is exactly
# right. It is not right here: `yoloai exec` has no `--env`, so anything
# forwarded is forwarded ON ARGV, and SB12 measured a sandbox's environment
# readable from other sandboxes on this machine. Passing the host environment
# through would publish every variable the operator's shell happens to hold.
#
# So the set is closed and small. Everything in it is configuration — which
# model, where it answers, how much room it has — and none of it is a secret,
# which is what makes argv acceptable at all. `_secret_shaped` keeps that true
# if someone adds a key later.
FORWARDED: tuple[str, ...] = (
    "GOOSE_PROVIDER",
    "GOOSE_MODEL",
    "GOOSE_MODE",
    "GOOSE_CONTEXT_LIMIT",
    "GOOSE_PATH_ROOT",
    "OLLAMA_HOST",
)

_SECRET_WORDS = (
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PASSWD",
    "CREDENTIAL",
    "API_KEY",
    "KEY",
)


class SecretOnArgv(Exception):
    """A value that must not be placed on a command line was about to be.

    Raised rather than dropped: silently omitting a variable would produce a
    turn that runs with the wrong configuration and says nothing, and silently
    keeping it would put a credential in `ps` output for every process on the
    machine to read.
    """


def _secret_shaped(name: str) -> bool:
    upper = name.upper()
    return any(word in upper for word in _SECRET_WORDS)


def exec_argv(
    sandbox: str,
    argv: list[str],
    environment: dict[str, str] | None = None,
    binary: str = "yoloai",
) -> list[str]:
    """`argv`, wrapped so it runs inside `sandbox`.

    ⚠ **The `--` is load-bearing and was measured.** `yoloai exec` parses
    flags belonging to the command as its own: `yoloai exec <box> curl -s ...`
    fails with *"unknown shorthand flag: 's' in -s"*, and so does `sh -c`. With
    `--` the whole tail reaches the sandbox untouched.

    The environment is placed with `env`, because `yoloai exec` takes no
    `--env`. Only `FORWARDED` keys travel, and a secret-shaped name refuses
    rather than riding on argv.
    """
    placed: list[str] = []
    for key in FORWARDED:
        value = (environment or {}).get(key)
        if value is None or value == "":
            continue
        if _secret_shaped(key):
            raise SecretOnArgv(
                f"{key} is forwarded into a sandbox on the command line, where "
                "SB12 measured it readable from other sandboxes — give it to "
                "the sandbox at creation (`--env`) instead of per turn"
            )
        placed.append(f"{key}={value}")
    for key, value in sorted((environment or {}).items()):
        if key not in FORWARDED and _secret_shaped(key) and value:
            # Not placed, so not a leak — but a caller that expected it to
            # arrive would get a turn configured differently from what it
            # asked for, and that must not be silent.
            raise SecretOnArgv(
                f"{key} was passed to a sandboxed turn; this launcher forwards "
                f"only {', '.join(FORWARDED)}, and will not put a secret on a "
                "command line"
            )
    tail = ["env", *placed, *argv] if placed else list(argv)
    return [binary, "exec", sandbox, "--", *tail]


def instruction_dir_for(sandbox: str, binary: str = "") -> str:
    """Where a sandboxed turn's instruction file should be CREATED, or "".

    The sandbox's exchange directory, which is host-writable and sits inside the
    sandbox's own layer — measured isolated (`ol2-w1` could neither read
    `ol2-w2`'s work nor write into its layer). ⚠ Its path is the SAME string
    from the host and from inside, so a file written here needs no copying and
    no argv rewriting; Goose reads the path it was given.

    This is the fix for a leak that merely functioning hid. A host `tempfile` is
    readable from inside a Worker (measured, OL1), so the instruction file WORKED
    there — while sitting in the per-user temp root, which every sandbox on this
    machine is granted. The subtask's intent and spec slice were therefore
    readable by every other Worker for as long as the turn ran. Copying the file
    in after the fact only shortens that window; writing it here removes it.

    "" when the sandbox cannot be located, and the caller then leaves
    `GooseAgent.instruction_dir` empty — the host temp root, which is correct
    for a turn that is not running in a sandbox at all.
    """
    from rite_ai.sandbox import sandbox_state_dir

    layer = sandbox_state_dir(sandbox, binary or shutil.which("yoloai") or "yoloai")
    return str(layer / "files") if layer is not None else ""


def exec_launcher(sandbox: str, binary: str = "", timeout: int = 0):
    """A `GooseAgent.launch` that runs the turn inside `sandbox`.

    ⚠ **`workspace` is deliberately ignored.** It is a HOST path, and the
    sandbox holds a COPY of the workdir at a path of yoloAI's own making
    (`.../rw/work/^s...^s` — the host path with separators escaped). `yoloai
    exec` already starts in that copy, which is the directory the turn should
    run in, so passing a host `cwd` would either fail or — worse, if the path
    happens to exist on the host — run the turn OUTSIDE the sandbox against the
    operator's real tree.
    """
    resolved = binary or shutil.which("yoloai") or "yoloai"

    def launch(argv, workspace, environment):  # noqa: ARG001 - see docstring
        from rite_ai.local.goose_agent import RUN_TIMEOUT_SECONDS

        return subprocess.run(
            exec_argv(sandbox, list(argv), dict(environment or {}), resolved),
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout or RUN_TIMEOUT_SECONDS,
            stdin=subprocess.DEVNULL,
        )

    return launch
