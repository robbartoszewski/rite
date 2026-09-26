# How a sandboxed Manager gets GitHub credentials — C6/C26, v0.6.0

**Status: DESIGN + MEASUREMENTS, 2026-09-26. The token half is BUILT (`f3926a1`) and not yet observed; see the banner below.** Robert put C6/C26
back into v0.6.0: a Manager that reads a private board anonymously and cannot
`git push` over HTTPS is not useful enough to ship. This note confirms or
refutes the proposal it was given, and states the path every credential takes
and what an attacker inside a compromised Manager can do with it.

⚠ **DECIDED AND BUILT, 2026-09-26: the token only, no SSH grant** (Robert,
final). Built in `f3926a1`, tested (full suite green on macOS), and **not
yet observed against GitHub**, because that needs the App (decision 1
below). What was built is "The design" below, minus every SSH clause:
- a GitHub App installation token, one repository, one hour, minted by
  `rite start` outside the sandbox;
- the token in a 0600 `hosts.yml` named by `GH_CONFIG_DIR`;
- `git push` over HTTPS through `gh auth git-credential`;
- exact-value redaction in the journal and the Slack relay;
- **every Manager's profile denies all Unix sockets except the resolver**
  (measurements 1–3).

The SSH measurements (4) and the SSH sections are kept as the record of why
SSH was dropped. The token serves `git push` as well as `gh`, so SSH added no
capability, and an agent key is the broader grant.

Two credentials this note did not cover are added at the end: **Jira**, and
**Claude's own login**. The second is the blocker for a sandboxed Claude
Manager.

**Citation convention:** a bare `§` is a section of `SPEC.md`.

**Every result below was measured on this machine against `main` at `47b14a0`,
with the profile `enclosure.compose()` produces**, unless it says "read" (from
documentation) or "inferred". No real credential was used in any probe:
- SSH probes used throwaway keys in throwaway agents;
- the `gh` probes used a deliberately invalid token;
- the keychain probe used a dummy item, deleted afterwards.

---

## 🔴 The constraint, stated first

**A credential must not reach the pane through argv or the environment**, and
must not appear in rite's journal. C6 was marked closed because the argv trap
was shut, while how a credential reaches the pane stayed open. This design
names the path of each credential it grants.

## Verdict on the proposal

| proposal | verdict | why |
|---|---|---|
| `git push`: grant the SSH agent socket only | **Refuted as stated, confirmed in a narrower form.** Granting *the operator's* agent is wider than it sounds. A **per-Manager agent** holding one **destination-restricted deploy key** is the scoped version, and it works | 1, 2, 3, 4 below |
| `gh`: a repo-scoped, short-lived token minted outside the sandbox | **Confirmed, and it also covers `git push`.** The only mintable, short-lived, repo-scoped GitHub token is a **GitHub App installation token** | 5, 6, 7 |
| A credential broker is v0.7.0 | **Agreed.** Recorded as the end state below | — |

**Recommendation for v0.6.0: the installation token for BOTH `gh` and `git
push`, and no SSH grant.** One credential, one refresh path, one blast radius.
SSH stays available later as the per-Manager agent below, for projects whose
remotes are SSH-only.

---

## Measurements

### 1. The shipped profile ALREADY reaches the operator's SSH agent

From inside the shipped Manager profile, `ssh-add -l` against the real
launchd agent answered `The agent has no identities.` It connected. The key
*file* was refused (`cat ~/.ssh/id_ed25519: Operation not permitted`).

**Nobody granted this on purpose.** It is harmless on this machine only
because the agent holds no keys. On a machine where `ssh-add` or
`AddKeysToAgent` has loaded one, every sandboxed Manager can sign with it
today, for any host that trusts it. `limitations()` does not say so.

### 2. On macOS, file rules do not govern `connect(2)` to a Unix socket

A throwaway agent's socket in a directory the profile does **not** grant was
reachable from the shipped profile. Adding or removing `file-read*` and
`file-write*` rules on that path changed nothing. **Only `network-outbound`
decides**, and `(allow network*)` allows every Unix socket the user can
reach: SSH agents, 1Password or gpg agents, and other tools' control sockets.
The tmux fix (`9862b59`) works only because it is an explicit
`deny network-outbound`, placed last.

### 3. A socket-by-socket grant works

Appended to the shipped profile:

    (deny network-outbound (remote unix-socket))
    (allow network-outbound (remote unix-socket (path-literal "<agent socket>")))
    (allow network-outbound (remote unix-socket (path-literal "/private/var/run/mDNSResponder")))

| probe | result |
|---|---|
| `ssh-add -l` via the granted agent | lists its key |
| the operator's real agent | `Error connecting to agent: Operation not permitted` |
| another Unix socket | `PermissionError: Operation not permitted` |
| HTTPS by name | 200 |
| the same, without the resolver line | curl exit 6: **DNS goes through that socket** |
| `tmux ls` | refused |

