# `test_no_dead_wiring`'s matcher is a substring, and it masks four functions

**Status: a note to ticket. Found 2026-10-03 while landing L-6. Not fixed here,
deliberately — see §3.**

**Citation convention:** a bare `§` means a section of `SPEC.md`; this note's
own sections are written out, because the citation gate's regex is
context-free and would match a real SPEC section.

## 1. The defect

`tests/test_no_dead_wiring.py:called_outside` asks whether a public function has
a production caller like this:

```python
if f"{name}(" in path.read_text():
    return True
```

⚠ **A substring is not a call.** A shorter name sits inside a longer one, so
`compose` reads as called the moment any module writes `decompose(`. It also
cannot tell code from prose: a comment or docstring that merely quotes
`somefunction(` satisfies it.

This is the same text-versus-code mistake `tests/test_blast_radius.py` documents
when it explains why it enumerates every `["git", ...]` argument list instead of
grepping file text — *"docstrings legitimately discuss pushing"*.

**It was hit three times in one afternoon:** L-6 named a parameter after the
command it runs and `compose`'s exemption was reported stale; renaming the
parameter fixed it; and then the COMMENT explaining the rename tripped it again
by quoting the offending text.

## 2. What the fix uncovers

A word boundary is the fix, and it is one line:

```python
pattern = re.compile(rf"\b{re.escape(name)}\(")
```

⚠ **With it, four public functions are reported as having no production caller
and no recorded reason:**

| function | module |
|---|---|
| `path_for` | `last_tick.py` |
| `outstanding` | `asking.py` |
| `rite_config` | `cursor_login.py` |
| `settled_alive` | `session.py` |

Each was passing only because a LONGER name containing it is called somewhere —
`_path_for(`, and so on. So the test that exists to catch functions that shipped
complete and unwired has been masking four of exactly that kind.

⚠ **Whether each is genuinely dead or genuinely exempt is not established here.**
`UNCALLED_ON_PURPOSE` wants a reason, and inventing four reasons for code this
note's author does not own would put a false claim in the test that is supposed
to catch false claims. That is the work this asks for.

## 3. Why it is not fixed in the Ollama branch

Because the one-line fix fails for four reasons that have nothing to do with the
Ollama track, and a feature branch that cannot merge until someone else's dead
code is classified is a branch holding the wrong thing hostage. L-6 instead
renamed its own parameter so the substring stops matching, with a comment saying
why, and this note carries the real defect.

## 4. The decision it asks for

Fix the matcher and, in the same change, either wire or explain the four. The
order matters: fixing the matcher alone turns `test_no_dead_wiring` red on main,
and explaining them alone leaves the masking in place for the next short name.
