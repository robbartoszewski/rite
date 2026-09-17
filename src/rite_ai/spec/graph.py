"""The reference graph between spec units, and what each unit is to it.

An edge `a -> b` means reading `a` may need `b`. Two kinds:

* **citations** in `a`'s own lines: `§5.3.3` to that numbered section, and
  `D-31` to that decision row. `/spec` specs have no section numbers, so for them
  D-numbers are the only citations there are;
* **structure**: every unit depends on the sections it sits inside — except the
  document's title, which contains nothing but the document. Counting it made
  the title the top hub of rite's own spec, cited 187 times for no information.

A section is not given edges to the decision rows inside it. The register's
text names every row it holds, and counting that as the register citing its own
rows would make every register look like an index of decisions rather than of
what the rows themselves cite.

**Not every reference is a dependency**, and three kinds are told apart by the
shape of the graph alone:

* a **hub** is cited by many and is small — rite's §2 *Roles* is five lines and
  cited 29 times. Hubs are pinned: always loaded, never traversed into. Pinning
  the top eight took median transitive closure on rite's spec from 70.5% of the
  document to 10.3%;
* an **index** cites many — a decisions register, a revision history, a command
  reference. Judged by citations only: sitting inside sections is not citing
  them. Nobody's ticket is "implement the revision history", so an index is
  neither traversed nor a retrieval target. A register *section* is an index; the
  decision rows inside it are ordinary units, which is what keeps `D-3`
  resolvable;
* everything else is **ordinary**.

**A known false positive.** Out-degree cannot tell navigation from content
that cites a lot. On rite's spec, §8.3 (`config.yaml`, 16 citations in 99
lines) classifies as an index beside §9.1 (Command reference, 24 in 147), and
lines per citation does not separate them either — 6.2 against 6.1. So §8.3 is
excluded as a retrieval target. That is measured, not fixed here: a Worker
that needs it falls back to the whole spec, and the insufficiency rate is what
shows it.

A hub needs at least `hub_min_in` citations as well as a top rank, so a sparse
spec does not pin whatever happens to rank highest. A sparse graph is the
failure this module cannot see — few written references look like small slices
— and the insufficiency rate (`telemetry`) is what catches it.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from rite_ai.config.models import SpecConfig
from rite_ai.spec.units import ITEM, SECTION, Parsed, Unit

HUB = "hub"
INDEX = "index"
ORDINARY = "ordinary"

DEFAULT_PIN_COUNT = SpecConfig().pin_count
DEFAULT_HUB_MIN_IN = 3
DEFAULT_INDEX_MIN_OUT = 15

# The same section-citation form `tests/test_spec_citations.py` checks.
_SECTION_CITATION = re.compile(r"§\s*(\d+(?:\.\d+)*)")
_DECISION_CITATION = re.compile(r"(?<![\w-])(D-\d+)\b")


@dataclass(frozen=True)
class Graph:
    units: dict[str, Unit]
    edges: dict[str, frozenset[str]]  # unit -> units it depends on
    citations: dict[str, frozenset[str]]  # the part of `edges` that is cited
    dangling: dict[str, frozenset[str]]  # unit -> cited ids that are not units

    def in_degree(self) -> Counter:
        counts: Counter = Counter({uid: 0 for uid in self.units})
        for targets in self.edges.values():
            counts.update(targets)
        return counts

    def out_degree(self) -> dict[str, int]:
        return {uid: len(targets) for uid, targets in self.edges.items()}

    def citation_count(self) -> dict[str, int]:
        return {uid: len(cited) for uid, cited in self.citations.items()}


def _titles(units: list[Unit]) -> set[str]:
    """The document title of each file: its only level-1 section, when that is
    the file's first section."""
    titles = set()
    for source in {u.source for u in units}:
        sections = [u for u in units if u.source == source and u.kind == SECTION]
        level_ones = [u for u in sections if u.level == 1]
        if len(level_ones) == 1 and sections and sections[0] is level_ones[0]:
            titles.add(level_ones[0].id)
    return titles


def _ancestors(unit: Unit, units: dict[str, Unit], titles: set[str]) -> list[str]:
    chain, parent = [], unit.parent
    while parent is not None and parent in units and parent not in chain:
        if parent not in titles:
            chain.append(parent)
        parent = units[parent].parent
    return chain


def build_graph(parsed: Parsed) -> Graph:
    units = {u.id: u for u in parsed.units}
    titles = _titles(parsed.units)
    ancestors = {uid: _ancestors(u, units, titles) for uid, u in units.items()}
    edges: dict[str, frozenset[str]] = {}
    citations: dict[str, frozenset[str]] = {}
    # A project's own items (`spec.extra_units`) are cited by their ids, whatever
    # shape those have; longest first so `REQ-14` is not read as `REQ-1`.
    item_ids = sorted(
        (u.id for u in parsed.units if u.kind == ITEM), key=len, reverse=True
    )
    item_citation = (
        re.compile(r"(?<![\w-])(" + "|".join(map(re.escape, item_ids)) + r")(?![\w-])")
        if item_ids
        else None
    )
    dangling: dict[str, frozenset[str]] = {}
    for uid, unit in units.items():
        body = "\n".join(parsed.lines.get(unit.source, [])[unit.start - 1 : unit.end])
        cited = {m.group(1) for m in _SECTION_CITATION.finditer(body)}
        cited |= {m.group(1) for m in _DECISION_CITATION.finditer(body)}
        if item_citation is not None:
            cited |= {m.group(1) for m in item_citation.finditer(body)}
        cited.discard(uid)
        # A section does not cite what sits inside it: see the module docstring.
        inside = (
            {other for other in units if uid in ancestors[other]}
            if unit.kind == SECTION
            else set()
        )
        found = {c for c in cited if c in units and c not in inside}
        missing = {c for c in cited if c not in units}
        citations[uid] = frozenset(found)
        edges[uid] = frozenset(found | set(ancestors[uid]))
        if missing:
            dangling[uid] = frozenset(missing)
    return Graph(units, edges, citations, dangling)


def classify(
    graph: Graph,
    *,
    pin_count: int = DEFAULT_PIN_COUNT,
    hub_min_in: int = DEFAULT_HUB_MIN_IN,
    index_min_out: int = DEFAULT_INDEX_MIN_OUT,
) -> dict[str, str]:
    """HUB, INDEX or ORDINARY for every unit.

    Indexes first, by out-degree, and they cannot also be hubs: a register is
    cited by every row inside it, and pinning the whole register into every slice
    would be the monolith again.
    """
    cites = graph.citation_count()
    kinds = {
        uid: INDEX if cites[uid] >= index_min_out else ORDINARY for uid in graph.units
    }
    ranked = sorted(
        (uid for uid in graph.units if kinds[uid] != INDEX),
        key=lambda uid: (-graph.in_degree()[uid], graph.units[uid].start, uid),
    )
    indeg = graph.in_degree()
    for uid in ranked[:pin_count]:
        if indeg[uid] >= hub_min_in:
            kinds[uid] = HUB
    return kinds