⚠ **The path must be the resolved one.** `/var/folders/…` did not match, and
`/private/var/folders/…` did. rite already resolves paths before composing
the profile; the socket path must go through the same step.

### 4. Signing without the key, and a restriction the agent enforces itself

- **Inside the sandbox, with the key file refused, the agent signed a
  payload, and the signature verified** against the throwaway public key.
  Key material never entered the sandbox.
- **The full SSH path to GitHub works from inside, up to authorisation.**
  The host key verified against a known-hosts file taken from
  `api.github.com/meta` at a granted path. The default `~/.ssh/known_hosts`
  is unreadable inside, and without that file `ssh` fails with
  `Host key verification failed`. The agent offered the key, and GitHub
  answered `Permission denied (publickey)`, because the throwaway key is not
  registered.
- **A destination-restricted key (`ssh-add -h github.com`, OpenSSH 10.0p2 on
  this machine):**
  - it was offered to github.com;
  - it was **not offered** to gitlab.com;
  - a **raw signature request was refused** (`agent refused operation`),
    while an unrestricted key in the same agent signed.

  So even a hostile client talking to the agent directly cannot use a
  restricted key for another host or for arbitrary data.

**Not observed:** a real push over SSH. No key on this machine is registered
with GitHub (`Permission denied (publickey)`). It needs a deploy key added
to a repository. That is a GitHub settings change, so it is Robert's.

### 5. The file route for `gh`, and what each failure looks like

The token reaches `gh` through a **file**:
- `hosts.yml` sits in a per-Manager `GH_CONFIG_DIR`, mode `0600`, written by
  the supervisor outside the sandbox;
- the variable carries a **path**, not a secret;
- nothing goes on argv, and nothing credential-bearing goes into the
  environment.

| case | what the Manager sees |
|---|---|
| no `hosts.yml` in its `GH_CONFIG_DIR` | refused, exit 4, "populate GH_TOKEN…". **Loud, not anonymous** |
| `hosts.yml` naming a keyring user (today's `~/.config/gh` shape) | the same refusal, exit 4 |
| token in `hosts.yml` (invalid on purpose) | `gh: Bad credentials (HTTP 401)`: the file token was sent |
| `git ls-remote` over HTTPS, with `gh auth git-credential` as helper | `Authentication failed`: `git` uses the same token |
| `env` inside the sandbox | 0 matches for the token |

⚠ C26 recorded `gh` going **anonymous** (60 requests) with the operator's
real `~/.config/gh`. With a config of the same shape it refused here. That
may be version- or keyring-specific. It does not matter to this design,
which gives each Manager its own `GH_CONFIG_DIR` and removes the need to
grant `~/.config/gh`.

⚠ **`git` over HTTPS gets nothing from the keychain inside the sandbox
today.** Xcode's system gitconfig sets `credential.helper osxkeychain`, and a
github.com credential exists in the keychain. From inside, `git ls-remote` of
the private board failed with `could not read Username`. So C26's "no HTTPS
push" holds, and the keychain credential is not a leak.

### 6. Minting: only a GitHub App gives a short-lived, repo-scoped token

**Read** from GitHub's documentation:
- an installation token *"expire[s] one hour from the time you create
  them"*;
- the request can name `repositories` and `permissions`;
- the request itself must be authenticated with a JWT signed by the App's
  private key.

Fine-grained personal tokens are documented as created in the web UI only,
with an expiry of 1–366 days or none. **No create API was found**, which is
absence in the documentation, not a proven negative. So the proposal's
"minted" and "short-lived" together mean **a GitHub App**. A fine-grained PAT
is repo-scoped, but it is neither minted nor short.

**Measured:** the system `openssl` (3.6.4) produces the RS256 signature a
JWT needs. rite has no crypto dependency (`cryptography` is not installed),
so minting needs either the `openssl` CLI or a new dependency.

### 7. The App's private key stays out of the sandbox

A dummy keychain item was visible outside and **not found** inside
(`SecKeychainSearchCopyNext: The specified item could not be found`).
`security list-keychains` inside names `login.keychain-db`, but the file is
unreadable from there. So a private key kept in rite's credential store (the
keychain) cannot be read by a Manager. That is what keeps an attacker from
minting their own tokens.

### 8. Redaction: structural rules miss the token; exact values do not

`redact_assignments` caught `GH_TOKEN=<t>`. It **passed** `oauth_token: <t>`,
which is exactly what `cat hosts.yml` prints, and it passed a bare token and
`Authorization: token <t>`. Given the value, through its existing `secrets=`
parameter, it redacted every one. rite mints the token, so it knows the
exact value and needs no pattern.

