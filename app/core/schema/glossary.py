from __future__ import annotations

from functools import lru_cache
from typing import Any

from app.core.config_store import get_config_store


class Glossary:
    """Business-context / semantic layer, sourced from the config store."""

    def for_database(self, db_name: str) -> dict[str, Any]:
        return get_config_store().get_glossary().get(db_name, {})

    def render_for_prompt(self, db_name: str, tables: list[str] | None = None) -> str:
        db = self.for_database(db_name)
        if not db:
            return ""

        lines: list[str] = [f"# Business context for database '{db_name}'"]
        if desc := db.get("description"):
            lines.append(f"Domain: {desc.strip()}")

        tables_block = db.get("tables", {})
        if tables:
            tables_block = {t: tables_block[t] for t in tables if t in tables_block}

        for tname, tdef in tables_block.items():
            lines.append(f"\n## Table: {tname}")
            if d := tdef.get("description"):
                lines.append(f"  Meaning: {d.strip()}")
            if a := tdef.get("aliases"):
                lines.append(f"  Also called: {', '.join(a)}")
            for cname, cdef in tdef.get("columns", {}).items():
                meaning = cdef.get("meaning", "").strip()
                flags = []
                if cdef.get("is_key"):
                    flags.append("KEY")
                if cdef.get("is_pii"):
                    flags.append("PII")
                tag = f" [{', '.join(flags)}]" if flags else ""
                line = f"  - {cname}{tag}: {meaning}"
                if s := cdef.get("synonyms"):
                    line += f" (user may say: {', '.join(s)})"
                if e := cdef.get("example"):
                    line += f" e.g. {e}"
                lines.append(line)

        if rel := db.get("relationships"):
            lines.append("\n## Known relationships")
            lines += [f"  - {r}" for r in rel]

        return "\n".join(lines)


@lru_cache
def get_glossary() -> Glossary:
    return Glossary()
