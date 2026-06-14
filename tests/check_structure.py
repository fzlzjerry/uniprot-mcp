"""Offline structural checks (no network) for CI.

Asserts the server imports, registers exactly the expected tools with `ctx`
hidden from the public schema, exposes the cheat-sheet resource, and that the
cheat-sheet is non-trivial. Exits non-zero on any failure.

Run:  uv run python -m tests.check_structure
"""

from __future__ import annotations

import asyncio

from uniprot_mcp import server
from uniprot_mcp.cheatsheet import CHEATSHEET

EXPECTED_TOOLS = {
    "search_uniprotkb",
    "get_entry",
    "get_fasta",
    "map_ids",
    "get_taxonomy",
    "search_uniref",
    "search_proteomes",
}


def _schema(tool) -> dict:
    return (getattr(tool, "inputSchema", None) or getattr(tool, "parameters", None) or {})


async def main() -> None:
    tools = await server.mcp.list_tools()
    names = {t.name for t in tools}
    assert names == EXPECTED_TOOLS, f"tool set mismatch: {names ^ EXPECTED_TOOLS}"

    by = {t.name: t for t in tools}
    props = set(_schema(by["map_ids"]).get("properties", {}))
    assert "ctx" not in props, "Context param leaked into map_ids public schema"
    assert {"from_db", "to_db", "ids"} <= props, f"map_ids missing params: {props}"

    # every tool must carry a description for the model
    for t in tools:
        assert (t.description or "").strip(), f"tool {t.name} has no description"

    resources = await server.mcp.list_resources()
    uris = {str(r.uri) for r in resources}
    assert "resource://uniprot/query-cheatsheet" in uris, f"cheat-sheet resource missing: {uris}"

    assert "organism_id:" in CHEATSHEET and len(CHEATSHEET) > 200, "cheat-sheet looks empty"

    print(f"OK: {len(names)} tools registered, ctx hidden, cheat-sheet resource present.")


if __name__ == "__main__":
    asyncio.run(main())
