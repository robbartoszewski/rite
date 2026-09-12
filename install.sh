#!/bin/sh
# rite installer.
#
# Three ways to install, in increasing order of how much you verify first.
# All three end at the same place: rite in its own isolated environment,
# with `rite` on your PATH.
#
#   1. curl -fsSL https://raw.githubusercontent.com/robbartoszewski/rite/v0.1.0/install.sh | sh
#   2. curl -fsSLO https://raw.githubusercontent.com/robbartoszewski/rite/v0.1.0/install.sh
#      shasum -a 256 install.sh     # compare against the release notes
#      sh install.sh
#   3. git clone https://github.com/robbartoszewski/rite.git
#      cd rite && git checkout v0.1.0 && less install.sh && uv tool install .
#
# Note the version in those URLs. This script is pinned to a release tag
# rather than `main`, so it does not change under you as development
# continues. That is drift protection and NOT a cryptographic guarantee: a
# git tag is a movable pointer, and whoever owns the repository can repoint
# one with `git tag -f` and a force-push. If you want a reference that
# cannot move, use the commit SHA published in the release notes. If you are
# reading this from `main`, you are reading the development copy.
#
# Note also what the checksum in the release notes covers: THIS FILE, and
# not the tool. (That block comes from `tools/release_checksums.py` in the
# repository: it hashes this file as committed at the tag, and prints the
# release commit SHA beside it.) Everything below fetches rite itself from the tag, so a
# verified installer still pulls its payload from a movable pointer. The
# README says the same; the two are meant to agree.
#
# What it does: checks for a supported installer, installs rite from the
# git tag into an isolated environment, and tells you what it did. It does
# not modify your shell profile and writes nothing outside that environment
# — except the `rite` and `rite-ai` shims your installer puts on your PATH,
# which is the point of installing it. The only thing it runs afterwards is
# `rite --version`, to report whether the install landed on your PATH and
# whether the `rite` found there is this one.

set -eu

REPO="https://github.com/robbartoszewski/rite.git"
VERSION="${RITE_VERSION:-v0.1.0}"

say()  { printf '%s\n' "$*"; }
fail() { printf 'install.sh: %s\n' "$*" >&2; exit 1; }

# --- what we are installing into -------------------------------------------
# pipx and `uv tool` both create a dedicated venv per tool. That isolation is
# the point, not a nicety: PyPI's `rite` is an unrelated package that also
# ships a `rite` command and owns the `rite` import name, so a non-isolated
# install alongside it merges the two on disk and breaks both.
if command -v uv >/dev/null 2>&1; then
    INSTALLER="uv"
elif command -v pipx >/dev/null 2>&1; then
    INSTALLER="pipx"
else
    fail "needs either 'uv' or 'pipx', and found neither.
  uv:   https://docs.astral.sh/uv/getting-started/installation/
  pipx: https://pipx.pypa.io/stable/installation/
Both install rite into its own environment, which is what keeps it from
colliding with the unrelated 'rite' package on PyPI."
fi

command -v git >/dev/null 2>&1 || fail "needs 'git' on PATH to fetch the tagged source."

# --- python version --------------------------------------------------------
# uv fetches its own interpreter if needed. pipx does not: it builds the venv
# with $PIPX_DEFAULT_PYTHON when that is set, and otherwise with whatever
# interpreter pipx itself runs on — NOT necessarily the `python3` on your
# PATH. An earlier version of this check tested `python3` and called it "the
# python3 pipx will use", which is simply not what pipx does.
#
# So: test the interpreter pipx will actually use where we can name it, and
# name the one we are guessing at otherwise.
#
# Be clear about the limit: this is a VERSION check. It cannot tell you that
# an interpreter of the right version has a broken `venv`/`ensurepip` — which
# is a real failure mode (a Homebrew python@3.14 on the author's machine does
# exactly that, and pipx dies creating its shared venv before it ever reaches
# rite). If pipx fails right after this check passes, that is the reason, and
# setting PIPX_DEFAULT_PYTHON to a known-good interpreter is the way past it.
if [ "$INSTALLER" = "pipx" ]; then
    if [ -n "${PIPX_DEFAULT_PYTHON:-}" ]; then
        PIPX_PY="$PIPX_DEFAULT_PYTHON"
        PIPX_PY_SOURCE="PIPX_DEFAULT_PYTHON"
        [ -x "$PIPX_PY" ] || command -v "$PIPX_PY" >/dev/null 2>&1 || \
            fail "PIPX_DEFAULT_PYTHON is set to '${PIPX_PY}', which is not an
