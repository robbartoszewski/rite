# v0.6.0 — tag readiness

**Rebuilt 2026-09-26 for Robert's definition of done.** Written against `main`
at `f3926a1` (the C6/C26 token path included). Rows marked
"reported" come from the Linux session's findings as relayed on 2026-09-26,
and were not re-measured by the author of this list.

**The target is Wednesday:** a functional **Claude and local** multi-Manager
setup, **driven through Slack**, **working on macOS and Linux**. Linux is a
requirement, not a deferral. A slip of 1–2 days is acceptable, and shipping
something subpar to hit the date is not.

**Re-check every line against `main` on the day.** This project has shipped
functions that were correct and uncalled.

## ⚠ The headline: every feature observation is macOS

Every row below that says "observed" was observed on **macOS**: the Slack
relay, check-ins, multi-Manager, the credential work, text cleaning, the
local engine and the sandbox.

**Linux has no observation of rite working.** The Linux evidence that exists
is:
- the unit suite in CI, which on `main` fails the four tests that start a
  real Manager;
- the Landlock boundary's own probes (spike B9b, and CI on `ubuntu-latest`
  on the backend branch).

**No Manager has completed a cycle on Linux.** A Manager cannot start there
on `main` at all (C28).

Legend:
- **✅ obs** means observed by a person running rite;
- **🧪 test** means tests or probes only;
- **❌** means not working;
- **—** means nothing.

## 1. Against the definition of done

