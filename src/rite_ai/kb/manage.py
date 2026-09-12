"""KB operations — add files/links, list, refresh (SPEC §8.7).

Authored files are committed; link caches live in `.rite/kb/.cache/` and
are gitignored. `refresh` re-fetches all link-type entries.
"""

from __future__ import annotations

import hashlib
import html as html_module
import re
import shutil
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from rite_ai.state import write_atomic


@dataclass
class KbEntry:
    name: str
    entry_type: str  # "file" | "link" | "full-copy"
    source: str
    notes: str = ""


_ROW_RE = re.compile(
    r"^\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|"
)


# `rite init` seeds the table with this row so an empty KB reads as
# deliberately empty rather than broken. It is the display placeholder
# only — `list_entries` has always skipped it.
_PLACEHOLDER_ROW = "_(none yet)_"


def _kb_dir(root: Path) -> Path:
    return root / ".rite" / "kb"


def _cache_dir(root: Path) -> Path:
    d = _kb_dir(root) / ".cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _index_path(root: Path) -> Path:
    return _kb_dir(root) / "INDEX.md"


def _locked_index(root: Path):
    """Exclusion around `kb/INDEX.md`'s read-modify-write.

    The same shape as `context/INDEX.md`, and missed in the same sweep:
    `_append_index_row` and `_set_index_notes` each read the whole file,
    change one row and write it back. `refresh` calls the second once per
    entry, so a single refresh is safe on its own — two callers are not,
    and the index is how a session finds out what the KB holds."""
    from rite_ai.state import locked

    return locked(_index_path(root))


def list_entries(root: Path) -> list[KbEntry]:
    idx = _index_path(root)
    if not idx.is_file():
        return []
    entries: list[KbEntry] = []
    for line in idx.read_text().splitlines():
        if line.lstrip("|").lstrip().startswith("-"):
            continue
        m = _ROW_RE.match(line)
        if not m:
            continue
        name = m.group(1).strip()
        if name in ("Entry", "---", "", _PLACEHOLDER_ROW):
            continue
        entries.append(
            KbEntry(
                name=name,
                entry_type=m.group(2).strip(),
                source=m.group(3).strip(),
                notes=m.group(4).strip(),
            )
        )
    return entries


def add_file(root: Path, file_path: Path) -> str | None:
    """Copy a file into kb/ and register it. Returns None on success."""
    kb = _kb_dir(root)
    kb.mkdir(parents=True, exist_ok=True)

    if not file_path.is_file():
        return f"file not found: {file_path}"

    dest = kb / file_path.name
    shutil.copyfile(file_path, dest)

    with _locked_index(root):
        _append_index_row(
            _index_path(root),
            file_path.name,
            "file",
            "(authored)",
            f"copied from `{file_path}`",
        )
    return None


def add_link(root: Path, url: str, full: bool = False) -> str | None:
    """Register a link in the index. Returns None on success.

    The actual fetch is deferred to `refresh` — registering without
    fetching means `kb add` works offline and fast.
    """
    kb = _kb_dir(root)
    kb.mkdir(parents=True, exist_ok=True)
    _cache_dir(root)

    entry_type = "full-copy" if full else "link"
    notes = "not yet fetched — run `rite kb refresh`"
    if full:
        notes = "full-copy, not yet fetched — run `rite kb refresh`"

    with _locked_index(root):
        _append_index_row(_index_path(root), url, entry_type, url, notes)
    return None


# How much of a document is READ, as distinct from how much is kept. A
# `link` entry keeps the first 8000 characters of extracted text (see
# `refresh`), so reading an entire document to discard nearly all of it is
# not just waste — it is UNBOUNDED waste, on a URL that is whatever
# someone pasted. Measured against a 120 MB HTML page: `httpx.get` pulled
# the whole body into memory and peak RSS reached 1.93 GB, sixteen times
# the page, to produce an 8 KB snapshot. `rite kb refresh` runs
# unattended from a session's routine; a KB holding one large page was
# enough to make it the heaviest thing on the machine.
#
# Generous against real reference documents (the largest page in this
# project's own KB is under 400 KB) and still a ceiling.
MAX_FETCH_BYTES = 5 * 1024 * 1024

