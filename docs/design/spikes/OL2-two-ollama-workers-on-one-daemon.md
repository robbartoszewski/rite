# OL2 — two Ollama Workers against one daemon, and the 32 GB ceiling

**Citation convention:** a bare `§` means a section of `SPEC.md`; this note's
own sections are written out, because the citation gate's regex is
context-free and would match a real SPEC section.

**Measured 2026-10-02. Two real seatbelt Workers (`ol2-w1`, `ol2-w2`), Goose
1.51.0, Ollama 0.34.2, one M-series Mac with a 32 GB ceiling.** Method as
`OL1-ollama-inside-a-worker-sandbox.md`: `--agent idle`, rite driving each turn
through `yoloai exec`, `GOOSE_PATH_ROOT` per sandbox.

The v0.7.0 goal includes mixing Claude and Ollama Workers under one Manager.
Claude Workers cost this machine nothing — the model is not here. **Two local
Workers are the question**, and rite has no budget concept and must not gain
one, so the answer has to be something the SCHEDULE can act on.

---

## 0. The answer in one line

⚠ **Two local Workers on the SAME model are nearly free. Two on DIFFERENT
models thrash, and no ceiling arithmetic from `ollama list` would have
predicted it.**

| configuration | wall time | what the daemon held |
|---|---|---|
| one Worker, alone (OL1 baseline) | **71 s** | one copy, 17 GB |
| two Workers, same model | **77 s** and **128 s** | **one copy**, 17 GB, 100% GPU, no eviction |
| two Workers, different models | **161 s** and **254 s** | **continuous eviction**, one model at a time |

Slowdown against the single-Worker baseline: **1.1× / 1.8×** sharing a model,
**2.3× / 3.6×** not sharing one.

**Every turn in every configuration succeeded** — exit 0, file written, no
infrastructure fault. This is a throughput property, not a correctness one.
Nothing here needs a guard to stop rite breaking; it needs the schedule not to
waste the machine.

## 1. Sharing a model: one copy, two clients

`ollama ps` sampled every 10 s for the whole run showed one entry and only one,
from first load to last:

```
rite-ctx32768-qwen3.8-latest:latest   17 GB   100% GPU   32768   4 minutes from now
```

No `Stopping...`, no second row. The daemon served both sandboxed clients from
a single resident copy. The second Worker paid a queueing cost (128 s against
77 s) and the machine paid nothing extra in memory.

## 2. Not sharing a model: eviction, back and forth

With `rite-ctx32768-qwen3.8-latest` in one Worker and `qwen3-32b-ctx32k` in the
other, the samples alternate for the entire run — each model loading, being
marked `Stopping...`, and giving way to the other:

```
t=1   rite-ctx32768-qwen3.8   17 GB   4 minutes from now
t=4   qwen3-32b-ctx32k        29 GB   Stopping...
t=13  rite-ctx32768-qwen3.8   17 GB   Stopping...
t=18  qwen3-32b-ctx32k        29 GB   4 minutes from now
```

Both turns still finished. They finished having loaded 17 GB and 29 GB of
weights across the bus more than once each, which is where 161 s and 254 s went.

## 3. ⚠ The finding that changes model-sizing

**`ollama list` understates resident size, and the pinned context window is
why.**

| model | `ollama list` | `ollama ps` at 32,768 |
|---|---|---|
| `rite-ctx32768-qwen3.8-latest` | 17 GB | **17 GB** |
| `qwen3-32b-ctx32k` | 20 GB | **29 GB** |

The 32b model costs **+9 GB** resident over its on-disk size — KV cache for the
window rite pinned. So:

- **a single 32b model at a 32 k window very nearly fills a 32 GB machine by
  itself**, and
- **sizing two models against `ollama list` (20 + 17 = 37 GB) would have been
  wrong in the same direction as the truth but for the wrong reason** — the real
  pair is 29 + 17 = 46 GB, and the arithmetic that matters is the one the
  context pin produces, not the one the library listing shows.

⚠ Anything that reasons about the ceiling must read `ollama ps` at the pinned
window. `context_window.py`'s note that "`ollama list` sums weights" is about
a derived model's disk cost; this is the other half, and it is the half a
fleet-sizing decision needs.

## 4. What follows for rite

- **A fleet's local Workers should share one model.** It is the difference
  between 1.8× and 3.6×, it is free to arrange, and the duty router already
  makes engine a per-unit choice.
- **The lever is the schedule, as ruled.** Nothing above argues for a budget, a
  quota or a concurrency cap in config: two overlapping local Workers on one
  model are a reasonable thing to run, and two on different models are merely
  slow. A schedule that does not overlap them costs nothing to express.
- **`rite doctor` could say the resident arithmetic** — the pinned window's real
  cost against the machine — rather than leaving someone to infer it from
  `ollama list`. Not built here.

## 5. What this did NOT measure

- **n = 1 per configuration.** One task, one pair of models, no repeats. The
  direction is unambiguous and the exact ratios are not.
- **Short turns, so load time dominates.** A 70-second turn that reloads 29 GB
  pays proportionally more than a ten-minute one would. The thrash is real; its
  3.6× is specific to turns this short.
- **Three or more local Workers.** Untested.
- **Whether `OLLAMA_MAX_LOADED_MODELS` or `OLLAMA_NUM_PARALLEL` change any of
  it.** This machine's daemon is the GUI app with its settings in its own
  sqlite, and nothing here was tuned — the numbers are the defaults a person
  would actually get.
