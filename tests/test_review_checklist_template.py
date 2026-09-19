"""Proves the shipped default checklist template actually parses — a
markdown edit that silently breaks the parser's expectations (wrong bullet
marker, wrong checkbox syntax) would otherwise only be caught by eye."""

from pathlib import Path

from rite_ai.review.checklist import load_checklist

TEMPLATE_PATH = Path(__file__).parent.parent / "templates" / "review-checklist.md"


def test_default_template_parses_without_error():
    items = load_checklist(TEMPLATE_PATH)
    assert len(items) > 0


def test_default_template_has_baseline_categories():
    items = load_checklist(TEMPLATE_PATH)
    categories = {i.category for i in items}
    assert {"Security", "Correctness", "Style", "Dependencies"} <= categories


def test_default_template_has_verification_category_with_named_lessons():
    """The specific lessons this checklist was designed from — each must
    survive as its own parsed item, not just exist as prose.

    Asserting on a phrase from each line, rather than on a count alone, is
    what makes a line that gets WIDENED stay honest: the proxy line and the
    uncalled-artifact line were both rewritten to cover more than they used
    to, and a count-only test would have gone green on a rewrite that
    dropped the original lesson instead of extending it.
    """
    items = load_checklist(TEMPLATE_PATH)
    verification_items = [i.text for i in items if i.category == "Verification"]
    assert len(verification_items) == 11

    joined = " ".join(verification_items).lower()
    assert "exits non-zero" in joined  # silent failures that exit zero
    assert "delete the code under test" in joined  # tests asserting the unobservable
    assert "ticket or a test that owns it" in joined  # integration seams no ticket owns
    assert "actually measured" in joined  # generated content asserting unverified facts
    assert "proves the module works and nothing else" in joined  # built, uncalled
    assert "the state a new user starts in" in joined  # never run from step 0
    assert "missing or undefined input" in joined  # silently plausible defaults
    assert "verdict is an exit code, read directly" in joined  # piped away
    # a condition that was detectable all along, by a command nobody ran
    assert "a check nobody runs is a check that is not there" in joined
    # a test file the runner never collects, which reads as a pass
    assert "indistinguishable from a" in joined
    # a fixture that inherits the machine's answer instead of stating one
    assert "gives the convenient reply" in joined


def test_the_proxy_line_is_not_scoped_to_tests_alone():
    """It used to read "every assertion in a new or changed test…", and a
    checksum gate — comparing bytes instead of running the thing — walked
    straight past it. A gate and a health check fail the same way a test
    assertion does."""
    items = load_checklist(TEMPLATE_PATH)
    line = next(i.text for i in items if "not a proxy for it" in i.text)
    lowered = line.lower()
    assert "gate" in lowered
    assert "health check" in lowered
    assert "bytes are unchanged" in lowered
    assert "not that it is on path" in lowered


def test_the_uncalled_line_covers_artifacts_not_just_modules():
    """`templates/ci/publish-gate.yml` was carried in the tree with nothing
    referencing it — no Python, no test, no doc — while the module-worded
    version of this line was on the checklist the whole time."""
    items = load_checklist(TEMPLATE_PATH)
    line = next(i.text for i in items if "outside its own test suite" in i.text)
    lowered = line.lower()
    assert "artifact" in lowered
    for kind in ("template", "workflow", "config key"):
        assert kind in lowered, kind


def test_every_verification_line_survives_as_a_whole_item():
    """Each line wraps onto continuation lines. `load_checklist` used to drop
    those silently, handing review agents a truncated item — so every item
    here is asserted to carry more than just its first line's worth of
    text."""
    items = load_checklist(TEMPLATE_PATH)
    verification = [i for i in items if i.category == "Verification"]
    assert verification
    for item in verification:
        assert len(item.text) > 90, item.text


def test_the_readme_quotes_this_file_accurately():
    """The "What rite deliberately doesn't do" section argues against
    coverage thresholds by quoting a checklist line back at the reader. It
    quoted the PRE-widening wording within the same change that widened it.

    That section now lives in `docs/guide.md` — the README was cut to a
    landing page and the non-goals went with the rest of the reference
    material. The quote travelled verbatim, and the property is unchanged:
    wherever the argument is made, it must quote the line the checklist
    actually ships.

    Both files wrap prose at different widths, so the comparison is on
    whitespace-normalised text — a quote that only matches because the line
    breaks happen to fall in the same places is testing the wrapping.
    """
    readme = Path(__file__).parent.parent / "docs" / "guide.md"
    quote = (
        "delete the code under test, or break the property itself, "
        "and confirm the check goes red"
    )
    normalise = lambda p: " ".join(p.read_text().split()).lower()  # noqa: E731
    assert quote in normalise(TEMPLATE_PATH), "the checklist line changed"
    assert quote in normalise(readme), "the guide quotes a stale version of it"
