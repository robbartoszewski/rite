# MM-2b — moving the inbox out of the project tree

Approved by Robert, to land **after** the Linux observation pass so it does not
disturb what is being measured. This is the preparation: what moves, what it
costs, and two corrections to the estimate that got it approved.

## Why it is worth more than a tidy-up

MM-2 — no Manager writes a Manager's inbox, its own included — is enforced by
the sandbox, not by application code. Inside the project tree that is expensive
on Linux, because **Landlock has no deny rule**: rules only grant and the
effective access is the UNION, so a hole cannot be carved out of a granted
tree. The fence is therefore an ENUMERATION of the project root's existing
entries, which costs two things measured in `landlock._fenced_project_paths`:

* a Manager **cannot create a new top-level file or directory** in its project
  during a cycle (shipped as a known issue, D17);
* the enumeration is a **snapshot** taken when the policy is written, so a
  directory appearing mid-cycle is outside the boundary until the next launch.

Move the mail out of the project and both disappear: the project goes back to
being granted as a single tree, which is what seatbelt does today and what
Landlock can express in one rule.

**It also removes the `/tmp` interaction.** `compose_policy` grants `/tmp` and
`/var/tmp` read+write to mirror seatbelt, and because Landlock unions grants
that OVERRIDES the enumeration — measured: for a project under `/tmp`, every
inbox was writable and the credential narrowing was defeated too. With mail
outside the project, no wholesale grant reaches it and the interaction is gone
regardless of where the project lives. (`fix/landlock-no-wholesale-temp`
remains worth landing on its own merits; it is a separate hole.)

So: yes, it follows. The move retires D17, the snapshot caveat, and the `/tmp`
interaction with MM-2 — three divergences for one change.

## ⚠ Correction 1: `mailbox_dir()` is NOT the only path join

The estimate that got this approved said one function decides the location.
It does not. `mailbox.py` joins the mail path in **three** places:

| line | path | what it is |
|---|---|---|
| 77 | `manager_dir(root, m) / "mail" / box` | `mailbox_dir`, the boxes |
| 95 | `manager_dir(root, m) / "mail" / f"{box}.read" / f"{reader}.json"` | one reader's receipt |
| 226 | `manager_dir(root, m) / "mail" / f"{box}.read"` | the receipts directory |

The real chokepoint is `manager_dir(root, manager) / "mail"`. So the first step
is to introduce `mail_root(root, manager)` and derive all three from it —
otherwise the boxes move and the **read receipts stay behind**, which would
re-deliver every message already read. That is the failure this correction
exists to prevent.

## ⚠ Correction 2: not `~/.rite/<namespace>/`, because that location is
## already rejected in this codebase

The proposed layout was `~/.rite/<namespace>/managers/<name>/mail/`.
`github_access._credential_root` rejects exactly that, in its own words:

> ⚠ NOT under the project and NOT under `~/.rite`. Both are granted to every
> Manager of a project (the project read+write, `~/.rite` read), so a token
> there would be readable by every sibling Manager.

`~/.rite` is granted READABLE to every Manager by `_tool_paths`, so mail there
stays readable by every sibling. That is no worse than today — in-tree mail is
readable by every Manager already — but it wastes the move.

**Use the credential root's precedent instead**: a per-Manager directory that
only that Manager's profile grants, beside the credentials that already live
there. Then:

* a Manager reaches **its own** mail and no other Manager's — not even to read,
  which is stronger than today;
* **the inbox needs no enumeration**: grant `mail/out` writable by name and
  simply do not grant `mail/in`. Landlock's lack of a deny stops mattering,
  because nothing is being carved out of a tree;
* the macOS fence collapses from three ordered rules to one grant and one
  omission, as predicted.

## Migration, and how it avoids losing messages

Existing projects have mail in-tree, and a project may be mid-flight.

* **Drain, do not move once.** A one-shot move races a writer that still knows
  the old path. `rite start` already takes `run.lock` (`github_access.py:112`)
  before touching any credential; the migration runs **under that lock**, and
  the legacy directory is drained on every start until it is empty and gone,
  not just the first.
* **Never delete an undelivered message.** Files move; a name collision is
  resolved by keeping both, because a mail file's name carries its timestamp
  and two messages are two messages.
* **Read receipts move with the boxes**, per correction 1, or every message
  already read is delivered again.
* **Say what happened.** The count of messages carried across is printed at
  start. A silent migration of authority-carrying files is the wrong default.
* A Manager's pane holds no mail path — rite resolves them per call — so a
  running Manager is not invalidated mid-cycle by the move.

## What does NOT need changing, checked rather than assumed

* **Nothing archives, cleans up or removes `.rite/managers`.** No `rmtree` in
  `pool/` or `lifecycle/` touches it.
* **No journal, audit or `rite status` walks the mail tree.** `manager_dir` has
  ten callers across eight modules and only `mailbox.py`'s three are mail
  paths; the rest are the journal, instance records and the broker, none of
  which join `mail`.
* So out-of-tree mail does not orphan on project removal, because nothing
  removes a project's `.rite/` in the first place.

⚠ **One consequence to accept deliberately:** mail stops being inside the
project, so it stops being captured by anything that copies or archives the
project tree. Nothing does that today. If something later does, mail will need
naming explicitly — the same way credentials already do.
