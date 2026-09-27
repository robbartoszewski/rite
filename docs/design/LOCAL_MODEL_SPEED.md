# Local model speed, measured

For the appliance question: what a local model costs in wall-clock time when it
is a Manager. Measured 2026-09-27 on Robert's Mac, during A6's macOS runs.

## The headline

**A 27.3B model at Q4 on an M4 Pro GPU generates at roughly a third of the
tokens per second that an 8B model does on four CPU cores of the same machine**:
6.0 tok/s in a real Manager run against ~20. At best, with a short prompt, it
reaches half. Anyone sizing an appliance would assume the opposite.

Same hardware on both sides: the "Linux VM" is a Parallels guest on this M4 Pro
(48 GB) with 4 vCPUs and 15 GB, and Ollama inside it runs on the CPU. So this
is GPU against CPU, and a large model against a small one, on one machine. It is
not two different boxes.

| | `qwen3.8:latest` (27.3B, Q4_K_M, 17 GB), Mac GPU | `qwen3:8b` (5.2 GB), VM, 4 CPU cores |
|---|---|---|
| Cold load | 8.1–10.9 s | 20.0 s |
| Generation, short prompt | 7.4–10.6 tok/s | 19.8–21.4 tok/s |
| Generation, in a real Manager run (18 Goose calls, two runs) | **6.0 tok/s** overall, 5.1–9.6 per call | not measured |
| Prompt processing, short prompt | 42–173 tok/s | 54–56 tok/s |
| Prompt processing, in a real Manager run | **95 tok/s** overall; ~106 tok/s on the 5.6k-token opening prompt, which alone took 53 s | not measured |
| **One Goose turn, as a Manager loop feels it** | **15 s to 4 min 17 s** | not measured |
| One routed task end to end (Owner routes, secondary acts and replies, Owner reports) | 443 s and 705 s | — |

The Mac numbers were taken on the 32k-window server the runs used (below). The
machine's load average was 8–10 from other sessions throughout, so they may be
pessimistic. Generation slows as the context grows: 4.1 tok/s with a 6k-token
prompt, against ~10 with a short one.

## Why: generation is bandwidth-bound, so the M4 Pro is not a ceiling

(Robert's framing.) Generating a token reads every weight once, so **generation
speed scales with memory bandwidth ÷ model size**, not with GPU against CPU. The
M4 Pro has about 300 GB/s (Apple specifies 273), shared by the GPU and the CPU
cores, the VM included. That is why the ratio above holds: 5.2 GB ÷ 17 GB ≈
0.31, against a measured 6.0 ÷ ~20 ≈ 0.30.

So the 6.0 tok/s here is this machine's bandwidth, not the model's limit. Scaled
linearly from 6.0 tok/s at 300 GB/s to Robert's target machines:

| Machine | Memory bandwidth | `qwen3.8:latest` generation, **estimated** |
|---|---|---|
| M4 Pro (measured) | ~300 GB/s | 6.0 tok/s in a Manager run (measured) |
| NVIDIA GPU | ~900 GB/s | **~18 tok/s** |
| M6 Max | ~1.2 TB/s | **~24 tok/s** |

⚠ **These are first-order ESTIMATES, not measurements.** They assume
generation stays bandwidth-bound and reaches the same fraction of peak
bandwidth on each machine; neither was measured.

⚠ **Prompt processing is compute-bound and will not scale the same way.**
Reading the prompt is a batched matrix multiply, limited by compute rather than
memory bandwidth. Here it took **53 s for Goose's 5.6k-token opening prompt**
(~106 tok/s). An agent loop re-sends a growing context every turn. Ollama
reuses the cached prefix, which helps, but each turn still pays for what is new,
and the context only grows. On faster hardware this, not generation, is the
cost most likely to become the constraint. How it scales on the target machines
is not estimated here, because a bandwidth figure does not predict it.

## The 250 s VM call: most likely queueing, test deferred to pre-release

On the VM, the second of two identical short calls took **250 s of wall time
against 13 s of generation** (255 tokens at 19.8 tok/s; load 0 s; prompt 50
tokens). It is excluded from every number above.

**What the VM's Ollama log shows (read afterwards, not a test):** the call
overlapped another session's Goose request on the same server, a
`/v1/chat/completions` over 5,265 tokens that ran 14:12:28–14:17:27 (4 min 59 s).
The call finished at 14:17:42, 15 s later. That server has one slot, so about
235 s of the 250 appears to have been **waiting in Ollama's queue behind another
client's request**. Host descheduling (the Mac's load average was 8–12) is the
other candidate. The log fits queueing, but a log read is not a test.

**The contrast test runs in the pre-release verification**, when the machine
is quiet and the tag work has stopped: several identical calls on an idle VM,
with the host load average and the VM's load recorded per call, then two
simultaneous calls to reproduce the queueing on purpose. It is not run on a busy
machine, because a contended run would reproduce the original result for the
wrong reason. **If queueing is confirmed, it matters to rite:** a local Manager
sharing an Ollama server with any other client, a second Manager included,
waits out that client's whole request, and faster hardware does not remove the
wait.

## How the Mac was served, and why it matters

Ollama.app on this Mac has no `OLLAMA_CONTEXT_LENGTH`, so it serves every model
at **4096** tokens. Goose's opening prompt is ~5.6k tokens. The runs above used
a private second server with the same binary and model, at 32768 (what the VM
sets):

    OLLAMA_HOST=127.0.0.1:11435 OLLAMA_CONTEXT_LENGTH=32768 \
      /Applications/Ollama.app/Contents/Resources/ollama serve

At 4096, Ollama truncates Goose's prompt **silently**: its log says
`truncating input prompt limit=2050 prompt=5583`. That is a correctness
problem, not a speed one, and it is W19 in `V060_TAG_READINESS.md`.
