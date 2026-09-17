"""The contract every state-layer backend must pass (P2-1a, SPEC §3.3.5).

Not a test file — pytest does not collect it (no `test_` prefix). A backend
binds to it by subclassing `StateLayerConformance` in its own test module and
implementing the four hooks. `test_state_layer_local.py` binds the local
backend; P2-1b's git backend must bind and pass it UNCHANGED.

**Why this suite is the hard part, and must not be weakened to fit a backend.**
It is the git backend's contract. A test that the local backend can pass and
the git backend structurally cannot would make the abstraction a lie. So:

- **Versions are whole-state** (§2.4.2). The single most important assertion
  here is `test_a_write_conflicts_if_ANY_key_changed_since_the_read`. Git's
  `--force-with-lease` compares one ref and every key lives on it; per-key CAS
  is not implementable there, so it must not be the contract here.
- **Concurrency is real, not simulated.** Every racing actor opens its OWN
  handle on the shared store and runs in its own thread. For the local backend
  that is separate open file descriptions contending on `flock`; for git it is
  separate clones pushing. P2-0c's burst harness can later sharpen the timing;
  it will not change what is asserted.

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
"""

from __future__ import annotations

import threading

import pytest

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

RACERS = 8


def _race(n, work):
    """Run `work(i)` in `n` threads released together; return the results."""
    barrier = threading.Barrier(n)
    results = [None] * n

    def run(i):
        barrier.wait()
        results[i] = work(i)

    threads = [threading.Thread(target=run, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


class StateLayerConformance:
    # --- hooks ---

    def new_store(self, tmp_path):
        raise NotImplementedError

    def open_layer(self, store):
        raise NotImplementedError

    def corrupt_state(self, store):
        raise NotImplementedError

    def corrupt_messages(self, store):
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

    # --- versions are WHOLE-STATE ---

    def test_a_second_first_writer_conflicts(self, store):
        a, b = self.open_layer(store), self.open_layer(store)
        assert isinstance(a.write_state("owner-lease.json", b"A", ABSENT), Written)
        assert isinstance(b.write_state("owner-lease.json", b"B", ABSENT), Conflict)

    def test_a_stale_version_conflicts(self, layer):
        v1 = layer.write_state("k.json", b"1", ABSENT).version
        layer.write_state("k.json", b"2", v1)
        assert isinstance(layer.write_state("k.json", b"3", v1), Conflict)

    def test_a_write_conflicts_if_ANY_key_changed_since_the_read(self, store):
        """THE contract. A reads key A; B writes an unrelated key; A's write
        must conflict. Per-key CAS would pass on a local store and be
        impossible on git's single ref (§2.4.2's own warning)."""
        a, b = self.open_layer(store), self.open_layer(store)
        seed = a.write_state("managers/a.json", b"a0", ABSENT).version
        read = a.read_state("managers/a.json")
        assert read.version == seed
        assert isinstance(b.write_state("managers/b.json", b"b0", seed), Written)
        assert isinstance(a.write_state("managers/a.json", b"a1", seed), Conflict)

    def test_a_successful_write_moves_the_version(self, layer):
        v1 = layer.write_state("k.json", b"1", ABSENT).version
        v2 = layer.write_state("k.json", b"2", v1).version
        assert v1 != v2
        assert layer.read_state("k.json") == Present(b"2", v2)

    def test_absent_keys_report_the_current_whole_state_version(self, layer):
        """A caller about to create a key needs the version to expect."""
        v = layer.write_state("a.json", b"x", ABSENT).version
        assert layer.read_state("b.json") == Absent(v)

    # --- other keys are preserved verbatim ---

    def test_writing_one_key_preserves_every_other(self, layer):
        """Every single-key write is §2.4.2's read-merge-write."""
        v = layer.write_state("a.json", b"alpha", ABSENT).version
        v = layer.write_state("b.json", b"beta", v).version
        assert layer.read_state("a.json") == Present(b"alpha", v)
        assert layer.read_state("b.json") == Present(b"beta", v)

    def test_arbitrary_bytes_round_trip_verbatim(self, layer):
        """D-54's pass-through: the layer never parses a value, so content
        another Manager cannot parse is still carried unchanged."""
        raw = b"\xff\x00{not json at all\n\x80"
        v = layer.write_state("claims.json", raw, ABSENT).version
        assert layer.read_state("claims.json") == Present(raw, v)

    def test_unparseable_content_survives_a_write_to_another_key(self, layer):
        raw = b"\xfe\xfe truncated {"
        v = layer.write_state("claims.json", raw, ABSENT).version
        v = layer.write_state("owner-lease.json", b"{}", v).version
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

    # --- concurrency, with real handles ---

    def test_racing_first_writers_produce_exactly_one_winner(self, store):
        results = _race(
            RACERS,
            lambda i: self.open_layer(store).write_state(
                "owner-lease.json", f"racer-{i}".encode(), ABSENT
            ),
        )
        winners = [r for r in results if isinstance(r, Written)]
        losers = [r for r in results if isinstance(r, Conflict)]
        assert len(winners) == 1, results
        assert len(losers) == RACERS - 1, results

    def test_retrying_writers_lose_no_one_elses_key(self, store):
        """The data-loss property at the interface level: N Managers each add
        their own key under contention, retrying on Conflict. Every key must
        survive — a lost one is exactly the silent destruction §2.4.2 warns
        a delta-push causes."""

        def add_own_key(i):
            layer = self.open_layer(store)
            key = f"managers/m{i}.json"
            for _ in range(200):
                read = layer.read_state(key)
                if isinstance(read, Unavailable):
                    return read
                result = layer.write_state(key, f"m{i}".encode(), read.version)
                if not isinstance(result, Conflict):
                    return result
            return "gave up"

        results = _race(RACERS, add_own_key)
        assert all(isinstance(r, Written) for r in results), results
        final = self.open_layer(store)
        for i in range(RACERS):
            got = final.read_state(f"managers/m{i}.json")
            assert isinstance(got, Present) and got.value == f"m{i}".encode(), i

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

    def test_concurrent_appends_lose_nothing_and_never_share_a_cursor(self, store):
        per = 10

        def append_many(i):
            layer = self.open_layer(store)
            return [layer.append_message(f"r{i}-{j}") for j in range(per)]

        results = _race(RACERS, append_many)
        assert all(isinstance(r, Appended) for batch in results for r in batch)
        got = self.open_layer(store).read_messages()
        assert len(got.items) == RACERS * per
        assert len({m.cursor for m in got.items}) == RACERS * per
        assert {m.content for m in got.items} == {
            f"r{i}-{j}" for i in range(RACERS) for j in range(per)
        }
