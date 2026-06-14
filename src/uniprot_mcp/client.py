"""Shared HTTP layer for the UniProt REST API.

A single ``httpx.AsyncClient`` is reused across tool calls. All requests go
through :func:`request`, which retries 429/5xx and transient network errors
with exponential backoff. :func:`check` maps non-2xx responses to actionable
``ToolError`` messages (no raw tracebacks ever reach the model).

Redirects are NOT auto-followed: the ID-mapping flow needs to inspect the raw
``303`` + ``Location`` that signals job completion, so callers opt in per request.
"""

from __future__ import annotations

import asyncio
import os
import re
from typing import Any

import httpx
from fastmcp.exceptions import ToolError

from . import __version__

BASE_URL = "https://rest.uniprot.org"

# UniProt asks clients to identify themselves with a contact address.
CONTACT = os.environ.get("UNIPROT_MCP_CONTACT", "unset-contact@example.com")
USER_AGENT = f"uniprotkb-mcp/{__version__} (https://github.com/fzlzjerry/uniprot-mcp; mailto:{CONTACT})"

_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=30.0, pool=10.0)
_RETRY_STATUS = {429, 500, 502, 503, 504}
_MAX_RETRIES = 4
_BACKOFF_BASE = 0.5
_BACKOFF_CAP = 8.0

_client: httpx.AsyncClient | None = None


def get_client() -> httpx.AsyncClient:
    """Return the process-wide AsyncClient, creating it on first use."""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            base_url=BASE_URL,
            headers={"User-Agent": USER_AGENT},
            timeout=_TIMEOUT,
            follow_redirects=False,
        )
    return _client


async def aclose() -> None:
    """Close the shared client (used by the smoke test / clean shutdown)."""
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


def _backoff(attempt: int) -> float:
    return min(_BACKOFF_BASE * (2**attempt), _BACKOFF_CAP)


def _retry_delay(resp: httpx.Response, attempt: int) -> float:
    """Honor Retry-After on 429s; otherwise exponential backoff."""
    if resp.status_code == 429:
        ra = resp.headers.get("retry-after")
        if ra and ra.isdigit():
            return min(float(ra), 30.0)
    return _backoff(attempt)


def _clean_params(params: dict[str, Any] | None) -> dict[str, Any] | None:
    if not params:
        return None
    return {k: v for k, v in params.items() if v is not None}


async def request(
    method: str,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
    follow_redirects: bool = False,
) -> httpx.Response:
    """Send a request with retry/backoff. Returns the response unraised.

    Retries 429/5xx (respecting ``Retry-After``) and transient network errors.
    Callers decide how to interpret the final response (e.g. the ID-mapping
    poller treats a 303 as success); use :func:`check` to raise on 4xx/5xx.
    """
    client = get_client()
    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            resp = await client.request(
                method,
                url,
                params=_clean_params(params),
                data=data,
                follow_redirects=follow_redirects,
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES:
                await asyncio.sleep(_backoff(attempt))
                continue
            raise ToolError(
                f"Network error contacting UniProt ({type(exc).__name__}: {exc}). "
                "The service may be unreachable; please retry shortly."
            ) from exc

        if resp.status_code in _RETRY_STATUS and attempt < _MAX_RETRIES:
            await asyncio.sleep(_retry_delay(resp, attempt))
            continue
        return resp

    # Defensive: loop always returns or raises above.
    raise ToolError(f"Request to UniProt failed after retries: {last_exc}")


def _extract_messages(resp: httpx.Response) -> str:
    """Pull UniProt's human-readable error text out of a JSON error body."""
    try:
        body = resp.json()
    except Exception:
        text = (resp.text or "").strip()
        return text[:300]
    if isinstance(body, dict):
        msgs = body.get("messages")
        if isinstance(msgs, list) and msgs:
            return " ".join(str(m) for m in msgs)
        for key in ("error", "errorMessage", "detail", "message"):
            if body.get(key):
                return str(body[key])
    return ""


def check(resp: httpx.Response) -> httpx.Response:
    """Raise an actionable ``ToolError`` for non-2xx responses; else pass through."""
    if resp.is_success:
        return resp

    status = resp.status_code
    detail = _extract_messages(resp)
    if status == 400:
        raise ToolError(
            "UniProt rejected the request (400 Bad Request). "
            + (detail or "Check your query syntax and parameter values.")
            + " See the query cheat-sheet resource for valid field syntax."
        )
    if status == 404:
        raise ToolError(
            "Not found (404). " + (detail or "The requested UniProt resource does not exist.")
        )
    if status == 429:
        raise ToolError(
            "UniProt rate limit reached (429) and automatic retries were exhausted. "
            "Please wait a bit and try again, ideally with fewer/larger batched requests."
        )
    if status >= 500:
        raise ToolError(
            f"UniProt server error ({status}) after retries. "
            + (detail or "This is usually transient — try again shortly.")
        )
    raise ToolError(f"Unexpected UniProt response ({status}). {detail}".strip())


_LINK_RE = re.compile(r"<([^>]+)>\s*;\s*([^,]*)")


def next_cursor_url(resp: httpx.Response) -> str | None:
    """Return the ``rel="next"`` URL from the ``Link`` header, if present.

    Parses ``<url>; params`` pairs per RFC 8288 rather than splitting the whole
    header on commas — UniProt emits unencoded commas inside ``fields=`` query
    params, and ``rel`` is not always the first parameter.
    """
    link = resp.headers.get("link")
    if not link:
        return None
    for url, params in _LINK_RE.findall(link):
        if 'rel="next"' in params:
            return url.strip()
    return None


def total_results(resp: httpx.Response) -> int | None:
    """Return the ``x-total-results`` header as an int, if present."""
    v = resp.headers.get("x-total-results")
    if v is not None and v.isdigit():
        return int(v)
    return None