executable interpreter. Point it at one, or unset it."
    elif command -v python3 >/dev/null 2>&1; then
        PIPX_PY="$(command -v python3)"
        PIPX_PY_SOURCE="python3 on your PATH (pipx may use a different one; \
set PIPX_DEFAULT_PYTHON to be sure)"
    else
        fail "needs 'python3' on PATH, or PIPX_DEFAULT_PYTHON set, when installing via pipx."
    fi
    "$PIPX_PY" - <<'PY' || fail "${PIPX_PY} (${PIPX_PY_SOURCE}) is not a working
Python 3.11+. Either it is older than 3.11, or it failed to run at all — the
line above this one is its own error, if it printed one."
import sys
sys.exit(0 if sys.version_info >= (3, 11) else 1)
PY
fi

# Without this, a tag that is not there surfaces as uv's or pipx's own raw git
# error, which says nothing about what to do. A 40-char commit SHA is skipped.
# `--exit-code` gives 2 for "no such ref" and other codes for "could not ask";
# `|| fail` collapsed those, so a DNS failure and a missing tag said the same
# untrue thing. `|| rc=$?` not a bare assignment: under `set -eu` a failing
# substitution in an assignment exits here silently.
case "$VERSION" in ????????????????????????????????????????) ;; *)
    ls_remote_rc=0
    ls_remote_err=$(git ls-remote --exit-code "$REPO" "refs/tags/${VERSION}" \
        "refs/heads/${VERSION}" 2>&1 >/dev/null) || ls_remote_rc=$?
    if [ "$ls_remote_rc" -eq 2 ]; then
        fail "'${VERSION}' is not a tag or a branch in ${REPO}.
\`git ls-remote --tags ${REPO}\` lists the ones that are; choose with RITE_VERSION.
If a README or a release note sent you here, that release is not published yet
— nothing below this line would have installed anything."
    elif [ "$ls_remote_rc" -ne 0 ]; then
        fail "Could not check '${VERSION}' in ${REPO} (git exited ${ls_remote_rc}):
  ${ls_remote_err}
The check could not RUN — that is not the same as the version being missing."
    fi ;; esac

say "Installing rite ${VERSION} with ${INSTALLER}, into its own environment."

SPEC_URL="git+${REPO}@${VERSION}"
if [ "$INSTALLER" = "uv" ]; then
    uv tool install "$SPEC_URL"
else
    pipx install "$SPEC_URL"
fi

# --- verify, and say what happened -----------------------------------------
if ! command -v rite >/dev/null 2>&1; then
    say ""
    say "Installed, but 'rite' is not on your PATH yet."
    if [ "$INSTALLER" = "uv" ]; then
        say "Run:  uv tool update-shell     then open a new shell."
    else
        say "Run:  pipx ensurepath          then open a new shell."
    fi
    exit 0
fi

INSTALLED="$(rite --version 2>/dev/null || true)"
case "$INSTALLED" in
    rite*)
        say ""
        say "Done: ${INSTALLED}"
        say "Start with:  rite help        (or: rite init, in a project directory)"
        ;;
    *)
        say ""
        say "Installed, but 'rite' on your PATH is something else:"
        say "  rite --version -> ${INSTALLED:-(no output)}"
        say ""
        say "That is almost certainly the unrelated 'rite' package from PyPI."
        say "This tool also installs as 'rite-ai' — use that instead, it is the"
        say "same program:  rite-ai --version"
        ;;
esac
