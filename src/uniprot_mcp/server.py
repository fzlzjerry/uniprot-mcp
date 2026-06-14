"""FastMCP server exposing the UniProt REST API over stdio.

Run with ``uv run uniprot-mcp`` (or ``python -m uniprot_mcp.server``). Tools
return compact, model-readable digests by default and full payloads on request.
"""

from __future__ import annotations

from typing import Annotated, Literal, Optional

from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError
from pydantic import Field

from . import formatting, idmapping
from .cheatsheet import CHEATSHEET, SEARCH_HINT
from .client import check, request, total_results
from .config import validate_pair

mcp = FastMCP(
    "UniProt",
    instructions=(
        "Tools for the UniProt protein knowledgebase (https://uniprot.org).\n"
        "- search_uniprotkb: find proteins with UniProt query syntax.\n"
        "- get_entry / get_fasta: fetch a single entry digest or sequences.\n"
        "- map_ids: convert ids between databases (e.g. RefSeq->UniProtKB, UniProtKB->PDB).\n"
        "- get_taxonomy: resolve an organism name to an organism_id for filtering.\n"
        "- search_uniref / search_proteomes: protein clusters and proteomes.\n"
        "Summaries are returned by default; pass format='json'/'fasta'/etc. for raw data. "
        "Read resource://uniprot/query-cheatsheet for valid query field syntax."
    ),
)

SEARCH_SUMMARY_FIELDS = "accession,id,protein_name,gene_names,organism_name,organism_id,length,reviewed"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _assemble_query(query: str, reviewed: Optional[bool], organism_id: Optional[int]) -> str:
    if not query or not query.strip():
        raise ToolError("`query` must not be empty (use '*' to match everything).")
    parts = [f"({query.strip()})"]
    if reviewed is not None:
        parts.append(f"reviewed:{str(reviewed).lower()}")
    if organism_id is not None:
        parts.append(f"organism_id:{organism_id}")
    return " AND ".join(parts)


def _more_note(total: int | None, shown: int) -> str:
    if total is not None and total > shown:
        return (
            f"\n# {total - shown} more result(s) not shown (total {total}); "
            "raise `size` (max 500) or narrow the query."
        )
    return ""


def _as_list(value, what: str) -> list[str]:
    items = [value] if isinstance(value, str) else list(value)
    items = [str(x).strip() for x in items if x and str(x).strip()]
    if not items:
        raise ToolError(f"Provide at least one {what}.")
    return items


# --------------------------------------------------------------------------- #
# 1. search_uniprotkb
# --------------------------------------------------------------------------- #
@mcp.tool
async def search_uniprotkb(
    query: Annotated[
        str,
        Field(description="UniProtKB query in native syntax, e.g. 'gene:BRCA1' or "
                          "'keyword:Kinase AND length:[300 TO 500]'. " + SEARCH_HINT),
    ],
    reviewed: Annotated[
        Optional[bool],
        Field(description="True = Swiss-Prot (reviewed) only; False = TrEMBL only; "
                          "None = both. Appended to the query for you."),
    ] = None,
    organism_id: Annotated[
        Optional[int],
        Field(description="NCBI taxonomy id filter, e.g. 9606 for human. Resolve a name "
                          "with get_taxonomy. Appended to the query for you."),
    ] = None,
    fields: Annotated[
        Optional[str],
        Field(description="Comma-separated result fields for format='tsv' "
                          "(e.g. 'accession,gene_names,length'). Ignored for 'summary'."),
    ] = None,
    size: Annotated[int, Field(description="Max entries to return (1-500).", ge=1, le=500)] = 25,
    format: Annotated[
        Literal["summary", "fasta", "tsv"],
        Field(description="'summary' = compact per-entry digest (default); "
                          "'fasta' = raw sequences; 'tsv' = tab-separated table."),
    ] = "summary",
) -> str:
    """Search UniProtKB and return matching protein entries.

    Use this to find proteins by gene, name, organism, keyword, length, etc.
    Returns a compact summary (accession, entry name, protein name, gene,
    organism, length, Swiss-Prot/TrEMBL) by default. Pass `reviewed=True` to
    restrict to curated Swiss-Prot entries, and `organism_id` to filter by
    species. The total match count is always reported so you can page or narrow.
    """
    try:
        full_query = _assemble_query(query, reviewed, organism_id)
        if format == "summary":
            resp = check(await request(
                "GET", "/uniprotkb/search",
                params={"query": full_query, "format": "json", "fields": SEARCH_SUMMARY_FIELDS, "size": size},
            ))
            data = resp.json()
            return formatting.summarize_search(data.get("results", []), total_results(resp), full_query)

        if format == "fasta":
            resp = check(await request(
                "GET", "/uniprotkb/search",
                params={"query": full_query, "format": "fasta", "size": size},
            ))
            text = resp.text
            return text + _more_note(total_results(resp), text.count(">"))

        # tsv
        resp = check(await request(
            "GET", "/uniprotkb/search",
            params={"query": full_query, "format": "tsv", "fields": fields or SEARCH_SUMMARY_FIELDS, "size": size},
        ))
        text = resp.text
        shown = max(0, text.rstrip().count("\n"))  # minus header row
        return text + _more_note(total_results(resp), shown)
    except ToolError:
        raise
    except Exception as exc:  # pragma: no cover - defensive
        raise ToolError(f"search_uniprotkb failed: {type(exc).__name__}: {exc}")


