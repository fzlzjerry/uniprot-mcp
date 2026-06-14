# uniprot-mcp

A production-quality **MCP server** that exposes the [UniProt REST API](https://rest.uniprot.org)
to LLM clients (Claude Code, Claude Desktop, …) over **stdio**. Built with
[FastMCP](https://gofastmcp.com) and managed with [`uv`](https://docs.astral.sh/uv/).

Tools return **compact, token-efficient summaries by default** and full payloads
only on request, with robust error handling and an embedded UniProt query
cheat-sheet so the model writes valid queries.

## Tools

| Tool | What it does |
|------|--------------|
| `search_uniprotkb` | Search UniProtKB with native query syntax. `reviewed` / `organism_id` filters are added for you. Summary, FASTA, or TSV output. |
| `get_entry` | One entry as a curated digest (function, names, organism, length, subcellular location, family/domains, key features, PTMs, keywords, PDB/AlphaFold/Ensembl/RefSeq/InterPro/GO cross-refs) or `json`/`fasta`/`txt`/`gff`. |
| `get_fasta` | Raw FASTA for one accession or a batch. |
| `map_ids` | Convert ids across databases via UniProt's async ID-mapping (e.g. `RefSeq_Protein`→`UniProtKB`, `UniProtKB_AC-ID`→`PDB`). Returns mapped pairs **and** unmapped ids; validates the db pair against the live config. |
| `get_taxonomy` | Resolve an organism name or taxon id → taxon id, names, rank, lineage. Turn "human" into `organism_id:9606`. |
| `search_uniref` | Search UniRef100/90/50 sequence-similarity clusters. |
| `search_proteomes` | Search proteomes (whole-organism protein sets); reference-proteome filter. |

Plus an MCP **resource** `resource://uniprot/query-cheatsheet` documenting the
UniProtKB query syntax (`gene:`, `organism_id:`, `reviewed:true`,
`length:[X TO Y]`, `keyword:`, `ec:`, boolean `AND/OR/NOT`, …).

## Requirements

- Python ≥ 3.10 (the repo pins 3.13 via `.python-version`)
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/)

## Install

```bash
git clone <this repo>   # or copy the directory
cd uniprot
uv sync                 # creates .venv and installs fastmcp + httpx
```

## Run

```bash
# stdio server (what MCP clients launch):
uv run uniprot-mcp
```

UniProt asks API clients to identify themselves with a contact address. Set one
via the `UNIPROT_MCP_CONTACT` environment variable (it goes into the
`User-Agent`); otherwise a placeholder is used.

```bash
UNIPROT_MCP_CONTACT="you@example.org" uv run uniprot-mcp
```

## Run with `uvx` (no clone / no sync)

`uvx` (a.k.a. `uv tool run`) fetches, builds, and runs the console script in a
throwaway environment — nothing to install first. Pick whichever source you have:

```bash
# From PyPI (once published — see "Publishing" below):
uvx uniprot-mcp

# From a Git repo:
uvx --from git+https://github.com/fzlzjerry/uniprot-mcp uniprot-mcp

# From a local checkout (this directory):
uvx --from /ABSOLUTE/PATH/TO/uniprot uniprot-mcp

# From a built wheel:
uvx --from ./dist/uniprot_mcp-0.1.0-py3-none-any.whl uniprot-mcp
```

Pin a version with `uvx uniprot-mcp@0.1.0`, or force a refresh of the cached
build with `uvx --refresh --from <source> uniprot-mcp`.

## Register with Claude Desktop

Edit `claude_desktop_config.json`
(macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`,
Windows: `%APPDATA%\Claude\claude_desktop_config.json`) and add:

```json
{
  "mcpServers": {
    "uniprot": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/fzlzjerry/uniprot-mcp", "uniprot-mcp"],
      "env": { "UNIPROT_MCP_CONTACT": "you@example.org" }
    }
  }
}
```

Swap the `--from` source for a local path (`/ABSOLUTE/PATH/TO/uniprot`) or, once
published, drop `--from` entirely and use `"args": ["uniprot-mcp"]`. Make sure
`uvx` is on the `PATH` Claude Desktop sees (it ships with `uv`; give the absolute
path to `uvx` if needed, e.g. `~/.local/bin/uvx`). Restart Claude Desktop and the
`uniprot` tools appear.

> Prefer a cloned checkout instead of `uvx`? Use
> `"command": "uv", "args": ["run", "--directory", "/ABSOLUTE/PATH/TO/uniprot", "uniprot-mcp"]`.

## Register with Claude Code

Project-scoped via a `.mcp.json` in your project root (same shape):

```json
{
  "mcpServers": {
    "uniprot": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/fzlzjerry/uniprot-mcp", "uniprot-mcp"],
      "env": { "UNIPROT_MCP_CONTACT": "you@example.org" }
    }
  }
}
```

Or from the CLI:

```bash
# via uvx (published / git / local source):
claude mcp add uniprot -e UNIPROT_MCP_CONTACT=you@example.org -- uvx uniprot-mcp

# via a local checkout with uv:
claude mcp add uniprot -e UNIPROT_MCP_CONTACT=you@example.org \
  -- uv run --directory /ABSOLUTE/PATH/TO/uniprot uniprot-mcp
```

## Smoke test

Exercises every tool against the live API and prints the output:

```bash
UNIPROT_MCP_CONTACT="you@example.org" uv run python -m tests.smoke
```

## Publishing to PyPI (enables bare `uvx uniprot-mcp`)

```bash
# 1. Put your real repo URLs in [project.urls] (replace fzlzjerry), bump version.
# 2. Build sdist + wheel:
uv build
# 3. Publish (needs a PyPI API token):
UV_PUBLISH_TOKEN="pypi-..." uv publish
```

After it is on PyPI, anyone can run it with **`uvx uniprot-mcp`** — no clone, no
install — and the Claude config simplifies to `"command": "uvx", "args": ["uniprot-mcp"]`.
(The package name `uniprot-mcp` must be free on PyPI; pick another `name` in
`pyproject.toml` if it is taken.)

## Design notes

- **Single shared `httpx.AsyncClient`** with a descriptive `User-Agent` including
  your contact.
- **Retry/backoff** on `429` (honoring `Retry-After`) and `5xx`; `400` surfaces
  UniProt's own error message; no raw tracebacks reach the client (errors are
  raised as `ToolError`).
- **Pagination** via the `Link` header / `x-total-results`; result sizes are
  capped (≤ 500) and the total is always reported so you can narrow or page.
- **ID mapping** follows the real async flow: `POST /idmapping/run` →
  poll `/idmapping/status/{job}` (a `303` + `Location` signals completion) →
  fetch results, automatically choosing the enriched UniProtKB results endpoint
  vs. the simple-pair endpoint based on the target database.

## Project layout

```
src/uniprot_mcp/
  server.py       # FastMCP instance, the 7 tools, cheat-sheet resource, main()
  client.py       # shared AsyncClient, retry/backoff, error mapping, header parsing
  idmapping.py    # async run/poll/results flow with target-aware routing
  config.py       # cached idmapping db config + from/to validation
  formatting.py   # JSON -> compact summary digests
  cheatsheet.py   # UniProt query cheat-sheet
tests/smoke.py    # live-API smoke test
```