# Content types `_extract_text` can actually make text out of. Anything
# else is refused rather than tag-stripped.
#
# Nothing looked at Content-Type at all, so `rite kb add <pdf-url>` —
# about the most ordinary thing anyone wants in a knowledge base —
# reported `cached:` and exited 0 having written the PDF's container
# syntax into the KB as though it were the document:
#
#     # http://.../doc.pdf
#     %PDF-1.4 1 0 obj >endobj 2 0 obj >endobj trailer > %%EOF
#
# A PNG produced 633 bytes of U+FFFD. Both sat in `INDEX.md` looking
# exactly like a good snapshot, for a session to read as reference
# material. An entry that cannot be snapshotted has to SAY so.
# `application/json` and the `+json`/`+xml` structured-suffix family are
# text by construction, and were refused anyway: a JSON link — an API
# schema, an OpenAPI document, a `.well-known` descriptor, all ordinary
# reference material — could never be added, and `rite kb refresh`
# exited 1 on it forever after, so one JSON entry made the whole refresh
# fail every time it ran. The gate is meant to stop BINARY containers
# being written into the KB as though they were prose; JSON is neither
# binary nor a container.
_TEXTUAL_TYPES = (
    "text/",
    "application/json",
    "application/xhtml+xml",
    "application/xml",
)

# RFC 6839 structured syntax suffixes: `application/vnd.api+json`,
# `image/svg+xml`, `application/ld+json`. Matching the suffix rather than
# enumerating vendor types, because the vendor half is unbounded.
_TEXTUAL_SUFFIXES = ("+json", "+xml")


def _unsupported_type(content_type: str) -> str | None:
    """Why this Content-Type cannot be snapshotted, or None if it can.

    A response with no Content-Type at all is allowed through: plenty of
    plain file servers send none, and refusing them would lose real
    pages to fix a problem they do not have."""
    kind = content_type.split(";")[0].strip().lower()
    if not kind:
        return None
    if any(kind.startswith(prefix) for prefix in _TEXTUAL_TYPES):
        return None
    if kind.endswith(_TEXTUAL_SUFFIXES):
        return None
    # Short on purpose: this string is both a terminal line and a cell in
    # `INDEX.md`'s table, and a paragraph in the Notes column makes the
    # whole table unreadable.
    return (
        f"not a text document (Content-Type: {kind}) — download it and "
        "`rite kb add <file>` to keep it as an authored entry"
    )


@dataclass
class FetchResult:
    """`error` is set on failure and `text` is then empty. `capped` means
    the download was stopped at `MAX_FETCH_BYTES` and `text` is a prefix
    of the document — recorded so the cache can say so rather than
    presenting a partial page as a whole one."""

    text: str = ""
    error: str | None = None
    capped: bool = False


def _fetch_url(url: str) -> FetchResult:
    """Fetch a URL's readable bytes, bounded, and only where they are
    readable at all."""
    try:
        import httpx

        with httpx.stream("GET", url, follow_redirects=True, timeout=30.0) as resp:
            if resp.status_code >= 400:
                return FetchResult(error=f"HTTP {resp.status_code}")
            unsupported = _unsupported_type(resp.headers.get("content-type", ""))
            if unsupported:
                return FetchResult(error=unsupported)
            chunks: list[bytes] = []
            total = 0
            capped = False
            for chunk in resp.iter_bytes():
                remaining = MAX_FETCH_BYTES - total
                if len(chunk) >= remaining:
                    chunks.append(chunk[:remaining])
                    capped = True
                    break
                chunks.append(chunk)
                total += len(chunk)
            encoding = getattr(resp, "charset_encoding", None) or "utf-8"
            try:
                text = b"".join(chunks).decode(encoding, errors="replace")
            except LookupError:
                # A charset the server named and this Python does not have.
                text = b"".join(chunks).decode("utf-8", errors="replace")
            return FetchResult(text=text, capped=capped)
    except Exception as e:
        return FetchResult(error=str(e) or e.__class__.__name__)


