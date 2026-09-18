"""The contract every state-layer backend must pass (P2-1a, SPEC §3.3.5).

Not a test file — pytest does not collect it (no `test_` prefix). A backend
binds to it by subclassing `StateLayerConformance` in its own test module and
implementing the four hooks. `test_state_layer_local.py` binds the local
backend; P2-1b's git backend must bind and pass it UNCHANGED.

**Written against the CONTRACT, never against an implementation.** The design
test is: could a Redis backend pass this suite unchanged? `test_state_layer_kv`
answers it by binding a key-value store that has no trees, no refs and no
merges — if a test here needed one of those, it would fail there, loudly.

- **Compare-and-swap is per key.** The most important assertion is now
  `test_an_unrelated_key_changing_does_NOT_conflict`. It replaces an earlier
  test that asserted the opposite, on the mistaken ground that git could not
  do per-key CAS: git absorbs its ref-level race by re-merging inside the
  backend, and the old contract would have forced a key-value store to
  serialise every write through one global version.
- **Nothing here names a backend's mechanism.** No oid, ref, branch, file or
  socket appears in an assertion — only values, versions and the three
  outcomes.
- **Concurrency is real, repeated, and calibrated.** Every actor is a separate
  PROCESS that opens its own handle and re-synchronises on a shared wall-clock
  instant before every attempt (P2-0c's `burst` harness), so the collision
  window is revisited thousands of times rather than once. The harness is
  calibrated in `test_burst_harness.py` — it loses ~83% of updates on an
  unlocked counter and none on a locked one — so a pass here means something.
  Each actor LOGS what it believed happened; the parent checks the store
  against those logs, which needs no sampling luck.

**What this suite deliberately cannot test: §2.4.2 step 5's lost
acknowledgement** — a write that landed on the remote while the writer saw
Unavailable. Reproducing it needs control over the transport, which is
backend-specific. The interface requires callers to re-read after Unavailable;
proving that the git backend's Unavailable really can mean "landed" is an
OBLIGATION ON P2-1b, not something this suite pretends to cover.

Hooks a backend implements:

    new_store(tmp_path)        -> an opaque token naming one shared store
    open_layer(store)          -> a NEW StateLayer handle on that store
    corrupt_state(store)       -> make the state unreadable, in place
    corrupt_messages(store)    -> make the message log unreadable, in place
    actor_layer_spec(store, i) -> (module, attribute, args): how a CHILD
                                  PROCESS opens its own handle. Picklable by
                                  construction; git passes one clone per actor.
"""

from __future__ import annotations

import importlib
import json
import time
from pathlib import Path

import pytest

from burst import align, deadline, run_actors
from rite_ai.coordination.state_layer import (
    ABSENT,
    Absent,
    Appended,
    Conflict,
    Messages,
    Present,
    Unavailable,
    Written,
)

BURST_ACTORS = 6


def _open(spec):
    module, attribute, args = spec
    return getattr(importlib.import_module(module), attribute)(*args)


def _cas_actor(args):
    """Read, then write against exactly the version read, as often as the
    bursts allow. Logs every attempt's (expected version, outcome).

    Each attempt writes DISTINCT bytes. A version fingerprints the value
    (state_layer property 2), so rewriting the same bytes is a no-op that
    cannot move it — and every actor would appear to win the same version
    for a reason that has nothing to do with the race."""
    spec, log_path, seconds, actor = args
    layer = _open(spec)
    stop = deadline(seconds)
    rows = []
    n = 0
    while time.time() < stop:
        align()
        read = layer.read_state("owner-lease.json")
        if isinstance(read, Unavailable):
            rows.append(["?", "Unavailable"])
            continue
        n += 1
        result = layer.write_state(
            "owner-lease.json", f"{actor}:{n}".encode(), read.version
        )
        rows.append([read.version, type(result).__name__])
    Path(log_path).write_text(json.dumps(rows))
    return len(rows)


