"""JIRA Cloud REST API backend (SPEC §6.2).

Uses httpx for all API calls. Authentication is via API token (Basic Auth
with email + token), retrieved through the credentials store (P1.8).

JIRA's REST API uses different field names and structures than rite's
normalised model. This module handles the translation:

- JIRA "key" (e.g. "PROJ-42") → rite "id"
- JIRA "status.name" → rite "status"
- JIRA "assignee.accountId" → rite "assignee"
- JIRA "labels" → rite "labels" (pass-through, both are string lists)
- JIRA transitions → rite "move" (requires discovering the transition ID
  for a target status name)

The `query` method passes JQL through directly — this is the escape hatch
for JIRA-specific queries that don't fit the normalised filter model.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime

import httpx

from .interface import (
    PAGE_LIMIT,
    BackendError,
    Ticket,
    TicketBackend,
    TicketFilter,
    TicketPage,
)


@dataclass
class JiraConfig:
    site: str
    email: str
    token: str
    project_key: str | None = None


def _parse_jira_datetime(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def adf_to_text(node: object) -> str:
    """Plain text of an Atlassian Document Format value, which is what the
    v3 API returns for a description: one line per block, text runs joined
    within it. A plain string passes through."""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(adf_to_text(n) for n in node)
    if not isinstance(node, dict):
        return ""
    kind = node.get("type")
    if kind == "text":
        return node.get("text", "")
    if kind == "hardBreak":
        return "\n"
    inner = adf_to_text(node.get("content") or [])
    if kind == "listItem":
        return "- " + inner.strip("\n") + "\n"
    if kind in ("paragraph", "heading", "codeBlock", "blockquote"):
        return inner + "\n"
    return inner


def _issue_to_ticket(data: dict) -> Ticket:
    fields = data.get("fields", {})

    assignee_obj = fields.get("assignee") or {}
    assignee = assignee_obj.get("displayName") or assignee_obj.get("accountId") or ""

    labels = fields.get("labels") or []

    status_obj = fields.get("status") or {}
    status = status_obj.get("name", "")

    return Ticket(
        id=data.get("key", ""),
        title=fields.get("summary", ""),
        status=status,
        assignee=assignee,
        labels=labels,
        description=adf_to_text(fields.get("description")).strip(),
        url=data.get("self", ""),
        created_at=_parse_jira_datetime(fields.get("created")),
        updated_at=_parse_jira_datetime(fields.get("updated")),
        metadata=data,
    )


def normalise_site(site: str) -> str:
    """A bare JIRA host from whatever the user put in `ticket_backend.site`.

    SPEC §8.3 documents a bare hostname (`myteam.atlassian.net`), but the
    value a person actually has to hand is the URL in their browser bar.
    Pasting it produced `https://https://myteam.atlassian.net/rest/api/3`
    and a DNS failure that named neither the URL nor the setting — so
    accept the URL, a trailing slash, and a trailing path, and reduce them
    all to the host the API lives on.
    """
    site = site.strip()
    for scheme in ("https://", "http://"):
        if site.lower().startswith(scheme):
            site = site[len(scheme) :]
            break
    return site.split("/", 1)[0].strip().rstrip("/")


class JiraBackend(TicketBackend):
    def __init__(self, config: JiraConfig):
        self.config = config
        self._base = f"https://{normalise_site(config.site)}/rest/api/3"
        token_bytes = f"{config.email}:{config.token}".encode()
        auth_header = base64.b64encode(token_bytes).decode()
        self._client = httpx.Client(
            headers={
                "Authorization": f"Basic {auth_header}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=30.0,
        )
        # None until asked; see `_auth_failed`.
        self._auth_ok: bool | None = None

    def _url(self, path: str) -> str:
        return f"{self._base}{path}"

    _CREDENTIALS_REJECTED = (
        "JIRA rejected the credentials. Check jira_email and jira_token "
        "(`rite credential check jira_token`), and that the token is "
        "authorised for this site."
    )

    def _auth_failed(self) -> bool:
        """Whether this site is refusing our credentials, asked of the one
        endpoint that answers honestly.

        Measured against the real API, JIRA does not report a bad token on
        the calls rite actually makes. A search comes back `200` with zero
        issues, because JQL matches nothing an unauthenticated caller can
        see; a `GET /issue/KEY` for an issue that plainly exists comes back
        `404` for the same reason. So the credential error added for 401
        and 403 fired on neither of the two commonest operations — rite
        reported an empty board and a missing ticket, both untrue and both
        indistinguishable from the ordinary cases.

        `/myself` is the endpoint with nothing to hide behind: it is about
        the caller, not about content permissions, so it returns 401 when
        the credentials are bad and 200 when they are not. Consulted only
        to disambiguate a result that is already suspicious, and the answer
        is remembered for the life of this backend so a loop cannot turn
        into one probe per iteration."""
        if self._auth_ok is None:
            result = self._request("GET", "/myself")
            # Only an outright rejection counts. A transport failure is not
            # evidence either way, and must not get the credentials blamed
            # for a network problem.
            self._auth_ok = not (
                isinstance(result, BackendError)
                and ("→ 401" in result.message or "→ 403" in result.message)
            )
        return not self._auth_ok

    def _clarify(self, error: BackendError, subject: str = "") -> BackendError:
        """Re-explain a write failure that is really a rejected token.

        `read` and `list` already do this; every WRITE path did not, and
        measured against the real API that is the same defect twice. With
        a bad token JIRA answers `PUT /issue/DEF-3` and every other write
        with `404 Issue does not exist or you do not have permission to
        see it` (and `POST /issue` with a 400 blaming the project) — so
        `rite board move`, `label`, `assign`, `link`, `comment` and
        `update` all reported a missing ticket or a missing project, and
        none of them mentioned credentials.

        A mocked suite cannot catch this: the mock returns whatever status
        the test author chose, and nobody writes a test asserting that a
        404 is secretly a 401. Only `/myself` distinguishes them, and only
        against a live site.
        """
        if "→ 404" not in error.message and "→ 400" not in error.message:
            return error
        if not self._auth_failed():
            return error
        prefix = f"{subject}: " if subject else ""
        return BackendError(f"{prefix}{self._CREDENTIALS_REJECTED}")

    def _browse_url(self, key: str) -> str:
        """Human-facing link for an issue.

        Built from the NORMALISED host, not `config.site` as written. The
        API base was normalised from the day pasting a browser URL was
        first supported, but these two links were left interpolating the
        raw value — so a pasted URL gave working API calls and
        a link with the pasted scheme still embedded in the host as the
        link to click."""
        return f"https://{normalise_site(self.config.site)}/browse/{key}"

    @staticmethod
    def _failure_detail(resp: httpx.Response) -> str:
        """What to add after the status code. A rejected token is the
        overwhelmingly common failure and JIRA answers it with either an
        empty body or an HTML error page — a bare "→ 401:" and a dump of
        `<html><head><title>Unauthorized` are equally useless, so say what
        to check instead. Other statuses keep their body, which for JIRA's
        JSON errors is the actual explanation."""
        if resp.status_code in (401, 403):
            return (
                " — JIRA rejected the credentials. Check jira_email and "
                "jira_token (`rite credential check jira_token`), and that "
                "the token is authorised for this site."
            )
        body = resp.text[:300].strip()
        if not body or body.startswith("<"):
            return ""  # an HTML error page explains nothing
        return f": {body}"

    def _request(
        self, method: str, path: str, **kwargs: object
    ) -> dict | list | BackendError:
        url = self._url(path)
        try:
            resp = self._client.request(method, url, **kwargs)
        except httpx.HTTPError as e:
            # Name the URL. A connection error that quotes only errno text
            # gives no way to tell a site that is down from a
            # `ticket_backend.site` that was mistyped.
            return BackendError(
                f"JIRA request to {url} failed: {e} "
                f"(site comes from .rite/config.yaml: ticket_backend.site)"
            )
        if resp.status_code >= 400:
            return BackendError(
                f"JIRA {method} {path} → {resp.status_code}{self._failure_detail(resp)}"
            )
        if resp.status_code == 204 or not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError:
            return BackendError("JIRA returned non-JSON response")

    def create(
        self,
        title: str,
        description: str = "",
        labels: list[str] | None = None,
    ) -> Ticket | BackendError:
        if not self.config.project_key:
            return BackendError("project_key required to create JIRA issues")
        payload: dict = {
            "fields": {
                "project": {"key": self.config.project_key},
                "summary": title,
                "issuetype": {"name": "Task"},
            }
        }
        if description:
            payload["fields"]["description"] = {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [{"type": "text", "text": description}],
                    }
                ],
            }
        if labels:
            payload["fields"]["labels"] = labels

        result = self._request("POST", "/issue", json=payload)
        if isinstance(result, BackendError):
            return self._clarify(result)
        key = result.get("key", "")
        return Ticket(
            id=key,
            title=title,
            labels=labels or [],
            url=self._browse_url(key),
        )

    def read(self, ticket_id: str) -> Ticket | BackendError:
        result = self._request("GET", f"/issue/{ticket_id}")
        if isinstance(result, BackendError):
            if "→ 404" in result.message and self._auth_failed():
                return BackendError(f"{ticket_id}: {self._CREDENTIALS_REJECTED}")
            return result
        if not isinstance(result, dict):
            return BackendError(f"unexpected response for {ticket_id}")
        ticket = _issue_to_ticket(result)
        ticket.url = self._browse_url(ticket_id)
        return ticket

    def update(self, ticket_id: str, **fields: str) -> None | BackendError:
        jira_fields: dict = {}
        if "title" in fields:
            jira_fields["summary"] = fields["title"]
        if "description" in fields or "body" in fields:
            text = fields.get("body", fields.get("description", ""))
            jira_fields["description"] = {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [{"type": "text", "text": text}],
                    }
                ],
            }
        if not jira_fields:
            return None
        result = self._request(
            "PUT", f"/issue/{ticket_id}", json={"fields": jira_fields}
        )
        if isinstance(result, BackendError):
            return self._clarify(result, ticket_id)
        return None

    def move(self, ticket_id: str, status: str) -> str | None | BackendError:
        transitions = self._request("GET", f"/issue/{ticket_id}/transitions")
        if isinstance(transitions, BackendError):
            return self._clarify(transitions, ticket_id)
        if not isinstance(transitions, dict):
            return BackendError("unexpected transitions response")

        # A transition's NAME and the status it lands on are two different
        # strings, and only the second one is what the board will show.
        # Prefer an exact destination match; fall back to the transition
        # name (workflows that label the button rather than the column),
        # and remember where that button actually goes.
        target_id = None
        landing = ""
        by_name: tuple[str, str] | None = None
        for t in transitions.get("transitions", []):
            to_status = t.get("to", {}).get("name", "")
            if to_status.lower() == status.lower():
                target_id, landing = t["id"], to_status
                break
            if by_name is None and t.get("name", "").lower() == status.lower():
                by_name = (t["id"], to_status)
        if target_id is None and by_name is not None:
            target_id, landing = by_name

        if not target_id:
            available = sorted(
                {
                    t.get("to", {}).get("name", "") or t.get("name", "")
                    for t in transitions.get("transitions", [])
                }
            )
            return BackendError(
                f"no transition to '{status}' found. Available: {', '.join(available)}"
            )

        result = self._request(
            "POST",
            f"/issue/{ticket_id}/transitions",
            json={"transition": {"id": target_id}},
        )
        if isinstance(result, BackendError):
            return self._clarify(result, ticket_id)
        # Only speak up when the destination is not the name that was
        # asked for — a transition NAMED "In Progress" whose `to` is "In
        # Development" moved the ticket somewhere the caller did not name,
        # and reporting the request back as if it were the outcome is the
        # failure this whole interface is being audited for.
        if landing and landing.lower() != status.lower():
            return landing
        return None

    # An Atlassian accountId is a 24-character hex string, or the older
    # `<provider>:<uuid>` form. Anything else is a person's name or email
    # and has to be looked up before JIRA will accept it.
    @staticmethod
    def _looks_like_account_id(value: str) -> bool:
        if ":" in value:
            return True
        return len(value) == 24 and all(c in "0123456789abcdefABCDEF" for c in value)

    def _resolve_account_id(self, worker: str) -> str | BackendError:
        """An accountId for a display name or email address.

        `assign()` used to send whatever string it was given straight to
        `PUT /issue/KEY/assignee`, and the JIRA API takes ONLY an
        accountId. So `rite board assign ABC-12 alpha` — the literal
        example in the command's own `--help` — answered "Specified user
        does not exist or you do not have required permissions" against
        every real instance. Nothing in a mocked suite notices, because a
        mock accepts any string as an assignee.

        An ambiguous name is refused rather than guessed at: assigning a
        ticket to the wrong colleague is worse than being asked to be
        specific."""
        result = self._request("GET", "/user/search", params={"query": worker})
        if isinstance(result, BackendError):
            return self._clarify(result)
        if not isinstance(result, list) or not result:
            # `/user/search` is the third endpoint that answers a rejected
            # token with `200 []` rather than a 401 — same shape as the
            # issue search. Blaming the name for what is a bad token would
            # reproduce, in the fix for one credential defect, the exact
            # credential defect being fixed.
            if self._auth_failed():
                return BackendError(self._CREDENTIALS_REJECTED)
            return BackendError(
                f"no JIRA user matches '{worker}'. JIRA assigns by account, "
                f"not by rite worker name — pass a display name, an email "
                f"address, or the accountId from the person's JIRA profile "
                f"URL. To record which WORKER owns a ticket, use `rite board "
                f"label` instead; that is rite's own assignment mechanism."
            )
        if len(result) > 1:
            names = ", ".join(
                f"{u.get('displayName', '?')} ({u.get('accountId', '?')})"
                for u in result[:5]
            )
            return BackendError(
                f"'{worker}' matches {len(result)} JIRA users — pass an "
                f"accountId instead: {names}"
            )
        account_id = result[0].get("accountId", "")
        if not account_id:
            return BackendError(f"JIRA user '{worker}' has no accountId")
        return account_id

    def assign(self, ticket_id: str, worker: str) -> None | BackendError:
        account_id = worker
        if not self._looks_like_account_id(worker):
            resolved = self._resolve_account_id(worker)
            if isinstance(resolved, BackendError):
                return resolved
            account_id = resolved
        result = self._request(
            "PUT",
            f"/issue/{ticket_id}/assignee",
            json={"accountId": account_id},
        )
        if isinstance(result, BackendError):
            return self._clarify(result, ticket_id)
        return None

    def label(
        self, ticket_id: str, labels: list[str], remove: list[str] | None = None
    ) -> None | BackendError:
        ops: list[dict] = [{"add": lbl} for lbl in labels]
        # Verified against the real API: `add` and `remove` may be sent in
        # one `update`, and removing a label the issue does not carry is a
        # 204, not an error — so a caller need not read the issue first.
        ops += [{"remove": lbl} for lbl in remove or []]
        if not ops:
            return None
        result = self._request(
            "PUT",
            f"/issue/{ticket_id}",
            json={"update": {"labels": ops}},
        )
        if isinstance(result, BackendError):
            return self._clarify(result, ticket_id)
        return None

    def list_tickets(
        self, filters: TicketFilter | None = None
    ) -> list[Ticket] | BackendError:
        jql_parts: list[str] = []
        if self.config.project_key:
            jql_parts.append(f"project = {self.config.project_key}")
        if filters:
            if filters.status:
                jql_parts.append(f'status = "{filters.status}"')
            if filters.assignee:
                jql_parts.append(f'assignee = "{filters.assignee}"')
            if filters.label:
                jql_parts.append(f'labels = "{filters.label}"')
            if filters.labels:
                for lbl in filters.labels:
                    jql_parts.append(f'labels = "{lbl}"')

        jql = " AND ".join(jql_parts) if jql_parts else "ORDER BY created DESC"
        return self._search_jql(jql)

    def comment(self, ticket_id: str, text: str) -> None | BackendError:
        body = {
            "body": {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [{"type": "text", "text": text}],
                    }
                ],
            }
        }
        result = self._request("POST", f"/issue/{ticket_id}/comment", json=body)
        if isinstance(result, BackendError):
            return self._clarify(result, ticket_id)
        return None

    def query(self, raw_query: str) -> list[Ticket] | BackendError:
        return self._search_jql(raw_query)

    def link(
        self, ticket_id: str, target_id: str, link_type: str
    ) -> None | BackendError:
        """`ticket_id` is the DESTINATION of the link: for the default
        "Blocks", `target_id` blocks `ticket_id` — i.e. §6.2's "worker
        tickets link to project tickets as *blocked by*".

        The payload is `inwardIssue: target_id, outwardIssue: ticket_id`,
        which looks backwards until you measure it. Against the real API,
        `POST /issueLink` reads as **inwardIssue &lt;type.outward&gt;
        outwardIssue**: sending `inwardIssue: DEF-3, outwardIssue: DEF-4`
        with type "Blocks" produced "DEF-3 blocks DEF-4" on DEF-3 and "DEF-4
        is blocked by DEF-3" on DEF-4 — the exact inverse of what the field
        names suggest and of what rite claimed it had created.

        That inversion is invisible to a mocked test: the mock records the
        call and never renders the relationship, so only the live API can
        say which issue ended up blocked. It matters because "blocked by"
        is the one structural assumption SPEC §6.2 says rite depends on —
        an inverted link makes the dependency logic read every blocker as
        a dependent.
        """
        result = self._request(
            "POST",
            "/issueLink",
            json={
                "type": {"name": link_type},
                "inwardIssue": {"key": target_id},
                "outwardIssue": {"key": ticket_id},
            },
        )
        if isinstance(result, BackendError):
            return self._clarify(self._name_link_types(result), ticket_id)
        return None

    def _name_link_types(self, error: BackendError) -> BackendError:
        """Add the site's real link-type names to a "no such type" error.

        JIRA's own message ("No issue link type with name 'blocks' found")
        names what was wrong and not what would be right, and the names
        are case-sensitive — `--type blocks` is rejected where `--type
        Blocks` is accepted. One extra request, only on this failure."""
        if "issue link type" not in error.message.lower():
            return error
        types = self._request("GET", "/issueLinkType")
        if isinstance(types, BackendError) or not isinstance(types, dict):
            return error
        names = [
            t.get("name", "") for t in types.get("issueLinkTypes", []) if t.get("name")
        ]
        if not names:
            return error
        return BackendError(
            f"{error.message} — this site's link types (names are "
            f"case-sensitive): {', '.join(sorted(names))}"
        )

    # The fields every search asks for. The `/search/jql` endpoint returns
    # only issue ids unless told otherwise, so this is not optional the way
    # it was on the old `/search`.
    _SEARCH_FIELDS = "summary,status,assignee,labels,description,created,updated"

    def _search_jql(
        self, jql: str, max_results: int = PAGE_LIMIT
    ) -> TicketPage | BackendError:
        """Run JQL against `/search/jql`.

        Atlassian REMOVED `GET /rest/api/3/search` (CHANGE-2046); it now
        answers 410 for everyone, so every list and query in rite failed
        against a real instance while passing against mocks. The
        replacement is token-paged rather than offset-paged: no `total`,
        no `startAt`, just `nextPageToken` and `isLast`.

        Truncation is deliberately NOT inferred from the row count. The
        old code over-fetched by one (`maxResults + 1`) and treated a
        short page as complete — which silently breaks the moment the
        server caps `maxResults`, since asking for 101 and being handed
        100 is indistinguishable from a board with exactly 100 tickets.
        Measured against the real API, `isLast` is not usable on its own
        either: a page holding every remaining issue still comes back
        `isLast: false` with a token, and that token then yields nothing.

        So the only signal that is true regardless of any cap is the next
        page itself. Never ask for more than `max_results`, and when the
        server offers a continuation, spend one cheap single-row request
        to find out whether it actually holds anything.
        """
        result = self._request(
            "GET",
            "/search/jql",
            params={
                "maxResults": max_results,
                "jql": jql,
                "fields": self._SEARCH_FIELDS,
            },
        )
        if isinstance(result, BackendError):
            return result
        if not isinstance(result, dict):
            return BackendError("unexpected search response")

        issues = result.get("issues", [])
        if not issues and self._auth_failed():
            # "No tickets" and "your token is wrong" are the same response
            # from this endpoint. Only one of them is worth acting on.
            return BackendError(self._CREDENTIALS_REJECTED)

        tickets = [_issue_to_ticket(issue) for issue in issues]
        return TicketPage(tickets, truncated=self._has_more(jql, result))

    def _has_more(self, jql: str, page: dict) -> bool:
        """Whether a returned page left anything behind.

        `isLast` true is trustworthy; `isLast` false is not, so the token
        is followed for one row to settle it. A search that returned
        everything therefore costs exactly one request in the common case
        (short page, `isLast` true) and two only at an exact page
        boundary."""
        if page.get("isLast"):
            return False
        token = page.get("nextPageToken")
        if not token:
            return False
        probe = self._request(
            "GET",
            "/search/jql",
            params={
                "maxResults": 1,
                "jql": jql,
                "fields": "summary",
                "nextPageToken": token,
            },
        )
        if isinstance(probe, BackendError) or not isinstance(probe, dict):
            # Cannot prove there is more; do not claim a complete board
            # either — the conservative reading is that more may exist.
            return True
        return bool(probe.get("issues"))