| # | part of "done" | macOS | Linux | what closes the Linux column |
|---|---|---|---|---|
| D1 | **A Manager runs at all, inside a boundary** | ✅ obs. B9 (`4ebbbd7`); tmux and signal escapes closed (`9862b59`); re-measured at `afa41e9` | ❌ on `main`: the launch is `sandbox-exec`, which Linux lacks (C28). 🧪 A Landlock backend is on branch `c28/sandbox-backends`, and its probes pass in CI (ARM64 and x86_64) | land the backend, then **observe a Manager cycle on Linux** |
| D2 | **The tmux socket escape is closed** | ✅ obs. Socket directory denied; every Unix socket except the resolver denied (C6/C26 landing) | ❌ open under Landlock, which does not govern `connect(2)` (B9b). 🧪 Closable by starting the tmux server inside the boundary, measured in a container (`18ffd5d`) | land "tmux inside the boundary", then **observe** the escape refused on Linux. Also re-observe it on macOS if that session changes the macOS launch |
| D3 | **A Claude Manager, sandboxed** | ❌ **`claude -p` prints `Not logged in` inside the sandbox** (`96d340e`). K2/K3's real-model runs were done OUTSIDE it. The keychain is unreadable inside | — unobserved. Linux keeps Claude's login in `~/.claude/.credentials.json`, which the backend's grants may reach. Unmeasured | **Robert's decision Q1** (how Claude's credential reaches the pane), then observe on both |
| D4 | **A local (Goose) Manager** | ✅ obs. Declared model, endpoint checks, cycles in the sandbox, `rite reply`, broker Worker; continuation across runs (C29 fixed) | — **unmeasured. The VM has no GPU** | Robert's Q4: a Linux machine that can serve a model, then observe a cycle |
| D5 | **Two Managers in one root (Claude Owner plus local secondary)** | ✅ obs with stub engines inside the real profiles (MM-1…5). The first concurrent run had a real Goose Owner (`c1cc840`, which found the put-back defect). **Not yet with a real Claude Owner** | — | D3, then a real Claude Owner with a Goose secondary, on each platform |
| D6 | **Separation between the two** (§5.4.8) | ✅ obs: processes separated (P2). ❌ state is not (P1, P3, P4) | — | the building session's call. The notes must say which properties hold |
| D7 | **Driven through Slack**: DM instructions, broadcast as context, threads, posts, redaction, delivery while stopped | ✅ obs live, including a second person (A3–A6). Only the Owner opens a relay (MM-1, `24bc75c`) | — unobserved. The relay is Python over HTTPS, so probably portable, and unproven | one live Slack round trip from a Linux-hosted Owner |
| D8 | **Check-ins** (K1–K6) | ✅ obs: K1, K4, K6; K2/K3 real-model (outside the sandbox, because of D3); K5 posting live (`68f3060`) | — | an observed check-in from a Linux-hosted Manager |
| D9 | **Ticket and Slack text cleaned; phrases reported** (N1, N2) | ✅ obs, both clauses. **Slack keeps hidden characters, and rite removes them** (`9d09507`), so the cleaning stands on rite's code, not on Slack's client. Slack's HTML entities are unescaped (`ed707f0`) | — | platform-independent code; one Linux board read would observe it |
| D10 | **GitHub credentials** (C6/C26, token only) | 🧪 built, full suite green (4448 passed on macOS). **Not observed against GitHub**: needs the App | ⚠ the seatbelt profile lines (credential directory read-only, Unix sockets denied) have **no Landlock equivalent written**, and Landlock cannot deny pathname-socket `connect(2)` | Robert's Q2 (the App); the Landlock backend must grant the credential directory; observe on both |
| D11 | **The Manager runs the `rite` that started it** (C27) | ✅ obs: pipx, checkout, uv tool | 🧪 the backend branch hit `PermissionError: …/VERSION` and fixed it; not observed with a Manager | covered by D1's observation |
| D12 | **CI green** | local suite green | ❌ `main` fails 4 of 4482 on Linux: the Manager-start tests (C28) | lands with D1 |
| D13 | **Credentials stored and found headless** (the Slack bot token, board tokens, the App key) | ✅ obs: the keychain, from a logged-in session | ❌ **reported by the Linux session:** Ubuntu's keyring works for a logged-in desktop user and **fails headless and inside a cron tick**, where rite falls back to environment variables **silently**. Not re-measured here | Robert's Q6 |
| D14 | **Unattended ticks: cron, and check-in windows under it** | ✅ obs (launchd/cron install writes the running rite's absolute path, `f777972`) | ✅ **reported by the Linux session:** cron works and check-in windows survive it. Not re-checked here | — |
| D15 | **tmux session names** | ✅ tmux 3.7c keeps names | ✅ **reported:** Ubuntu's tmux 3.4 rewrites names like Debian's 3.3a, so C12's exact-name fix (`0136447`) carries every Ubuntu user. The Mac is the outlier | — |

## 2. Robert's decisions, ordered by what they block

| # | decision | blocks | options (from the design notes) |
|---|---|---|---|
| Q1 | **How does Claude's credential reach a sandboxed Manager?** The environment is ruled out (C6) | **D3, D5, and therefore Wednesday on both platforms** | (i) a per-Manager `CLAUDE_CONFIG_DIR` with a 0600 `.credentials.json` holding a `claude setup-token` token: one year, whole subscription, readable inside; it also fixes SB4. Needs his token to measure. (ii) Claude Managers unsandboxed, which reverses B9. |
| Q2 | **Create the GitHub App** and send its ids | D10's observation | as listed in `V060_MANAGER_CREDENTIALS.md` |
| Q4 | **A Linux machine that can run a local model** | D4 on Linux | a GPU machine; a small model on CPU (measure whether usable); or ship Linux-local unobserved, which contradicts "functional" |
| Q3 | Jira for a sandboxed Manager | Jira projects only | refuse in 0.6.0; a service account's token by the file route; the v0.7.0 broker |
| Q6 | **Linux credential storage.** The keyring fails headless and in cron, and rite falls back to the environment silently (D13) | **D7 and every board or Slack credential on Linux, under cron or a headless login** | the recommendation already with Robert: a 0600 file store; `doctor` names the store in use; a failure is loud, never a silent fall to the environment. The alternative, keeping the keyring and documenting "log in to a desktop session", does not work under cron |
| Q5 | C25: opt out of the Manager sandbox | setups whose hooks reach outside | config key; `--no-sandbox`; widen per project |
| — | MMQ5, K6, C24, C7 (`--record-issues`), B8 | nothing; each ships as built | — |

## 3. Work that is not a decision

| # | work | platform |
|---|---|---|
| W1 | land the Landlock backend; observe a Linux Manager cycle | Linux |
| W2 | land "tmux server inside the boundary"; observe the escape refused | both |
| W3 | write the Landlock grants for the token's directory | Linux |
| W4 | README: "Linux is implemented" and "Claude is the only agent" are both wrong today | docs |
| W5 | SB4: `~/.claude` readable whole (other projects' transcripts). Fixed by Q1(i), otherwise narrowed by measurement | both |
| W7 | `rite start --record-issues` in a project with **no board** starts a setup session whose prompt omits the journal instructions, while the start notice says recording is on. Found by `f777972`'s author; recorded, not fixed (the journal stays unadvertised) | both |
| W6 | release steps: version bump, `verify-and-push.sh`, history tools, the verification statement naming **both** platforms | release |

## 4. Is Wednesday real?

**Not without Q1.** A sandboxed Claude Manager does not authenticate on
macOS today, and "Claude and local multi-Manager" needs one. Nothing else on
the list substitutes for that answer.

**And not without a Linux machine for D3–D9.** Every Linux cell except D1,
D2 and D10's code is "—". Most of that is platform-independent code with
nobody having run it. It is cheap to observe once D1 lands and there is a
Linux host with a model and Slack credentials. It cannot be claimed before
then.

The Linux items do not separate from the tag any more. Robert made Linux part
of done, so every "—" above is on the tag's critical path.
