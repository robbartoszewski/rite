#!/usr/bin/env bash
# =============================================================================
#  rite — Linux sandboxing spike
# =============================================================================
#
#  WHAT THIS IS FOR
#    rite sandboxes a Manager on macOS with a seatbelt profile. This asks
#    whether the same boundary can be built on Linux, and — just as
#    importantly — whether it can be WALKED OUT OF. The macOS profile shipped
#    with two escapes (a tmux server outside the boundary ran commands for
#    it, and signals crossed it), so this tests the Linux equivalents of both
#    explicitly rather than assuming.
#
#  WHAT IT DOES TO THIS MACHINE
#    * Creates ONE temporary directory (mktemp -d) and removes it on exit.
#    * Writes only inside that directory. Nothing else on disk is created,
#      modified or deleted.
#    * Starts, and then stops, its own `tmux` server on a private socket
#      inside that directory. It does not touch any tmux you are running.
#    * Starts, and then stops, its own `sleep` processes. It signals only
#      those. It never signals anything it did not start.
#    * READS a few well-known paths to see whether they are reachable
#      (e.g. whether $HOME is listable from inside a sandbox). It reads
#      directory NAMES only, never file contents, and never credentials.
#
#  WHAT IT DOES NOT DO
#    * No installing, no package manager, no sudo, no systemctl.
#    * No writes anywhere outside its temp directory.
#    * No network calls, and no calls to any model provider — UNLESS you opt
#      in (see SPIKE_LIVE_AUTH below), which is off by default.
#    * It does not read, print or transmit any credential. Where it needs to
#      know whether one exists it reports only "present" or "absent".
#
#  OPTIONAL, OFF BY DEFAULT
#    SPIKE_LIVE_AUTH=1  also asks `claude` and `goose` to answer one short
#                       prompt, to prove they can authenticate. This makes a
#                       real API call and spends a small amount of quota.
#                       Without it, this script reports only whether the
#                       binaries and a credential source are present.
#
#  HOW TO RUN
#    bash spike.sh              > spike-output.txt 2>&1
#    SPIKE_LIVE_AUTH=1 bash spike.sh > spike-output.txt 2>&1
#
#  Then paste spike-output.txt back. Every check prints what it TRIED, what
#  HAPPENED, and a verdict of PASS, FAIL or N/A with a reason. Nothing is
#  reported as passing without saying what was observed.
# =============================================================================

set -u   # not -e: a failing probe is a RESULT, and the run must continue.
set +m   # no job-control chatter ('Killed  sleep 300') in the pasted output.

SPIKE_VERSION="1"
PASSES=0; FAILS=0; SKIPS=0
WORK=""