# --------------------------------------------------------------------------- #
# 2. get_entry
# --------------------------------------------------------------------------- #
@mcp.tool
async def get_entry(
    accession: Annotated[str, Field(description="UniProtKB accession, e.g. 'P38398' or 'P04637'.")],
    format: Annotated[
        Literal["summary", "json", "fasta", "txt", "gff"],
        Field(description="'summary' = curated digest (default); 'json' = full record; "
                          "'fasta' = sequence; 'txt' = flat file; 'gff' = features."),
    ] = "summary",
) -> str:
    """Fetch one UniProtKB entry.

    'summary' returns a clean digest: protein/gene names, organism, length,
    function, subcellular location, family/domains, key features, PTMs,
    keywords, and top cross-references (PDB, AlphaFold, Ensembl, RefSeq,
    InterPro, GO). Use 'json' for the complete record, 'fasta' for the
    sequence, 'txt' for the flat file, or 'gff' for feature coordinates.
    """
    acc = accession.strip()
    if not acc:
        raise ToolError("`accession` must not be empty.")
    try:
        if format in ("summary", "json"):
            resp = check(await request("GET", f"/uniprotkb/{acc}.json", follow_redirects=True))
            if format == "json":
                return resp.text
            return formatting.summarize_entry(resp.json())
        resp = check(await request("GET", f"/uniprotkb/{acc}.{format}", follow_redirects=True))
        return resp.text
    except ToolError:
        raise
    except Exception as exc:  # pragma: no cover
        raise ToolError(f"get_entry failed for {acc!r}: {type(exc).__name__}: {exc}")


# --------------------------------------------------------------------------- #
# 3. get_fasta
# --------------------------------------------------------------------------- #
@mcp.tool
async def get_fasta(
    accessions: Annotated[
        list[str] | str,
        Field(description="One accession (string) or several (list of strings), e.g. "
                          "['P38398', 'P04637']."),
    ],
) -> str:
    """Return raw FASTA sequence(s) for one or more UniProtKB accessions.

    A single accession uses the entry endpoint; a list uses the batch
    /accessions endpoint. Well-formed but unknown/obsolete accessions are
    dropped silently (a note reports the count discrepancy), but a
    malformed-format accession makes UniProt reject the whole request — so pass
    syntactically valid accessions.
    """
    accs = _as_list(accessions, "accession")
    try:
        if len(accs) == 1:
            resp = check(await request("GET", f"/uniprotkb/{accs[0]}.fasta", follow_redirects=True))
            return resp.text
        resp = check(await request(
            "GET", "/uniprotkb/accessions",
            params={"accessions": ",".join(accs), "format": "fasta"},
            follow_redirects=True,
        ))
        text = resp.text
        got = text.count(">")
        if got < len(accs):
            text += (f"\n# Note: requested {len(accs)} accession(s) but received {got} record(s); "
                     "some ids may be invalid, obsolete, or demerged.")
        return text
    except ToolError:
        raise
    except Exception as exc:  # pragma: no cover
        raise ToolError(f"get_fasta failed: {type(exc).__name__}: {exc}")


