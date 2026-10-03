# SB5 — what `(allow mach-lookup)` with no filter was buying

**Measured 2026-09-30** on macOS 26 (Darwin 25.2), with the Manager profile
`enclosure.compose()` produces from `main` at `458c944`. **Fixtures only**: a
throwaway keychain item and throwaway files, all removed at the end; no real
credential was read or placed anywhere.

**Every run carried its controls.** In the profile: a read of an ungranted
file under `$HOME` was refused, and `/etc/hosts` was readable, so the profile
was in force rather than silently failing to load. Outside it: the fixture
read succeeded, so the fixture existed.

**What the SB5 row asked.** *"Not measured: whether a Manager can read other
projects' rite credentials from [the keychain]. The Worker-profile
measurement does not carry over, because this profile differs."* That
question is answered below, and the answer is no. The rule was buying
something else.

---

## 1. The keychain: NOT reachable, and this rule is not what stops it

A dummy generic-password item, readable outside the boundary, was **not
found** inside it:

    security find-generic-password -s rite-sb5-probe -w
      outside                          the fixture value, exit 0
      inside the Manager profile       SecKeychainSearchCopyNext: The
                                       specified item could not be found

That much repeats measurement 7 of `V060_MANAGER_CREDENTIALS.md` against
today's profile, which has changed since (SB4 removed the `~/.claude` grant,
the socket denials and `github_access` lines were added). **What is new is
that it survives three attempts to attribute it:**

| profile | keychain item |
|---|---|
| as rite composes it | not found |
| plus `(allow file-read* (subpath "~/Library/Keychains"))` | not found |
| plus read **and write** on the same directory | not found |
| plus every unix socket allowed back (both denials removed) | not found |

So the denial is **not** the file grant, **not** the socket deny, and **not**
anything this profile is holding up. It is refused below rite, by something
this measurement did not identify. `security list-keychains` inside still
names `login.keychain-db`, so the search list is visible and the contents are
not — metadata, not content, the same shape as the Worker-profile result.

⚠ **And the row's premise has moved anyway.** rite's own credentials have not
been in the keychain since C6/C26: the store is one 0600 file
(`credentials/file_store.py`), the OS keychain is read only by `rite
credential import-keychain`, and `~/.rite` and `~/.config/rite` are denied by
name in this profile. There is no longer a route from "the keychain is
reachable" to "another project's rite credential is readable".

**SB5's stated worry closes negative.** What follows is what was found while
checking it.

## 2. The clipboard: reachable, and the rule is exactly what made it so

    pbpaste
      inside the profile as rite composed it    the operator's clipboard, exit 0
      with `(allow mach-lookup)` removed        nothing, exit 1

A clipboard is where a token or a password sits for the seconds between
copying it and pasting it. Nothing in the boundary was stopping a Manager
from reading it on every cycle, and `limitations()` did not say so — the same
class as the two claims `7b5462d` and the tmux entry had to correct.

⚠ **This was found by running the profile, not by reading it.** Reading it,
`(allow mach-lookup)` looks like plumbing for system daemons, which is how it
was justified when it was written.

## 3. `open` via LaunchServices: refused

Worth recording because it is the shape of the tmux escape — ask a process
outside the boundary to do the thing the boundary refuses. A minimal
`.app` bundle whose executable writes a marker outside every grant, launched
from inside the profile:

    open -g <bundle>    _LSOpenURLsWithCompletionHandler() failed with error -54
                        the marker was never written

So LaunchServices is not a route out of this profile today. A negative, and
only about the route that was tried.

## 4. What the narrowing costs: one name

Every command a Manager runs, under the profile as composed and under the
same profile with `(allow mach-lookup)` removed — **identical exit status in
every case except `pbpaste`**:

| command | as composed | no mach-lookup |
|---|---|---|
| `git status`, `git log` | 128 | 128 |
| `rite --version`, `rite status` | 0, 1 | 0, 1 |
| `curl` over https | 0 (`200`) | 0 (`200`) |
| `gh --version`, `gh api` | 1 | 1 |
| `claude --version` | 0 | 0 |
| `goose --version` | 0 | 0 |
| `python3`, `node` | 0 | 0 |
| **`pbpaste`** | **0** | **1** |

(The `git` 128 and the `gh` 1 are unrelated and present either way: this
checkout is a worktree whose `.git` metadata lives outside the grant, and
`gh` has no `GH_CONFIG_DIR` in a bare probe.)

**One difference in stderr,** and it is what the single allowed name is for:
without any `mach-lookup`, `confstr(_CS_DARWIN_USER_TEMP_DIR)` fails and
`git` and `python3` print `confstr() failed with code 5 … using /tmp
instead` on **every** invocation. Allowing back
`com.apple.bsd.dirhelper`, and nothing else, removes that warning and leaves
`pbpaste` refused:

| profile | `confstr` | `pbpaste` |
|---|---|---|
| unfiltered `(allow mach-lookup)` | works | exit 0 |
| no `mach-lookup` at all | warns on every command | exit 1 |
| `dirhelper` only | works | exit 1 |

So the shipped rule is the class refused by `(deny default)` with that one
name allowed back, and `enclosure._mach_services()` holds the name and its
reason.

⚠ **Denied as a class, not by name.** Denying `com.apple.pasteboard.1` and
leaving the class open was the other available shape, and it is the shape
`_socket_denials` had to abandon **twice**: a deny aimed at the one path a
problem was noticed at leaves the problem everywhere else.

## 5. What is NOT measured

- **A real `claude -p` turn under the narrowed profile.** The engine is node,
  and node may look up a name none of the commands above did. It would fail
  closed and loudly (`Operation not permitted`), not quietly, which is why
  this ships denied rather than left open — but "no Manager cycle has run
  under it" is the honest state.
- **Which mach services a Manager reaches that are neither of these.** There
  is no working enumerator here. `(trace …)` produced no file, `(with
  report)` is rejected on a `deny` action (*"report modifier does not apply
  to deny action"*), and violations from a `sandbox-exec` profile do not
  reach the unified log — the `com.apple.sandbox.reporting` entries visible
  during these runs all belonged to Apple's own sandboxed daemons, and
  reading them as rite's would have been wrong. The table in section 4 is
  therefore **behavioural**: it says what broke, not what was asked for.
- **Linux.** `mach-lookup` is macOS-only; the Landlock policy has no
  counterpart and is untouched.
- **Why the keychain is refused.** Section 1 rules out three explanations and
  offers none.