⚠ **Neither the journal nor the Slack relay passes `secrets=` today.** A token
split across lines or words defeats exact matching. That limit is real, and
it is stated rather than pattern-matched around.

---

## The design

### The path each credential takes

| credential | where it lives | how it reaches the Manager | never on |
|---|---|---|---|
| **GitHub App private key** | rite's credential store (keychain), per project | **it does not.** Only the supervisor reads it, outside the boundary | the Manager's files, env, argv |
| **Installation token** (≤ 1 h, one repository, named permissions) | minted by the supervisor | written to `.rite/managers/<name>/gh/hosts.yml`, `0600`. The pane's environment carries only `GH_CONFIG_DIR=<that dir>`, a path | argv, env, the journal (exact-value redaction), Slack (the same) |
| **`git push` over HTTPS** | the same token | `gh auth git-credential` as git's helper, set per repository by the supervisor in the project's git config, or by `GIT_CONFIG_*` variables naming the helper, never the token | argv, env |

**Profile changes that go with it:**
- deny every Unix socket, then allow back the resolver (measured necessary)
  and nothing else;
- drop the `~/.config/gh` grant (`b9a5f76`), which the per-Manager
  `GH_CONFIG_DIR` makes unnecessary;
- grant the Manager's `gh/` directory **read-only**. The supervisor writes it
  from outside.

### Refresh, and what an expired token looks like

- **Mint at every cycle start**, and again from the supervisor's poll loop
  when less than 10 minutes remain. `gh` reads `hosts.yml` on each
  invocation, so a replacement written atomically is picked up by the next
  command with nothing restarted. That is inferred from `gh` reading its
  config at start, and it is the first thing the build must observe.
- **Minting fails at start:** `rite start` refuses, naming GitHub's error,
  the same rule as an unreachable board (D-74). An unreachable credential is
  not an absent one.
- **Minting fails mid-run:** the old token runs out, and the Manager sees
  `gh: Bad credentials (HTTP 401)` and `Authentication failed` (the shapes
  measured in 5). The supervisor says it in the pane, in stderr and in the
  next check-in: *"GitHub token for <repo> expired at HH:MM and could not be
  refreshed: <GitHub's words>"*. **It never deletes the file to "go
  anonymous".** An absent token is a loud refusal (exit 4), and that is the
  failure C26 recorded, which this design exists to remove.

### What an attacker inside a compromised Manager can do

This is the honest version.

**With the installation token** (one repository; contents write, issues,
pull requests, metadata read):
- **read and change that repository's code, including force-pushing** to any
  branch without protection, and delete branches and tags;