# --------------------------------------------------------------------------- #
# 4. map_ids
# --------------------------------------------------------------------------- #
@mcp.tool
async def map_ids(
    from_db: Annotated[
        str,
        Field(description="Source database id, e.g. 'UniProtKB_AC-ID', 'Gene_Name', "
                          "'RefSeq_Protein', 'Ensembl', 'PDB', 'GeneID', 'KEGG'. "
                          "An invalid value returns the full allowed list."),
    ],
    to_db: Annotated[
        str,
        Field(description="Target database id, e.g. 'UniProtKB' (enriched entries), "
                          "'PDB', 'Ensembl', 'KEGG', 'RefSeq_Protein'."),
    ],
    ids: Annotated[
        list[str] | str,
        Field(description="One id (string) or many (list), e.g. ['P38398', 'P04637']."),
    ],
    ctx: Context | None = None,
) -> str:
    """Map identifiers between databases via UniProt's async ID-mapping service.

    Submits a job, polls until it finishes, then returns the mapped pairs plus
    any unmapped input ids. When `to_db` is 'UniProtKB'/'UniProtKB-Swiss-Prot'
    each result is enriched with the protein name, entry name, and organism.
    Use 'UniProtKB_AC-ID' as `from_db` when starting from UniProt accessions.
    Validates the database pair against the live UniProt config and, on an
    invalid value, returns the allowed databases.
    """
    from_db, to_db = from_db.strip(), to_db.strip()
    if not from_db or not to_db:
        raise ToolError("`from_db` and `to_db` must not be empty.")
    id_list = _as_list(ids, "id")
    if len(id_list) > 100_000:
        raise ToolError("Too many ids (max 100,000 per mapping request).")
    try:
        await validate_pair(from_db, to_db)
        result = await idmapping.map_ids_flow(from_db, to_db, id_list, ctx)
        return formatting.summarize_idmapping(from_db, to_db, result)
    except ToolError:
        raise
    except Exception as exc:  # pragma: no cover
        raise ToolError(f"map_ids failed: {type(exc).__name__}: {exc}")


# --------------------------------------------------------------------------- #
# 5. get_taxonomy
# --------------------------------------------------------------------------- #
@mcp.tool
async def get_taxonomy(
    query_or_id: Annotated[
        str,
        Field(description="An organism name ('Homo sapiens', 'human', 'E. coli') or a "
                          "numeric NCBI taxon id ('9606')."),
    ],
) -> str:
    """Resolve a taxonomy name or id.

    Pass a numeric taxon id to fetch that record, or a name to search. Returns
    the taxon id, scientific/common names, rank, and lineage — letting you turn
    an organism name into the organism_id used by search_uniprotkb.
    """
    q = query_or_id.strip()
    if not q:
        raise ToolError("`query_or_id` must not be empty.")
    try:
        if q.isdigit():
            resp = check(await request("GET", f"/taxonomy/{q}", params={"format": "json"}, follow_redirects=True))
            return formatting.summarize_taxonomy_record(resp.json())
        resp = check(await request(
            "GET", "/taxonomy/search",
            params={"query": q, "format": "json", "size": 10},
        ))
        return formatting.summarize_taxonomy_search(resp.json().get("results", []), q)
    except ToolError:
        raise
    except Exception as exc:  # pragma: no cover
        raise ToolError(f"get_taxonomy failed for {q!r}: {type(exc).__name__}: {exc}")


