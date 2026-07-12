from __future__ import annotations

import json

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

from app.core.config_store import get_config_store


DEFAULT_SYSTEM_PROMPT = """You are an expert {dialect} SQL engineer.
Given the user's natural-language question and the database schema + business
context below, write a single, valid, read-only SQL query.

Rules:
- Target DBMS is {dialect}. Use the correct SQL syntax for this dialect.
- Output ONLY the SQL. No commentary, no markdown fences.
- Use the business context to map business terms to the right columns.
- Prefer JOINs using the provided relationships.
- Do NOT use any write/DDL operations (INSERT/UPDATE/DELETE/DROP/ALTER/etc.).
- If aggregation is needed, include a sensible GROUP BY.
- If the question is ambiguous, make a reasonable assumption and note it in a
  trailing SQL comment.
"""

# Stronger system prompt for a locally hosted / fine-tuned model that is
# specialized for SQL + chart + report generation.
_TUNED_SYSTEM = """You are a model fine-tuned for analytics over databases.
Given the schema + business context and the user's question, return a JSON
object with exactly these keys:
  "sql": a single valid, read-only {dialect} SELECT/WITH statement
  "chart": one of ["bar","line","pie","table"] best representing the result
  "report": a 2-4 sentence plain-language summary of what the query answers
Output ONLY the JSON object, no markdown fences.
"""


_HUMAN = """Business context and schema:
{context}

Question: {question}

SQL:"""


_REPAIR = """The {dialect} SQL below failed when executed. Rewrite it so it runs
correctly and still answers the question. Common causes: referencing a table
alias or column that is not in scope, selecting a column a CTE/subquery does not
expose, or wrong join keys. Use ONLY tables/columns and the PRIMARY/FOREIGN KEY
relationships in the schema.

Return ONLY the corrected SQL — no markdown, no commentary.

Schema and business context:
{context}

Question: {question}

Failed SQL:
{sql}

Database error:
{error}

Corrected SQL:"""


def _llm_config() -> dict:
    cfg = get_config_store().get_llm()
    return {
        "provider": cfg.get("provider", "openai"),
        "model": cfg.get("model", "gpt-4o-mini"),
        "api_key": cfg.get("api_key", ""),
        "base_url": cfg.get("base_url", ""),
        "temperature": float(cfg.get("temperature", 0)),
        "local": bool(cfg.get("local", False)),
        "tuned_model": cfg.get("tuned_model", ""),
        "system_prompt": cfg.get("system_prompt", ""),
    }


def _system_prompt() -> str:
    """The user-configured system prompt, or the built-in default."""
    return (_llm_config().get("system_prompt") or "").strip() or DEFAULT_SYSTEM_PROMPT


def _build_llm(tuned: bool = False, override: dict | None = None):
    c = dict(_llm_config())
    if override:
        c.update({k: v for k, v in override.items() if v is not None})
    model = (c.get("tuned_model") if tuned else "") or c["model"]
    kwargs = dict(temperature=c.get("temperature", 0), model=model)
    provider = c.get("provider", "openai")
    if provider in ("openai", "azure"):
        from langchain_openai import ChatOpenAI
        kwargs["api_key"] = c.get("api_key") or None
        kwargs["base_url"] = c.get("base_url") or None
        return ChatOpenAI(**kwargs)
    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        kwargs["api_key"] = c.get("api_key") or None
        if c.get("base_url"):
            kwargs["base_url"] = c["base_url"]
        return ChatAnthropic(**kwargs)
    if provider == "ollama":
        from langchain_community.chat_models import ChatOllama
        kwargs.pop("api_key", None)
        kwargs["base_url"] = c.get("base_url") or "http://localhost:11434"
        return ChatOllama(**kwargs)
    raise ValueError(f"Unsupported llm_provider: {provider}")


async def test_llm_config(override: dict | None = None) -> str:
    """Ping the LLM with a tiny prompt; returns its reply text."""
    from langchain_core.messages import HumanMessage
    llm = _build_llm(override=override)
    resp = await llm.ainvoke([HumanMessage(content="Reply with the single word: OK")])
    return getattr(resp, "content", str(resp)).strip()


def _is_tuned() -> bool:
    c = _llm_config()
    return bool(c["local"]) or bool(c["tuned_model"])


class SQLGenerator:
    def __init__(self) -> None:
        self._chain = None
        self._tuned_chain = None
        self._sig = None

    def _ensure(self):
        # Rebuild if the LLM/system-prompt config changed at runtime.
        sig = json.dumps(_llm_config(), sort_keys=True)
        if self._chain is not None and sig == self._sig:
            return
        self._sig = sig
        self._chain = (
            ChatPromptTemplate.from_messages(
                [("system", _system_prompt()), ("human", _HUMAN)]
            )
            | _build_llm(tuned=False)
            | StrOutputParser()
        )
        self._tuned_chain = (
            ChatPromptTemplate.from_messages(
                [("system", _TUNED_SYSTEM), ("human", _HUMAN)]
            )
            | _build_llm(tuned=True)
            | StrOutputParser()
        )

    async def generate(self, question: str, context: str, dialect: str) -> str:
        out = await self.generate_full(question, context, dialect)
        return out["sql"]

    async def generate_full(
        self, question: str, context: str, dialect: str
    ) -> dict:
        """Return {'sql', 'chart', 'report'}. Chart/report populated for tuned
        models; otherwise sql only (filled later by the pipeline)."""
        self._ensure()
        if _is_tuned():
            raw = await self._tuned_chain.ainvoke(
                {"question": question, "context": context, "dialect": dialect}
            )
            return _parse_tuned(raw)
        raw = await self._chain.ainvoke(
            {"question": question, "context": context, "dialect": dialect}
        )
        return {"sql": _clean_sql(raw), "chart": None, "report": None}

    async def repair(
        self, question: str, context: str, dialect: str, sql: str, error: str
    ) -> str:
        """Ask the LLM to fix a query that failed at execution time."""
        self._ensure()
        chain = (
            ChatPromptTemplate.from_messages(
                [("system", _system_prompt()), ("human", _REPAIR)]
            )
            | _build_llm(tuned=False)
            | StrOutputParser()
        )
        raw = await chain.ainvoke(
            {
                "question": question,
                "context": context,
                "dialect": dialect,
                "sql": sql,
                "error": error,
            }
        )
        return _clean_sql(raw)


def _clean_sql(text: str) -> str:
    import re
    text = re.sub(r"```(?:sql)?", "", text, flags=re.IGNORECASE).replace("```", "")
    text = text.strip()
    m = re.search(r"\b(SELECT|WITH|SHOW|EXPLAIN|DESCRIBE)\b", text, re.IGNORECASE)
    if m:
        text = text[m.start():]
    return text.strip().rstrip(";")


def _extract_sql(text: str) -> str:
    """Pull the `sql` field out of a tuned model's JSON response."""
    return _parse_tuned(text).get("sql", "")


def _parse_tuned(text: str) -> dict:
    import json
    import re
    text = text.strip().strip("`").replace("```json", "").replace("```", "")
    try:
        obj = json.loads(text)
    except Exception:
        m = re.search(r'"sql"\s*:\s*"(.*?)"\s*,', text, re.DOTALL)
        obj = {
            "sql": _clean_sql(m.group(1).encode().decode("unicode_escape"))
            if m else _clean_sql(text),
            "chart": None,
            "report": None,
        }
        return obj
    return {
        "sql": _clean_sql(str(obj.get("sql", ""))),
        "chart": obj.get("chart"),
        "report": obj.get("report"),
    }
