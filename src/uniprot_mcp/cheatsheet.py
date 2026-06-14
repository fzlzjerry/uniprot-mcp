"""UniProt query cheat-sheet.

Exposed as an MCP resource (``resource://uniprot/query-cheatsheet``) and reused
in tool docstrings so the model writes valid UniProtKB queries.
"""

CHEATSHEET = """\
# UniProtKB query cheat-sheet

Queries use UniProt's native search syntax: `field:value` terms joined with
boolean operators. Combine freely; quote multi-word values.

## Boolean & grouping
- `AND`, `OR`, `NOT` (uppercase). Example: `insulin AND organism_id:9606`
- Group with parentheses: `(kinase OR phosphatase) AND reviewed:true`
- Quote phrases: `protein_name:"DNA polymerase"`
- Default (no field) searches across many fields: `BRCA1`

## Most useful fields
- `gene:BRCA1` — gene name (also `gene_exact:BRCA1`)
- `organism_id:9606` — NCBI taxon id (use get_taxonomy to resolve a name -> id)
- `organism_name:human` — organism by name
- `taxonomy_id:40674` — taxon **and all descendants** (e.g. all mammals)
- `reviewed:true` — Swiss-Prot only (`reviewed:false` -> TrEMBL)
- `accession:P38398` — by accession
- `protein_name:"breast cancer type 1"`
- `length:[100 TO 200]` — sequence length range (`[* TO 500]` for open ends)
- `mass:[10000 TO 20000]` — molecular weight (Da)
- `existence:1` — protein existence evidence (1 protein, 2 transcript, 3 homology, 4 predicted, 5 uncertain)
- `keyword:KW-0067` or `keyword:ATP-binding` — UniProt keyword
- `ec:2.7.11.1` — Enzyme Commission number
- `xref:pdb-1JM7` / `database:pdb` — has a cross-reference to a database
- `go:0005634` — Gene Ontology term (by GO id)
- `cc_scl_term:SL-0191` — subcellular location
- `cc_disease:cancer` — associated disease text
- `fragment:false` — exclude sequence fragments
- `proteome:UP000005640` — member of a proteome

## Ranges & wildcards
- Ranges: `length:[50 TO 150]`, dates `date_modified:[2024-01-01 TO *]`
- Wildcard: `gene:BRCA*`

## Examples
- Reviewed human kinases: `keyword:Kinase AND organism_id:9606 AND reviewed:true`
- BRCA1 in human, Swiss-Prot: `gene:BRCA1 AND organism_id:9606 AND reviewed:true`
- Short reviewed peptides: `reviewed:true AND length:[2 TO 30]`
- Has a PDB structure: `gene:TP53 AND database:pdb AND reviewed:true`

Tip: `search_uniprotkb` adds `reviewed:` and `organism_id:` filters for you when
you pass the `reviewed`/`organism_id` arguments — no need to put them in `query`.
"""

# Short version embedded in tool docstrings.
SEARCH_HINT = (
    "Query syntax: field:value joined by AND/OR/NOT, parentheses to group, "
    'quote phrases. Key fields: gene:, organism_id:, taxonomy_id:, reviewed:true, '
    "length:[X TO Y], existence:, keyword:, ec:, database:pdb, go:. "
    "Full reference: resource://uniprot/query-cheatsheet."
)