- create, edit and close its issues and pull requests, and create releases;
- **copy the token out.** The network is not confined (0.7.0's egress work).
  They can use it from anywhere **until it expires, at most one hour**, and
  are cut off within the hour of the Manager stopping, because the
  supervisor stops refreshing;
- **cannot:**
  - reach any other repository;
  - mint a new token (the App's private key is unreadable, 7);
  - change workflow files, unless the App is given the `workflows`
    permission. Recommendation: do not give it;
  - read the repository's Actions secrets, unless given that permission.

⚠ **Branch protection on the default branch is the operator's mitigation for
the force-push case**, and the setup docs should say so. rite cannot enforce
it.

**What is still exposed, unchanged by this design:**
- **Claude's own credential.** Inside the sandbox the keychain is
  unreadable (7), so a Claude Manager that authenticates most plausibly got
  `CLAUDE_CODE_OAUTH_TOKEN` through the **environment**, inherited from the
  tmux server. That is C6's open half for Claude, not re-measured here. This
  design does not solve it.
- **Other projects' Claude transcripts** (`~/.claude` readable whole, SB4).
- **The operator's SSH agent** (1). This design closes it.

**For comparison, the proposal as stated** (grant the operator's agent
socket):
- every key in that agent, for every host that trusts it (other GitHub
  accounts, production servers);
- **raw signatures over arbitrary data**, which an unrestricted key gives
  (4). That includes signing git commits as the operator.
- for as long as the Manager runs. The key cannot be copied out, but it
  does not need to be.

That is wider than the token, which is why the recommendation above differs
from the proposal.

### If SSH is wanted as well (not recommended for v0.6.0)

1. The supervisor starts **one `ssh-agent` per Manager**, on a socket under
   `.rite/managers/<name>/`, and loads **a deploy key for one repository**,
   `ssh-add -h github.com` with GitHub's host keys from `api.github.com/meta`.
2. The profile allows exactly that socket path (resolved).
3. `GIT_SSH_COMMAND` points `ssh` at rite's known-hosts file with
   `StrictHostKeyChecking=yes`.

**Blast radius:** push to that one repository while the Manager runs. The
key never enters the sandbox, and the agent refuses it for other hosts and
for raw signatures (4). **Cost:** a second credential with a long life, and
a second setup step (a deploy key per repository).

## The v0.7.0 end state: a credential broker

The Worker broker (B9) is the precedent. The Manager **asks**, and something
outside the boundary decides. v0.6.0 writes a scoped token into a file the
Manager can read. A broker would perform the GitHub operation itself, or
hand out per-request tokens, so the Manager never holds a credential at all.
That removes the copy-it-out case and is recorded as the intended end state.
It is not proposed for v0.6.0.

## Jira: a second board credential, with no narrower scope to mint

**Jira is a real, supported tracker, not only text the sanitizer scans.**
`src/rite_ai/tickets/jira.py` is a full board backend (create, move, list,
query, show) over Jira Cloud's REST API v3. It has been in since v0.1.0 and
is selected by `ticket_backend.type: jira` (`tickets/__init__.py`). It
authenticates with `jira_email` plus `jira_token` (Basic auth) from rite's
credential store.

- **Inside the sandbox that credential is almost certainly unreachable
  today.** The keychain is unreadable there (measurement 7), so a sandboxed
  Manager's `rite board` on a Jira project most likely fails. Inferred, not
  measured.
- **Its scoping story is worse than GitHub's, and there is no fix for it
  inside rite.** An Atlassian API token is account-scoped: whatever the
  account can reach, across every project. There is no per-project,
  fine-grained equivalent to mint. OAuth 3LO gives granular scopes, and
  still not per-project. The only real lever is a **dedicated service
  account limited to one project**, which is a setup step for the user, not
  something rite can mint.
- **So the honest v0.6.0 position is one of these, and it is Robert's:**
  1. refuse a sandboxed Manager on a Jira project, loudly;
  2. require a service account, delivered by the same file route as the
     GitHub token, with the blast radius stated as "whatever that service
     account can do";
  3. the v0.7.0 broker, where the board call happens outside the sandbox.

## Claude's own login: a sandboxed Claude Manager cannot authenticate

**Recorded in `96d340e`:** inside the Manager profile, `claude -p` prints
`Not logged in`, with a fresh keychain login outside it. That is consistent
with measurement 7: the keychain is unreadable inside the sandbox. The only
route that works today is `CLAUDE_CODE_OAUTH_TOKEN` in the environment,
which is C6's hole and is ruled out.

**Read from Claude Code's authentication documentation:**
- credentials live in the macOS keychain, falling back to
  `~/.claude/.credentials.json` (mode 0600), which is the only store on
  Linux;
- `CLAUDE_CONFIG_DIR` moves that directory and the file with it;
- the only non-interactive subscription credential is `claude setup-token`:
  one year, model requests only;
- `apiKeyHelper` returns an API key, meaning Console billing, not the
  subscription.

**The option that meets the constraint**: a per-Manager `CLAUDE_CONFIG_DIR`,
outside every other grant, holding a 0600 `.credentials.json` with a
`setup-token` token. The environment then carries a path. It would also
end SB4: the profile would stop granting the whole of `~/.claude`.
**Its cost, stated:** the token lives a year, covers the whole
subscription, and is readable inside the sandbox. A compromised Manager
can copy it out and spend the subscription until it is revoked.
**Unmeasured:** whether macOS Claude reads that file inside the sandbox
rather than insisting on the keychain. Measuring it needs a real
`setup-token` token, which is Robert's to create. The alternative is running
Claude Managers unsandboxed, which reverses B9.

## Decisions for Robert

| # | decision | blocks |
|---|---|---|
| 1 | **Create a GitHub App** (owner: your account), install it on the target repository or repositories, and store its private key with `rite credential set`. Permissions proposed: contents write, issues write, pull requests write, metadata read; **not** workflows or secrets | everything in this design |
| 2 | ✅ DECIDED: token only (Robert, final) | — |
| 3 | ✅ SETTLED as an implementation choice: the system `openssl`, with the key passed through a pipe (no new dependency) | — |
| 5 | **Claude's credential in a sandboxed Manager** (above) | every sandboxed Claude Manager, on both platforms |
| 6 | **Jira for a sandboxed Manager** (above) | Jira projects |
| 4 | Branch protection on the default branch, recommended in the setup docs | the docs |

## First build steps, each observed

1. **The profile:** deny Unix sockets except the resolver. Observe a Claude
   and a Goose Manager each complete a cycle. This also closes the
   operator's-agent grant (1), and it stands on its own if the rest slips.
2. **Mint, write and refresh.** Observe a real sandboxed Manager:
   - reading the private board;
   - pushing a throwaway branch over HTTPS;
   - then, with the token forced to expire, getting the 401 and the
     supervisor's line.
3. **Pass the live token values as `secrets=`** to the journal and the Slack
   relay. Observe a Manager printing `hosts.yml`, and the journal and Slack
   carrying `[redacted]`.
