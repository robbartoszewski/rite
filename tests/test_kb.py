from pathlib import Path
from unittest.mock import patch

from rite_ai.kb import add_file, add_link, list_entries, refresh
from rite_ai.kb.manage import FetchResult, _extract_text


def _setup(tmp_path: Path) -> Path:
    (tmp_path / ".rite" / "kb" / ".cache").mkdir(parents=True)
    return tmp_path


class TestAddFile:
    def test_copies_file_and_registers(self, tmp_path: Path):
        root = _setup(tmp_path)
        src = tmp_path / "standards.md"
        src.write_text("# Standards\n")
        err = add_file(root, src)
        assert err is None
        assert (root / ".rite" / "kb" / "standards.md").exists()
        entries = list_entries(root)
        assert len(entries) == 1
        assert entries[0].name == "standards.md"
        assert entries[0].entry_type == "file"

    def test_rejects_missing_file(self, tmp_path: Path):
        root = _setup(tmp_path)
        err = add_file(root, tmp_path / "nope.md")
        assert err is not None
        assert "not found" in err


class TestAddLink:
    def test_registers_link(self, tmp_path: Path):
        root = _setup(tmp_path)
        err = add_link(root, "https://example.com/guide")
        assert err is None
        entries = list_entries(root)
        assert len(entries) == 1
        assert entries[0].entry_type == "link"
        assert "example.com" in entries[0].source

    def test_full_copy_flag(self, tmp_path: Path):
        root = _setup(tmp_path)
        add_link(root, "https://example.com/spec", full=True)
        entries = list_entries(root)
        assert entries[0].entry_type == "full-copy"


class TestListEntries:
    def test_empty_when_no_index(self, tmp_path: Path):
        root = _setup(tmp_path)
        assert list_entries(root) == []


class TestRefresh:
    @patch("rite_ai.kb.manage._fetch_url")
    def test_fetches_and_caches_links(self, mock_fetch, tmp_path: Path):
        mock_fetch.return_value = FetchResult(
            "<html><body><h1>Guide</h1><p>Content here.</p></body></html>"
        )
        root = _setup(tmp_path)
        add_link(root, "https://example.com/guide")
        report = refresh(root)
        assert len(report.messages) == 1
        assert "cached" in report.messages[0]
        assert report.failures == []
        cache_dir = root / ".rite" / "kb" / ".cache"
        cached_files = list(cache_dir.glob("*.md"))
        assert len(cached_files) == 1
        content = cached_files[0].read_text()
        assert "Content here" in content
        assert "source: https://example.com/guide" in content

    @patch("rite_ai.kb.manage._fetch_url")
    def test_full_copy_stores_raw_content(self, mock_fetch, tmp_path: Path):
        html = "<html><body><p>Full page</p></body></html>"
        mock_fetch.return_value = FetchResult(html)
        root = _setup(tmp_path)
        add_link(root, "https://example.com/spec", full=True)
        report = refresh(root)
        assert len(report.messages) == 1
        cache_dir = root / ".rite" / "kb" / ".cache"
        content = list(cache_dir.glob("*.md"))[0].read_text()
        assert "<body>" in content

    @patch("rite_ai.kb.manage._fetch_url")
    def test_fetch_failure_records_error(self, mock_fetch, tmp_path: Path):
        mock_fetch.return_value = FetchResult(error="connection refused")
        root = _setup(tmp_path)
        add_link(root, "https://example.com/down")
        report = refresh(root)
        assert len(report.messages) == 1
        assert "error" in report.messages[0]
        assert report.failures == report.messages
        cache_dir = root / ".rite" / "kb" / ".cache"
        content = list(cache_dir.glob("*.md"))[0].read_text()
        assert "fetch failed" in content

    def test_skips_file_entries(self, tmp_path: Path):
        root = _setup(tmp_path)
        src = tmp_path / "doc.md"
        src.write_text("x")
        add_file(root, src)
        report = refresh(root)
        assert report.messages == []
        assert report.failures == []


class TestExtractText:
    def test_strips_tags(self):
        html = "<p>Hello <b>world</b></p>"
        assert "Hello" in _extract_text(html)
        assert "<p>" not in _extract_text(html)

    def test_strips_scripts(self):
        html = "<script>var x = 1;</script><p>Content</p>"
        text = _extract_text(html)
        assert "var x" not in text
        assert "Content" in text

    def test_strips_styles(self):
        html = "<style>body{color:red}</style><p>Text</p>"
        text = _extract_text(html)
        assert "color" not in text
        assert "Text" in text
