# Reading list

## Problem

I save articles to read later across three devices and lose track of them. The
existing services either want a subscription, keep the articles on their own
servers, or stop working when the original page disappears. I want the saved
text to be mine, on disk, and readable on a phone on a train with no signal.

## Scope

The first working version:

- A browser extension button that saves the current page.
- A server that fetches the page, extracts the readable text and stores it.
- A web reader that lists what is saved and displays one article at a time.
- Full-text search over saved articles.
- Offline reading on a phone: the reader works from what the browser has
  already cached.

### Non-goals

- No accounts, sharing, or multi-user anything. One person, one library.
- No mobile apps. The reader is a web page.
- No annotation, highlighting or notes in the first version.
- No RSS or newsletter ingestion.
- No recommendation or "related articles" feature. It is a shelf, not a feed.

## Architecture

Three parts and one store.

The **extension** is a browser action that POSTs the current URL to the server
with a shared token. It does not scrape the page itself: pages behind a paywall
render differently for a logged-in browser, and a saved article that differs
from what the user read is worse than a failure to save.

The **server** is a single Python process. On save it queues a fetch, pulls the
page, runs readability extraction, writes the article text as Markdown to disk
and a row to SQLite. Fetching is retried on failure and gives up after an hour,
recording why.

The **reader** is a static web page talking to the server's JSON API. It caches
the article list and article bodies in the browser so that a phone without
signal shows what it last synced.

The **store** is a directory of Markdown files plus a SQLite database holding
metadata and the full-text index. The files are the source of truth: the
database can be rebuilt from them, and a user who abandons this tool keeps a
folder of readable articles.

## Decisions

| D | Decision | Choice | Why |
|---|----------|--------|-----|
| D-1 | Scope of the first version | Save, read, search, offline read; no accounts, apps, annotation or feeds | The point is not losing articles; everything else can wait until that works |
| D-2 | Where articles live | Markdown files on disk, SQLite for metadata and search | Survives the tool; a directory of Markdown is readable with anything |
| D-3 | Who fetches the page | The server, not the extension | One code path, and the extension stays a button rather than a scraper to maintain |
| D-4 | Authentication | A single shared token in a config file | One user; a login system is a feature with no user |
| D-5 | Offline strategy | The reader caches articles in the browser | A phone on a train is the case this is for |
| D-6 | What happens when extraction fails | Save the raw HTML and mark the article as unextracted | A saved article that reads badly beats a save that silently did nothing |

## Open questions

- How long should the server keep an article whose original URL has gone?
  Forever, or is there a cleanup rule? Only I can answer this.
- Should saving be idempotent per URL, or should saving twice keep both copies
  with their fetch dates? Depends on whether I re-save updated pages; I do not
  know yet.
- Which readability library survives real paywalled pages best? Needs trying
  against a handful of sites I actually read.
