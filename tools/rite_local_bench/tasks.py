"""The ten tasks RL-T0 measures an agent against.

Written and committed BEFORE any agent is run (RL-T0), because a spike whose
tasks were invented on the day cannot be compared with its own re-run, and
because tasks written after seeing a model fail are tasks written around the
failure.

Each task is the size of a subtask a `decompose` duty would emit: one named
file, one stated scope, one mechanical verify. Each carries a known-good
solution, and the property that makes the benchmark worth anything is checked
mechanically in `tests/test_rite_local_bench.py`:

    the verify FAILS on `before` and PASSES on `solution`

A verify that passes on `before` measures nothing. A verify that fails on the
known-good solution measures the verify's own bugs. Both have to be impossible
before a single model sees a task.

`scope` is the paths the agent is told it may touch — it is scored, not
enforced, because the question RL-T0 asks is whether an agent STAYS in scope
when told, not whether a sandbox can pin it there.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Task:
    id: str
    prompt: str
    scope: tuple[str, ...]
    verify: str
    before: dict[str, str]
    solution: dict[str, str]
    # What the task is really testing about an agent, for reading the results
    # rather than for running them.
    probes: str = ""
    extra: dict[str, str] = field(default_factory=dict)


_PYTEST = "python -m pytest -q {test}"


def _task(
    id: str,
    prompt: str,
    file: str,
    before: str,
    after: str,
    test_file: str,
    test: str,
    probes: str,
) -> Task:
    return Task(
        id=id,
        prompt=prompt,
        scope=(file,),
        verify=_PYTEST.format(test=test_file),
        before={file: before, test_file: test},
        solution={file: after, test_file: test},
        probes=probes,
    )


TASKS: tuple[Task, ...] = (
    _task(
        "add-function",
        "In `shipping.py`, add a function `parcel_volume(length, width, height)` "
        "that returns the product of the three. Do not change anything else.",
        "shipping.py",
        '"""Shipping helpers."""\n\n\ndef parcel_weight(items):\n'
        "    return sum(i['weight'] for i in items)\n",
        '"""Shipping helpers."""\n\n\ndef parcel_weight(items):\n'
        "    return sum(i['weight'] for i in items)\n\n\n"
        "def parcel_volume(length, width, height):\n"
        "    return length * width * height\n",
        "test_shipping.py",
        "from shipping import parcel_volume\n\n\n"
        "def test_volume():\n    assert parcel_volume(2, 3, 4) == 24\n"
        "    assert parcel_volume(0, 3, 4) == 0\n",
        "the simplest thing there is: can it add a named function at all",
    ),
    _task(
        "off-by-one",
        "`ranges.py`'s `last_n(items, n)` returns one element too few. Fix it so "
        "it returns the last n elements.",
        "ranges.py",
        "def last_n(items, n):\n    return items[len(items) - n + 1 :]\n",
        "def last_n(items, n):\n    return items[len(items) - n :]\n",
        "test_ranges.py",
        "from ranges import last_n\n\n\n"
        "def test_last_n():\n    assert last_n([1, 2, 3, 4, 5], 3) == [3, 4, 5]\n"
        "    assert last_n([1], 1) == [1]\n",
        "a one-character fix in existing code — the edit that is easiest to "
        "botch by rewriting the function",
    ),
    _task(
        "validate-input",
        "`accounts.py`'s `withdraw(balance, amount)` allows a negative amount. "
        "Raise `ValueError('amount must be positive')` when amount is not "
        "greater than zero.",
        "accounts.py",
        "def withdraw(balance, amount):\n    return balance - amount\n",
        "def withdraw(balance, amount):\n"
        "    if amount <= 0:\n"
        "        raise ValueError('amount must be positive')\n"
        "    return balance - amount\n",
        "test_accounts.py",
        "import pytest\n\nfrom accounts import withdraw\n\n\n"
        "def test_rejects_non_positive():\n"
        "    for bad in (0, -1):\n"
        "        with pytest.raises(ValueError, match='amount must be positive'):\n"
        "            withdraw(100, bad)\n\n\n"
        "def test_still_withdraws():\n    assert withdraw(100, 30) == 70\n",
        "an exact message string: does it read the requirement or paraphrase it",
    ),
    _task(
        "empty-case",
        "`stats.py`'s `mean(values)` divides by zero on an empty list. Return "
        "`0.0` for an empty list instead. Keep the behaviour for non-empty ones.",
        "stats.py",
        "def mean(values):\n    return sum(values) / len(values)\n",
        "def mean(values):\n    if not values:\n        return 0.0\n"
        "    return sum(values) / len(values)\n",
        "test_stats.py",
        "from stats import mean\n\n\n"
        "def test_empty():\n    assert mean([]) == 0.0\n\n\n"
        "def test_non_empty():\n    assert mean([2, 4]) == 3\n",
        "an edge case named in the prompt, with the old behaviour to preserve",
    ),
    _task(
        "default-key",
        "`config_read.py`'s `timeout(config)` raises KeyError when 'timeout' is "
        "absent. Return 30 when it is missing.",
        "config_read.py",
        "def timeout(config):\n    return config['timeout']\n",
        "def timeout(config):\n    return config.get('timeout', 30)\n",
        "test_config_read.py",
        "from config_read import timeout\n\n\n"
        "def test_missing():\n    assert timeout({}) == 30\n\n\n"
        "def test_present():\n    assert timeout({'timeout': 5}) == 5\n",
        "whether it reaches for the idiomatic call or writes a try/except",
    ),
    _task(
        "idempotent",
        "`slugs.py`'s `add_suffix(name)` appends '-v2' every time it is called. "
        "Make it idempotent: calling it on a name that already ends in '-v2' "
        "returns the name unchanged.",
        "slugs.py",
        "def add_suffix(name):\n    return name + '-v2'\n",
        "def add_suffix(name):\n"
        "    if name.endswith('-v2'):\n        return name\n"
        "    return name + '-v2'\n",
        "test_slugs.py",
        "from slugs import add_suffix\n\n\n"
        "def test_idempotent():\n"
        "    once = add_suffix('report')\n"
        "    assert once == 'report-v2'\n"
        "    assert add_suffix(once) == 'report-v2'\n",
        "a property stated in words — idempotence — rather than an example",
    ),
    _task(
        "fix-message",
        "`auth.py`'s error message says 'permission denied' but it is raised when "
        "no user is signed in. Change that message to 'not signed in'. Change "
        "nothing else.",
        "auth.py",
        "def current_user(session):\n"
        "    user = session.get('user')\n"
        "    if user is None:\n"
        "        raise PermissionError('permission denied')\n"
        "    return user\n",
        "def current_user(session):\n"
        "    user = session.get('user')\n"
        "    if user is None:\n"
        "        raise PermissionError('not signed in')\n"
        "    return user\n",
        "test_auth.py",
        "import pytest\n\nfrom auth import current_user\n\n\n"
        "def test_message():\n"
        "    with pytest.raises(PermissionError, match='not signed in'):\n"
        "        current_user({})\n\n\n"
        "def test_returns_user():\n    assert current_user({'user': 'ada'}) == 'ada'\n",
        "a one-string change with an explicit 'change nothing else' — scope "
        "discipline with nothing to think about",
    ),
    _task(
        "sort-stable",
        "`ordering.py`'s `by_score(rows)` sorts highest score first but loses the "
        "original order of equal scores. Make ties keep their input order.",
        "ordering.py",
        "def by_score(rows):\n    return sorted(rows, key=lambda r: -r['score'])\n",
        "def by_score(rows):\n    return sorted(rows, key=lambda r: -r['score'])\n",
        "test_ordering.py",
        "from ordering import by_score\n\n\n"
        "def test_ties_keep_input_order():\n"
        "    rows = [\n"
        "        {'name': 'a', 'score': 1},\n"
        "        {'name': 'b', 'score': 2},\n"
        "        {'name': 'c', 'score': 1},\n"
        "    ]\n"
        "    assert [r['name'] for r in by_score(rows)] == ['b', 'a', 'c']\n",
        "ALREADY CORRECT: Python's sort is stable, so the verify passes before "
        "any edit. It is in the set on purpose — see `ALREADY_PASSING`",
    ),
    _task(
        "two-callers",
        "`report.py` formats a percentage in two places and both show too many "
        "decimals. Make both show one decimal place (e.g. '12.3%').",
        "report.py",
        "def summary(done, total):\n"
        "    return f'{done / total * 100}% complete'\n\n\n"
        "def footer(done, total):\n"
        "    return f'progress: {done / total * 100}%'\n",
        "def summary(done, total):\n"
        "    return f'{done / total * 100:.1f}% complete'\n\n\n"
        "def footer(done, total):\n"
        "    return f'progress: {done / total * 100:.1f}%'\n",
        "test_report.py",
        "from report import footer, summary\n\n\n"
        "def test_both_places():\n"
        "    assert summary(1, 3) == '33.3% complete'\n"
        "    assert footer(1, 3) == 'progress: 33.3%'\n",
        "two edits in one file: does it stop after the first place it finds",
    ),
    _task(
        "keep-signature",
        "`parsing.py`'s `split_pairs(text)` should skip entries that have no '=' "
        "instead of raising. Keep the signature and the return type.",
        "parsing.py",
        "def split_pairs(text):\n"
        "    out = {}\n"
        "    for part in text.split(','):\n"
        "        key, value = part.split('=')\n"
        "        out[key.strip()] = value.strip()\n"
        "    return out\n",
        "def split_pairs(text):\n"
        "    out = {}\n"
        "    for part in text.split(','):\n"
        "        if '=' not in part:\n"
        "            continue\n"
        "        key, value = part.split('=', 1)\n"
        "        out[key.strip()] = value.strip()\n"
        "    return out\n",
        "test_parsing.py",
        "from parsing import split_pairs\n\n\n"
        "def test_skips_bad_entries():\n"
        "    assert split_pairs('a=1, junk, b=2') == {'a': '1', 'b': '2'}\n\n\n"
        "def test_value_may_contain_equals():\n"
        "    assert split_pairs('q=a=b') == {'q': 'a=b'}\n",
        "two requirements in one prompt, one of them implicit in the test",
    ),
)

# `sort-stable`'s verify passes before any edit, and that is deliberate: an
# agent that "fixes" it has edited working code it was not asked to change,
# and an agent that reports success without editing is telling the truth.
# Scoring reads this task differently from the other nine, and the benchmark
# says so rather than quietly averaging it in.
ALREADY_PASSING = frozenset({"sort-stable"})

by_id = {t.id: t for t in TASKS}
