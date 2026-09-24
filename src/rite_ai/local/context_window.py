"""Serve a local model with a window big enough to work in (B7's other half).

`rite doctor` reports that a model is being served with too little context.
This is what does something about it.

**The mechanism, and it is cheaper than it looks.** `ollama create` from a
one-line Modelfile that sets `num_ctx` produces a DERIVED model: a new
manifest pointing at the base model's existing weight blob, plus a params
layer. The larger window is then served through the same `/v1` path an agent
already uses, and the operator's environment is untouched.

⚠ **MEASURED 2026-09-24, because two reports disagreed and one was wrong.**
Deriving from `qwen3:32b`, a 20 GB model:

    models dir before : 187,924,440 KB      blobs: 77
    models dir after  : 187,924,444 KB      blobs: 77
    delta             : 4 KB, ZERO new blobs
    create took       : 0.07 seconds
    largest layer     : sha256:3291abe70f16… 18.8 GB — THE SAME BLOB
    unique to derived : 1 layer, 136 bytes

**136 bytes.** The claim that it doubles a large model's footprint is false,
and the reason somebody believed it is worth keeping: **`ollama list` sums
layer sizes, so it reports the derived model as "20 GB"** exactly like its
base. Reading `list` is what makes a shared blob look like a copy.

⚠ **IT WRITES TO THE OPERATOR'S SHARED MODEL LIBRARY**, which is global
state outside the project and something rite otherwise never touches. So
everything here is named, reported and removable: the name says rite made it
and what for, `created` says whether this call was the one that made it, and
`removal_command` is printed rather than left to be worked out.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from dataclasses import dataclass

DERIVED_PREFIX = "rite-ctx"
"""Every derived model rite creates starts with this, so an operator can see
what rite put in their library with `ollama list | grep rite-ctx` and remove
all of it without knowing which project asked for which."""

CREATE_TIMEOUT_SECONDS = 120.0
"""Measured at 0.07s for a 20 GB base, because nothing is copied. Generous
by two orders of magnitude, so a slow disk is not a failure."""


def derived_name(model: str, window: int) -> str:
    """`rite-ctx32768-qwen3-32b` from `qwen3:32b`.

    The base model's tag separator and any other character ollama would not
    accept in a name become `-`. The window is IN the name so two projects
    wanting different windows get different models rather than one silently
    winning.
    """
    base = re.sub(r"[^a-z0-9._-]+", "-", model.lower()).strip("-")
    return f"{DERIVED_PREFIX}{window}-{base}"


@dataclass(frozen=True)
class Ensured:
    """What to use, and what it cost to get it."""

    model: str
    """The model name to actually serve with — the original when it was
    already big enough."""
    created: bool = False
    """Whether THIS call wrote to the operator's library."""
    detail: str = ""
    problem: str = ""

    @property
    def removal_command(self) -> str:
        """How to undo it, or "" when rite created nothing."""
        return f"ollama rm {self.model}" if self.created else ""


def ensure_window(
    model: str,
    window: int,
    *,
    served: int | None = None,
    run=None,
) -> Ensured:
    """A model name served with at least `window` tokens.

    `served` is the window the model currently gets, from `engine_probe` —
    `None` means unknown, and **unknown is not treated as too small**: a
    model that is merely not loaded yet would otherwise have a derived twin
    created for it on every start.

    Returns the original name unchanged when nothing needs doing, so the
    caller has one thing to use either way.
    """
    if served is not None and served >= window:
        return Ensured(model, detail=f"already served with {served} tokens")
    if served is None:
        return Ensured(
            model,
            detail=(
                "the served window could not be established, so nothing was "
                "created — an unloaded model is not a small one"
            ),
        )
    if model.startswith(DERIVED_PREFIX):
        # Deriving from a derived model would work and would also make a
        # chain nobody can read. The first one already carries the window.
        return Ensured(
            model,
            problem=(
                f"{model} is already a rite-derived model served with "
                f"{served} tokens, which is below {window}. Remove it "
                f"(`ollama rm {model}`) and let rite derive again from the "
                "base model rather than stacking."
            ),
        )

    name = derived_name(model, window)
    runner = run if callable(run) else subprocess.run
    modelfile = f"FROM {model}\nPARAMETER num_ctx {window}\n"
    # ⚠ A REAL FILE, not stdin. `ollama create -f -` does not read stdin —
    # measured, it answers "no Modelfile or safetensors files found" and
    # exits non-zero. The unit tests could not have caught that, because a
    # fake runner never exercises ollama's own argument handling; the live
    # run did, which is why one exists.
    try:
        with tempfile.NamedTemporaryFile(
            "w", suffix=".Modelfile", prefix="rite-ctx-", delete=False
        ) as handle:
            handle.write(modelfile)
            modelfile_path = handle.name
    except OSError as e:
        return Ensured(model, problem=f"could not write a Modelfile: {e}")
    try:
        done = runner(
            ["ollama", "create", name, "-f", modelfile_path],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=CREATE_TIMEOUT_SECONDS,
        )
    except FileNotFoundError:
        return Ensured(model, problem="ollama is not on PATH, so nothing was created.")
    except Exception as e:  # noqa: BLE001 - any failure here is a result
        return Ensured(model, problem=f"could not create {name}: {e}")
    finally:
        try:
            os.unlink(modelfile_path)
        except OSError:
            pass

    if getattr(done, "returncode", 1) != 0:
        detail = (done.stderr or done.stdout or "").strip()[:200]
        return Ensured(model, problem=f"`ollama create {name}` failed: {detail}")

    return Ensured(
        name,
        created=True,
        detail=(
            f"created {name} in this machine's ollama library — a derived "
            f"model serving {model} with a {window}-token window. It shares "
            f"{model}'s weights and costs about 136 bytes; `ollama list` "
            "will nonetheless report it at the base model's full size, "
            "because that command sums shared layers."
        ),
    )
