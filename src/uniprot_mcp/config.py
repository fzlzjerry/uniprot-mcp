"""Cached UniProt configuration used for validation.

Currently fetches ``/configure/idmapping/fields`` once per process and exposes
helpers to validate ``from_db``/``to_db`` for :func:`map_ids`. The validation
mirrors the live API rules:

* ``from_db`` must be a database with ``from == true``;
* ``to_db`` must be a database with ``to == true``;
* the target must appear in the source database's rule (``rules[ruleId].tos``).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from fastmcp.exceptions import ToolError

from .client import check, request


@dataclass
class IdMappingDbs:
    from_dbs: list[str]
    to_dbs: list[str]
    rule_tos: dict[int, set[str]]
    name_to_rule: dict[str, int]
    groups: list[tuple[str, list[str]]] = field(default_factory=list)

    def allowed_targets(self, from_db: str) -> list[str]:
        """Targets reachable from ``from_db`` (intersection of rule + to-dbs)."""
        rule = self.name_to_rule.get(from_db)
        if rule is None:
            return []
        tos = self.rule_tos.get(rule, set())
        return [t for t in self.to_dbs if t in tos]


_cache: IdMappingDbs | None = None
_lock = asyncio.Lock()


async def get_idmapping_dbs() -> IdMappingDbs:
    """Fetch + parse the idmapping field config once, then serve from cache."""
    global _cache
    if _cache is not None:
        return _cache
    async with _lock:
        if _cache is not None:
            return _cache
        resp = check(await request("GET", "/configure/idmapping/fields"))
        data = resp.json()
        from_dbs: list[str] = []
        to_dbs: list[str] = []
        name_to_rule: dict[str, int] = {}
        groups: list[tuple[str, list[str]]] = []
        for group in data.get("groups", []):
            names: list[str] = []
            for item in group.get("items", []):
                name = item.get("name")
                if not name:
                    continue
                names.append(name)
                if item.get("from"):
                    from_dbs.append(name)
                if item.get("to"):
                    to_dbs.append(name)
                rule_id = item.get("ruleId")
                if rule_id is not None:
                    name_to_rule[name] = rule_id
            if names:
                groups.append((group.get("groupName", ""), names))
        rule_tos: dict[int, set[str]] = {}
        for rule in data.get("rules", []):
            rid = rule.get("ruleId")
            if rid is not None:
                rule_tos[rid] = set(rule.get("tos", []))
        _cache = IdMappingDbs(
            from_dbs=from_dbs,
            to_dbs=to_dbs,
            rule_tos=rule_tos,
            name_to_rule=name_to_rule,
            groups=groups,
        )
        return _cache


def _suggest(value: str, options: list[str], limit: int = 12) -> str:
    """Cheap substring-based hint to help the model recover from a typo."""
    low = value.strip().lower()
    if len(low) < 2:  # an empty/1-char fragment is a substring of everything
        return ""
    hits = [o for o in options if low in o.lower() or o.lower() in low]
    if not hits:
        hits = [o for o in options if low[:3] in o.lower()]
    return ", ".join(hits[:limit])


async def validate_pair(from_db: str, to_db: str) -> None:
    """Raise ``ToolError`` with the allowed list if the db pair is invalid."""
    dbs = await get_idmapping_dbs()

    if from_db not in dbs.from_dbs:
        hint = _suggest(from_db, dbs.from_dbs)
        raise ToolError(
            f"Invalid from_db {from_db!r}. It is not a valid ID-mapping source database."
            + (f" Did you mean one of: {hint}?" if hint else "")
            + f"\nValid from_db values ({len(dbs.from_dbs)}): {', '.join(dbs.from_dbs)}"
        )

    if to_db not in dbs.to_dbs:
        hint = _suggest(to_db, dbs.to_dbs)
        raise ToolError(
            f"Invalid to_db {to_db!r}. It is not a valid ID-mapping target database."
            + (f" Did you mean one of: {hint}?" if hint else "")
            + f"\nValid to_db values ({len(dbs.to_dbs)}): {', '.join(dbs.to_dbs)}"
        )

    allowed = dbs.allowed_targets(from_db)
    if to_db not in allowed:
        raise ToolError(
            f"Mapping {from_db!r} -> {to_db!r} is not supported by UniProt. "
            f"From {from_db!r} you can map to: {', '.join(allowed) if allowed else '(none)'}."
        )