def _extract_text(html: str) -> str:
    """Extract readable text from HTML. Simple heuristic: strip tags,
    decode entities, collapse whitespace. For proper extraction, a future
    version could use readability or similar — but this covers most
    reference docs.

    Entities are decoded because the cache is READ, by a Claude session
    or a person, and an undecoded one changes what the page says.
    Measured on a real fetch of PEP 8: `&lt;` and `&gt;` survived into
    the cache, so every code sample using a comparison operator read
    wrong. `&nbsp;` is worse — it is not whitespace to the collapse
    below, so it welds two words together instead of separating them.
    Decoding happens AFTER tags are stripped, so an entity that decodes
    into something angle-bracketed cannot then be eaten as a tag."""
    text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.S)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_module.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


@dataclass
class RefreshReport:
    """`messages` is everything to print, in order; `failures` is the
    subset that went wrong.

    `refresh` returned one flat list and `rite kb refresh` printed all of
    it to stdout and exited 0 — so a run that fetched two pages and
    failed on two more was, to anything but a human reading the terminal,
    a complete success. That is the shape this project refuses everywhere
    else: a caller that cannot tell the two apart is a cron entry or a
    session routine that never reports a KB going stale."""

    messages: list[str]
    failures: list[str]


def refresh(root: Path) -> RefreshReport:
    """Re-fetch all link-type entries."""
    entries = list_entries(root)
    cache = _cache_dir(root)
    messages: list[str] = []
    failures: list[str] = []
    expected: set[str] = set()

    for entry in entries:
        if entry.entry_type not in ("link", "full-copy"):
            continue
        slug = _url_to_slug(entry.source)
        cache_path = cache / f"{slug}.md"
        expected.add(cache_path.name)

        fetched = _fetch_url(entry.source)
        if fetched.error:
            err = fetched.error
            write_atomic(
                cache_path,
                f"---\nsource: {entry.source}\n"
                f"fetched: {date.today().isoformat()}\n"
                f"error: {err}\n---\n\n"
                f"# {entry.name}\n\n"
                f"_(fetch failed: {err})_\n",
            )
            with _locked_index(root):
                _set_index_notes(
                    _index_path(root),
                    entry.source,
                    f"fetch FAILED {date.today().isoformat()}: {err}",
                )
            failures.append(f"error: {entry.source} — {err}")
            messages.append(f"error: {entry.source} — {err}")
            continue

        content = fetched.text
        if entry.entry_type == "full-copy":
            body = content
        else:
            body = _extract_text(content)
            if len(body) > 8000:
                body = body[:8000] + "\n\n_(truncated to 8000 chars)_"
        if fetched.capped:
            body += (
                f"\n\n_(the download itself stopped at {MAX_FETCH_BYTES} "
                "bytes — this snapshot is the start of the document, not "
                "all of it)_"
            )

        write_atomic(
            cache_path,
            f"---\nsource: {entry.source}\n"
            f"fetched: {date.today().isoformat()}\n"
            f"type: {entry.entry_type}\n---\n\n"
            f"# {entry.name}\n\n"
            f"{body}\n",
        )
        with _locked_index(root):
            _set_index_notes(
                _index_path(root),
                entry.source,
                f"fetched {date.today().isoformat()} → `.cache/{cache_path.name}`",
            )
        messages.append(f"cached: {entry.source} -> {cache_path.name}")

    messages.extend(_prune_cache(cache, expected))
    return RefreshReport(messages=messages, failures=failures)