def _merge_actor(args):
    """Add a NEW key per successful write, retrying on Conflict.

    Every key here belongs to ONE actor, so nobody else ever writes it. Two
    things are logged: the keys it was told it wrote (each must survive every
    other actor's writes) and any Conflict it was given (there must be none —
    a conflict on a key nobody else touched is a backend exporting its own
    contention as a semantic)."""
    spec, actor, log_path, seconds = args
    layer = _open(spec)
    stop = deadline(seconds)
    written, conflicted, n = [], [], 0
    while time.time() < stop:
        align()
        key = f"managers/a{actor}-{n}.json"
        read = layer.read_state(key)
        if isinstance(read, Unavailable):
            continue
        result = layer.write_state(key, f"{actor}:{n}".encode(), read.version)
        if isinstance(result, Written):
            written.append(key)
            n += 1
        elif isinstance(result, Conflict):
            conflicted.append(key)
    Path(log_path).write_text(
        json.dumps({"written": written, "conflicted": conflicted})
    )
    return len(written)


def _append_actor(args):
    spec, actor, log_path, seconds = args
    layer = _open(spec)
    stop = deadline(seconds)
    appended, n = [], 0
    while time.time() < stop:
        align()
        result = layer.append_message(f"a{actor}-{n}")
        if isinstance(result, Appended):
            appended.append([result.cursor, f"a{actor}-{n}"])
            n += 1
    Path(log_path).write_text(json.dumps(appended))
    return len(appended)


