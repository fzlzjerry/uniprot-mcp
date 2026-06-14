"""Live-API smoke test for the UniProt MCP server.

Exercises every tool against the real https://rest.uniprot.org and prints the
output so a human can eyeball it. No assertions — this is a manual sanity check.

Run:  uv run python -m tests.smoke
"""

from __future__ import annotations

import asyncio

from fastmcp.exceptions import ToolError

from uniprot_mcp import server
from uniprot_mcp.client import aclose


def banner(title: str) -> None:
    print("\n" + "=" * 78)
    print(f"### {title}")
    print("=" * 78)


async def run(title: str, coro) -> None:
    banner(title)
    try:
        result = await coro
        print(result)
    except ToolError as exc:
        print(f"[ToolError] {exc}")
    except Exception as exc:  # pragma: no cover
        print(f"[UNEXPECTED {type(exc).__name__}] {exc}")


async def main() -> None:
    await run(
        "get_taxonomy('Homo sapiens')  -> organism_id",
        server.get_taxonomy("Homo sapiens"),
    )
    await run(
        "search_uniprotkb('BRCA1 AND organism_id:9606', reviewed=True, size=5)",
        server.search_uniprotkb("BRCA1 AND organism_id:9606", reviewed=True, size=5),
    )
    await run(
        "get_entry('P38398')  [summary]",
        server.get_entry("P38398"),
    )
    await run(
        "get_fasta(['P38398', 'P04637'])",
        server.get_fasta(["P38398", "P04637"]),
    )
    await run(
        "map_ids('UniProtKB_AC-ID', 'PDB', ['P38398', 'NOTREAL123'])  [simple pairs + failedIds]",
        server.map_ids("UniProtKB_AC-ID", "PDB", ["P38398", "NOTREAL123"]),
    )
    await run(
        "map_ids('RefSeq_Protein', 'UniProtKB', ['NP_009225.1'])  [enriched target]",
        server.map_ids("RefSeq_Protein", "UniProtKB", ["NP_009225.1"]),
    )
    await run(
        "map_ids('UniProtKB_AC-ID', 'NOT_A_DB', ['P38398'])  [validation error]",
        server.map_ids("UniProtKB_AC-ID", "NOT_A_DB", ["P38398"]),
    )
    await run(
        "search_uniref('BRCA1', identity='0.5', size=3)",
        server.search_uniref("BRCA1", identity="0.5", size=3),
    )
    await run(
        "search_proteomes(organism_id=9606, reference_only=True, size=3)",
        server.search_proteomes(organism_id=9606, reference_only=True, size=3),
    )
    await run(
        "search_uniprotkb('insulin', organism_id=9606, size=3, format='tsv')",
        server.search_uniprotkb("insulin", organism_id=9606, size=3, format="tsv"),
    )
    await aclose()
    print("\n" + "=" * 78)
    print("### smoke test complete")
    print("=" * 78)


if __name__ == "__main__":
    asyncio.run(main())
