"""Regression tests for the KB after fetching real web pages for the
first time.

`rite kb add <url>` registers a link and `rite kb refresh` fetches it into
`.rite/kb/.cache/`. That cache is not decoration: it is what a Claude
session reads to find out what the project's reference material says. Three
things were wrong with what ended up in it, all of them found by fetching
live pages rather than fixtures.
"""

from __future__ import annotations

from pathlib import Path

from rite_ai.kb.manage import _extract_text, _url_to_slug, add_link, list_entries


def _project(tmp_path: Path) -> Path:
    (tmp_path / ".rite").mkdir()
    return tmp_path


class TestEntitiesAreDecoded:
    """Measured on a live fetch of PEP 8: `&lt;`, `&gt;` and `&raquo;`
    survived into the cache, so every code sample using a comparison
    operator read wrong to whatever opened the file. A page of reference
    documentation is mostly code, which is the whole reason to cache
    one."""

    def test_comparison_operators_survive_as_operators(self):
        assert _extract_text("<p>if a &lt; b and b &gt; c:</p>") == (
            "if a < b and b > c:"
        )

    def test_a_non_breaking_space_becomes_a_space(self):
        """The nastiest of them: `&nbsp;` is not whitespace to the
        collapse step, so an undecoded one welds two words together."""
        assert _extract_text("<p>rite&nbsp;init</p>") == "rite init"

    def test_numeric_entities_too(self):
        assert _extract_text("<p>don&#39;t &amp; won&#39;t</p>") == "don't & won't"

    def test_an_entity_that_decodes_to_a_tag_is_not_then_eaten(self):
        """Decoding runs AFTER tags are stripped. The other order turns
        an escaped code sample into a stripped tag and deletes it."""
        assert _extract_text("<p>&lt;script&gt;alert(1)&lt;/script&gt;</p>") == (
            "<script>alert(1)</script>"
        )

    def test_script_and_style_bodies_are_still_dropped(self):
        html = "<style>p{color:red}</style><script>var x=1&lt;2</script><p>hi</p>"
        assert _extract_text(html) == "hi"


class TestCacheFilenamesCannotCollide:
    """The filename was the URL with punctuation replaced, truncated to
    80 characters. Documentation URLs run long past that."""

    def test_two_urls_differing_past_the_truncation_get_different_files(self):
        base = (
            "https://docs.example.com/en/latest/reference/configuration/"
            "advanced/networking/"
        )
        connect = base + "timeouts-connect.html"
        read = base + "timeouts-read.html"

        # The readable prefix is identical — that was the whole defect:
        # `refresh` wrote both pages into one file and whichever came
        # second silently replaced the other, leaving the KB holding one
        # page under two index rows.
        assert _url_to_slug(connect)[:60] == _url_to_slug(read)[:60]
        assert _url_to_slug(connect) != _url_to_slug(read)

    def test_a_slug_is_still_readable(self):
        slug = _url_to_slug("https://peps.python.org/pep-0008/")
        assert slug.startswith("peps-python-org-pep-0008")

    def test_a_slug_is_stable_for_the_same_url(self):
        url = "https://example.com/a/b/c"
        assert _url_to_slug(url) == _url_to_slug(url)

    def test_a_query_string_still_distinguishes(self):
        assert _url_to_slug("https://x.com/a") != _url_to_slug("https://x.com/a?v=2")

    def test_the_filename_stays_a_sane_length(self):
        long_url = "https://example.com/" + "segment/" * 60
        assert len(_url_to_slug(long_url)) <= 81


