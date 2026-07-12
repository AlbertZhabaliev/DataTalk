from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ColumnMeta:
    name: str
    type: str
    description: str = ""


@dataclass
class ForeignKeyMeta:
    columns: list[str]
    ref_table: str
    ref_columns: list[str]
    ref_schema: str = ""

    @property
    def ref_qualified(self) -> str:
        return f"{self.ref_schema}.{self.ref_table}" if self.ref_schema else self.ref_table


@dataclass
class TableMeta:
    name: str
    columns: list[ColumnMeta]
    schema: str = ""
    description: str = ""
    primary_key: list[str] = field(default_factory=list)
    foreign_keys: list[ForeignKeyMeta] = field(default_factory=list)

    @property
    def qualified_name(self) -> str:
        return f"{self.schema}.{self.name}" if self.schema else self.name


@dataclass
class SchemaSnapshot:
    db_name: str
    engine: str
    tables: list[TableMeta]

    def table_names(self) -> list[str]:
        return [t.qualified_name for t in self.tables]

    def render_ddl(self) -> str:
        """Compact DDL-like block for the LLM prompt.

        Table and column comments are emitted inline as ``-- comment`` hints so
        the model can use human descriptions to disambiguate the question.
        """
        out = [f"-- Schema of '{self.db_name}' ({self.engine})"]
        for t in self.tables:
            if t.description:
                out.append(f"-- {t.qualified_name}: {t.description}")
            entries: list[tuple[str, str]] = []
            for c in t.columns:
                comment = f"  -- {c.description}" if c.description else ""
                entries.append((f"{c.name} {c.type}", comment))
            if t.primary_key:
                entries.append((f"PRIMARY KEY ({', '.join(t.primary_key)})", ""))
            for fk in t.foreign_keys:
                entries.append((
                    f"FOREIGN KEY ({', '.join(fk.columns)}) "
                    f"REFERENCES {fk.ref_qualified} ({', '.join(fk.ref_columns)})",
                    "",
                ))
            lines = []
            for i, (defn, comment) in enumerate(entries):
                sep = "," if i < len(entries) - 1 else ""
                lines.append(f"  {defn}{sep}{comment}")
            out.append(f"CREATE TABLE {t.qualified_name} (\n" + "\n".join(lines) + "\n);")
        return "\n".join(out)