class StateLayerConformance:
    # --- how hard to push, per backend ---
    #
    # A backend may lower these when its transport is slower (git forks
    # several processes per operation), because a floor only asserts THE
    # HARNESS RAN. The properties below it are absolute and are never
    # relaxed: no version won twice, no key lost, no message lost.
    BURST_SECONDS = 1.0
    MIN_CAS_ATTEMPTS = 200
    MIN_WRITES = 50
    MIN_MESSAGES = 50

    # --- hooks ---

    def new_store(self, tmp_path):
        raise NotImplementedError

    def open_layer(self, store):
        raise NotImplementedError

    def corrupt_state(self, store):
        raise NotImplementedError

    def corrupt_messages(self, store):
        raise NotImplementedError

    def actor_layer_spec(self, store, actor):
        raise NotImplementedError

    @pytest.fixture
    def store(self, tmp_path):
        return self.new_store(tmp_path)

    @pytest.fixture
    def layer(self, store):
        return self.open_layer(store)

    # --- a store that has never been written ---

    def test_a_fresh_store_reads_absent_not_unavailable(self, layer):
        """Absent is a real state — §2.4.2 step 1's first-write case."""
        assert layer.read_state("owner-lease.json") == Absent(ABSENT)

    def test_the_first_write_expects_absent(self, layer):
        assert isinstance(layer.write_state("claims.json", b"{}", ABSENT), Written)

    # --- compare-and-swap, PER KEY ---

    def test_a_second_first_writer_conflicts(self, store):
        a, b = self.open_layer(store), self.open_layer(store)
        assert isinstance(a.write_state("owner-lease.json", b"A", ABSENT), Written)
        assert isinstance(b.write_state("owner-lease.json", b"B", ABSENT), Conflict)

    def test_a_stale_version_conflicts(self, layer):
        v1 = layer.write_state("k.json", b"1", ABSENT).version
        layer.write_state("k.json", b"2", v1)
        assert isinstance(layer.write_state("k.json", b"3", v1), Conflict)

    def test_an_unrelated_key_changing_does_NOT_conflict(self, store):
        """THE contract. A reads its own key; B writes a different one; A's
        write must still succeed.

        This is what keeps the interface substitutable. A store with no
        shared structure between keys — Redis, a table, a bucket — could only
        satisfy the opposite rule by funnelling every write through one
        global version, which is slower than git and absurd on its own
        terms. It is better for git too: §2.4.2 warns that a Manager whose
        heartbeat keeps losing the ref-level race "could be marked falsely
        stalled", and under this rule that race never reaches the caller."""
        a, b = self.open_layer(store), self.open_layer(store)
        mine = a.write_state("managers/a.json", b"a0", ABSENT).version
        assert isinstance(b.write_state("managers/b.json", b"b0", ABSENT), Written)
        assert isinstance(a.write_state("managers/a.json", b"a1", mine), Written)

    def test_the_same_key_changing_DOES_conflict(self, store):
        """The other half: two writers on one key, one winner."""
        a, b = self.open_layer(store), self.open_layer(store)
        seed = a.write_state("owner-lease.json", b"a0", ABSENT).version
        assert isinstance(b.write_state("owner-lease.json", b"b0", seed), Written)
        assert isinstance(a.write_state("owner-lease.json", b"a1", seed), Conflict)

    def test_a_version_is_opaque_and_says_nothing_about_other_keys(self, layer):
        """A caller must not be able to infer a store's shape from a version.
        Writing another key may or may not change this one's — the contract
        says only that equal versions mean equal bytes."""
        v = layer.write_state("a.json", b"alpha", ABSENT).version
        layer.write_state("b.json", b"beta", ABSENT)
        assert layer.read_state("a.json") == Present(b"alpha", v)

    def test_another_handle_sees_a_write_immediately(self, store):
        """No caching, and no read-your-writes weasel room.

        The interface promises one operation per call (state_layer's closing
        rule). A backend that cached to hide a slow round-trip would pass
        every single-handle test here and then hand a Manager a lease that
        moved minutes ago — and a store whose round-trip is cheap would have
        inherited the machinery for nothing."""
        writer, reader = self.open_layer(store), self.open_layer(store)
        first = writer.write_state("owner-lease.json", b"one", ABSENT)
        assert reader.read_state("owner-lease.json") == Present(b"one", first.version)
        second = writer.write_state("owner-lease.json", b"two", first.version)
        assert reader.read_state("owner-lease.json") == Present(b"two", second.version)

    def test_a_successful_write_moves_the_version(self, layer):
        v1 = layer.write_state("k.json", b"1", ABSENT).version
        v2 = layer.write_state("k.json", b"2", v1).version
        assert v1 != v2
        assert layer.read_state("k.json") == Present(b"2", v2)

    def test_an_absent_key_reports_ABSENT_whatever_else_exists(self, layer):
        """The version a caller about to create this key must expect — and it
        cannot depend on what other keys hold, or a first writer would need
        to read the whole store to create one key."""
        layer.write_state("a.json", b"x", ABSENT)
        assert layer.read_state("b.json") == Absent(ABSENT)

    def test_rewriting_identical_bytes_keeps_the_version(self, layer):
        """Property 2. A version fingerprints the VALUE, so A -> B -> A
        cannot let a stale writer win: what it read is what is there."""
        v1 = layer.write_state("k.json", b"same", ABSENT).version
        v2 = layer.write_state("k.json", b"other", v1).version
        v3 = layer.write_state("k.json", b"same", v2).version
        assert v3 == v1
        assert isinstance(layer.write_state("k.json", b"next", v1), Written)

    # --- other keys are preserved verbatim ---

    def test_writing_one_key_preserves_every_other(self, layer):
        """Whether that costs a whole-tree merge (git) or nothing at all (a
        key-value store) is the backend's business; the caller sees the same
        thing either way."""
        va = layer.write_state("a.json", b"alpha", ABSENT).version
        vb = layer.write_state("b.json", b"beta", ABSENT).version
        assert layer.read_state("a.json") == Present(b"alpha", va)
        assert layer.read_state("b.json") == Present(b"beta", vb)

    def test_arbitrary_bytes_round_trip_verbatim(self, layer):
        """D-58's pass-through: the layer never parses a value, so content
        another Manager cannot parse is still carried unchanged."""
        raw = b"\xff\x00{not json at all\n\x80"
        v = layer.write_state("claims.json", raw, ABSENT).version
        assert layer.read_state("claims.json") == Present(raw, v)

    def test_unparseable_content_survives_a_write_to_another_key(self, layer):
        raw = b"\xfe\xfe truncated {"
        v = layer.write_state("claims.json", raw, ABSENT).version
        layer.write_state("owner-lease.json", b"{}", ABSENT)
        assert layer.read_state("claims.json") == Present(raw, v)

    def test_an_empty_value_is_present_not_absent(self, layer):
        v = layer.write_state("k.json", b"", ABSENT).version
        assert layer.read_state("k.json") == Present(b"", v)

    # --- unavailable is never absent ---

    @pytest.mark.parametrize(
        "key", ["../escape", "/abs", "a/../b", "a//b", "", "a/./b"]
    )
    def test_an_invalid_key_is_unavailable_for_read_and_write(self, layer, key):
        assert isinstance(layer.read_state(key), Unavailable)
        assert isinstance(layer.write_state(key, b"x", ABSENT), Unavailable)

    def test_a_corrupt_state_reads_unavailable_not_absent(self, store, layer):
        layer.write_state("owner-lease.json", b"{}", ABSENT)
        self.corrupt_state(store)
        assert isinstance(layer.read_state("owner-lease.json"), Unavailable)

    def test_a_corrupt_state_refuses_writes_rather_than_replacing_it(
        self, store, layer
    ):
        """Writing over an unreadable state would destroy it; there is no
        version to compare against and nothing to carry through."""
        layer.write_state("a.json", b"x", ABSENT)
        self.corrupt_state(store)
        for expected in (ABSENT, "1", "anything"):
            assert isinstance(layer.write_state("a.json", b"y", expected), Unavailable)

    # --- concurrency: synchronised process bursts (P2-0c) ---

    def _logs(self, tmp_path, name):
        d = tmp_path / f"burst-{name}"
        d.mkdir()
        return d

    def test_no_version_is_ever_won_twice_under_bursts(self, store, tmp_path):
        """THE CAS PROPERTY, checked directly. Across thousands of collisions,
        two writers that read the same version must never BOTH be told
        Written — that is two Owners."""
        logs = self._logs(tmp_path, "cas")
        run_actors(
            _cas_actor,
            [
                (
                    self.actor_layer_spec(store, i),
                    str(logs / f"{i}.json"),
                    self.BURST_SECONDS,
                    i,
                )
                for i in range(BURST_ACTORS)
            ],
        )
        winners: dict[str, int] = {}
        attempts = unavailable = 0
        for log in logs.glob("*.json"):
            for version, outcome in json.loads(log.read_text()):
                attempts += 1
                unavailable += outcome == "Unavailable"
                if outcome == "Written":
                    winners[version] = winners.get(version, 0) + 1
        assert attempts > self.MIN_CAS_ATTEMPTS, (
            f"bursts barely ran ({attempts}) — not a test"
        )
        assert unavailable == 0, f"{unavailable} spurious Unavailable under load"
        doubled = {v: n for v, n in winners.items() if n > 1}
        assert not doubled, f"versions won more than once (split brain): {doubled}"
        assert winners, "no write ever succeeded"

    def test_no_written_key_is_lost_under_bursts(self, store, tmp_path):
        """§2.4.2's data-loss property. Every actor is told its key was
        written; if any is missing afterwards, some writer merged from a stale
        read and silently destroyed another Manager's state."""
        logs = self._logs(tmp_path, "merge")
        run_actors(
            _merge_actor,
            [
                (
                    self.actor_layer_spec(store, i),
                    i,
                    str(logs / f"{i}.json"),
                    self.BURST_SECONDS,
                )
                for i in range(BURST_ACTORS)
            ],
        )
        logged = [json.loads(log.read_text()) for log in logs.glob("*.json")]
        claimed = [k for entry in logged for k in entry["written"]]
        conflicted = [k for entry in logged for k in entry["conflicted"]]
        assert len(claimed) > self.MIN_WRITES, (
            f"bursts barely ran ({len(claimed)}) — not a test"
        )
        # THE PORTABILITY PROPERTY, under load. Every key here belongs to one
        # actor and nobody else writes it, so a Conflict can only mean the
        # backend reported its own internal contention — git's ref-level race
        # — as the caller's lost race. That is what made the first contract
        # git-shaped, and it is invisible in any sequential test, because a
        # writer that re-reads immediately before writing never sees it.
        assert not conflicted, (
            f"{len(conflicted)} conflicts on keys nobody else wrote, "
            f"e.g. {conflicted[:3]} — contention leaked into the contract"
        )
        final = self.open_layer(store)
        lost = [k for k in claimed if not isinstance(final.read_state(k), Present)]
        assert not lost, (
            f"{len(lost)} of {len(claimed)} written keys lost, e.g. {lost[:3]}"
        )

    def test_no_appended_message_is_lost_or_shares_a_cursor_under_bursts(
        self, store, tmp_path
    ):
        logs = self._logs(tmp_path, "append")
        run_actors(
            _append_actor,
            [
                (
                    self.actor_layer_spec(store, i),
                    i,
                    str(logs / f"{i}.json"),
                    self.BURST_SECONDS,
                )
                for i in range(BURST_ACTORS)
            ],
        )
        claimed = [
            tuple(r) for log in logs.glob("*.json") for r in json.loads(log.read_text())
        ]
        assert len(claimed) > self.MIN_MESSAGES, (
            f"bursts barely ran ({len(claimed)}) — not a test"
        )
        got = self.open_layer(store).read_messages()
        stored = {(m.cursor, m.content) for m in got.items}
        assert len(got.items) == len(claimed), (len(got.items), len(claimed))
        assert len({m.cursor for m in got.items}) == len(got.items), "shared cursor"
        missing = [c for c in claimed if c not in stored]
        assert not missing, (
            f"{len(missing)} appended messages missing, e.g. {missing[:3]}"
        )

    # --- the message log ---

    def test_a_p2_0d_message_round_trips_with_its_trailers_parseable(self, layer):
        """The real payload, not a toy string. P2-0d puts a message's meaning
        in `Rite-` trailers in the FINAL paragraph, so a backend that
        normalises the text — git's default commit cleanup strips and
        reflows whitespace — can silently turn an event into an ordinary
        commit that `parse_message` returns None for. A git backend must
        carry the text faithfully (e.g. `--cleanup=verbatim`)."""
        from rite_ai.coordination.message_log import (
            format_message,
            parse_message,
            promotion_event,
        )

        event = promotion_event("mac-studio", "laptop", "lease-not-credible")
        event.body = "Clock on laptop read a day ahead.\n\nSecond paragraph."
        layer.append_message(format_message(event))
        got = layer.read_messages()
        assert len(got.items) == 1
        assert parse_message(got.items[0].content) == event

    def test_a_fresh_log_is_empty(self, layer):
        assert layer.read_messages() == Messages([])

    def test_messages_come_back_in_append_order(self, layer):
        for text in ("one", "two", "three"):
            assert isinstance(layer.append_message(text), Appended)
        got = layer.read_messages()
        assert [m.content for m in got.items] == ["one", "two", "three"]

    def test_since_returns_only_later_messages(self, layer):
        layer.append_message("one")
        mark = layer.append_message("two").cursor
        layer.append_message("three")
        got = layer.read_messages(since=mark)
        assert [m.content for m in got.items] == ["three"]

    def test_the_last_cursor_yields_nothing_new(self, layer):
        last = layer.append_message("only").cursor
        assert layer.read_messages(since=last) == Messages([])

    def test_an_unknown_cursor_is_unavailable_not_empty(self, layer):
        """An empty answer would silently skip every message after a position
        the caller believes exists."""
        layer.append_message("one")
        assert isinstance(layer.read_messages(since="no-such-cursor"), Unavailable)

    def test_a_corrupt_log_is_unavailable_not_truncated(self, store, layer):
        """An audit log with a hole must not be presented as complete."""
        layer.append_message("one")
        self.corrupt_messages(store)
        assert isinstance(layer.read_messages(), Unavailable)
