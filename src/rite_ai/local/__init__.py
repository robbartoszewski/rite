"""rite local — Manager tiers backed by local models.

The design is `docs/design/RITE_LOCAL_DESIGN.md`; it is not adopted into SPEC yet
(RL-T2). Nothing here calls an Anthropic API, and nothing here pushes: local
engines commit to a local branch and stop, and `integrate` — a claude session
or a person — takes it from there (RL-11, RL-12).
"""