# --------------------------------------------------------------------------- #
# 6. search_uniref (stretch)
# --------------------------------------------------------------------------- #
@mcp.tool
async def search_uniref(
    query: Annotated[
        str,
        Field(description="UniRef query, e.g. 'BRCA1', 'uniprotkb:P38398', or "
                          "'taxonomy_id:9606'."),
    ],
    identity: Annotated[
        Optional[Literal["1.0", "0.9", "0.5"]],
        Field(description="Cluster identity threshold: '1.0'=UniRef100, '0.9'=UniRef90, "
                          "'0.5'=UniRef50."),
    ] = None,
    size: Annotated[int, Field(description="Max clusters to return (1-500).", ge=1, le=500)] = 25,
    format: Annotated[
        Literal["summary", "tsv"],
        Field(description="'summary' = compact list (default); 'tsv' = table."),
    ] = "summary",
) -> str:
    """Search UniRef clusters (sequence-similarity clusters of UniProt proteins).

    UniRef100/90/50 group sequences at 100/90/50% identity. Returns cluster id,
    name, member/organism counts, and the representative member. Use `identity`
    to restrict to one clustering level.
    """
    full = f"({query.strip()}) AND identity:{identity}" if identity else query.strip()
    if not full:
        raise ToolError("`query` must not be empty.")
    try:
        if format == "summary":
            resp = check(await request(
                "GET", "/uniref/search",
                params={"query": full, "format": "json", "size": size},
            ))
            return formatting.summarize_uniref(resp.json().get("results", []), total_results(resp), full)
        resp = check(await request(
            "GET", "/uniref/search",
            params={"query": full, "format": "tsv", "fields": "id,name,count,organism,identity", "size": size},
        ))
        text = resp.text
        return text + _more_note(total_results(resp), max(0, text.rstrip().count("\n")))
    except ToolError:
        raise
    except Exception as exc:  # pragma: no cover
        raise ToolError(f"search_uniref failed: {type(exc).__name__}: {exc}")


# --------------------------------------------------------------------------- #
# 7. search_proteomes (stretch)
# --------------------------------------------------------------------------- #
@mcp.tool
async def search_proteomes(
    query: Annotated[
        Optional[str],
        Field(description="Free-text proteome query, e.g. 'Escherichia coli' or "
                          "'Mycobacterium tuberculosis'. May be omitted if organism_id is set."),
    ] = None,
    organism_id: Annotated[
        Optional[int],
        Field(description="NCBI taxonomy id filter, e.g. 9606 for human."),
    ] = None,
    reference_only: Annotated[
        bool,
        Field(description="Only reference proteomes (one high-quality proteome per species group)."),
    ] = False,
    size: Annotated[int, Field(description="Max proteomes to return (1-500).", ge=1, le=500)] = 25,
    format: Annotated[
        Literal["summary", "tsv"],
        Field(description="'summary' = compact list (default); 'tsv' = table."),
    ] = "summary",
) -> str:
    """Search UniProt proteomes (the protein set of an organism's genome).

    Returns the proteome id (UPID), organism, proteome type (Reference /
    Non-reference / etc.), and protein/gene counts. Filter by `organism_id` and
    set `reference_only=True` for reference proteomes.
    """
    parts: list[str] = []
    if query and query.strip():
        parts.append(f"({query.strip()})")
    if organism_id is not None:
        parts.append(f"organism_id:{organism_id}")
    if reference_only:
        parts.append("reference:true")
    if not parts:
        raise ToolError("Provide `query`, `organism_id`, or `reference_only=True`.")
    full = " AND ".join(parts)
    try:
        if format == "summary":
            resp = check(await request(
                "GET", "/proteomes/search",
                params={"query": full, "format": "json", "size": size},
            ))
            return formatting.summarize_proteomes(resp.json().get("results", []), total_results(resp), full)
        # NOTE: proteome_type has no result-field; tsv cannot include it.
        resp = check(await request(
            "GET", "/proteomes/search",
            params={"query": full, "format": "tsv",
                    "fields": "upid,organism,organism_id,protein_count,busco,cpd,genome_assembly", "size": size},
        ))
        text = resp.text
        return text + _more_note(total_results(resp), max(0, text.rstrip().count("\n")))
    except ToolError:
        raise
    except Exception as exc:  # pragma: no cover
        raise ToolError(f"search_proteomes failed: {type(exc).__name__}: {exc}")


# --------------------------------------------------------------------------- #
# resource: query cheat-sheet
# --------------------------------------------------------------------------- #
@mcp.resource(
    "resource://uniprot/query-cheatsheet",
    name="UniProt query cheat-sheet",
    description="Reference for UniProtKB query syntax (fields, operators, ranges, examples).",
    mime_type="text/markdown",
)
def query_cheatsheet() -> str:
    """Return the UniProtKB query cheat-sheet."""
    return CHEATSHEET


def main() -> None:
    """Console-script / module entry point: serve over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
