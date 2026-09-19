# RL-T1 — Can a sandbox reach a host model endpoint? (2026-09-18)

Decisions touched: RL-14 (inference on the host, tool execution sandboxed).

## Result: yes on seatbelt, at `localhost`. Docker unmeasured.

| Backend | Available here | Reaches host endpoint | Address that works |
|---|---|---|---|
| seatbelt | yes | **yes** | `http://localhost:11434` and `http://127.0.0.1:11434` |
| docker | no — daemon not responding | **unmeasured** | `host.docker.internal` is unreachable from seatbelt, as expected |
| podman, apple, tart | not installed | unmeasured | — |

Method: `yoloai new --backend seatbelt --agent claude --no-start`, workdir
`:copy-all`, then `yoloai exec` inside it — the same shape `rite sandbox start`
uses. Not just a port probe: a full OpenAI-compatible
`POST /v1/chat/completions` against `qwen3:8b` returned a normal response from
inside the sandbox, 5.5s for 20 tokens.

D-30 (seatbelt has no network isolation) is why this works here, and is exactly
the gap the design already names. **A container backend would be the honest
test, and it is the one that cannot be taken on this machine** — Docker
Desktop's daemon will not start. Treat "the sandbox can reach the host
endpoint" as proven for seatbelt only.

## Incidental finding, and it changes RL-T0's measurement

qwen3 is a reasoning model, and Ollama returns its thinking in a separate
`reasoning` field:

- `max_tokens: 20` → `content: ""`, `finish_reason: "length"`, all 20 tokens
  spent in `reasoning`. A harness reading `choices[0].message.content` sees an
  EMPTY STRING and would record "the model said nothing".
- `max_tokens: 400` → `content: "reachable"`, `finish_reason: "stop"`,
  **150 completion tokens to say one word** — about 130 of them reasoning.

Two consequences the design does not mention:

1. **The harness contract (§6.2, RL-T6) must handle a reasoning field**, or
   every tight-budget request looks like silence rather than truncation.
2. **RL-T0's thresholds are affected.** "Completed" and "reported honestly"
   are both corrupted if an empty `content` is read as a failed or dishonest
   answer when it is a budget artefact. Budget the reasoning, and treat
   `finish_reason: "length"` as a distinct outcome from a wrong answer.
