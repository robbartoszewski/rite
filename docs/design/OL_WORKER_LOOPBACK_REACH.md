# A Worker reaches every loopback service on the machine

**Status: a note to ticket. Measured 2026-10-02, yoloAI 0.11.0, seatbelt.
Not a defect in rite, and not something rite can fix.**

**Citation convention:** a bare `§` means a section of `SPEC.md`; this note's
own sections are written out, because the citation gate's regex is
context-free and would match a real SPEC section.

## What was measured

Step 1 of the v0.7.0 Ollama track needed to know whether a sandboxed Worker
could reach the Ollama daemon. It can:

```
curl http://localhost:11434/api/tags   ->  HTTP 200, 23 models
curl http://127.0.0.1:11434/api/tags   ->  HTTP 200, body read
```

Full method and controls in
`spikes/OL1-ollama-inside-a-worker-sandbox.md`. The sandbox was enforcing at
the time — `cat ~/.ssh/id_ed25519` returned `Operation not permitted` in the
same session.

## The part worth ticketing

⚠ **Ollama is not special here.** The grant is for loopback, not for a port.
A Worker can therefore reach **any** service listening on `localhost` on the
operator's machine: another project's dev server, a database, a staging admin
panel, a local MCP server, an unauthenticated Redis, a second Ollama serving
a different project's models.

Three things follow, and they are the reason this is a note and not a line in
the spike:

1. **rite cannot grant or revoke it.** rite does not render the Worker
   profile — yoloAI does (`runtime/seatbelt/profile.go`), and
   `managers/enclosure.py` records that the shipped Worker profile already
   contains `(allow network*)`. rite's Manager enclosure is rite's own file
   and can be narrowed; the Worker's cannot, short of `--network-none` or
   `--network-isolated`, which would also cut the Ollama access this track
   depends on.
2. **It is a second road to a leak rite already decided about.** SB12
   measured a sandbox's environment readable from other sandboxes on the same
   machine, and `enclosure.engine_tmp` measured the shared temp root handing a
   Manager every other process's scratch. Loopback is the same shape: the
   boundary holds on the filesystem and is open on the network.
3. **It is not new with the Ollama track** — every Claude Worker rite has ever
   started had it. The Ollama work is what made somebody look.

## What it does NOT say

- **Not that a Worker did anything.** No Worker on this machine was observed
  reaching a non-Ollama loopback service; this is reachability, not an event.
- **Not that `--network-isolated` is the answer.** Unmeasured here, and
  `yoloai help security` says the allowlist is tamper-resistant on docker
  only — on seatbelt an agent may be able to flush it. Worth measuring before
  anyone proposes it.
- **Not a reason to hold the Ollama track.** The track needs exactly this
  grant. Narrowing it to the one port, if that is even expressible, is
  separate work.

## The decision it asks for

Whether rite should (a) accept and document it as a property of the Worker
backend, the way SPEC §5.3.5 already documents what each backend does and does
not contain, (b) measure `--network-isolated` plus `--network-allow` to see
whether a Worker can keep Ollama and lose the rest, or (c) refuse to start a
Worker when the machine is serving something rite can name as sensitive —
which would be a check that cannot be complete and may be worse than (a).

No budget or quota dimension here; nothing about this is a spend question.