def _prune_cache(cache: Path, expected: set[str]) -> list[str]:
    """Remove cache files no current entry maps to.

    A link removed from `INDEX.md` used to leave its cache file behind
    forever, and the change that put a digest in the filename orphaned
    every file cached before it — so an upgrade would have left the old
    copy of each page sitting next to the new one, indistinguishable to
    anything reading the directory.

    Refuses to prune when nothing is expected. `list_entries` returns an
    empty list for an index that is missing or will not parse, and
    "delete the entire cache" is the wrong reading of "I could not read
    the index" — the same distinction `rite_ai.state` draws between an
    absent file and an unreadable one."""
    if not expected:
        return []
    removed = []
    for path in sorted(cache.glob("*.md")):
        if path.name in expected:
            continue
        try:
            path.unlink()
        except OSError:
            continue
        removed.append(path.name)
    if not removed:
        return []
    return [
        f"pruned {len(removed)} cache file(s) no entry refers to: " + ", ".join(removed)
    ]


def _set_index_notes(idx: Path, source: str, notes: str) -> None:
    """Rewrite one index row's Notes column.

    `add_link` writes "not yet fetched — run `rite kb refresh`", and
    nothing ever changed it. Measured: after a `rite kb refresh` that
    fetched two pages and failed on two more, all four rows still read
    "not yet fetched", so the index — the file a session actually reads
    to find out what the KB holds — said nothing had been fetched, and
    gave a successful entry and a failed one identical text.

    Matching is on the Source column, which is the URL and is what makes
    a row unique; the Entry column is a display name and may repeat."""
    if not idx.is_file():
        return
    lines = idx.read_text().splitlines()
    changed = False
    for i, line in enumerate(lines):
        m = _ROW_RE.match(line)
        if not m or m.group(3).strip() != source:
            continue
        lines[i] = (
            f"| {m.group(1).strip()} | {m.group(2).strip()} | "
            f"{m.group(3).strip()} | {notes} |"
        )
        changed = True
    if changed:
        write_atomic(idx, "\n".join(lines) + "\n")


# How much of the URL survives into the filename. The rest of the budget
# goes to the digest below.
_SLUG_CHARS = 72
_SLUG_DIGEST_CHARS = 8


def _url_to_slug(url: str) -> str:
    """A cache filename for a URL: readable prefix, plus a digest of the
    WHOLE url so two long URLs cannot share a file.

    The prefix alone was the filename, truncated to 80 characters, and
    documentation URLs run long past that. Two pages under one deep path
    — `.../networking/timeouts-connect.html` and
    `.../networking/timeouts-read.html` — produce an identical
    79-character prefix, so `refresh` wrote both into one file and
    whichever came second silently replaced the other. The KB then holds
    one page under two index rows, and nothing anywhere says so."""
    slug = re.sub(r"https?://", "", url)
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", slug).strip("-")
    digest = hashlib.sha256(url.encode()).hexdigest()[:_SLUG_DIGEST_CHARS]
    return f"{slug[:_SLUG_CHARS]}-{digest}"


def _append_index_row(
    idx: Path, name: str, entry_type: str, source: str, notes: str
) -> None:
    if not idx.is_file():
        idx.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(
            idx,
            "# Knowledge Base Index\n\n"
            "| Entry | Type | Source | Notes |\n"
            "|-------|------|--------|-------|\n",
        )
    row = f"| {name} | {entry_type} | {source} | {notes} |\n"
    # Drop `rite init`'s placeholder the moment there is a real entry.
    # `list_entries` skips it, so nothing was functionally wrong — but
    # `INDEX.md` is a file people and sessions READ, and it rendered as
    #
    #     | _(none yet)_ |  |  |  |
    #     | https://example.com | link | ... |
    #
    # a table saying it is empty directly above two entries.
    body = "".join(
        line
        for line in idx.read_text().splitlines(keepends=True)
        if _PLACEHOLDER_ROW not in line
    )
    write_atomic(idx, body + row)
