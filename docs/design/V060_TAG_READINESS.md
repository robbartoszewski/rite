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

**⚠ UPDATED 2026-09-26: Linux now has observations** (D1, D2, D4, D7, D8, D9, D16–D18, on a Parallels Ubuntu ARM64 VM at `6ea56f8`, by the Slack and check-ins session). The paragraph below is the state before them. **Linux had no observation of rite working.** The Linux evidence that exists
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
| D1 | **A Manager runs at all, inside a boundary** | ✅ obs. B9 (`4ebbbd7`); tmux and signal escapes closed (`9862b59`); re-measured at `afa41e9` | ✅ **obs 2026-09-26, Parallels Ubuntu 24.04 ARM64 VM, `6ea56f8`, Goose on `qwen3:1.7b` (CPU)**: `sandbox: Manager 'small' runs inside a landlock boundary`; the session ran and the model answered in the pane; `ceiling reached: 1 session(s) started`. ⚠ **Only after two directories were created by hand; see D16.** ⚠ One cycle only: a project with no board gets one setup session, so a multi-cycle run waits on the board (Robert's GitHub login on the VM) | a multi-cycle run on a board; D16 fixed and observed on a FRESH machine |
| D2 | **The tmux socket escape is closed** | ✅ obs. Socket directory denied; every Unix socket except the resolver denied (C6/C26 landing) | ❌ **obs, open:** from inside the real Landlock ruleset, `tmux new-session -d 'touch ~/escaped_tmux'` through a server started outside **created the file**. Direct writes outside the project were refused, another project's file was unreadable, an outside process survived `kill -TERM`, and HTTPS answered 200. 🧪 Closable by starting tmux inside the boundary (`18ffd5d`) | land "tmux inside the boundary", then **observe** the escape refused on Linux. Also re-observe it on macOS if that session changes the macOS launch |
| D3 | **A Claude Manager, sandboxed** | ❌ **`claude -p` prints `Not logged in` inside the sandbox** (`96d340e`). K2/K3's real-model runs were done OUTSIDE it. The keychain is unreadable inside | ⏳ **obs 2026-09-26 at `1ba8ea5`, VM, Claude Code 2.1.283 installed:** with no token stored, `rite start lead` REFUSES before launching: `refusing to start Manager 'lead': a Claude Manager runs inside a sandbox, which cannot read your keychain login, so it needs a token of its own. Run claude setup-token, then rite credential set claude_token …`. The refusal is correct; its wording names a keychain, which Linux does not use. **A signed-in cycle needs Robert's token on the VM**: `claude setup-token` needs his account, and only he pastes it | **Robert's decision Q1** (how Claude's credential reaches the pane), then observe on both |
| D4 | **A local (Goose) Manager** | ✅ obs. Declared model, endpoint checks, cycles in the sandbox, `rite reply`, broker Worker; continuation across runs (C29 fixed) | ✅ **obs 2026-09-26, Parallels Ubuntu 24.04 ARM64 VM, `6ea56f8`, Goose on `qwen3:1.7b` (CPU)**, with D16's workaround. The model served a **32768-token** window at **49 tok/s** decode (12.7 s cold load, 100% CPU, 5.3 GB resident); `rite doctor`: `local engine small: answering, 1 model(s)`. ⚠ At 1.7B it did not run `rite reply` when asked: tool use is weak, and throughput is not the problem | Robert: is a 1.7B model 'functional' enough, or measure a 4B/8B on the same CPU |
| D5 | **Two Managers in one root (Claude Owner plus local secondary)** | ✅ obs with stub engines inside the real profiles (MM-1…5). The first concurrent run had a real Goose Owner (`c1cc840`, which found the put-back defect). **Not yet with a real Claude Owner** | — | D3, then a real Claude Owner with a Goose secondary, on each platform |
| D6 | **Separation between the two** (§5.4.8) | ✅ obs: processes separated (P2). ❌ state is not (P1, P3, P4) | — | the building session's call. The notes must say which properties hold |
| D7 | **Driven through Slack**: DM instructions, broadcast as context, threads, posts, redaction, delivery while stopped | ✅ obs live, including a second person (A3–A6). Only the Owner opens a relay (MM-1, `24bc75c`) | ✅ **obs 2026-09-26, Parallels Ubuntu 24.04 ARM64 VM, `6ea56f8`, Goose on `qwen3:1.7b` (CPU)**. **Posting:** `slack: posted … → D0C4ALD46TF ts 1790391694.066499`, fetched back from Slack as `*small*: Linux relay test (automated): posted from the Parallels VM`. The reply was written from the VM terminal with `rite reply`, not by the model. **Reading:** Robert's real DM (cursor rewound, not retyped) arrived as `1 message(s) sent in the Owner's DM while 'small' was not running`, then `delivering 1 message(s)`. `prompt.txt` shows the header `[Owner's DM · sent Sat 01:18 · rite normalised … · addressed · INSTRUCTION]`, the tag text decoded and `<!-- … -->` unescaped. The token went via stdin into the environment, and rite said so on stderr as designed. One transient `URLError … Temporary failure in name resolution`, reported once, and the relay carried on | threads and a second person on Linux (not repeated; the code is platform-independent) |
| D8 | **Check-ins** (K1–K6) | ✅ obs: K1, K4, K6; K2/K3 real-model (outside the sandbox, because of D3); K5 posting live (`68f3060`) | ✅ **obs 2026-09-26, Parallels Ubuntu 24.04 ARM64 VM, `6ea56f8`, Goose on `qwen3:1.7b` (CPU)**: `slack: posted … → D0C4ALD46TF ts 1790391375.727519`, then `slack: mirrored the check-in → C0C4KB709T6 ts 1790391376.206019`; the mirror reads `*small* (check-in, mirrored from the Owner's DM; replies here are read as context): Check-in — small …` | K2/K3 with a real model on Linux |
| D9 | **Ticket and Slack text cleaned; phrases reported** (N1, N2) | ✅ obs, both clauses. **Slack keeps hidden characters, and rite removes them** (`9d09507`), so the cleaning stands on rite's code, not on Slack's client. Slack's HTML entities are unescaped (`ed707f0`) | ✅ **Slack clause obs on Linux** (D7: the same message, normalised and unescaped). ⏳ The board clause waits on the VM's GitHub login | platform-independent code; one Linux board read would observe it |
| D10 | **GitHub credentials** (C6/C26, token only) | 🧪 built, full suite green (4448 passed on macOS). **Not observed against GitHub**: needs the App | ⚠ the seatbelt profile lines (credential directory read-only, Unix sockets denied) have **no Landlock equivalent written**, and Landlock cannot deny pathname-socket `connect(2)` | Robert's Q2 (the App); the Landlock backend must grant the credential directory; observe on both |
| D11 | **The Manager runs the `rite` that started it** (C27) | ✅ obs: pipx, checkout, uv tool | 🧪 the backend branch hit `PermissionError: …/VERSION` and fixed it; not observed with a Manager | covered by D1's observation |
| D12 | **CI green** | local suite green | ❌ `main` fails 4 of 4482 on Linux: the Manager-start tests (C28) | lands with D1 |
| D13 | **Credentials stored and found headless** (the Slack bot token, board tokens, the App key) | ✅ obs: the keychain, from a logged-in session | ❌ **reported by the Linux session:** Ubuntu's keyring works for a logged-in desktop user and **fails headless and inside a cron tick**, where rite falls back to environment variables **silently**. Not re-measured here | Robert's Q6 |
| D14 | **Unattended ticks: cron, and check-in windows under it** | ✅ obs (launchd/cron install writes the running rite's absolute path, `f777972`) | ✅ **reported by the Linux session:** cron works and check-in windows survive it. Not re-checked here | — |
| D15 | **tmux session names** | ✅ tmux 3.7c keeps names | ✅ **reported:** Ubuntu's tmux 3.4 rewrites names like Debian's 3.3a, so C12's exact-name fix (`0136447`) carries every Ubuntu user. The Mac is the outlier | — |
| D16 | **A Goose Manager starts on a FRESH machine** | ✅ seatbelt grants a path that does not exist yet | ✅ **FIXED at `1ba8ea5`.** Verified by the boundary session in a container with an empty HOME. ⚠ **THE VM ALONE CANNOT SHOW THIS:** the directories were first created there BY HAND (2026-09-26 05:00), so from then on the VM no longer reproduced the bug. The retest therefore moved them aside (`~/.local/share/goose`, `~/.config/goose` → `*.aside`). On `1ba8ea5`, rite then CREATED both itself at launch (06:03) and granted `~/.config/goose` READ-ONLY and `~/.local/share/goose` writable, and Goose ran to `status 0`. **Watched for the predicted second failure, a Goose config WRITE refused:** it did not occur. `~/.config/goose` stayed empty, and Goose's logs hold no `os error 13`, because `goose run` with the model and mode in its environment writes no config. **Untriggered, not disproved**: a Goose action that saves config would still hit it | the `landlock.py` owner: create the engine's directories before building the ruleset, then observe on a fresh account |
| D17 | **A Manager can create new files anywhere in its project** | ✅ the project is granted as a tree | ⚠ **obs, by design:** Landlock has no deny rule, so MM-2's inbox fence ENUMERATES the project root instead of granting it as a tree. From inside the real ruleset, `touch <project>/probe_in` at the ROOT was refused. The code says existing subdirectories stay fully writable, and the enumeration is a snapshot per launch. A Manager cannot add a new top-level file or directory during a cycle | release notes must say it; the design note's alternative is moving inboxes out of the project |
| D18 | **Workers are boundaried** | ✅ by yoloAI's container, when `sandbox.enabled` | ⚠ **Workers are UNSANDBOXED by default on Linux.** `rite init` turns Worker sandboxing OFF there (`questionnaire.py:199`: "`flock` is a no-op inside a docker sandbox"), confirmed in the VM project (`sandbox: enabled: false`). **No Worker path uses Landlock**: Landlock is the MANAGER's boundary only. Not observed on the VM: yoloAI is not installed there, by decision | the release notes must say Workers are unsandboxed on Linux by default, not "containerised on both" |

## 2. Robert's decisions, ordered by what they block

| # | decision | blocks | options (from the design notes) |
|---|---|---|---|
| Q1 | **How does Claude's credential reach a sandboxed Manager?** The environment is ruled out (C6) | **D3, D5, and therefore Wednesday on both platforms** | (i) a per-Manager `CLAUDE_CONFIG_DIR` with a 0600 `.credentials.json` holding a `claude setup-token` token: one year, whole subscription, readable inside; it also fixes SB4. Needs his token to measure. (ii) Claude Managers unsandboxed, which reverses B9. |
| Q2 | **Create the GitHub App** and send its ids | D10's observation | as listed in `V060_MANAGER_CREDENTIALS.md` |
| Q4 | **A Linux machine that can run a local model** — ⚠ **measured 2026-09-26:** the no-GPU VM serves `qwen3:1.7b` on CPU at 49 tok/s with a 32768 window, and a Manager ran on it (D4). The open part is whether 1.7B is capable enough | D4 on Linux | keep 1.7B; measure `qwen3:4b`/`8b` on the same CPU; or a GPU machine |
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
