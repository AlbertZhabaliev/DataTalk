from __future__ import annotations

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

from app.core.config_store import get_config_store
from app.core.llm.sql_chain import _build_llm, _llm_config


_REPORT_SYSTEM = (
    "You are a data analyst. Given the user's question, the SQL used, and a "
    "sample of the result rows, write a concise 2-4 sentence plain-language "
    "report of the findings. No code, no preamble."
)

_REPORT_HUMAN = """Question: {question}
SQL: {sql}
Result sample (first rows): {sample}

Report:"""

_CHART_SYSTEM = (
    "Given the question, SQL and result shape, choose the best chart type to "
    "visualize the result. Reply with exactly one word: bar, line, pie or table."
)

_CHART_HUMAN = """Question: {question}
SQL: {sql}
Columns: {columns}

Chart type:"""


class ReportGenerator:
    def __init__(self) -> None:
        self._report_chain = None
        self._chart_chain = None

    def _ensure(self):
        if self._report_chain is None:
            self._report_chain = (
                ChatPromptTemplate.from_messages(
                    [("system", _REPORT_SYSTEM), ("human", _REPORT_HUMAN)]
                )
                | _build_llm()
                | StrOutputParser()
            )
            self._chart_chain = (
                ChatPromptTemplate.from_messages(
                    [("system", _CHART_SYSTEM), ("human", _CHART_HUMAN)]
                )
                | _build_llm()
                | StrOutputParser()
            )

    async def report(self, question: str, sql: str, sample: str) -> str:
        self._ensure()
        return (await self._report_chain.ainvoke(
            {"question": question, "sql": sql, "sample": sample}
        )).strip()

    async def chart(self, question: str, sql: str, columns: list[str]) -> str:
        self._ensure()
        raw = await self._chart_chain.ainvoke(
            {"question": question, "sql": sql, "columns": ", ".join(columns)}
        )
        return raw.strip().lower()
