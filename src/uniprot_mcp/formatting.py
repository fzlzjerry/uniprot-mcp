"""Turn UniProt JSON payloads into compact, model-readable text digests.

All extractors are defensive: TrEMBL entries omit most comments/features/
keywords, ``recommendedName`` may be absent, and ``properties`` on a cross-
reference is a list of ``{key, value}`` (never a dict). Field paths follow the
live ``rest.uniprot.org`` schema (release 2026_02).
"""

from __future__ import annotations

from typing import Any

REVIEWED_ENTRY_TYPE = "UniProtKB reviewed (Swiss-Prot)"

# Feature ``type`` values are Title-Case strings in the JSON (not flat-file tokens).
PTM_FEATURE_TYPES = {
    "Modified residue",
    "Glycosylation",
    "Lipidation",
    "Disulfide bond",
    "Cross-link",
}
DOMAIN_FEATURE_TYPES = [
    "Domain",
    "Region",
    "Repeat",
    "Zinc finger",
    "Binding site",
    "Active site",
    "Motif",
    "DNA binding",
    "Transmembrane",
    "Signal",
]


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def _cap_list(values: list[Any], cap: int, sep: str = ", ") -> str:
    vals = [str(v) for v in values]
    shown = vals[:cap]
    extra = len(vals) - len(shown)
    out = sep.join(shown)
    if extra > 0:
        out += f" (+{extra} more)"
    return out


