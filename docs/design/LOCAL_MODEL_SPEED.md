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

## ⚠ An unexplained 20x discrepancy, recorded rather than averaged

On the VM, the second of two identical short calls took **250 s of wall time
against 13 s of generation** (255 tokens at 19.8 tok/s; load 0 s; prompt 50
tokens). The first call's wall time was 36 s, 20 s of which was load. Nothing
here establishes where the other ~235 s went: queueing behind another request,
the VM being descheduled, or something in Ollama. It is not part of any number
above. Someone should look at it before a VM figure is used for sizing.

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
