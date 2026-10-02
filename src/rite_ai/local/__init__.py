"""rite local — Manager tiers backed by local models.

The design is `docs/design/RITE_LOCAL_DESIGN.md`; it is not adopted into SPEC yet
(RL-T2). Nothing here calls an Anthropic API, and **nothing here pushes** — which
is still exactly true and no longer means what it used to.

⚠ **RL-11 as superseded (Robert, 2026-10-02).** A local Manager MAY hold
`integrate`, so an all-Ollama fleet integrates its own work with no Claude
session and no person in the path. What it does not do is run `git push`: it
posts the same two-value request a Claude Manager posts
(`publishing/requests.py`), and rite validates it and pushes on the host
(`publishing/deliver.py`). So this package still contains no push, and the
sentence above is a property of this directory rather than a limit on local
engines. `docs/design/OL_WHY_A_LOCAL_MANAGER_CANNOT_PUSH.md` has the argument
and the §5.1.1 wording.
"""
