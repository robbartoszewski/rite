"""A3a's proof: a standalone script reads a Slack channel message.

**An instrument, not product code.** Nothing in rite imports this. It is kept
because A3a's done-when is an observation — *"send a message in the Slack
channel; a standalone script using the stored token prints it within 5
seconds"* — and a spike whose script was thrown away cannot be re-run when
somebody doubts the result.

**The transport is already decided** (`docs/design/spikes/A3a-slack-transport.md`):
Web API polling, `conversations.history` with an `oldest` timestamp, because
it is the only transport needing neither an inbound listener nor a held-open
connection. This proves it works; it does not choose it.

⚠ **The token is read from the environment and nowhere else.** Never printed,
never written to a file, never passed as an argument — `RITE_SLACK_BOT_TOKEN`
is tier 1 of `credentials.store.resolve`, which is also the only channel that
reaches a sandboxed reader.

⚠ **Only two scopes are needed, and `channels:read` is NOT one of them.**
That matters because `conversations.history` wants a channel ID and listing
channels to find one would need a third scope. `chat.postMessage` accepts a
channel NAME and returns the ID, so the ID is discoverable from a post — or
it is configuration rite asks for.

    RITE_SLACK_BOT_TOKEN=... python -m tools.slack_probe.probe '#all-rite'

Measured 2026-09-25 against `rite-ai.slack.com`, channel `#all-rite`: the
posted sentinel was readable **0.24 s** later, on the first poll of a 2.0 s
loop. Human-typed messages in the same channel are readable identically,
carrying `user`, `text`, `ts` and `type` — so the read path is not specific to
the bot's own posts.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
import urllib.request

API = "https://slack.com/api/"
POLL_SECONDS = 2.0
"""The supervisor's own interval, so the latency measured here is the latency
A3b will actually deliver."""


def _call(method, token, payload=None, params=None):
    if params is not None:
        url = API + method + "?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(url, method="GET")
    else:
        request = urllib.request.Request(
            API + method, data=json.dumps(payload).encode(), method="POST"
        )
        request.add_header("Content-Type", "application/json; charset=utf-8")
    request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


def main(argv: list[str]) -> int:
    channel = argv[1] if len(argv) > 1 else "#all-rite"
    token = os.environ.get("RITE_SLACK_BOT_TOKEN", "")
    if not token:
        print("RITE_SLACK_BOT_TOKEN is not set", file=sys.stderr)
        return 1

    sentinel = f"A3A-PROOF-{int(time.time())}"
    posted = _call("chat.postMessage", token, {"channel": channel, "text": sentinel})
    if not posted.get("ok"):
        print(f"chat.postMessage failed: {posted.get('error')}", file=sys.stderr)
        return 1
    channel_id = posted["channel"]
    print(f"posted to {channel} (id {channel_id})")

    # `oldest` rather than "everything": the read is resumable, which is what
    # makes a 2-second poll cheap instead of re-reading history each time.
    oldest = f"{time.time() - 10:.6f}"
    began, polls = time.time(), 0
    while time.time() - began < 10:
        polls += 1
        got = _call(
            "conversations.history",
            token,
            params={"channel": channel_id, "oldest": oldest, "limit": 20},
        )
        if not got.get("ok"):
            print(f"conversations.history failed: {got.get('error')}", file=sys.stderr)
            return 1
        if any(sentinel in (m.get("text") or "") for m in got.get("messages", [])):
            print(f"read it back after {time.time() - began:.2f}s, on poll {polls}")
            return 0
        time.sleep(POLL_SECONDS)
    print(f"NOT SEEN within 10s after {polls} poll(s)", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
