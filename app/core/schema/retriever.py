from __future__ import annotations

from app.config.connections import registry
from app.core.schema.glossary import get_glossary
from app.core.schema.models import SchemaSnapshot, TableMeta, ColumnMeta


class SchemaRetriever:
    """Introspects live schema and merges it with the business glossary.

    Only the tables relevant to a question (matched by glossary aliases /
    column synonyms or by a supplied allowlist) are returned, to keep the
    prompt small and accurate.
    """

    def snapshot(self, db_name: str) -> SchemaSnapshot:
        return registry.get(db_name).introspect()

    def relevant_tables(self, db_name: str, question: str) -> list[str]:
        """Cheap keyword match to limit injected tables."""
        snap = self.snapshot(db_name)
        q = question.lower()
        glossary = get_glossary().for_database(db_name)
        picked: list[str] = []
        for t in snap.tables:
            tdef = glossary.get("tables", {}).get(t.name, {})
            hay = " ".join(
                [t.name]
                + tdef.get("aliases", [])
                + [t.description]
                + list(tdef.get("columns", {}).keys())
                + [c for c in tdef.get("columns", {}).values()
                   for c in c.get("synonyms", [])]
            ).lower()
            if any(token in q for token in hay.split() if len(token) > 3):
                picked.append(t.qualified_name)
        return picked or snap.table_names()

    def build_context(self, db_name: str, question: str) -> str:
        """Return DDL + glossary context block for the LLM prompt."""
        snap = self.snapshot(db_name)
        tables = set(self.relevant_tables(db_name, question))
        # Pull in tables reachable via foreign keys so join targets are always
        # present in the prompt (otherwise the model references unknown tables).
        by_name = {t.qualified_name: t for t in snap.tables}
        for name in list(tables):
            t = by_name.get(name)
            if not t:
                continue
            for fk in t.foreign_keys:
                if fk.ref_qualified in by_name:
                    tables.add(fk.ref_qualified)
        filtered = SchemaSnapshot(
            db_name=snap.db_name,
            engine=snap.engine,
            tables=[t for t in snap.tables if t.qualified_name in tables],
        )
        ddl = filtered.render_ddl()
        biz = get_glossary().render_for_prompt(db_name, tables)
        return f"{ddl}\n\n{biz}".strip()
