"""The spec digest: a project spec as addressable units, so a Worker loads the
slice its ticket needs instead of the whole document.

This package is the mechanical half — parsing, hashing, the reference graph,
slices, verification — all of it token-free. The judgement half, writing unit
bodies and reviewing them, is the `/spec-digest` command. Each module carries
its own rationale.
"""
