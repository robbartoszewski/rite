"""Phrases commonly used in prompt injection: REPORTED, never blocked (N2, D-97).

⚠ **WHAT THIS IS NOT.** It does not vet ticket text, and nothing may say that
it sanitised, cleaned or checked anything. A match means only that the text
contains a phrase commonly used to address a model, and a clean scan means
only that no such phrase was found. The evaluation behind D-97 measured **8
of 8 agent-directed attacks passing**: `curl … | bash` in a setup step,
"paste `.env` into a comment", "add this SSH key". They read as ordinary
requirements and no phrase list catches them (SPEC §6.6.3).

**Reporting, not blocking, is the whole design.** In blocking mode the same
evaluation quarantined 9 of 18 ordinary tickets. Reported, a false positive
costs one line in a standup, and a true one is free signal. Nothing here
changes, withholds or delays the text it scans.

**Where the phrases come from.** Ported verbatim from Robert's `sanitizer`
library (BentoraAI, MIT), `src/sanitizer/signatures.py` at `5e63afb`: its
high-precision overt-instruction layer (L3), English and Polish. That layer
covers instruction override, system-prompt extraction, persona and
jailbreak, chat-role tokens, exfiltration of the prompt, and task
redirection. **Left out:** `L3.exfil_image_beacon`, which matches any
Markdown image with an http link. That is an ordinary thing in a ticket (a
screenshot), so it would mostly report legitimate text. ⚠ **The rates D-97
cites (6 of 9 model-directed attacks caught, 9 of 18 ordinary tickets
flagged) were measured on the sanitizer as a whole**, with its
de-obfuscation layers, which this does not port. rite's own measurements of
this list are in the N2 commit and the plan row. Do not quote the
sanitizer's rates as this scan's.

Scanned: the text as N1 NORMALISED it, so a phrase hidden in tag characters
is found in its decoded form.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_SIGNATURES: tuple[tuple[str, str], ...] = (
    (
        "L3.override_en",
        "\\bignore\\s+(?:all\\s+|any\\s+|the\\s+|these\\s+)*(?:previous|prior|above|preceding|earlier|prior|foregoing|system)\\s+instructions?\\b",
    ),
    (
        "L3.override_en2",
        "\\bdisregard\\s+(?:all\\s+|the\\s+|any\\s+)?(?:previous|prior|above|system)\\s+(?:instructions?|prompts?|messages?)\\b",
    ),
    (
        "L3.override_en3",
        "\\bforget\\s+(?:everything|all|what)\\b.{0,80}\\b(?:before|above|told|said|instructions?)\\b",
    ),
    (
        "L3.override_en4",
        "\\b(?:override|overrule|bypass)\\s+(?:your\\s+|the\\s+)?(?:previous\\s+)?(?:instructions?|directives?|rules?|guardrails?|safety)\\b",
    ),
    ("L3.override_en5", "\\bnew\\s+instructions?\\s*:"),
    (
        "L3.override_en6",
        "\\bfrom\\s+now\\s+on,?\\s+(?:you\\s+(?:are|will|must)|act\\s+as|ignore|respond)\\b",
    ),
    (
        "L3.persona_en",
        "\\byou\\s+are\\s+now\\b.{0,80}\\b(?:developer\\s+mode|dan|jailbroken|unrestricted|the\\s+system)\\b",
    ),
    (
        "L3.persona_en2",
        "\\bact\\s+as\\s+(?:an?\\s+|the\\s+)?(?:unrestricted|jailbroken|dan|evil|developer)\\b",
    ),
    (
        "L3.extract_en",
        "\\b(?:reveal|print|repeat|show|output|leak)\\b.{0,80}\\b(?:system\\s+prompt|your\\s+(?:instructions|prompt)|the\\s+text\\s+above)\\b",
    ),
    (
        "L3.override_pl",
        "\\bzignoruj(?:\\w*)?\\b.{0,80}\\b(?:instrukcj|polece[nń]|wytyczn|wiadomo[śs]ci\\s+systemow|prompt\\w*\\s+systemow)",
    ),
    (
        "L3.override_pl2",
        "\\b(?:pomi[nń]|nie\\s+bierz\\s+pod\\s+uwagę|nie\\s+zważaj\\s+na)\\b.{0,80}\\b(?:instrukcj|polece[nń]|wytyczn|wiadomo[śs]ci\\s+systemow|prompt\\w*\\s+systemow)",
    ),
    (
        "L3.override_pl3",
        "\\b(?:zapomnij|odrzuć)\\b.{0,80}\\b(?:instrukcj|polece[nń]|wytyczn|wiadomo[śs]ci\\s+systemow|prompt\\w*\\s+systemow|wszystk\\w+,?\\s+co\\s+(?:by[łl]o|powiedziano|napisano|us[łl]ysza[łl]e[śs]|ci\\s+przekazano))",
    ),
    ("L3.override_pl4", "\\bnowe\\s+instrukcje\\s*:"),
    (
        "L3.extract_pl",
        "\\b(?:ujawnij|poka[żz]|wypisz|powtórz)\\b.{0,80}\\b(?:instrukcj|prompt|polece[nń]\\s+systemow|tekst\\s+powyżej)",
    ),
    (
        "L3.persona_pl",
        "\\bjesteś\\s+teraz\\b.{0,80}\\b(?:trybie|asystentem\\s+bez|systemem|developer)",
    ),
    (
        "L3.chatml_token",
        "<\\|(?:im_start|im_end|system|user|assistant|endoftext|end_of_turn|start_of_turn|eot_id|start_header_id|end_header_id)\\|>",
    ),
    (
        "L3.instruct_token",
        "\\[/?INST\\]|<</?SYS>>|<\\|/?s\\|>|</?(?:start_of_turn|end_of_turn)>",
    ),
    (
        "L3.alpaca_header",
        "(?:^|\\n)[ \\t]{0,8}###\\s*(?:instruction|system|response|assistant)\\s*:\\s*(?=(?:ignore\\b|disregard\\b|forget\\b|new\\s+instruction|from\\s+now\\b|you\\s+are\\s+now\\b|act\\s+as\\b|respond\\s+only\\b|output\\s+only\\b|do\\s+not\\s+follow\\b))",  # noqa: E501 - verbatim from the sanitizer
    ),
    (
        "L3.role_marker",
        "(?:^|\\n)[ \\t]{0,8}(?:system|assistant)\\s*:\\s*(?=(?:ignore\\b|disregard\\b|forget\\b|new\\s+instruction|from\\s+now\\b|you\\s+are\\s+now\\b|act\\s+as\\b|respond\\s+only\\b|output\\s+only\\b|do\\s+not\\s+follow\\b))",  # noqa: E501 - verbatim from the sanitizer
    ),
    (
        "L3.extract_sysprompt_en",
        "\\b(?:what\\s+(?:is|are|were)|repeat|show\\s+me|tell\\s+me|reveal|print|summar[iy][sz]e)\\b.{0,40}\\byour\\s+system\\s+prompt\\b",
    ),
    (
        "L3.extract_above_en",
        "\\b(?:repeat|print|output|show|reveal|display)\\b.{0,30}\\bthe\\s+(?:text|words|instructions?|content)\\b.{0,20}\\b(?:at\\s+the\\s+(?:start|beginning|top)|above|before\\s+this\\s+(?:message|document|line))\\b",
    ),
    (
        "L3.extract_initial_en",
        "\\bwhat\\s+(?:are|were)\\s+your\\s+(?:initial\\s+|original\\s+|first\\s+)?system\\s+(?:instructions|rules|guidelines|directives|prompt)\\b",
    ),
    (
        "L3.extract_prompt_en",
        "\\b(?:what\\s+(?:is|are|was|were)|repeat|show\\s+me|tell\\s+me|reveal|print|output|display)\\b.{0,40}\\byour\\s+(?:initial|original|first|system)\\s+prompt\\b",
    ),
    (
        "L3.extract_sysprompt_pl",
        "\\b(?:co\\s+(?:jest|było)|jaki\\s+jest|podaj|wyświetl|streść)\\b.{0,40}\\b(?:tw[oó]j\\s+)?prompt\\s+systemow\\w+\\b",
    ),
    (
        "L3.jailbreak_dan_en",
        "\\b(?:do\\s+anything\\s+now|dan\\s+mode|developer\\s+mode\\s+(?:enabled|on|activated)|jailbreak\\s+mode|god\\s+mode\\s+enabled)\\b",
    ),
    (
        "L3.jailbreak_dan_en2",
        "\\b(?:enable|activate|turn\\s+on|enter|switch\\s+to)\\s+(?:the\\s+)?(?:developer|dan|god|jailbreak|unrestricted)\\s+mode\\b",
    ),
    (
        "L3.jailbreak_norestrict_en",
        "\\byou\\s+(?:have\\s+no|are\\s+no\\s+longer\\s+bound\\s+by|no\\s+longer\\s+have\\s+any)\\s+(?:restrictions?|filters?|limits?|guidelines?|safeguards?|censorship|boundaries|content\\s+polic\\w+)\\b",
    ),
    (
        "L3.jailbreak_persona_en",
        "\\b(?:pretend|imagine|suppose|act\\s+as\\s+if)\\b.{0,30}\\byou\\s+(?:are|have\\s+no|can\\s+do|were)\\b.{0,40}\\b(?:unrestricted|jailbroken|no\\s+(?:rules|limits|filters?)|anything|evil|without\\s+(?:rules|limits|restrictions))\\b",
    ),
    (
        "L3.override_safety_en",
        "\\b(?:ignore|disregard|bypass|forget|turn\\s+off|disable|override)\\b.{0,30}\\byour\\s+(?:safety|content|security|ethical|moral)\\s+(?:polic\\w+|guidelines?|filters?|rules?|guardrails?|restrictions?)\\b",
    ),
    (
        "L3.jailbreak_dan_pl",
        "\\b(?:tryb(?:ie)?\\s+dewelopera|tryb\\s+bez\\s+ograniczeń|nieograniczony\\s+tryb|zr[oó]b\\s+wszystko\\s+teraz)\\b",
    ),
    (
        "L3.jailbreak_norestrict_pl",
        "\\bnie\\s+masz\\s+(?:żadnych\\s+|ju[żz]\\s+)?(?:filtr[oó]w|zabezpiecze[nń]|cenzury|ogranicze[nń]\\s+ani\\s+zasad)\\b",
    ),
    (
        "L3.jailbreak_persona_pl",
        "\\b(?:udawaj|wyobra[źz]\\s+sobie|za[łl][oó][żz])\\b.{0,30}\\b[żz]e\\s+jesteś\\b.{0,40}\\b(?:bez\\s+ogranicze[nń]|nieograniczony|z[łl]y|jailbroken|bez\\s+zasad)\\b",
    ),
    (
        "L3.exfil_send_en",
        "\\b(?:send|e-?mail|post|upload|transmit|exfiltrate|forward|leak|copy|report)\\b.{0,50}\\b(?:your\\s+system\\s+prompt|the\\s+system\\s+prompt|your\\s+(?:system\\s+)?configuration|everything\\s+you\\s+were\\s+(?:told|given))\\b",
    ),
    (
        "L3.exfil_include_en",
        "\\b(?:include|embed|append|add|insert|put)\\b.{0,40}\\b(?:the\\s+following\\s+)?(?:link|url|image|markdown\\s+image|tracking\\s+pixel)\\b.{0,30}\\b(?:in|to|into)\\s+your\\s+(?:response|answer|reply|output|summary)\\b",
    ),
    (
        "L3.exfil_send_pl",
        "\\b(?:prześlij|wyślij|wyeksportuj|opublikuj|przeka[żz]|skopiuj)\\b.{0,50}\\b(?:sw[oó]j\\s+prompt\\s+systemow\\w+|prompt\\s+systemow\\w+|(?:swoje\\s+)?instrukcje\\s+systemowe|swoj[ąa]\\s+konfiguracj\\w+)\\b",
    ),
    (
        "L3.exfil_include_pl",
        "\\b(?:umie[śs][ćc]|dodaj|wstaw|do[łl][ąa]cz)\\b(?:.{0,40}\\b(?:link|url|obraz|adres|piksel)\\b.{0,30}\\bw\\s+(?:swojej\\s+)?odpowiedzi|.{0,40}\\bw\\s+(?:swojej\\s+)?odpowiedzi\\b.{0,30}\\b(?:link|url|obraz|adres|piksel))\\b",
    ),
    (
        "L3.redirect_must_en",
        "\\byou\\s+must\\s+(?:now|instead)\\s+(?:ignore|disregard|forget|act\\s+as|pretend|output)\\b",
    ),
    (
        "L3.redirect_task_en",
        "\\byour\\s+(?:new|real|actual|true|only|sole|primary)\\s+(?:task|job|goal|instruction|role|purpose|objective|directive)\\s+is\\s+now\\s+to\\b",
    ),
    (
        "L3.redirect_stop_en",
        "\\bstop\\s+(?:being|acting\\s+as|behaving\\s+as|following)\\b.{0,40}\\b(?:assistant|the\\s+ai|your\\s+(?:instructions|rules|role|guidelines))\\b",
    ),
    (
        "L3.redirect_task_pl",
        "\\btwoim\\s+(?:nowym|jedynym|prawdziwym|rzeczywistym)\\s+(?:zadaniem|celem|rol[ąa])\\s+jest\\s+teraz\\b",
    ),
    (
        "L3.redirect_must_pl",
        "\\bmasz\\s+(?:teraz|od\\s+teraz)\\s+(?:ignorować|zignorować|udawać|zapomnieć)\\b",
    ),
    (
        "L3.redirect_stop_pl",
        "\\bprzesta[nń]\\s+(?:być|dzia[łl]ać\\s+jako|zachowywać\\s+się\\s+jak)\\b.{0,40}\\b(?:asystent|sztuczn\\w+\\s+inteligencj|instrukcj|regu[łl])",
    ),
)

_COMPILED = tuple(
    (name, re.compile(pattern, re.IGNORECASE | re.UNICODE))
    for name, pattern in _SIGNATURES
)


@dataclass(frozen=True)
class Phrase:
    signature: str
    """The sanitizer's detector name, so a report can be traced to its rule."""
    text: str
    """The matched words, as they appear."""


