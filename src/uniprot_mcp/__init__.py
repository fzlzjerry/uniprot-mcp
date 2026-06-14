"""UniProt MCP server.

A FastMCP server that exposes the UniProt REST API (https://rest.uniprot.org)
to LLM clients over stdio: UniProtKB search, entry/FASTA retrieval, the async
ID-mapping flow, taxonomy resolution, and UniRef/Proteomes search.
"""

__version__ = "0.1.0"
