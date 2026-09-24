"""Designate a session that really is this project's (C8).

Since C8 a designation is continued only if one of the project's transcripts
states its id — so a test that designates a made-up id and expects it to be
resumed must first give it a transcript, in the directory the suite's
autouse fixture points `RITE_CLAUDE_PROJECTS_DIR` at. Without this a test of
"the provider refused the resume" would be testing "rite refused the
designation" instead, and pass or fail for the wrong reason.
"""

from __future__ import annotations

import json
from pathlib import Path

from rite_ai.managers import designate
from rite_ai.managers.transcripts import project_transcript_dir


def designate_known(root: Path, manager: str, session_id: str) -> None:
    where = project_transcript_dir(root)
    where.mkdir(parents=True, exist_ok=True)
    (where / f"{session_id}.jsonl").write_text(
        json.dumps({"sessionId": session_id}) + "\n"
    )
    designate(root, manager, session_id)