cleanup() {
  if [ -n "${WORK:-}" ] && [ -d "$WORK" ]; then
    # Our own tmux server, if we started one.
    if [ -S "$WORK/tmux.sock" ] && command -v tmux >/dev/null 2>&1; then
      tmux -S "$WORK/tmux.sock" kill-server >/dev/null 2>&1
    fi
    # Our own sleeps, by pid file, never by name.
    if [ -f "$WORK/victims" ]; then
      while read -r pid; do
        [ -n "$pid" ] && kill -9 "$pid" >/dev/null 2>&1
      done < "$WORK/victims"
    fi
    case "$WORK" in
      /tmp/*|/var/tmp/*|"${TMPDIR:-/nonexistent}"*) rm -rf "$WORK" ;;
      *) echo "NOT removing unexpected work dir: $WORK" ;;
    esac
  fi
}
trap cleanup EXIT INT TERM

say()  { printf '%s\n' "$*"; }
head1() { printf '\n== %s ==\n' "$*"; }
head2() { printf '\n-- %s --\n' "$*"; }

# Every verdict goes through here, so none can be a bare "ok".
verdict() { # verdict PASS|FAIL|N/A  <what was tried>  <what happened>
  local v="$1"; shift
  local tried="$1"; shift
  local got="$*"
  printf '  [%-4s] %s\n' "$v" "$tried"
  printf '         -> %s\n' "$got"
  case "$v" in
    PASS) PASSES=$((PASSES+1)) ;;
    FAIL) FAILS=$((FAILS+1)) ;;
    *)    SKIPS=$((SKIPS+1)) ;;
  esac
}

# Run a command, capture combined output and status, print nothing.
cap() { OUT=$( "$@" 2>&1 ); RC=$?; return 0; }
cap_sh() { OUT=$( sh -c "$1" 2>&1 ); RC=$?; return 0; }
first_line() { printf '%s' "$1" | head -n 1 | cut -c1-140; }

have() { command -v "$1" >/dev/null 2>&1; }

# ⚠ THE TRAP THIS SCRIPT ALMOST FELL INTO, AND THE REASON IT IS WRITTEN THIS
# WAY. If bwrap cannot create a namespace at all, EVERY command "fails" — and
# a check that reads "this must be refused" then reports PASS while having
# measured nothing. That is the same defect as a sandbox profile whose denial
# passes because the thing never ran. Caught by running this script in a
# container where namespaces are blocked.
#
# So: a non-zero status only counts as a REFUSAL when bwrap itself started.
bwrap_failed_to_start() {
  case "$1" in
    *"Creating new namespace failed"*|*"bwrap: "*"Operation not permitted"*|\
    *"setting up uid map"*|*"No permissions to creating new namespace"*) return 0 ;;
  esac
  return 1
}

# Does bwrap work here at all? Answered once, and every later check reads it.
BWRAP_USABLE="unknown"
bwrap_smoke() {
  if ! have bwrap; then BWRAP_USABLE="no: bwrap is not installed"; return; fi
  local out rc
  out=$(bwrap --ro-bind /usr /usr --ro-bind-try /lib /lib --ro-bind-try /lib64 /lib64 \
        --ro-bind /bin /bin --proc /proc --dev /dev --tmpfs /tmp -- /bin/true 2>&1); rc=$?
  if [ "$rc" -eq 0 ]; then
    BWRAP_USABLE="yes"
  else
    BWRAP_USABLE="no: $(printf '%s' "$out" | head -n 1 | cut -c1-110)"
  fi
}

say "rite Linux sandboxing spike, format v$SPIKE_VERSION"
say "started: $(date -u '+%Y-%m-%dT%H:%M:%SZ') UTC"

WORK=$(mktemp -d 2>/dev/null) || { say "FATAL: could not create a temp dir"; exit 1; }
say "work dir (removed on exit): $WORK"
: > "$WORK/victims"

# -----------------------------------------------------------------------------
head1 "0. The machine"
# -----------------------------------------------------------------------------
say "  uname            : $(uname -srmo 2>/dev/null || uname -a)"
if [ -r /etc/os-release ]; then
  # shellcheck disable=SC1091
  say "  distro           : $(. /etc/os-release 2>/dev/null; printf '%s' "${PRETTY_NAME:-unknown}")"
else
  say "  distro           : /etc/os-release not readable"
fi
say "  kernel release   : $(uname -r 2>/dev/null)"
say "  user             : uid=$(id -u) gid=$(id -g)$([ "$(id -u)" = 0 ] && printf ' (ROOT — note this: several checks below mean something different as root)')"
say "  container hints  : $( { [ -f /.dockerenv ] && printf 'in a docker container; '; } ; grep -qa 'docker\|containerd\|lxc' /proc/1/cgroup 2>/dev/null && printf 'cgroup names a container; '; printf 'no obvious marker' )"

# -----------------------------------------------------------------------------
head1 "1. What confinement mechanisms exist"
# -----------------------------------------------------------------------------

head2 "Landlock (the closest thing to a seatbelt profile)"
if [ -r /sys/kernel/security/lsm ]; then
  LSMS=$(cat /sys/kernel/security/lsm 2>/dev/null)
  case ",$LSMS," in
    *,landlock,*) verdict PASS "read /sys/kernel/security/lsm" "landlock is an active LSM: $LSMS" ;;
    *)            verdict FAIL "read /sys/kernel/security/lsm" "landlock NOT listed. active: $LSMS" ;;
  esac
else
  verdict "N/A" "read /sys/kernel/security/lsm" "not readable (securityfs may not be mounted); cannot tell"
fi
say "  NOTE: the Landlock ABI VERSION cannot be read without calling the"
say "        syscall from a compiled program. This script does not compile"
say "        anything, so ABI level is NOT established here. Kernel release"
say "        above is the best available proxy (Landlock lands in 5.13,"
say "        ABI 2 in 5.19, ABI 3 in 6.2, ABI 4 in 6.7)."

head2 "bubblewrap (bwrap)"
if have bwrap; then
  cap bwrap --version
  verdict PASS "bwrap --version" "$(first_line "$OUT")"
else
  verdict "N/A" "command -v bwrap" "not installed"
fi

head2 "unshare"
if have unshare; then
  cap_sh "unshare --version"
  verdict PASS "unshare --version" "$(first_line "$OUT")"
else
  verdict "N/A" "command -v unshare" "not installed"
fi

head2 "Unprivileged user namespaces (several distros restrict these)"
for knob in /proc/sys/kernel/unprivileged_userns_clone \
            /proc/sys/user/max_user_namespaces \
            /proc/sys/kernel/apparmor_restrict_unprivileged_userns; do
  if [ -r "$knob" ]; then
    say "  $knob = $(cat "$knob" 2>/dev/null)"
  else
    say "  $knob : not present"
  fi
done
if have unshare; then
  cap_sh "unshare --user --map-root-user true"
  if [ "$RC" -eq 0 ]; then
    verdict PASS "unshare --user --map-root-user true" "a user namespace was created (rc=0)"
  else
    verdict FAIL "unshare --user --map-root-user true" "rc=$RC: $(first_line "$OUT")"
  fi
else
  verdict "N/A" "unshare --user ..." "unshare not installed"
fi

head2 "Container backend (what rite's Worker sandbox would use)"
if have yoloai; then
  cap_sh "yoloai system backends --json"
  if [ "$RC" -eq 0 ]; then
    say "  yoloai system backends --json:"
    printf '%s\n' "$OUT" | sed 's/^/    /' | head -n 40
    verdict PASS "yoloai system backends --json" "rc=0 (see the listing above for which are available)"
  else
    verdict FAIL "yoloai system backends --json" "rc=$RC: $(first_line "$OUT")"
  fi
else
  verdict "N/A" "command -v yoloai" "not installed"
fi
if have docker; then
  cap_sh "docker version --format '{{.Server.Version}}'"
  if [ "$RC" -eq 0 ]; then
    verdict PASS "docker version (server)" "daemon reachable, server $(first_line "$OUT")"
  else
    verdict FAIL "docker version (server)" "client present, daemon NOT reachable: $(first_line "$OUT")"
  fi
else
  verdict "N/A" "command -v docker" "not installed"
fi

head2 "Engines"
for eng in claude goose rite git tmux; do
  if have "$eng"; then
    cap_sh "$eng --version"
    say "  $eng: $(command -v "$eng") | $(first_line "$OUT")"
  else
    say "  $eng: NOT INSTALLED"
  fi
done
# Credential PRESENCE only. Never the value, never the contents.
say "  CLAUDE_CODE_OAUTH_TOKEN in environment : $([ -n "${CLAUDE_CODE_OAUTH_TOKEN:-}" ] && echo present || echo absent)"
say "  ~/.claude/.credentials.json            : $([ -f "$HOME/.claude/.credentials.json" ] && echo present || echo absent)"
say "  ~/.config/goose/config.yaml            : $([ -f "$HOME/.config/goose/config.yaml" ] && echo present || echo absent)"
say "  GITHUB_TOKEN in environment            : $([ -n "${GITHUB_TOKEN:-}" ] && echo present || echo absent)"
say "  ~/.config/gh                           : $([ -d "$HOME/.config/gh" ] && echo present || echo absent)"

if [ "${SPIKE_LIVE_AUTH:-0}" = "1" ]; then
  head2 "Live authentication (SPIKE_LIVE_AUTH=1 — this spends a little quota)"
  if have claude; then
    cap_sh "printf 'Reply with the single word READY and nothing else.' | claude -p --permission-prompts none"
    if [ "$RC" -eq 0 ]; then
      verdict PASS "claude -p, one short prompt" "rc=0, replied: $(first_line "$OUT")"
    else
      verdict FAIL "claude -p, one short prompt" "rc=$RC: $(first_line "$OUT")"
    fi
  else
    verdict "N/A" "claude -p" "claude not installed"
  fi
  if have goose; then
    printf 'Reply with the single word READY and nothing else. Do not use any tools.\n' > "$WORK/p.txt"
    cap_sh "goose run -n ritespike -i '$WORK/p.txt'"
    if [ "$RC" -eq 0 ]; then
      verdict PASS "goose run, one short prompt" "rc=0, last line: $(printf '%s' "$OUT" | tail -n 1 | cut -c1-90)"
    else
      verdict FAIL "goose run, one short prompt" "rc=$RC: $(first_line "$OUT")"
    fi
  else
    verdict "N/A" "goose run" "goose not installed"
  fi
else
  head2 "Live authentication"
  verdict "N/A" "a real prompt to claude/goose" "skipped: set SPIKE_LIVE_AUTH=1 to include it (it spends quota)"
fi

# -----------------------------------------------------------------------------
head1 "2. Can the boundary actually be built?"
# -----------------------------------------------------------------------------
# The macOS profile: project tree read+write; system paths read-only; another
# project denied; $HOME outside named paths denied; rite/git/engine still work.

PROJ="$WORK/project"; OTHER="$WORK/otherproject"
mkdir -p "$PROJ" "$OTHER"
echo "project content" > "$PROJ/file.txt"
echo "another project's secret" > "$OTHER/secret.txt"

bwrap_smoke
head2 "bubblewrap: can it start here at all?"
if [ "$BWRAP_USABLE" = "yes" ]; then
  verdict PASS "bwrap ... -- /bin/true" "a sandbox started (rc=0)"
else
  verdict "N/A" "bwrap ... -- /bin/true" "bwrap cannot start here: ${BWRAP_USABLE#no: }"
  say "  ⚠ EVERY bwrap check below is therefore N/A rather than PASS. A"
  say "    'must fail' test would otherwise pass for the wrong reason —"
  say "    the command failing because no sandbox was created, not because"
  say "    the boundary refused it."
fi

if [ "$BWRAP_USABLE" = "yes" ]; then
  head2 "bubblewrap: the macOS profile's properties, one at a time"
  # Read-only system, project read-write, nothing else bound. /proc and a
  # fresh /tmp so programs can start at all.
  BW="bwrap --ro-bind /usr /usr --ro-bind /bin /bin --ro-bind /sbin /sbin \
      --ro-bind-try /lib /lib --ro-bind-try /lib64 /lib64 --ro-bind-try /etc /etc \
      --proc /proc --dev /dev --tmpfs /tmp \
      --bind $PROJ $PROJ --unshare-pid --die-with-parent"

  cap_sh "$BW -- /bin/sh -c 'cat $PROJ/file.txt'"
  [ "$RC" -eq 0 ] && verdict PASS "MUST WORK: read a project file, confined" "$(first_line "$OUT")" \
                  || verdict FAIL "MUST WORK: read a project file, confined" "rc=$RC: $(first_line "$OUT")"

  cap_sh "$BW -- /bin/sh -c 'touch $PROJ/written.txt && echo wrote'"
  [ "$RC" -eq 0 ] && verdict PASS "MUST WORK: write a project file, confined" "$(first_line "$OUT")" \
                  || verdict FAIL "MUST WORK: write a project file, confined" "rc=$RC: $(first_line "$OUT")"

  must_fail() { # must_fail <label> <command>
    cap_sh "$BW -- /bin/sh -c '$2'"
    if [ "$RC" -eq 0 ]; then
      verdict FAIL "MUST FAIL: $1" "IT SUCCEEDED — the boundary did not hold: $(first_line "$OUT")"
    elif bwrap_failed_to_start "$OUT"; then
      verdict "N/A" "MUST FAIL: $1" "bwrap did not start, so nothing was measured: $(first_line "$OUT")"
    else
      verdict PASS "MUST FAIL: $1" "refused INSIDE the sandbox, rc=$RC: $(first_line "$OUT")"
    fi
  }
  say "  NOTE: under bwrap a refusal usually reads 'No such file or"
  say "        directory', not 'Permission denied' — an unbound path simply"
  say "        does not exist inside the sandbox. Both are refusals."
  must_fail "read ANOTHER project" "cat $OTHER/secret.txt"
  must_fail "list \$HOME" "ls $HOME"

  for tool in git rite; do
    if have "$tool"; then
      cap_sh "$BW -- /bin/sh -c '$tool --version'"
      [ "$RC" -eq 0 ] && verdict PASS "MUST WORK: run $tool confined" "$(first_line "$OUT")" \
                      || verdict FAIL "MUST WORK: run $tool confined" "rc=$RC: $(first_line "$OUT") (it may need paths this minimal bind omits — that is the finding)"
    else
      verdict "N/A" "run $tool confined" "$tool not installed"
    fi
  done
else
  verdict "N/A" "build a bwrap boundary" "bwrap unusable here (${BWRAP_USABLE#no: }) — section 2 cannot run"
fi

# -----------------------------------------------------------------------------
head1 "3. THE ESCAPES — the two that macOS shipped with"
# -----------------------------------------------------------------------------
say "  On macOS the profile refused these directly and allowed them through"
say "  a tmux server that lived outside the boundary. Both are tested here"
say "  against whatever confinement exists, because finding them after"
say "  shipping cost this project a false 'verified' claim."

head2 "Escape 1: drive a tmux server that lives OUTSIDE the boundary"
if ! have tmux; then
  verdict "N/A" "tmux escape" "tmux not installed"
elif [ "$BWRAP_USABLE" != "yes" ]; then
  verdict "N/A" "tmux escape" "no usable boundary to escape FROM (${BWRAP_USABLE#no: }); a result here would mean nothing"
else
  SOCK="$WORK/tmux.sock"
  tmux -S "$SOCK" new-session -d -s spikehost "sleep 300" >/dev/null 2>&1
  if [ ! -S "$SOCK" ]; then
    verdict "N/A" "start a private tmux server" "the socket was not created; cannot test the escape"
  else
    TARGET="$OTHER/escaped-via-tmux.txt"
    rm -f "$TARGET"
    # The socket is deliberately bound INTO the sandbox, which is the
    # macOS situation: the profile granted the directory the socket lived in.
    cap_sh "bwrap --ro-bind /usr /usr --ro-bind /bin /bin --ro-bind /sbin /sbin \
        --ro-bind-try /lib /lib --ro-bind-try /lib64 /lib64 --ro-bind-try /etc /etc \
        --proc /proc --dev /dev --tmpfs /tmp --bind $PROJ $PROJ \
        --bind $SOCK $SOCK --die-with-parent -- \
        /bin/sh -c 'tmux -S $SOCK new-session -d \"touch $TARGET\"'"
    sleep 2
    if [ -f "$TARGET" ]; then
      verdict FAIL "confined process asks the outside tmux server to write a file it may not write" \
        "THE FILE WAS CREATED at $TARGET — the escape works on Linux too"
    elif bwrap_failed_to_start "$OUT"; then
      verdict "N/A" "confined process asks the outside tmux server to write a file it may not write" \
        "bwrap did not start, so nothing was measured: $(first_line "$OUT")"
    else
      verdict PASS "confined process asks the outside tmux server to write a file it may not write" \
        "no file appeared (tmux rc=$RC: $(first_line "$OUT"))"
    fi
    # And the control: is it the socket being reachable that matters?
    cap_sh "bwrap --ro-bind /usr /usr --ro-bind /bin /bin --ro-bind /sbin /sbin \
        --ro-bind-try /lib /lib --ro-bind-try /lib64 /lib64 --ro-bind-try /etc /etc \
        --proc /proc --dev /dev --tmpfs /tmp --bind $PROJ $PROJ --die-with-parent -- \
        /bin/sh -c 'tmux -S $SOCK list-sessions'"
    if [ "$RC" -eq 0 ]; then
      verdict FAIL "CONTROL: same thing with the socket NOT bound in" "it still reached the server: $(first_line "$OUT")"
    elif bwrap_failed_to_start "$OUT"; then
      verdict "N/A" "CONTROL: same thing with the socket NOT bound in" "bwrap did not start: $(first_line "$OUT")"
    else
      verdict PASS "CONTROL: same thing with the socket NOT bound in" "refused, rc=$RC: $(first_line "$OUT")"
    fi
    tmux -S "$SOCK" kill-server >/dev/null 2>&1
  fi
fi

head2 "Escape 2: signal a process the sandbox did not start"
if [ "$BWRAP_USABLE" != "yes" ]; then
  verdict "N/A" "signal escape" "no usable boundary to escape FROM (${BWRAP_USABLE#no: }); a result here would mean nothing"
else
  sleep 300 & VICTIM=$!
  disown "$VICTIM" 2>/dev/null || true   # no "Killed  sleep 300" in the output
  echo "$VICTIM" >> "$WORK/victims"
  sleep 0.5
  # --unshare-pid gives the sandbox its own pid namespace, which is the
  # Linux equivalent of macOS's `(allow signal (target same-sandbox))`.
  cap_sh "bwrap --ro-bind /usr /usr --ro-bind /bin /bin --ro-bind /sbin /sbin \
      --ro-bind-try /lib /lib --ro-bind-try /lib64 /lib64 --ro-bind-try /etc /etc \
      --proc /proc --dev /dev --tmpfs /tmp --bind $PROJ $PROJ \
      --unshare-pid --die-with-parent -- /bin/sh -c 'kill $VICTIM'"
  sleep 0.5
  if bwrap_failed_to_start "$OUT"; then
    verdict "N/A" "confined process signals a pid outside it (with --unshare-pid)" \
      "bwrap did not start, so nothing was measured: $(first_line "$OUT")"
  elif kill -0 "$VICTIM" 2>/dev/null; then
    verdict PASS "confined process signals a pid outside it (with --unshare-pid)" \
      "the process SURVIVED (kill rc=$RC: $(first_line "$OUT"))"
  else
    verdict FAIL "confined process signals a pid outside it (with --unshare-pid)" \
      "THE PROCESS WAS KILLED — signals cross this boundary"
  fi
  kill -9 "$VICTIM" >/dev/null 2>&1

  # And without a pid namespace, which is what a naive profile would do.
  sleep 300 & VICTIM2=$!
  disown "$VICTIM2" 2>/dev/null || true
  echo "$VICTIM2" >> "$WORK/victims"
  sleep 0.5
  cap_sh "bwrap --ro-bind /usr /usr --ro-bind /bin /bin --ro-bind /sbin /sbin \
      --ro-bind-try /lib /lib --ro-bind-try /lib64 /lib64 --ro-bind-try /etc /etc \
      --proc /proc --dev /dev --tmpfs /tmp --bind $PROJ $PROJ \
      --die-with-parent -- /bin/sh -c 'kill $VICTIM2'"
  sleep 0.5
  if bwrap_failed_to_start "$OUT"; then
    verdict "N/A" "CONTROL: the same, WITHOUT --unshare-pid" "bwrap did not start: $(first_line "$OUT")"
  elif kill -0 "$VICTIM2" 2>/dev/null; then
    verdict PASS "CONTROL: the same, WITHOUT --unshare-pid" "the process survived anyway"
  else
    verdict FAIL "CONTROL: the same, WITHOUT --unshare-pid" "killed — so --unshare-pid is what stops it, and a profile must include it"
  fi
  kill -9 "$VICTIM2" >/dev/null 2>&1
fi

# -----------------------------------------------------------------------------
head1 "Summary"
# -----------------------------------------------------------------------------
say "  PASS: $PASSES   FAIL: $FAILS   N/A: $SKIPS"
say ""
say "  A FAIL is a result, not an error in this script: it means the thing"
say "  was tried and did not hold. An N/A means it could not be tried here,"
say "  and says why. Nothing above is reported as passing without printing"
say "  what was observed."
say ""
say "  NOT established by this script, and deliberately:"
say "    * The Landlock ABI version (needs a compiled probe; kernel release"
say "      above is the proxy)."
say "    * Whether yoloAI's Linux backends actually CONFINE — only whether"
say "      they are reported available."
say "    * Anything about a Manager's real workload: no engine was run"
say "      inside a sandbox here, only the boundary's properties."
say ""
say "finished: $(date -u '+%Y-%m-%dT%H:%M:%SZ') UTC"
