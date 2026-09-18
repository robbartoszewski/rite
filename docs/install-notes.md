# Install notes

Why the [README](../README.md) offers three install commands rather than one,
what each verifies, and two things worth knowing before you run any of them.

*If you just want to try it, use option 1 and skip to the Quickstart. What
follows is for readers who would rightly want to know what a `curl | sh`
costs them — which, for a tool that scans your repo for secrets, is a fair
thing to want.*

**Why three, and what each one actually buys you.** Piping a remote script
into a shell runs code you have not read, from a URL whose contents can change
between your reading it and your running it. The three options differ in how
much you verify first:

- **Option 1** pins the URL to a release tag rather than `main`, which stops
  the ordinary case: the script changing under you as development continues.
  It is **not** a cryptographic guarantee. A git tag is a movable pointer —
  `git tag -f` plus a force-push repoints one — so this protects against
  drift, not against me. It has one more cost worth knowing, and it belongs
  to the pipe rather than to this project: **`curl … | sh` reports the
  shell's exit status, not curl's.** Measured against a URL that 404s, the
  whole line prints `curl: (56) …` and then **exits 0** — curl fetched
  nothing, `sh` read an empty script and succeeded. On a terminal you see
  the error; in a Dockerfile or a CI step you get a green install that
  installed nothing. Options 2 and 3 do not have this, because the download
  and the run are separate commands.
- **Option 2** lets you check that the URL served you what was released:
  compare the digest against the release notes before running it. Note what
  that digest covers — **`install.sh`, and not rite.** The installer then
  fetches the tool itself from the same movable tag, so a verified installer
  still pulls an unverified payload.
- **Option 3** is the only one where you read the whole thing first. It is
  169 lines of `sh` and it is the honest answer if you would flag
  `curl | sh` in someone else's project. Its `git checkout v0.4.0` is the
  same movable pointer as option 1, so if the distinction matters to you,
  check out the **commit SHA** published in the release notes instead — that
  cannot be repointed.

Worth saying plainly, since this is a tool whose whole job is stopping secrets
reaching a remote: **none of these three protects you from this project's
maintainer.** They are a ladder of how much you check before running, not of
whom you trust. The commit SHA is the one rung that pins what you got; the
release notes publish it for exactly that reason.

The installer puts rite in its own isolated environment and touches nothing
else — no shell profile, and nothing outside that environment except the
`rite` and `rite-ai` shims your installer puts on your PATH, which is the
point of installing it. The only thing it runs afterwards is `--version`: on
the `rite` it just installed, to report that version, and on the `rite` your
PATH finds, if that is a different one, to tell you which comes first.

**No telemetry, no analytics, nothing phones home.** That is the narrow claim
and it is checkable — grep the source. What rite *does* reach out to, so the
list is a list and not a gesture: the ticket board you configure; URLs you
hand it (`rite kb add`, a config preset given to `rite init`); `git
fetch`/`git clone` against your own module remotes during `rite prepare`;
`gh api` when provisioning a sandbox token; and PyPI when *you* run `rite
update`. The installer itself obviously fetches over the network — that is
what installing is.

**If `rite --version` prints anything other than `rite, version …`**, an
unrelated PyPI package of the same name is ahead of it on your PATH. This tool installs as `rite-ai`
too — same program, use that. (Which is also why the isolated environment
matters, and why `pip install` into a shared one is the one thing not to do:
the two projects share a command *and* an import name, and merge on disk.)

## The three ways, in full

**Not on PyPI yet** — that comes once there's been some feedback. Install from
the tagged source. Needs `uv` or `pipx`, `git`, and Python 3.11+.

Three ways, same result. Pick by how much you want to read first:

```bash
# 1. one-liner
curl -fsSL https://raw.githubusercontent.com/robbartoszewski/rite/v0.4.0/install.sh | sh

# 2. download, check, then run
curl -fsSLO https://raw.githubusercontent.com/robbartoszewski/rite/v0.4.0/install.sh
shasum -a 256 install.sh          # compare against the v0.4.0 release notes
sh install.sh

# 3. clone and read everything
git clone https://github.com/robbartoszewski/rite.git
cd rite && git checkout v0.4.0
less install.sh                   # 169 lines of sh
uv tool install .                 # or: pipx install .
```

*Option 1 is the fast path. If you would rather read a `curl | sh` before
running it — fair, for a tool that scans your repo for secrets — that is what
2 and 3 are for. Nothing phones home; the reasoning, and the PyPI name
collision, are in [`docs/install-notes.md`](docs/install-notes.md).*
