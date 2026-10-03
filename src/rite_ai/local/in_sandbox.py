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


class SandboxGone(Exception):
    """`yoloai exec` could not run the command, because the sandbox is not
    there to run it in. **The turn did not happen.**

    ⚠ **A race rite must not tolerate, and the one place it is real.**
    `worker_step.placement_for` checks the sandbox is active, and the turn
    starts afterwards — and the thing that stops a Worker's sandbox between
    those two moments is rite's own `publishing/deliver.py`, which stops it
    FIRST and by design ("so the Worker cannot commit between the collect and
    anything that later trusts it"). A supervisor cycle that steps a subtask
    while a delivery is honoured is therefore the ordinary case, not a freak
    one.

    Left unraised it was silent and wrong in the worst direction.
    `GooseAgent` deliberately never reads an exit code — because `goose run`
    returns 0 for an unreachable provider — so `yoloai exec`'s own refusal
    arrived as a turn that RAN, with yoloAI's error text as the model's
    output. The verify then failed on an unchanged tree, and the subtask
    recorded a failed ATTEMPT. RL-47 says only work counts; nothing ran.

    ⚠ That reasoning does not generalise back to goose: yoloAI's exit code is
    trustworthy where goose's is not (measured: `yoloai exec` on a stopped
    sandbox exits 1 having run nothing, and passes an inner command's own code
    through unchanged — `exit 3` arrives as 3). So this reads the code ONLY to
    ask yoloAI a second, definite question, and never to judge the turn.
    """


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
    subdir: str = "",
) -> list[str]:
    """`argv`, wrapped so it runs inside `sandbox`.

    ⚠ **The `--` is load-bearing and was measured.** `yoloai exec` parses
    flags belonging to the command as its own: `yoloai exec <box> curl -s ...`
    fails with *"unknown shorthand flag: 's' in -s"*, and so does `sh -c`. With
    `--` the whole tail reaches the sandbox untouched.

    The environment is placed with `env`, because `yoloai exec` takes no
    `--env`. Only `FORWARDED` keys travel, and a secret-shaped name refuses
    rather than riding on argv.

    `subdir` is where the turn runs, RELATIVE to the sandbox's copy of the
    workdir — and relative is the whole point. ⚠ `yoloai exec` has no `--cwd`
    (measured on 0.11.0: its flags are the global ones and nothing else), and
    it starts in the copy's root. A Worker's workdir is `workers/<name>/` while
    its modules are clones INSIDE it, so a turn left in the root would run the
    model against the directory above the repository it is meant to edit.

    ⚠ **Placed with `env -C`, and NOT with `sh -c 'cd … && …'`.** `runners.py`
    makes "no shell" a property of this tier — a verify is `shlex.split` so a
    decomposer cannot smuggle a second command — and a launcher that reached
    for a shell would take that property away from the half of the tier that
    runs a MODEL's turn. Measured inside a real seatbelt sandbox: macOS's own
    `env` honours `-C`, so the flag needs no coreutils and no shell. `env` is
    already how the environment is placed here, so this adds a flag to a
    process that was being run anyway rather than a process.

    A relative `subdir` is also what makes it safe to pass at all: an absolute
    HOST path is the mistake `exec_launcher`'s docstring warns about, and this
    cannot express one.
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
    if subdir.startswith("/"):
        # Refused rather than resolved: an absolute path here is a HOST path,
        # and `exec_launcher` already measured what that costs — it either
        # fails or, if the path happens to exist on the host, runs the turn
        # against the operator's real tree.
        raise ValueError(
            f"subdir {subdir!r} is absolute; a sandboxed turn's working "
            "directory is relative to the sandbox's own copy of the workdir, "
            "and a host path either fails inside or escapes the sandbox"
        )
    where = ["-C", subdir] if subdir else []
    tail = ["env", *where, *placed, *argv] if (placed or where) else list(argv)
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


def exec_launcher(sandbox: str, binary: str = "", timeout: int = 0, subdir: str = ""):
    """A `GooseAgent.launch` that runs the turn inside `sandbox`.

    ⚠ **`workspace` is deliberately ignored.** It is a HOST path, and the
    sandbox holds a COPY of the workdir at a path of yoloAI's own making
    (`.../rw/work/^s...^s` — the host path with separators escaped). `yoloai
    exec` already starts in that copy, which is the directory the turn should
    run in, so passing a host `cwd` would either fail or — worse, if the path
    happens to exist on the host — run the turn OUTSIDE the sandbox against the
    operator's real tree.

    ⚠ **Which is why `subdir` is the one that is honoured.** The host path is
    ignored and a RELATIVE path is taken instead, because the two are not the
    same question: the caller knows which module clone inside the copy the
    turn belongs in, and `subdir` is the only way to say it that cannot name a
    host path. `worker_step` passes the module's directory name.

    ⚠ **The caller must give `GooseAgent` `inherit_environment=False`.** This
    launcher refuses to put a secret-shaped variable on argv, and
    `GooseAgent.run` builds `dict(os.environ)` — correct on the host, fatal
    here. Measured: with `GITHUB_TOKEN` merely PRESENT in the operator's shell,
    a sandboxed turn came back `could not start goose: SecretOnArgv` and was
    recorded as a failed ATTEMPT, which is also RL-47 broken (a turn that never
    happened spent the subtask). The fix belongs on the agent, which is the
    thing that knows it is sandboxed; this note is here because the two have to
    be set together and nothing in the type system says so.
    """
    resolved = binary or shutil.which("yoloai") or "yoloai"

    def launch(argv, workspace, environment):  # noqa: ARG001 - see docstring
        from rite_ai.local.goose_agent import RUN_TIMEOUT_SECONDS

        completed = subprocess.run(
            exec_argv(
                sandbox, list(argv), dict(environment or {}), resolved, subdir=subdir
            ),
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout or RUN_TIMEOUT_SECONDS,
            stdin=subprocess.DEVNULL,
        )
        if completed.returncode != 0:
            # ⚠ **Asked of yoloAI, not read from its prose.** A non-zero code
            # is only a reason to ask the second question, because the code
            # alone cannot answer it — yoloAI passes an inner command's exit
            # through, so `1` is equally "goose exited 1" and "there is no
            # sandbox". The status is the definite answer, and it is definite
            # in exactly the direction that matters: if the sandbox is not
            # running NOW and the exec failed, the command did not run.
            from rite_ai.sandbox import sandbox_status_named

            # ⚠ `container_is_down`, never `!= "active"`. Three of yoloAI's five
            # status words describe a container that is UP (`activity.py`:
            # "active=working, idle=waiting at prompt, done=finished,
            # failed=error"), and a local Worker's sandbox runs the `idle` no-op
            # agent — so a positive test here would read every genuine `goose`
            # failure on an idle sandbox as "the sandbox is gone" and throw the
            # model's output away as a turn that never happened, inverting the
            # very rule this guard serves. None (yoloAI unaskable) does not
            # raise: "I could not check" is not "it is gone".
            status = sandbox_status_named(sandbox, resolved)
            if status.container_is_down:
                raise SandboxGone(
                    f"sandbox {sandbox} is {status}, so the turn did not run "
                    f"(`yoloai exec` exited {completed.returncode}). A "
                    "delivery stops a Worker's sandbox, so this is a cycle "
                    "that stepped a subtask while one was being honoured"
                )
        return completed

    return launch