class TestTheIndexStopsSayingNotYetFetched:
    """`add_link` writes "not yet fetched — run `rite kb refresh`" and
    nothing ever changed it. Measured after a real refresh that fetched
    two pages and failed on two more: all four rows still read "not yet
    fetched", so the index — the file a session reads to find out what
    the KB holds — said nothing had been fetched, and gave a successful
    entry and a failed one identical text.

    `_set_index_notes` is imported inside each test rather than at module
    scope so the entity and filename tests above still COLLECT against a
    build that predates it."""

    def test_a_successful_fetch_rewrites_the_row(self, tmp_path: Path):
        from rite_ai.kb.manage import _set_index_notes

        root = _project(tmp_path)
        add_link(root, "https://example.com/")
        index = root / ".rite" / "kb" / "INDEX.md"

        _set_index_notes(index, "https://example.com/", "fetched 2026-09-10 → x.md")

        [entry] = list_entries(root)
        assert entry.notes == "fetched 2026-09-10 → x.md"
        assert "not yet fetched" not in index.read_text()

    def test_a_failed_fetch_reads_differently_from_a_successful_one(
        self, tmp_path: Path
    ):
        from rite_ai.kb.manage import _set_index_notes

        root = _project(tmp_path)
        add_link(root, "https://good.example/")
        add_link(root, "https://bad.example/")
        index = root / ".rite" / "kb" / "INDEX.md"

        _set_index_notes(index, "https://good.example/", "fetched 2026-09-10 → a.md")
        _set_index_notes(index, "https://bad.example/", "fetch FAILED: HTTP 404")

        notes = {e.source: e.notes for e in list_entries(root)}
        assert notes["https://good.example/"] != notes["https://bad.example/"]
        assert "FAILED" in notes["https://bad.example/"]

    def test_only_the_matching_row_changes(self, tmp_path: Path):
        from rite_ai.kb.manage import _set_index_notes

        root = _project(tmp_path)
        add_link(root, "https://a.example/")
        add_link(root, "https://b.example/")
        index = root / ".rite" / "kb" / "INDEX.md"

        _set_index_notes(index, "https://a.example/", "touched")

        notes = {e.source: e.notes for e in list_entries(root)}
        assert notes["https://a.example/"] == "touched"
        assert "not yet fetched" in notes["https://b.example/"]

    def test_the_table_survives_the_rewrite(self, tmp_path: Path):
        from rite_ai.kb.manage import _set_index_notes

        root = _project(tmp_path)
        add_link(root, "https://a.example/")
        index = root / ".rite" / "kb" / "INDEX.md"

        _set_index_notes(index, "https://a.example/", "touched")

        text = index.read_text()
        assert "| Entry | Type | Source | Notes |" in text
        assert "|-------|------|--------|-------|" in text
        assert len(list_entries(root)) == 1

    def test_an_unknown_source_is_a_no_op(self, tmp_path: Path):
        from rite_ai.kb.manage import _set_index_notes

        root = _project(tmp_path)
        add_link(root, "https://a.example/")
        index = root / ".rite" / "kb" / "INDEX.md"
        before = index.read_text()

        _set_index_notes(index, "https://nobody.example/", "touched")

        assert index.read_text() == before


class TestTheCacheDoesNotAccumulateOrphans:
    """A link removed from `INDEX.md` left its cache file behind forever,
    and putting a digest in the filename orphaned every file cached
    before that change — so an upgrade would have left the old copy of
    each page sitting beside the new one, indistinguishable to anything
    reading the directory."""

    def _entry(self, root: Path, url: str) -> None:
        add_link(root, url)

    def test_a_file_no_entry_refers_to_is_removed(self, tmp_path: Path):
        from rite_ai.kb.manage import _cache_dir, _prune_cache, _url_to_slug

        root = _project(tmp_path)
        self._entry(root, "https://a.example/")
        cache = _cache_dir(root)
        keep = cache / f"{_url_to_slug('https://a.example/')}.md"
        keep.write_text("current")
        (cache / "left-over-from-an-older-slug.md").write_text("orphan")

        messages = _prune_cache(cache, {keep.name})

        assert keep.is_file()
        assert not (cache / "left-over-from-an-older-slug.md").exists()
        assert "pruned 1 cache file(s)" in messages[0]

    def test_nothing_expected_means_nothing_is_pruned(self, tmp_path: Path):
        """`list_entries` returns [] for an index that is missing OR will
        not parse, and "delete the whole cache" is the wrong reading of
        "I could not read the index" — the same distinction
        `rite_ai.state` draws between absent and unreadable."""
        from rite_ai.kb.manage import _cache_dir, _prune_cache

        root = _project(tmp_path)
        cache = _cache_dir(root)
        (cache / "something.md").write_text("keep me")

        assert _prune_cache(cache, set()) == []
        assert (cache / "something.md").is_file()

    def test_a_clean_cache_reports_nothing(self, tmp_path: Path):
        from rite_ai.kb.manage import _cache_dir, _prune_cache

        root = _project(tmp_path)
        cache = _cache_dir(root)
        (cache / "wanted.md").write_text("x")

        assert _prune_cache(cache, {"wanted.md"}) == []
