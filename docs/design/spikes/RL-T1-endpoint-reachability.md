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

---

## ✅ The Docker half, measured 2026-09-24 (B6)

**Docker Desktop starts on this machine now**, so the test this note called
"the honest one" and could not take has been taken.

**A Docker-backed yoloAI sandbox reaches the host endpoint, at
`host.docker.internal`.** Sandbox created with `yoloai new <name> <workdir>
--backend docker` — the same backend `yoloai system backends` reports
available here — and the request issued from inside the running container.

| address tried from inside the sandbox | `GET /api/tags` |
|---|---|
| **`host.docker.internal`** | **HTTP 200** |
| `gateway.docker.internal` | no response |
| `host.containers.internal` | no response |
| `172.17.0.1` | no response |

⚠ **Only one of the four works, so the address is not a detail to leave to
a default.** `172.17.0.1` is the docker0 bridge gateway that works on Linux
and does not exist on Docker Desktop for macOS;
`host.containers.internal` is Podman's spelling. A config that guesses will
be right on one platform and silently unreachable on the others.

**A real completion, not just a listing** — `POST /v1/chat/completions` to
`http://host.docker.internal:11434`, `qwen3:8b`:

    reply : 'PLATYPUS42'
    finish: stop | usage: 155 tokens

22 models were visible from inside, matching the host.

**So both backends are now proven** and for different reasons: seatbelt
because D-30 says it has no network isolation, docker because Docker
Desktop publishes the host under a name of its own. The two are not the
same guarantee — a Docker sandbox started with `--network-isolated` or
`--network-none` would block this, and neither flag was used here.

⚠ **This note's own incidental finding reproduced exactly, unprompted.**
The first attempt used `max_tokens: 40` and came back `content: ""` with 40
completion tokens spent — which reads as "the model said nothing" and is
really a budget artefact. It was a reasoning-budget truncation, not a
networking failure, and it took a second look to tell those apart. That is
the trap this note already warned about, met in the wild by a reader who
had just read the warning.