def scan(text: str) -> list[Phrase]:
    """Every distinct phrase found, in order of appearance. Never raises."""
    found: dict[str, Phrase] = {}
    for name, pattern in _COMPILED:
        for m in pattern.finditer(text or ""):
            words = " ".join(m.group(0).split())[:120]
            found.setdefault(words.lower(), Phrase(name, words))
    return sorted(
        found.values(), key=lambda p: (text or "").lower().find(p.text.lower())
    )


EVENT = "injection-phrase"

CAVEAT = (
    "- ⚠ This scan finds overt phrases aimed at a model. The evaluation behind "
    "it measured 8 of 8 agent-directed attacks passing any text filter: "
    '`curl … | bash` in a setup step, "paste .env into a comment", "add this '
    'SSH key". So no line here means no such PHRASE was read, not that ticket '
    "text was vetted (SPEC §6.6.3)."
)


def report(root, where: str, text: str) -> list[Phrase]:
    """Scan `text` and RECORD each phrase for the next check-in. Never raises,
    and never changes, withholds or delays the text: reporting is the design.
    `where` names what a reader checks: "ticket 3", "Slack #all-rite …"."""
    found = scan(text)
    if found and root is not None:
        from rite_ai.reporting import events

        for phrase in found:
            events.record(
                root, EVENT, where=where, phrase=phrase.text, signature=phrase.signature
            )
    return found


def standup_lines(recorded: list[dict]) -> list[str]:
    """The standup's section for this scan: one line per distinct phrase per
    source, then the caveat — ALWAYS, so a clean standup is not read as
    "tickets are vetted"."""
    seen: dict[tuple[str, str], dict] = {}
    for e in recorded:
        if e.get("event") == EVENT:
            key = (str(e.get("where")), str(e.get("phrase")).lower())
            seen.setdefault(key, e)
    lines = [
        "",
        "Phrases commonly used in prompt injection — reported, never blocked:",
    ]
    if seen:
        lines.extend(
            f"- {e.get('where')} contains a phrase commonly used in prompt "
            f"injection: '{e.get('phrase')}' (rule {e.get('signature')}). It "
            "was shown in full; nothing was withheld."
            for e in seen.values()
        )
    else:
        # Anchored like every standup line (K4): the record it looked in.
        lines.append(
            f"- none: `.rite/events.jsonl` has no `{EVENT}` record since then, "
            "for the ticket and Slack text read through rite"
        )
    lines.append(CAVEAT)
    return lines
