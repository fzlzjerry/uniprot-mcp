"""The UniProt asynchronous ID-mapping flow.

run -> poll status (303 + Location on completion) -> fetch results, branching on
whether the target is UniProtKB (enriched full entries, has a /stream endpoint)
or any other database (simple from/to pairs + ``failedIds``, cursor-paged).
"""

from __future__ import annotations

import asyncio

from fastmcp import Context
from fastmcp.exceptions import ToolError

from . import formatting
from .client import check, next_cursor_url, request, total_results

MAX_ROWS = 1000
POLL_DEADLINE = 90.0  # seconds
_REDIRECT_CODES = {301, 302, 303, 307, 308}
_ENRICHED_FIELDS = "accession,id,protein_name,organism_name,length,reviewed"


async def run_mapping(from_db: str, to_db: str, ids: list[str]) -> str:
    resp = check(
        await request(
            "POST",
            "/idmapping/run",
            data={"from": from_db, "to": to_db, "ids": ",".join(ids)},
        )
    )
    try:
        body = resp.json()
    except Exception:
        raise ToolError(f"Unexpected response submitting ID-mapping job: {resp.text[:200]}")
    job = body.get("jobId")
    if not job:
        msgs = body.get("messages")
        raise ToolError(
            "UniProt did not return a job id for the mapping request"
            + (f": {' '.join(msgs)}" if msgs else f": {body}")
        )
    return job


async def _results_url_from_details(job: str) -> str:
    resp = check(await request("GET", f"/idmapping/details/{job}"))
    url = (resp.json() or {}).get("redirectURL")
    if not url:
        raise ToolError(f"Could not resolve results URL for ID-mapping job {job}.")
    return url


async def poll_until_done(job: str, ctx: Context | None = None) -> str:
    """Poll the status endpoint until the job finishes; return the results URL."""
    loop = asyncio.get_event_loop()
    start = loop.time()
    interval = 0.5
    while True:
        resp = await request("GET", f"/idmapping/status/{job}", follow_redirects=False)
        if resp.status_code in _REDIRECT_CODES:
            loc = resp.headers.get("location")
            if loc:
                return loc
            return await _results_url_from_details(job)
        if resp.status_code == 200:
            try:
                body = resp.json()
            except Exception:
                body = {}
            status = body.get("jobStatus")
            if status in ("ERROR", "FAILURE"):
                msgs = body.get("messages") or []
                raise ToolError(
                    "UniProt ID-mapping job failed"
                    + (f": {' '.join(str(m) for m in msgs)}" if msgs else ".")
                )
            if status == "FINISHED":
                return await _results_url_from_details(job)
            if body.get("messages"):
                raise ToolError("ID-mapping error: " + " ".join(str(m) for m in body["messages"]))
            # else RUNNING / NEW -> keep polling
        else:
            check(resp)  # raise a friendly error for unexpected status

        elapsed = loop.time() - start
        if elapsed > POLL_DEADLINE:
            raise ToolError(
                f"ID-mapping job {job} did not finish within {int(POLL_DEADLINE)}s. "
                "Large mappings can take longer — try again or reduce the id set."
            )
        if ctx is not None:
            await ctx.report_progress(progress=min(elapsed, POLL_DEADLINE), total=POLL_DEADLINE)
        await asyncio.sleep(interval)
        interval = min(interval * 1.5, 5.0)


async def _fetch_enriched_meta(results_url: str) -> tuple[list[str], int | None]:
    """Read failedIds/obsoleteCount, which the /stream endpoint omits.

    The non-stream enriched results endpoint carries both at the top level; a
    ``size=0`` request returns just that metadata cheaply. Best-effort: on any
    hiccup we fall back to empty/None rather than failing the whole mapping.
    """
    try:
        resp = check(
            await request(
                "GET",
                results_url,
                params={"format": "json", "fields": _ENRICHED_FIELDS, "size": 0},
                follow_redirects=True,
            )
        )
        data = resp.json()
        return (data.get("failedIds") or [], data.get("obsoleteCount"))
    except Exception:
        return ([], None)


async def _fetch_enriched(results_url: str) -> dict:
    stream_url = results_url.replace("/results/", "/results/stream/", 1)
    resp = check(
        await request(
            "GET",
            stream_url,
            params={"format": "json", "fields": _ENRICHED_FIELDS},
            follow_redirects=True,
        )
    )
    data = resp.json()
    results = data.get("results", []) or []
    rows = []
    for r in results[:MAX_ROWS]:
        entry = r.get("to") or {}
        rows.append(
            {
                "from": r.get("from"),
                "to": formatting.accession(entry),
                "entry_name": formatting.entry_name(entry),
                "protein": formatting.protein_name(entry) if entry.get("proteinDescription") else None,
                "organism": (entry.get("organism") or {}).get("scientificName"),
            }
        )
    # The stream payload has no failedIds/obsoleteCount — fetch them separately.
    failed, obsolete = await _fetch_enriched_meta(results_url)
    return {
        "enriched": True,
        "rows": rows,
        "failed": failed,
        "obsolete_count": obsolete,
        "total": len(results),
        "truncated": len(results) > MAX_ROWS,
    }


async def _fetch_pairs(results_url: str) -> dict:
    rows: list[dict] = []
    failed: list[str] = []
    total: int | None = None
    url: str | None = results_url
    params = {"format": "json", "size": 500}
    first = True
    while url and len(rows) < MAX_ROWS:
        resp = check(await request("GET", url, params=params if first else None, follow_redirects=True))
        first = False
        data = resp.json()
        for r in data.get("results", []) or []:
            rows.append({"from": r.get("from"), "to": r.get("to")})
            if len(rows) >= MAX_ROWS:
                break
        if total is None:
            total = total_results(resp)
        fids = data.get("failedIds")
        if fids:
            failed = fids
        url = next_cursor_url(resp)
    truncated = (total is not None and total > len(rows)) or bool(url)
    return {
        "enriched": False,
        "rows": rows,
        "failed": failed,
        "obsolete_count": None,
        "total": total if total is not None else len(rows),
        "truncated": truncated,
    }


async def fetch_results(results_url: str, to_db: str) -> dict:
    enriched = "/uniprotkb/results" in results_url or to_db.startswith("UniProtKB")
    if enriched:
        return await _fetch_enriched(results_url)
    return await _fetch_pairs(results_url)


async def map_ids_flow(from_db: str, to_db: str, ids: list[str], ctx: Context | None = None) -> dict:
    job = await run_mapping(from_db, to_db, ids)
    if ctx is not None:
        await ctx.info(f"ID-mapping job {job} submitted ({from_db} -> {to_db}, {len(ids)} id(s))")
    results_url = await poll_until_done(job, ctx)
    return await fetch_results(results_url, to_db)