def _truncate(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + " …(truncated)"


def _prop(xref: dict, key: str) -> str | None:
    for p in xref.get("properties", []) or []:
        if p.get("key") == key:
            return p.get("value")
    return None


# --------------------------------------------------------------------------- #
# entry field extractors (shared by search + entry summaries)
# --------------------------------------------------------------------------- #
def accession(e: dict) -> str:
    return e.get("primaryAccession") or e.get("uniParcId") or "?"


def entry_name(e: dict) -> str:
    return e.get("uniProtkbId") or ""


def is_reviewed(e: dict) -> bool:
    return e.get("entryType") == REVIEWED_ENTRY_TYPE


def status_label(e: dict) -> str:
    return "Swiss-Prot" if is_reviewed(e) else "TrEMBL"


def protein_name(e: dict) -> str:
    pd = e.get("proteinDescription") or {}
    rec = pd.get("recommendedName") or {}
    name = (rec.get("fullName") or {}).get("value")
    if not name:
        subs = pd.get("submissionNames") or []
        if subs:
            name = (subs[0].get("fullName") or {}).get("value")
    if not name:
        alts = pd.get("alternativeNames") or []
        if alts:
            name = (alts[0].get("fullName") or {}).get("value")
    return name or "(unnamed protein)"


def ec_numbers(e: dict) -> list[str]:
    pd = e.get("proteinDescription") or {}
    out: list[str] = []
    for holder in (pd.get("recommendedName") or {},):
        out += [x.get("value") for x in holder.get("ecNumbers", []) or [] if x.get("value")]
    for grp in ("alternativeNames", "submissionNames"):
        for holder in pd.get(grp, []) or []:
            out += [x.get("value") for x in holder.get("ecNumbers", []) or [] if x.get("value")]
    # de-dup preserving order
    seen: set[str] = set()
    return [x for x in out if not (x in seen or seen.add(x))]


def gene_symbol(e: dict) -> str | None:
    for g in e.get("genes", []) or []:
        gn = g.get("geneName")
        if gn and gn.get("value"):
            return gn["value"]
        for alt in ("orderedLocusNames", "orfNames"):
            vals = g.get(alt) or []
            if vals and vals[0].get("value"):
                return vals[0]["value"]
    return None


def organism_str(e: dict) -> str:
    org = e.get("organism") or {}
    sci = org.get("scientificName") or "?"
    taxon = org.get("taxonId")
    common = org.get("commonName")
    s = sci
    if common:
        s += f" ({common})"
    if taxon:
        s += f" [taxon {taxon}]"
    return s


def seq_length(e: dict) -> int | None:
    return (e.get("sequence") or {}).get("length")


def _comments(e: dict, ctype: str) -> list[dict]:
    return [c for c in e.get("comments", []) or [] if c.get("commentType") == ctype]


def _comment_text(comments: list[dict]) -> str:
    parts: list[str] = []
    for c in comments:
        for t in c.get("texts", []) or []:
            if t.get("value"):
                parts.append(t["value"])
    return " ".join(parts)


# --------------------------------------------------------------------------- #
# search summary
# --------------------------------------------------------------------------- #
def summarize_search(items: list[dict], total: int | None, query: str) -> str:
    returned = len(items)
    if returned == 0:
        return (
            f"No UniProtKB entries matched: {query}\n"
            "Tip: broaden the query, check field syntax (resource://uniprot/query-cheatsheet), "
            "or drop the reviewed/organism filters."
        )
    lines = []
    total_str = str(total) if total is not None else "?"
    lines.append(f"Query: {query}")
    lines.append(f"Total matches: {total_str} · showing {returned}")
    if total is not None and total > returned:
        lines.append(
            f"({total - returned} more not shown — raise `size` (max 500) or narrow the query.)"
        )
    lines.append("")
    for i, e in enumerate(items, 1):
        gene = gene_symbol(e)
        ecs = ec_numbers(e)
        head = f"{i}. {accession(e)} {entry_name(e)} [{status_label(e)}] — {protein_name(e)}"
        if ecs:
            head += f" (EC {', '.join(ecs[:3])})"
        meta = []
        if gene:
            meta.append(f"gene {gene}")
        meta.append(organism_str(e))
        ln = seq_length(e)
        if ln:
            meta.append(f"{ln} aa")
        lines.append(head)
        lines.append("   " + " · ".join(meta))
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# entry summary
# --------------------------------------------------------------------------- #
def summarize_entry(e: dict) -> str:
    L: list[str] = []
    secondary = e.get("secondaryAccessions") or []
    head = f"{accession(e)} · {entry_name(e)} · {status_label(e)}"
    L.append(head)
    L.append("=" * len(head))

    name = protein_name(e)
    ecs = ec_numbers(e)
    L.append(f"Protein: {name}" + (f"  (EC {', '.join(ecs)})" if ecs else ""))

    genes = e.get("genes") or []
    if genes:
        g0 = genes[0]
        primary = (g0.get("geneName") or {}).get("value")
        syns = [s.get("value") for s in g0.get("synonyms", []) or [] if s.get("value")]
        gline = f"Gene: {primary or '—'}"
        if syns:
            gline += f"  (synonyms: {_cap_list(syns, 6)})"
        if len(genes) > 1:
            gline += f"  [+{len(genes) - 1} more gene(s)]"
        L.append(gline)

    L.append(f"Organism: {organism_str(e)}")
    lineage = (e.get("organism") or {}).get("lineage") or []
    if lineage:
        L.append("Lineage: " + " > ".join(lineage[:6]) + (" > …" if len(lineage) > 6 else ""))

    seq = e.get("sequence") or {}
    if seq:
        mw = seq.get("molWeight")
        L.append(
            f"Length: {seq.get('length', '?')} aa"
            + (f" · Mass: {mw} Da" if mw else "")
            + (f" · CRC64: {seq.get('crc64')}" if seq.get("crc64") else "")
        )
    if e.get("proteinExistence"):
        L.append(f"Existence: {e['proteinExistence']}")
    if secondary:
        L.append(f"Secondary accessions: {_cap_list(secondary, 8)}")

    func = _comment_text(_comments(e, "FUNCTION"))
    if func:
        L.append("")
        L.append("Function: " + _truncate(func, 700))

    subs = []
    for c in _comments(e, "SUBCELLULAR LOCATION"):
        for loc in c.get("subcellularLocations", []) or []:
            v = (loc.get("location") or {}).get("value")
            if v:
                subs.append(v)
    if subs:
        L.append("Subcellular location: " + _cap_list(list(dict.fromkeys(subs)), 12))

    family = _comment_text(_comments(e, "SIMILARITY"))
    if not family:
        # fall back to InterPro / Pfam entry names
        names = []
        for x in e.get("uniProtKBCrossReferences", []) or []:
            if x.get("database") in ("InterPro", "Pfam"):
                nm = _prop(x, "EntryName")
                if nm and nm != "-":
                    names.append(nm)
        if names:
            family = "Contains domains: " + _cap_list(list(dict.fromkeys(names)), 8)
    if family:
        L.append("Family/domains: " + _truncate(family, 400))

    diseases = [
        (c.get("disease") or {}).get("diseaseId")
        for c in _comments(e, "DISEASE")
        if (c.get("disease") or {}).get("diseaseId")
    ]
    if diseases:
        L.append("Disease: " + _cap_list(diseases, 8))

    # features grouped by type
    feats: dict[str, int] = {}
    feat_examples: dict[str, list[str]] = {}
    for f in e.get("features", []) or []:
        t = f.get("type")
        if not t:
            continue
        feats[t] = feats.get(t, 0) + 1
        if t in DOMAIN_FEATURE_TYPES and len(feat_examples.get(t, [])) < 4:
            loc = f.get("location") or {}
            start = (loc.get("start") or {}).get("value")
            end = (loc.get("end") or {}).get("value")
            desc = f.get("description") or t
            span = f"{start}-{end}" if start is not None else "?"
            feat_examples.setdefault(t, []).append(f"{desc} ({span})")

    domain_lines = []
    for t in DOMAIN_FEATURE_TYPES:
        if feats.get(t):
            ex = feat_examples.get(t)
            line = f"{t} ×{feats[t]}"
            if ex:
                line += ": " + _cap_list(ex, 4)
            domain_lines.append(line)
    if domain_lines:
        L.append("")
        L.append("Key features:")
        for dl in domain_lines:
            L.append("  - " + dl)

    ptm_counts = {t: feats[t] for t in PTM_FEATURE_TYPES if feats.get(t)}
    ptm_note = _comment_text(_comments(e, "PTM"))
    if ptm_counts or ptm_note:
        ptm_str = ", ".join(f"{k} ×{v}" for k, v in ptm_counts.items())
        line = "PTMs: " + (ptm_str or "—")
        if ptm_note:
            line += " · " + _truncate(ptm_note, 200)
        L.append(line)

    kws = [k.get("name") for k in e.get("keywords", []) or [] if k.get("name")]
    if kws:
        L.append("Keywords: " + _cap_list(kws, 15))

    # cross references of interest
    xrefs = e.get("uniProtKBCrossReferences", []) or []
    by_db: dict[str, list[dict]] = {}
    for x in xrefs:
        by_db.setdefault(x.get("database", "?"), []).append(x)

    xlines: list[str] = []
    if "PDB" in by_db:
        ids = [x.get("id") for x in by_db["PDB"]]
        xlines.append(f"PDB ({len(ids)}): {_cap_list(ids, 12)}")
    if "AlphaFoldDB" in by_db:
        af = by_db["AlphaFoldDB"][0].get("id")
        xlines.append(f"AlphaFold: {af}")
    ens = [x for db, lst in by_db.items() if db.startswith("Ensembl") for x in lst]
    if ens:
        xlines.append(f"Ensembl: {len(ens)} transcript/genome ref(s)")
    if "RefSeq" in by_db:
        ids = [x.get("id") for x in by_db["RefSeq"]]
        xlines.append(f"RefSeq ({len(ids)}): {_cap_list(ids, 6)}")
    if "InterPro" in by_db:
        items = [f"{x.get('id')}={_prop(x, 'EntryName') or ''}".rstrip("=") for x in by_db["InterPro"]]
        xlines.append(f"InterPro ({len(items)}): {_cap_list(items, 8)}")
    if "GO" in by_db:
        aspects = {"C": [], "F": [], "P": []}
        for x in by_db["GO"]:
            term = _prop(x, "GoTerm") or ""
            if len(term) > 2 and term[1] == ":":
                aspects.get(term[0], []).append(term[2:])
        go_bits = []
        names = {"F": "function", "P": "process", "C": "component"}
        for a in ("F", "P", "C"):
            if aspects[a]:
                go_bits.append(f"{names[a]} ×{len(aspects[a])} ({_cap_list(aspects[a], 3)})")
        if go_bits:
            xlines.append("GO: " + "; ".join(go_bits))
    if xlines:
        L.append("")
        L.append("Cross-references:")
        for xl in xlines:
            L.append("  - " + xl)

    L.append("")
    L.append(f"(Full record: get_entry('{accession(e)}', format='json'). "
             f"Sequence: format='fasta'.)")
    return "\n".join(L)


# --------------------------------------------------------------------------- #
# taxonomy
# --------------------------------------------------------------------------- #
def summarize_taxonomy_record(t: dict) -> str:
    L = []
    L.append(f"Taxon {t.get('taxonId')}: {t.get('scientificName')}"
             + (f" ({t.get('commonName')})" if t.get("commonName") else ""))
    if t.get("rank"):
        L.append(f"Rank: {t['rank']}")
    if t.get("mnemonic"):
        L.append(f"Mnemonic: {t['mnemonic']}")
    parent = (t.get("parent") or {}).get("taxonId")
    if parent:
        L.append(f"Parent taxon: {parent}")
    lineage = t.get("lineage") or []
    if lineage:
        names = [l.get("scientificName", l) if isinstance(l, dict) else l for l in lineage]
        L.append("Lineage: " + " > ".join(str(n) for n in names[:12]))
    others = t.get("otherNames") or []
    if others:
        L.append("Other names: " + _cap_list(others, 6))
    L.append(f"→ Use organism_id:{t.get('taxonId')} in search_uniprotkb.")
    return "\n".join(L)


def summarize_taxonomy_search(results: list[dict], query: str) -> str:
    if not results:
        return f"No taxonomy entries matched {query!r}."
    if len(results) == 1:
        return summarize_taxonomy_record(results[0])
    L = [f"{len(results)} taxonomy match(es) for {query!r}:"]
    for t in results:
        common = f" ({t.get('commonName')})" if t.get("commonName") else ""
        L.append(
            f"  - {t.get('taxonId')}  {t.get('scientificName')}{common}"
            f"  [{t.get('rank', '?')}]  → organism_id:{t.get('taxonId')}"
        )
    return "\n".join(L)


# --------------------------------------------------------------------------- #
# id mapping
# --------------------------------------------------------------------------- #
def summarize_idmapping(from_db: str, to_db: str, result: dict) -> str:
    rows = result.get("rows", [])
    failed = result.get("failed", [])
    total = result.get("total")
    truncated = result.get("truncated", False)
    obsolete = result.get("obsolete_count")
    enriched = result.get("enriched", False)

    L = []
    total_str = str(total) if total is not None else str(len(rows))
    L.append(f"ID mapping {from_db} → {to_db}: {total_str} result row(s); showing {len(rows)}.")
    if truncated:
        L.append("(Result list truncated — refine your input id set for the full mapping.)")
    if obsolete:
        L.append(f"Obsolete entries skipped: {obsolete}")
    L.append("")

    # group by source id
    grouped: dict[str, list[dict]] = {}
    order: list[str] = []
    for r in rows:
        frm = r.get("from")
        if frm not in grouped:
            grouped[frm] = []
            order.append(frm)
        grouped[frm].append(r)

    for frm in order:
        targets = grouped[frm]
        if enriched:
            parts = []
            for r in targets[:10]:
                bits = r.get("to", "?")
                extra = []
                if r.get("entry_name"):
                    extra.append(r["entry_name"])
                if r.get("protein"):
                    extra.append(r["protein"])
                if r.get("organism"):
                    extra.append(r["organism"])
                if extra:
                    bits += f" ({'; '.join(extra)})"
                parts.append(bits)
            more = len(targets) - len(parts)
            line = f"{frm} → " + " | ".join(parts)
            if more > 0:
                line += f" (+{more} more)"
            L.append(line)
        else:
            tos = [r.get("to") for r in targets]
            L.append(f"{frm} → {_cap_list(tos, 30)}")

    if failed:
        L.append("")
        L.append(f"Unmapped input ids ({len(failed)}): {_cap_list(failed, 50)}")
    return "\n".join(L)


# --------------------------------------------------------------------------- #
# uniref / proteomes
# --------------------------------------------------------------------------- #
def summarize_uniref(items: list[dict], total: int | None, query: str) -> str:
    if not items:
        return f"No UniRef clusters matched: {query}"
    L = [f"UniRef — {total if total is not None else len(items)} match(es) for: {query} · showing {len(items)}"]
    if total is not None and total > len(items):
        L.append(f"({total - len(items)} more — raise `size` or narrow the query.)")
    L.append("")
    for i, c in enumerate(items, 1):
        ct = (c.get("commonTaxon") or {}).get("scientificName", "")
        rep = (c.get("representativeMember") or {}).get("memberId", "")
        L.append(f"{i}. {c.get('id')} — {c.get('name', '')}")
        meta = [f"members {c.get('memberCount', '?')}", f"organisms {c.get('organismCount', '?')}"]
        if rep:
            meta.append(f"rep {rep}")
        if ct:
            meta.append(ct)
        L.append("   " + " · ".join(meta))
    return "\n".join(L)


def summarize_proteomes(items: list[dict], total: int | None, query: str) -> str:
    if not items:
        return f"No proteomes matched: {query}"
    L = [f"Proteomes — {total if total is not None else len(items)} match(es) for: {query} · showing {len(items)}"]
    if total is not None and total > len(items):
        L.append(f"({total - len(items)} more — raise `size` or narrow the query.)")
    L.append("")
    for i, p in enumerate(items, 1):
        tax = p.get("taxonomy") or {}
        org = tax.get("scientificName", "?")
        tid = tax.get("taxonId")
        stats = p.get("proteomeStatistics") or {}
        L.append(f"{i}. {p.get('id')} — {org}" + (f" [taxon {tid}]" if tid else ""))
        meta = [p.get("proteomeType", "?")]
        if p.get("proteinCount") is not None:
            meta.append(f"proteins {p['proteinCount']}")
        if p.get("geneCount") is not None:
            meta.append(f"genes {p['geneCount']}")
        if stats.get("reviewedProteinCount") is not None:
            meta.append(f"reviewed {stats['reviewedProteinCount']}")
        L.append("   " + " · ".join(str(m) for m in meta))
    return "\n".join(L)
