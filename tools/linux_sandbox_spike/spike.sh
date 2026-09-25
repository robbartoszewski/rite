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
#    * Writes a small python3 helper into that same temp directory and runs
#      it, to call the three Landlock syscalls through ctypes. This needs no
#      compiler and no privilege. Landlock restricts ONLY the process that
#      asks for it and that process's children, so the helper can confine
#      itself and nothing else: it cannot affect this shell, anything already
#      running, or the machine after it exits. Where python3 is absent, the
#      Landlock checks report N/A instead of guessing.
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

# --- Landlock, measured rather than inferred ---------------------------------
# Landlock's ABI level, and whether a Landlock boundary actually holds, can
# only be learned by CALLING the syscalls — which normally means compiling
# something. It does not have to: python3 can make the calls through ctypes,
# and python3 is in a default install of every distro this appliance is
# likely to run. Where it is absent, every Landlock check says N/A rather
# than guessing from the kernel release.
#
# Landlock restricts the calling process and its children only. It cannot
# reach this shell, cannot grant anything, and needs no privilege.
LL_PY=""
LL_ABI=""          # filled in by the ABI probe in section 1
LL_BOUNDARY=no     # set to yes only if a Landlock boundary is shown to hold
for _c in python3 python; do have "$_c" && { LL_PY="$_c"; break; }; done

write_landlock_probe() {
  cat > "$WORK/llprobe.py" <<'RITE_LANDLOCK_PROBE_EOF'
"""Landlock probe for the rite Linux sandboxing spike.

spike.sh calls this with one subcommand. It uses ctypes so the spike stays
self-contained: no compiler and no packages beyond the python3 that Ubuntu,
Debian, Fedora and RHEL all ship in a default install.

Landlock restricts the CALLING process and its future children only. It
cannot reach the parent, the shell, or anything already running, and it
cannot be used to grant anything. Nothing here writes outside the directory
spike.sh passes in.

Every subcommand prints ONE line starting with OK, NO or ERR, so the shell
side never parses prose. OK means the boundary held; NO means it did not.
"""
import ctypes, ctypes.util, os, signal, socket, sys

# These three syscalls sit in the architecture-neutral range, so the numbers
# are the same on x86_64 and aarch64.
CREATE, ADD_RULE, RESTRICT = 444, 445, 446
VERSION_FLAG = 1           # LANDLOCK_CREATE_RULESET_VERSION
SCOPE_SIGNAL = 1 << 1      # needs ABI 6
SCOPE_UNIX_SOCKET = 1 << 0 # needs ABI 6; used only as the control below
PR_SET_NO_NEW_PRIVS = 38

A_EXECUTE, A_WRITE_FILE, A_READ_FILE, A_READ_DIR = 1 << 0, 1 << 1, 1 << 2, 1 << 3
A_REMOVE_DIR, A_REMOVE_FILE = 1 << 4, 1 << 5
A_MAKE_REG, A_MAKE_DIR, A_MAKE_SOCK = 1 << 8, 1 << 7, 1 << 9

# What the ruleset takes charge of. Anything HANDLED is denied unless a rule
# allows it; anything not handled is untouched by Landlock entirely.
HANDLED = (A_EXECUTE | A_WRITE_FILE | A_READ_FILE | A_READ_DIR
           | A_REMOVE_DIR | A_REMOVE_FILE | A_MAKE_REG | A_MAKE_DIR | A_MAKE_SOCK)
READ_ONLY = A_EXECUTE | A_READ_FILE | A_READ_DIR

# Loaded by soname first. ctypes.util.find_library() shells out to gcc/ld
# and needs a writable temp dir, which a confined process may not have — it
# raises FileNotFoundError inside a boundary that denies $TMPDIR.
def _libc():
    for cand in ("libc.so.6", "libc.so", None):
        try:
            return ctypes.CDLL(cand or ctypes.util.find_library("c"), use_errno=True)
        except Exception:
            continue
    raise OSError("could not load libc")


libc = _libc()
libc.syscall.restype = ctypes.c_long


def syscall(n, *a):
    ctypes.set_errno(0)
    return libc.syscall(ctypes.c_long(n), *a), ctypes.get_errno()


def abi():
    """landlock_create_ruleset(NULL, 0, VERSION) returns the ABI level."""
    r, e = syscall(CREATE, None, ctypes.c_size_t(0), ctypes.c_uint32(VERSION_FLAG))
    if r > 0:
        return r
    raise OSError(e, os.strerror(e))


class RulesetAttr(ctypes.Structure):
    # handled_access_net arrived in ABI 4, scoped in ABI 6. Sending the
    # longer struct to an older kernel is rejected, so the size is trimmed
    # to what this kernel knows.
    _fields_ = [("handled_access_fs", ctypes.c_uint64),
                ("handled_access_net", ctypes.c_uint64),
                ("scoped", ctypes.c_uint64)]


class PathBeneathAttr(ctypes.Structure):
    _pack_ = 1                        # the kernel struct is packed
    _fields_ = [("allowed_access", ctypes.c_uint64),
                ("parent_fd", ctypes.c_int32)]


def make_ruleset(handled_fs=0, scoped=0, level=None):
    level = level or abi()
    attr = RulesetAttr(handled_fs, 0, scoped)
    size = ctypes.sizeof(attr) if level >= 6 else 8
    r, e = syscall(CREATE, ctypes.byref(attr), ctypes.c_size_t(size), ctypes.c_uint32(0))
    if r < 0:
        raise OSError(e, "landlock_create_ruleset: " + os.strerror(e))
    return r


def allow(ruleset_fd, path, access):
    fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
    try:
        pb = PathBeneathAttr(access, fd)
        r, e = syscall(ADD_RULE, ctypes.c_int(ruleset_fd), ctypes.c_uint32(1),
                       ctypes.byref(pb), ctypes.c_uint32(0))
        if r != 0:
            raise OSError(e, "landlock_add_rule(%s): %s" % (path, os.strerror(e)))
    finally:
        os.close(fd)


def enforce(ruleset_fd):
    """Irreversible for this process. Cannot affect anything outside it."""
    if libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
        raise OSError("prctl(NO_NEW_PRIVS) failed")
    r, e = syscall(RESTRICT, ctypes.c_int(ruleset_fd), ctypes.c_uint32(0))
    os.close(ruleset_fd)
    if r != 0:
        raise OSError(e, "landlock_restrict_self: " + os.strerror(e))


def confine_to(project):
    """The boundary a Manager would get: its project writable, the system
    readable, everything else — including the rest of $HOME — denied."""
    fd = make_ruleset(handled_fs=HANDLED)
    for p in ("/usr", "/bin", "/sbin", "/lib", "/lib64", "/etc", "/proc", "/dev"):
        if os.path.exists(p):
            allow(fd, p, READ_ONLY)
    allow(fd, project, HANDLED)
    enforce(fd)


def _run(fn):
    """Run fn in a child, so the irreversible restriction dies with it."""
    pid = os.fork()
    if pid == 0:
        try:
            os._exit(fn())
        except Exception:
            os._exit(9)
    return os.WEXITSTATUS(os.waitpid(pid, 0)[1])


def cmd_abi():
    print("OK abi=%d" % abi())


def cmd_fsbound(project, outside):
    """Can the boundary be built at all? Four checks, two of which MUST fail."""
    # Written BEFORE the boundary goes up, so check 1 tests reading rather
    # than the caller having happened to create this name.
    probe_file = os.path.join(project, "landlock-probe-read.txt")
    with open(probe_file, "w") as fh:
        fh.write("readable from inside the boundary")

    def child():
        confine_to(project)
        results = []
        try:                                        # 1. read inside: allowed
            open(probe_file).read(); results.append(1)
        except Exception: results.append(0)
        try:                                        # 2. write inside: allowed
            open(os.path.join(project, "w.txt"), "w").write("x"); results.append(1)
        except Exception: results.append(0)
        try:                                        # 3. read outside: MUST fail
            open(os.path.join(outside, "secret.txt")).read(); results.append(0)
        except Exception: results.append(1)
        try:                                        # 4. list $HOME: MUST fail
            os.listdir(os.path.expanduser("~")); results.append(0)
        except Exception: results.append(1)
        return sum(results)
    score = _run(child)
    if score == 4:
        print("OK boundary_holds 4/4 (read+write inside allowed, outside and $HOME denied)")
    else:
        print("NO boundary_incomplete %d/4" % score)


def cmd_signal(pid_scoped, pid_control):
    """Escape 2, with the control that makes the result mean something."""
    level = abi()
    if level < 6:
        print("NO scoping_unavailable abi=%d (LANDLOCK_SCOPE_SIGNAL needs 6)" % level)
        return

    def attempt(target, scoped):
        def child():
            try:
                enforce(make_ruleset(scoped=scoped, level=level))
                os.kill(int(target), signal.SIGTERM)
                return 0                    # the signal went through
            except PermissionError:
                return 3                    # the boundary refused it
        return _run(child)

    scoped_rc = attempt(pid_scoped, SCOPE_SIGNAL)
    # The control scopes something OTHER than signals. It cannot be an empty
    # ruleset: the kernel rejects one that handles and scopes nothing, and a
    # control that fails to build would look like a boundary that worked.
    control_rc = attempt(pid_control, SCOPE_UNIX_SOCKET)
    if control_rc != 0:
        print("ERR control_did_not_signal rc=%d — a process restricted WITHOUT "
              "signal scoping still failed to signal, so the test cannot "
              "discriminate and the scoped result means nothing" % control_rc)
    elif scoped_rc == 3:
        print("OK refused (control killed its target; LANDLOCK_SCOPE_SIGNAL did not)")
    elif scoped_rc == 0:
        print("NO signal_delivered (scoping did not stop it)")
    else:
        print("ERR scoped_child_exit=%d" % scoped_rc)


def cmd_socket(project, sockpath):
    """Escape 1's Linux shape: macOS closed a socket escape with a filesystem
    deny. Landlock's filesystem rules do not govern connect(), so the same
    fix may not exist here. Denying the directory and then connecting to a
    socket inside it answers that directly."""
    def child():
        confine_to(project)
        try:
            os.listdir(os.path.dirname(sockpath))
            return 5                          # the directory was not denied
        except Exception:
            pass                              # denied, as intended
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            s.connect(sockpath)
            return 0                          # connected THROUGH the deny
        except Exception:
            return 3                          # refused
        finally:
            s.close()
    rc = _run(child)
    if rc == 5:
        print("ERR directory_not_denied — nothing was measured")
    elif rc == 3:
        print("OK refused (connect() into a denied directory was blocked)")
    elif rc == 0:
        print("NO connected (filesystem rules do NOT govern connect(); a socket "
              "in a denied directory is still reachable)")
    else:
        print("ERR child_exit=%d" % rc)


def cmd_listen(sockpath, touchfile):
    """The far side of the socket escape: a process OUTSIDE the boundary that
    does something when a confined process reaches it. Run in the background
    by spike.sh, so that the escape is shown to have an effect rather than
    merely a successful connect()."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(sockpath)
    s.listen(1)
    print("OK listening")
    sys.stdout.flush()
    conn, _ = s.accept()
    with open(touchfile, "w") as fh:
        fh.write("a confined process reached a helper outside the boundary")
    conn.close()


if __name__ == "__main__":
    try:
        {"abi": cmd_abi, "fsbound": cmd_fsbound, "listen": cmd_listen,
         "signal": cmd_signal, "socket": cmd_socket}[sys.argv[1]](*sys.argv[2:])
    except Exception as exc:
        print("ERR %s: %s" % (type(exc).__name__, exc))
RITE_LANDLOCK_PROBE_EOF
}

# One line in, one line out. The probe prints OK / NO / ERR so nothing here
# has to parse prose.
ll() { OUT=$( "$LL_PY" "$WORK/llprobe.py" "$@" 2>&1 ); RC=$?; return 0; }
ll_answer() { printf '%s' "$1" | head -n 1; }

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
[ -n "$LL_PY" ] && write_landlock_probe

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
if [ -n "$LL_PY" ]; then
  ll abi
  case "$OUT" in
    OK\ abi=*)
      LL_ABI="${OUT#OK abi=}"
      verdict PASS "landlock_create_ruleset(NULL, 0, VERSION) via $LL_PY ctypes" \
        "ABI $LL_ABI. (Signal and abstract-socket scoping need ABI 6; network rules need 4.)"
      ;;
    *)
      LL_ABI=""
      verdict "N/A" "landlock_create_ruleset(NULL, 0, VERSION) via $LL_PY ctypes" \
        "could not read the ABI: $(first_line "$OUT")"
      ;;
  esac
else
  LL_ABI=""
  verdict "N/A" "landlock ABI version" \
    "no python3 here, and reading it needs the syscall called. Kernel release above is the only proxy (Landlock lands in 5.13, ABI 2 in 5.19, ABI 3 in 6.2, ABI 4 in 6.7)."
fi

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

head2 "Landlock: can a boundary be built without any privilege?"
# Asked before bubblewrap because it needs no namespace, no privilege and no
# setting loosened — so on a host that restricts unprivileged user namespaces
# it may be the ONLY mechanism left.
if [ -z "$LL_PY" ]; then
  verdict "N/A" "landlock boundary" "no python3 to call the syscalls with"
elif [ -z "$LL_ABI" ]; then
  verdict "N/A" "landlock boundary" "the ABI probe above did not answer, so nothing is built on it"
else
  ll fsbound "$PROJ" "$OTHER"
  case "$OUT" in
    OK\ boundary_holds*)
      LL_BOUNDARY=yes
      verdict PASS "landlock: project read+write, another project and \$HOME denied" \
        "$(ll_answer "$OUT")"
      ;;
    NO\ *)
      LL_BOUNDARY=no
      verdict FAIL "landlock: project read+write, another project and \$HOME denied" \
        "$(ll_answer "$OUT")"
      ;;
    *)
      LL_BOUNDARY=no
      verdict "N/A" "landlock boundary" "probe error: $(first_line "$OUT")"
      ;;
  esac
fi

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

head2 "Escape 1 under Landlock: reach a helper through a unix socket"
# The macOS fix for escape 1 was a filesystem deny on the socket's path.
# Landlock's filesystem rules govern opening files, not connect(2), so the
# same fix may simply not exist here. Denying the directory and then
# connecting to a socket inside it answers that directly, and a helper that
# ACTS on the connection shows whether the escape has an effect or only a
# successful syscall.
if [ "${LL_BOUNDARY:-no}" != "yes" ]; then
  verdict "N/A" "landlock socket escape" "no Landlock boundary was established above; a result here would mean nothing"
else
  LLSOCKDIR="$OTHER/sockets"; mkdir -p "$LLSOCKDIR"
  LLSOCK="$LLSOCKDIR/helper.sock"; LLTOUCH="$LLSOCKDIR/TOUCHED"
  rm -f "$LLSOCK" "$LLTOUCH"
  "$LL_PY" "$WORK/llprobe.py" listen "$LLSOCK" "$LLTOUCH" >/dev/null 2>&1 &
  LLLISTENER=$!
  echo "$LLLISTENER" >> "$WORK/victims"
  sleep 1
  if [ ! -S "$LLSOCK" ]; then
    verdict "N/A" "landlock socket escape" "the helper socket was not created; cannot test"
  else
    ll socket "$PROJ" "$LLSOCK"
    sleep 1
    case "$OUT" in
      OK\ refused*)
        verdict PASS "confined process connects to a socket in a DENIED directory" \
          "$(ll_answer "$OUT")"
        ;;
      NO\ connected*)
        if [ -f "$LLTOUCH" ]; then
          verdict FAIL "confined process connects to a socket in a DENIED directory" \
            "CONNECTED, and the helper outside the boundary ACTED. Landlock filesystem rules do not govern connect(2), so the macOS fix for this escape has no equivalent here."
        else
          verdict FAIL "confined process connects to a socket in a DENIED directory" \
            "connected through the deny (the helper did not act, so the reach is proven and the effect is not)"
        fi
        ;;
      *)
        verdict "N/A" "landlock socket escape" "probe error: $(first_line "$OUT")"
        ;;
    esac
  fi
  kill -9 "$LLLISTENER" >/dev/null 2>&1
fi

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

head2 "Escape 2 under Landlock: signal a process the boundary did not start"
# Landlock ABI 6 added LANDLOCK_SCOPE_SIGNAL, which is the direct equivalent
# of macOS's (allow signal (target same-sandbox)). The control restricts a
# process with everything EXCEPT signal scoping and has it kill its own
# target: without that, "the target survived" could mean the boundary worked
# or that the test never signalled anything.
if [ -z "$LL_PY" ] || [ -z "$LL_ABI" ]; then
  verdict "N/A" "landlock signal scoping" "no Landlock ABI established above"
elif [ "$LL_ABI" -lt 6 ] 2>/dev/null; then
  verdict "N/A" "landlock signal scoping" "ABI $LL_ABI; LANDLOCK_SCOPE_SIGNAL needs 6"
else
  sleep 300 & LLV1=$!; disown "$LLV1" 2>/dev/null || true
  sleep 300 & LLV2=$!; disown "$LLV2" 2>/dev/null || true
  echo "$LLV1" >> "$WORK/victims"; echo "$LLV2" >> "$WORK/victims"
  sleep 0.5
  ll signal "$LLV1" "$LLV2"
  case "$OUT" in
    OK\ refused*)
      verdict PASS "confined process signals a pid outside it, with LANDLOCK_SCOPE_SIGNAL" \
        "refused. The control — the same restriction WITHOUT signal scoping — killed its own target, so the test discriminates."
      ;;
    NO\ *)
      verdict FAIL "confined process signals a pid outside it, with LANDLOCK_SCOPE_SIGNAL" \
        "$(ll_answer "$OUT")"
      ;;
    *)
      verdict "N/A" "landlock signal scoping" "$(first_line "$OUT")"
      ;;
  esac
  kill -9 "$LLV1" "$LLV2" >/dev/null 2>&1
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
if [ -z "$LL_ABI" ]; then
  say "    * The Landlock ABI version — the probe could not run here, so the"
  say "      kernel release above is the only proxy."
fi
say "    * Whether yoloAI's Linux backends actually CONFINE — only whether"
say "      they are reported available."
say "    * Anything about a Manager's real workload: no engine was run"
say "      inside a sandbox here, only the boundary's properties."
say ""
say "finished: $(date -u '+%Y-%m-%dT%H:%M:%SZ') UTC"
