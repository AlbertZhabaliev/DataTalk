from __future__ import annotations

import asyncio
import io
import tempfile
from typing import Any

from nicegui import ui

from app.config.connections import registry
from app.config.settings import get_settings
from app.core.config_store import get_config_store
from app.core.saved_store import get_saved_store
from app.core.schema.retriever import SchemaRetriever
from app.models.request import OutputFormat, QueryRequest
from app.core.pipeline import run_query

store = get_saved_store()
cfg_store = get_config_store()


# --------------------------------------------------------------------------
# Helpers (pure / backend bridge)
# --------------------------------------------------------------------------
def _run_coro(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def run_async(coro):
    return await asyncio.to_thread(_run_coro, coro)


async def run_sync(fn, *a, **k):
    return await asyncio.to_thread(fn, *a, **k)


def _guess_chart_cols(columns, rows):
    x = columns[0] if columns else ""
    y = ""
    if rows:
        for i in range(1, len(columns)):
            v = rows[0][i]
            if isinstance(v, (int, float)) or (
                isinstance(v, str) and v.replace(".", "", 1).lstrip("-").isdigit()
            ):
                y = columns[i]
                break
    if not y and len(columns) > 1:
        y = columns[1]
    return {"x": x, "y": [y]}


def _num(v):
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v))
    except Exception:
        return 0.0


def _echart_options(chart_type, columns, rows, x, y):
    if not chart_type or chart_type == "none" or not columns:
        return None
    xi = columns.index(x) if x in columns else 0
    y0 = (y[0] if y else None) or (columns[1] if len(columns) > 1 else columns[0])
    yi = columns.index(y0) if y0 in columns else (1 if len(columns) > 1 else 0)
    cat = [str(r[xi]) for r in rows]
    ser = [_num(r[yi]) for r in rows]
    if chart_type == "pie":
        return {
            "tooltip": {},
            "series": [
                {
                    "type": "pie",
                    "radius": "62%",
                    "label": {"show": False},
                    "data": [{"name": c, "value": v} for c, v in zip(cat, ser)],
                }
            ],
        }
    if chart_type == "scatter":
        xs = [_num(r[xi]) for r in rows]
        return {
            "tooltip": {"trigger": "axis"},
            "xAxis": {"type": "value"},
            "yAxis": {"type": "value"},
            "series": [
                {
                    "type": "scatter",
                    "symbolSize": 8,
                    "data": [[a, b] for a, b in zip(xs, ser)],
                }
            ],
        }
    smooth = chart_type == "area"
    return {
        "tooltip": {"trigger": "axis"},
        "grid": {"left": 60, "right": 20, "top": 20, "bottom": 50},
        "xAxis": {
            "type": "category",
            "data": cat,
            "axisLabel": {"rotate": 30 if len(cat) > 10 else 0},
        },
        "yAxis": {"type": "value"},
        "series": [
            {
                "type": "line" if smooth else chart_type,
                "data": ser,
                "smooth": smooth,
                "areaStyle": ({} if smooth else None),
            }
        ],
    }


def _run_saved(qid):
    item = store.get(qid)
    if not item:
        return {"columns": [], "rows": [], "row_count": 0, "truncated": False}
    ex = registry.get(item["db"])
    res = ex.execute(item["sql"], max_rows=50000)
    return {
        "columns": res.columns,
        "rows": res.rows,
        "row_count": res.row_count,
        "truncated": res.truncated,
        "dialect": res.dialect,
    }


def _run_sql(db, sql, limit):
    ex = registry.get(db)
    res = ex.execute(sql, max_rows=limit or 50000)
    return {
        "columns": res.columns,
        "rows": res.rows,
        "row_count": res.row_count,
        "truncated": res.truncated,
        "dialect": res.dialect,
    }


async def _explain(db, sql, question):
    from app.core.llm.sql_chain import _build_llm
    from langchain_core.messages import HumanMessage

    retriever = SchemaRetriever()
    context = retriever.build_context(db, question or sql)
    prompt = (
        "You are a data analyst. Explain the following SQL query in clear, plain "
        "language a non-technical business user would understand. Describe what the "
        "query calculates, what it returns, and the key business logic (filters, "
        "aggregations, joins). Keep it concise.\n\n"
        f"Database schema:\n{context}\n\n"
        f"SQL query:\n{sql}\n\n"
    )
    if question:
        prompt += f"User question: {question}\n\n"
    prompt += "Explanation:"

    llm = _build_llm()
    resp = await llm.ainvoke([HumanMessage(content=prompt)])
    return getattr(resp, "content", str(resp)).strip()


async def _generate_name(db, sql, question):
    from app.core.llm.sql_chain import _build_llm
    from langchain_core.messages import HumanMessage

    prompt = (
        "Give a short, concise title (max 6 words, no quotes, no punctuation at "
        "the end) for a saved SQL query. Capture the gist of what it returns.\n\n"
        f"SQL:\n{sql}\n\n"
    )
    if question:
        prompt += f"User question: {question}\n\n"
    prompt += "Title:"

    llm = _build_llm()
    resp = await llm.ainvoke([HumanMessage(content=prompt)])
    return getattr(resp, "content", str(resp)).strip().strip('"').strip()


# --------------------------------------------------------------------------
# Frontend bootstrap
# --------------------------------------------------------------------------
def init_frontend(app):
    @ui.page("/")
    def index():
        state: dict[str, Any] = {
            "selected_db": None,
            "task": None,
            "saved_item": None,
            "saved_data": None,
            "saved_refs": {},
            "dash_cols": 2,
            "fav_filter": "all",
            "saved_search": "",
        }
        views: dict[str, ui.column] = {}

        dbs = cfg_store.get_databases()
        db_names = [d.get("name") for d in dbs]
        state["selected_db"] = cfg_store.get_default("selected_db") or (
            db_names[0] if db_names else None
        )
        if state["selected_db"] not in db_names:
            state["selected_db"] = db_names[0] if db_names else None
        state.setdefault("ask_layout", cfg_store.get_default("ask_layout", "full"))

        # ---------------- Root layout ----------------
        ui.add_css(
            """
            .mic-uploader { width: 40px !important; height: 40px !important; min-width: 40px; min-height: 40px; overflow: hidden; display: inline-flex; }
            .mic-uploader .q-uploader__header { padding: 0; min-height: 0; height: 100%; }
            .mic-uploader .q-uploader__title, .mic-uploader .q-uploader__subtitle { display: none !important; }
            .mic-uploader .q-linear-progress { display: none !important; }
            .mic-uploader .q-uploader__list, .mic-uploader .q-uploader__file { display: none !important; }
            .mic-uploader .q-uploader__file__info, .mic-uploader .q-uploader__file__size, .mic-uploader .q-uploader__file__status { display: none !important; }
            .mic-uploader .q-uploader__header-content { justify-content: center; align-items: center; padding: 0; height: 100%; display: flex; }
            .mic-uploader .q-btn { margin: 0; min-width: 38px; min-height: 38px; }
            """
        )
        ui.query(".nicegui-content").classes("p-0")
        ui.query("body").style("margin: 0; padding: 0;")
        with ui.column().classes("w-full h-screen no-wrap p-0 gap-0"):
            # Top bar
            with ui.row().classes(
                "w-full items-center gap-3 px-3 py-2 bg-slate-800 text-white shrink-0 no-wrap"
            ):
                ui.label("DataTalk").classes("text-lg font-bold")
                ui.space()
                ui.label("Database:")
                db_select = ui.select(
                    db_names,
                    value=state["selected_db"],
                    on_change=lambda e: [
                        state.update(selected_db=e.value),
                        cfg_store.set_default("selected_db", e.value),
                    ],
                ).classes("w-48 bg-white text-black rounded")
                ai_badge = ui.label("AI: —").classes("text-sm")

            llm = cfg_store.get_llm()
            ai_badge.text = "AI: " + (
                (llm.get("provider", "—") or "—")
                + ((" · " + llm.get("model")) if llm.get("model") else "")
            )

            # Body: sidebar + main
            with ui.row().classes("w-full flex-1 min-h-0 no-wrap"):
                with ui.column().classes(
                    "w-48 bg-slate-900 text-white p-2 gap-1 shrink-0 h-full overflow-auto"
                ):
                    nav_btns: dict[str, ui.button] = {}
                    for label, icon, name in [
                        ("Dashboard", "📊", "dashboard"),
                        ("Ask", "💬", "ask"),
                        ("Browse", "🗂", "browse"),
                        ("Saved", "⭐", "saved"),
                        ("Settings", "⚙", "settings"),
                    ]:
                        b = ui.button(
                            f"{icon}  {label}",
                            on_click=lambda n=name: asyncio.create_task(show_view(n)),
                        ).classes("w-full text-left").props("flat")
                        nav_btns[name] = b

                    def _highlight(name):
                        for n, b in nav_btns.items():
                            b.props(
                                "flat"
                                + (" color=blue" if n == name else "")
                            )

                with ui.column().classes("flex-1 h-full overflow-hidden p-2") as main_col:
                    for name in ["ask", "browse", "saved", "dashboard", "settings"]:
                        v = ui.column().classes("w-full h-full")
                        v.set_visibility(False)
                        views[name] = v

                    # Pre-create the persistent containers in the synchronous page
                    # context so background tasks can populate them via `with c:`.
                    with views["browse"]:
                        with ui.row().style("height: 100%; min-height: 0"):
                            with ui.column().style("width: 320px; min-height: 0; display: flex; flex-direction: column; padding: 8px; border-right: 1px solid #ccc"):
                                browse_search = ui.input("Search tables…",
                                    on_change=lambda e: (state.get("browse_search_cb") or _noop)(e),
                                ).classes("w-full shrink-0")
                                browse_tree_box = ui.column().style("flex: 1; min-height: 0; overflow-y: auto; width: 100%")
                                with browse_tree_box:
                                    ui.spinner(size="lg")
                                    ui.label("Loading schema…").classes("text-gray-500")
                            browse_preview = ui.column().style("flex: 1; overflow-y: auto; padding: 8px")
                            with browse_preview:
                                ui.label("Select a table to preview its data.").classes(
                                    "text-gray-500"
                                )
                    with views["saved"]:
                        with ui.row().style("height: 100%; min-height: 0"):
                            saved_list_box = ui.column().style("width: 320px; min-height: 0; overflow-y: auto; padding: 8px; border-right: 1px solid #ccc")
                            saved_detail = ui.column().style("flex: 1; min-height: 0; overflow-y: auto; padding: 8px")
                    with views["dashboard"]:
                        ui.label("Dashboard").classes("text-xl font-bold")
                        ui.label(
                            "Pin charts from a saved query to build your dashboard."
                        ).classes("text-gray-500")
                        dash_box = ui.column().classes("w-full")

        def _cancel(s):
            t = s.get("task")
            if t and not t.done():
                t.cancel()
                ui.notify("Request cancelled")

        def _noop(e):
            return None

        # ----------------------------------------------------------------
        # ASK
        # ----------------------------------------------------------------
        def build_ask(container):
            chat = None
            prompt = None

            def _on_audio(e):
                f = e.file
                suffix = "." + (f.name.split(".")[-1] if "." in f.name else "webm")
                tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
                tmp.close()
                asyncio.create_task(_transcribe(f, tmp.name, prompt))

            async def _transcribe(f, path, p):
                try:
                    await f.save(path)
                    ui.notify("Transcribing…")
                    text = await asyncio.to_thread(_transcribe_file, path)
                    p.value = text
                except Exception as e:  # noqa: BLE001
                    ui.notify(f"Transcription failed: {e}", type="negative")

            def _on_send():
                q = prompt.value.strip()
                if not q:
                    return
                if not state["selected_db"]:
                    ui.notify("Connect a database in Settings first.", type="warning")
                    return
                if state["task"] and not state["task"].done():
                    ui.notify("A query is already running.", type="warning")
                    return
                prompt.value = ""
                _add_user(chat, q)
                thinking = ui.label("Thinking…").classes("text-gray-500 italic")
                ask_btn.set_visibility(False)
                cancel_btn.set_visibility(True)

                async def _run():
                    try:
                        req = QueryRequest(
                            db=state["selected_db"],
                            question=q,
                            format=OutputFormat.table,
                        )
                        data = await run_async(run_query(req))
                        thinking.delete()
                        _render_answer(chat, data, state, cancel_btn)
                    except asyncio.CancelledError:
                        thinking.delete()
                        ui.notify("Cancelled")
                    except Exception as e:  # noqa: BLE001
                        thinking.delete()
                        ui.notify(str(e), type="negative")
                    finally:
                        ask_btn.set_visibility(True)
                        cancel_btn.set_visibility(False)
                        state["task"] = None

                state["task"] = asyncio.create_task(_run())

            def _apply_ask_layout(outer):
                if state.get("ask_layout", "full") == "compact":
                    outer.classes("max-w-4xl mx-auto")
                else:
                    outer.classes(remove="max-w-4xl mx-auto")

            container.clear()
            with container:
                ask_outer = ui.column().classes("w-full gap-3")
                with ui.row().classes("w-full items-center gap-1 mb-1"):
                    ui.label("Layout:").classes("text-sm text-gray-500 mr-1")
                    layout_toggle = ui.switch("Centered", value=state.get("ask_layout", "full") == "compact",
                        on_change=lambda e: [
                            state.update(ask_layout="compact" if e.value else "full"),
                            cfg_store.set_default("ask_layout", "compact" if e.value else "full"),
                            _apply_ask_layout(ask_outer),
                        ],
                    )
                    _apply_ask_layout(ask_outer)
                with ask_outer:
                    chat = ui.column().style(
                        "height: calc(100vh - 280px); overflow-y: auto; width:100%"
                    )
                    with ui.row().classes("w-full items-end gap-2 no-wrap mt-2"):
                        prompt = ui.textarea(
                            placeholder="Ask about your data…  (Enter to send, Shift+Enter for newline)"
                        ).classes("flex-1").props("autogrow")
                        mic_upload = ui.upload(
                            auto_upload=True, on_upload=_on_audio, label=""
                        ).props("icon=mic").classes("mic-uploader")
                        mic_upload.set_visibility(False)
                        ui.button(icon="mic", on_click=lambda: ui.run_javascript(
                            f"document.getElementById('c_{mic_upload.id}').querySelector('input').click()"
                        )).props("round")
                        ask_btn = ui.button(icon="send", on_click=_on_send).props(
                            "round"
                        ).classes("w-12 h-10")
                        cancel_btn = ui.button(
                            icon="stop", on_click=lambda: _cancel(state), color="red"
                        ).props("round").classes("w-12 h-10")
                        cancel_btn.set_visibility(False)

        # ----------------------------------------------------------------
        # BROWSE
        # ----------------------------------------------------------------
        async def build_browse(container):
            tree_box = browse_tree_box
            preview = browse_preview
            tree_box.clear()
            preview.clear()
            browse_search.value = ""
            with tree_box:
                ui.spinner(size="lg")
                ui.label("Loading schema…").classes("text-gray-500")
            with preview:
                ui.label("Select a table to preview its data.").classes(
                    "text-gray-500"
                )

            db = state["selected_db"]
            if not db:
                preview.clear()
                with preview:
                    ui.label("Select a database to preview tables.")
                return
            try:
                snap = await run_sync(registry.get(db).introspect)
            except Exception as e:  # noqa: BLE001
                preview.clear()
                with preview:
                    ui.label(f"Could not read schema: {e}").classes("text-negative")
                return

            idmap = {}
            nodes = []
            for t in snap.tables:
                tid = f"t::{t.schema}::{t.name}"
                idmap[tid] = (t.schema, t.name)
                col_nodes = [
                    {"id": f"c::{tid}::{c.name}", "label": f"{c.name} ({c.type})"}
                    for c in t.columns
                ]
                nodes.append(
                    {"id": tid, "label": t.qualified_name, "children": col_nodes}
                )

            def _on_search(ev):
                term = (ev.value or "").lower()
                filtered = [
                    n
                    for n in nodes
                    if term in n["label"].lower()
                ]
                tree_box.clear()
                with tree_box:
                    if filtered:
                        ui.tree(
                            filtered,
                            label_key="label",
                            on_select=lambda e: _on_tree_select(e, idmap, db, preview),
                        )
                    else:
                        ui.label("No tables match.").classes("text-gray-500")

            state["browse_search_cb"] = _on_search

            tree_box.clear()
            with tree_box:
                ui.tree(
                    nodes,
                    label_key="label",
                    on_select=lambda e: _on_tree_select(e, idmap, db, preview),
                )

        def _on_tree_select(e, idmap, db, preview):
            sel = e.value
            if isinstance(sel, list):
                sel = sel[0] if sel else None
            if not sel or not sel.startswith("t::"):
                return
            schema, table = idmap[sel]

            async def _load():
                try:
                    res = await run_sync(
                        registry.get(db).preview_table,
                        table,
                        schema=schema,
                        limit=1000,
                    )
                    preview.clear()
                    with preview:
                        ui.label(f"{schema}.{table}" if schema else table).classes(
                            "text-lg font-bold"
                        )
                        _make_table(res.columns, res.rows, height="70vh")
                except Exception as ex:  # noqa: BLE001
                    preview.clear()
                    with preview:
                        ui.label(f"Preview failed: {ex}").classes("text-negative")

            asyncio.create_task(_load())

        # ----------------------------------------------------------------
        # SAVED
        # ----------------------------------------------------------------
        async def build_saved(container):
            list_box = saved_list_box
            detail = saved_detail
            list_box.clear()
            detail.clear()
            with list_box:
                ui.input(
                    "Search saved queries…",
                    on_change=lambda e: _filter_saved_list(e.value, list_box, detail, state),
                ).classes("w-full")
                with ui.row().classes("w-full gap-1"):
                    ui.button("All", on_click=lambda: _set_fav("all", list_box, detail, state)).props(
                        "flat" + (" color=blue" if state["fav_filter"] == "all" else "")
                    )
                    ui.button("⭐ Favorites", on_click=lambda: _set_fav("fav", list_box, detail, state)).props(
                        "flat" + (" color=blue" if state["fav_filter"] == "fav" else "")
                    )
                items_box = ui.column().classes("w-full")
                state["saved_items_box"] = items_box

            items = store.list()
            _render_saved_list(list_box, items, detail, state)

        # ----------------------------------------------------------------
        # DASHBOARD
        # ----------------------------------------------------------------
        async def build_dashboard(container):
            dash_box.clear()
            items = store.list()
            pinned = []
            for q in items:
                for i, ch in enumerate(q.get("charts") or []):
                    if ch.get("pinned"):
                        pinned.append((q, i, ch))
            if not pinned:
                with dash_box:
                    ui.label("No pinned charts yet.").classes("text-gray-500")
                return

            cols = 1 if len(pinned) == 1 else (2 if len(pinned) == 2 else state["dash_cols"])
            with dash_box:
                grid = ui.grid(columns=cols).classes("w-full gap-4")
                for q, i, ch in pinned:
                    with grid:
                        await _render_dash_card(q, ch, single=(len(pinned) == 1))

        async def _render_dash_card(q, ch, single):
            with ui.card().classes("w-full"):
                data = await run_sync(_run_saved, q["id"])
                columns = data["columns"]
                rows = data["rows"]
                chart_type = ch.get("type", "bar")
                x = ch.get("x")
                y = ch.get("y") or []
                if not x or x not in columns:
                    g = _guess_chart_cols(columns, rows)
                    x, y = g["x"], g["y"]
                h = "75vh" if single else "360px"
                opts = _echart_options(chart_type, columns, rows[:200], x, y)
                if opts:
                    ui.echart(opts).style(f"height:{h};width:100%")
                else:
                    ui.label("No chartable data").classes("text-gray-500")
                with ui.row().classes("items-center justify-between w-full"):
                    ui.label(ch.get("name") or q.get("name") or "Chart").classes(
                        "font-bold"
                    )
                    ui.label(f"{q.get('db','')} · {data['row_count']} rows").classes(
                        "text-xs text-gray-500"
                    )
                with ui.row().classes("w-full items-center"):
                    ui.button(
                        "ℹ",
                        on_click=lambda q=q, ch=ch: _show_info(q, ch.get("x"), ch.get("y") or [], q.get("explanation")),
                    ).props("flat round")

        # ----------------------------------------------------------------
        # SETTINGS
        # ----------------------------------------------------------------
        def build_settings(container):
            container.clear()
            with container:
                _build_settings_ai()
                _build_settings_dbs()

        # ----------------------------------------------------------------
        # View switching
        # ----------------------------------------------------------------
        async def show_view(name):
            for k, v in views.items():
                v.set_visibility(k == name)
            _highlight(name)
            if name == "ask":
                # rebuild only once is unnecessary; Ask persists chat
                if not getattr(views["ask"], "_built", False):
                    build_ask(views["ask"])
                    views["ask"]._built = True
                return
            container = views[name]
            if name == "browse":
                await build_browse(container)
            elif name == "saved":
                await build_saved(container)
            elif name == "dashboard":
                await build_dashboard(container)
            elif name == "settings":
                build_settings(container)

        # Default view
        views["ask"].set_visibility(True)
        build_ask(views["ask"])

    ui.run_with(
        app,
        title="DataTalk",
        favicon="📊",
        show_welcome_message=False,
        dark=False,
    )


# --------------------------------------------------------------------------
# Shared builders used across views
# --------------------------------------------------------------------------
def _add_user(chat, text):
    with chat:
        with ui.row().classes("justify-end"):
            ui.label(text).classes(
                "bg-blue-600 text-white rounded-2xl px-3 py-2 max-w-[75%]"
            )


def _make_table(columns, rows, height="400px"):
    grid = ui.aggrid(
        {
            "columnDefs": [
                {"field": c, "headerName": c, "filter": True, "sortable": True}
                for c in columns
            ],
            "rowData": [dict(zip(columns, r)) for r in rows],
            "defaultColDef": {"flex": 1, "minWidth": 80},
            "pagination": len(rows) > 200,
            "paginationPageSize": 200,
        },
        theme="balham",
    )
    grid.style(f"height: {height}; width: 100%")
    return grid


def _render_answer(chat, data, state, cancel_btn=None):
    with chat:
        with ui.card().classes("w-full"):
            if data.get("report"):
                ui.label(data["report"]).style("white-space: pre-wrap")
            with ui.row().classes("gap-1 flex-wrap"):
                if data.get("db"):
                    ui.badge(data["db"])
                if data.get("dialect"):
                    ui.badge(data["dialect"])
                ui.badge(f"{data.get('row_count',0)} rows")
                if data.get("truncated"):
                    ui.badge("(capped)")
                if data.get("chart") and data["chart"] != "table":
                    ui.badge(f"chart: {data['chart']}")
            table = _make_table(data["columns"], data["rows"], height="380px")
            sql_box = ui.code(data.get("sql", ""), language="sql")
            sql_box.set_visibility(False)
            chart_refs = {}
            show_chart = len(data.get("columns", [])) >= 2

            def _draw():
                chart_refs["card"].clear()
                with chart_refs["card"]:
                    opts = _echart_options(
                        chart_refs["type"].value,
                        data["columns"],
                        data["rows"],
                        chart_refs["x"].value,
                        [chart_refs["y"].value],
                    )
                    if opts:
                        ui.echart(opts).style("height: 320px; width: 100%")
                    else:
                        ui.label("No chartable data").classes("text-gray-500")

            def _build():
                chart_refs["box"].clear()
                with chart_refs["box"]:
                    with ui.row().classes("items-center gap-2 mt-2 flex-wrap"):
                        g = _guess_chart_cols(data["columns"], data["rows"])
                        chart_refs["type"] = ui.select(
                            ["bar", "line", "area", "pie", "scatter"],
                            value=data.get("chart") or "bar",
                            label="Chart",
                            on_change=lambda e: _draw(),
                        ).classes("w-28")
                        chart_refs["x"] = ui.select(
                            data["columns"], value=g["x"], label="X axis",
                            on_change=lambda e: _draw(),
                        ).classes("w-40")
                        chart_refs["y"] = ui.select(
                            data["columns"], value=(g["y"] or [""])[0], label="Y axis",
                            on_change=lambda e: _draw(),
                        ).classes("w-40")
                        chart_refs["card"] = ui.card().classes("w-full mt-2")
                    _draw()

            def _on_show():
                chart_refs["box"].set_visibility(True)
                _build()
                chart_refs["show"].set_visibility(False)
                chart_refs["hide"].set_visibility(True)

            def _on_hide():
                chart_refs["box"].set_visibility(False)
                chart_refs["show"].set_visibility(True)
                chart_refs["hide"].set_visibility(False)

            with ui.row().classes("gap-2 flex-wrap mt-2 items-center"):
                ui.button(
                    "Show SQL",
                    on_click=lambda b=sql_box: _toggle_visibility(b, "Show SQL", "Hide SQL"),
                ).props("flat")
                ui.button(
                    "Copy SQL",
                    on_click=lambda: ui.clipboard.write(data.get("sql", "")) or ui.notify("Copied"),
                ).props("flat")
                ui.button(
                    "Save query",
                    on_click=lambda: _save_query(data),
                ).props("flat")
                ui.button(
                    "Load full data",
                    on_click=lambda: _load_full(table, data),
                ).props("flat")
                if data.get("dashboard_url"):
                    ui.link("Dashboard ↗", data["dashboard_url"], new_tab=True).classes(
                        "ml-2"
                    )
                if show_chart:
                    chart_refs["show"] = ui.button("Show chart", on_click=_on_show).props("flat")
                    chart_refs["hide"] = ui.button("Hide chart", on_click=_on_hide).props("flat")
                    chart_refs["hide"].set_visibility(False)

            if show_chart:
                chart_refs["box"] = ui.column().classes("w-full")
                chart_refs["box"].set_visibility(False)


def _toggle_visibility(box, show, hide):
    visible = not box.visible
    box.set_visibility(visible)
    box._toggle_label = hide if visible else show


def _save_query(data):
    def _do_save(name):
        item = {
            "name": name or data.get("question", "Query"),
            "db": data.get("db", ""),
            "question": data.get("question", ""),
            "sql": data.get("sql", ""),
            "explanation": data.get("report", ""),
            "layout": "table",
            "charts": [],
        }
        store.upsert(item)
        d.close()
        ui.notify("Query saved ✓")

    with ui.dialog() as d, ui.card().classes("w-96"):
        ui.label("Save query").classes("text-lg font-bold")
        name_in = ui.input("Name", value=data.get("question", "")).classes("w-full")
        spinner = ui.row().classes("mt-1").style("display:none")
        with spinner:
            ui.spinner(size="sm")
            ui.label("Generating name…").classes("text-gray-500 text-sm")

        async def _autoname():
            spinner.style("display:flex")
            try:
                nm = await _generate_name(
                    data.get("db", ""), data.get("sql", ""), data.get("question", "")
                )
                if nm:
                    name_in.value = nm
            except Exception as e:  # noqa: BLE001
                ui.notify(f"Auto-name failed: {e}", type="negative")
            finally:
                spinner.style("display:none")

        with ui.row().classes("gap-2 mt-3"):
            ui.button("Auto-name (AI)", on_click=_autoname).props("flat")
            ui.space()
            ui.button("Cancel", on_click=d.close).props("flat")
            ui.button("Save", on_click=lambda: _do_save(name_in.value)).props("primary")
    d.open()


def _load_full(table, data):
    async def _go():
        try:
            res = await run_sync(_run_sql, data.get("db"), data.get("sql"), 50000)
            table.options["rowData"] = [
                dict(zip(res["columns"], r)) for r in res["rows"]
            ]
            table.update()
            ui.notify(f"Loaded {res['row_count']} rows")
        except Exception as e:  # noqa: BLE001
            ui.notify(str(e), type="negative")

    asyncio.create_task(_go())


def _show_chart(chat, data):
    columns = data.get("columns", [])
    rows = data.get("rows", [])
    if len(columns) < 2:
        return
    g = _guess_chart_cols(columns, rows)
    opts = _echart_options("bar", columns, rows, g["x"], g["y"])
    with chat:
        with ui.card().classes("w-full"):
            ui.echart(opts).style("height: 320px; width: 100%")


def _transcribe_file(path):
    import faster_whisper

    model = faster_whisper.WhisperModel("base", device="cpu")
    segments, _ = model.transcribe(path)
    return " ".join(s.text for s in segments)


# --------------------------------------------------------------------------
# Saved-query helpers
# --------------------------------------------------------------------------
def _render_saved_list(list_box, items, detail, state):
    box = state.get("saved_items_box") or list_box
    box.clear()
    sq = state.get("saved_search", "").lower()
    fav = state.get("fav_filter", "all")
    filtered = items
    if sq:
        filtered = [
            q
            for q in filtered
            if sq in (q.get("name", "") + q.get("question", "") + q.get("db", "")).lower()
        ]
    if fav == "fav":
        filtered = [q for q in filtered if q.get("favorite")]

    if not filtered:
        with box:
            ui.label("No saved queries.").classes("text-gray-500 text-sm")
        return

    with box:
        for q in filtered:
            card = ui.card().classes("w-full cursor-pointer mb-2")
            with card:
                with ui.row().classes("items-center gap-1"):
                    ui.label(q.get("name") or q.get("question") or "Untitled").classes(
                        "font-semibold flex-1"
                    )
                    ui.button(
                        "★" if q.get("favorite") else "☆",
                        on_click=lambda e, qid=q["id"]: _toggle_fav(qid, list_box, detail, state),
                    ).props("flat dense")
                ui.label(f"{q.get('db','')} · {q.get('question','')}").classes(
                    "text-xs text-gray-500"
                )
                with ui.row().classes("gap-1 mt-1"):
                    ui.button(
                        "▶ Run",
                        on_click=lambda e, qid=q["id"]: _select_saved(qid, detail, state, list_box, items),
                    ).props("flat dense")
                    ui.button(
                        "Delete",
                        on_click=lambda e, qid=q["id"]: _delete_saved(qid, list_box, items, detail, state),
                    ).props("flat dense color=red")


def _filter_saved_list(value, list_box, detail, state):
    state["saved_search"] = value or ""
    items = store.list()
    _render_saved_list(list_box, items, detail, state)


def _set_fav(mode, list_box, detail, state):
    state["fav_filter"] = mode
    items = store.list()
    _render_saved_list(list_box, items, detail, state)


def _toggle_fav(qid, list_box, detail, state):
    item = store.get(qid)
    if item:
        item["favorite"] = not item.get("favorite", False)
        store.upsert(item)
    items = store.list()
    _render_saved_list(list_box, items, detail, state)


def _delete_saved(qid, list_box, items, detail, state):
    store.delete(qid)
    detail.clear()
    items = store.list()
    _render_saved_list(list_box, items, detail, state)


def _select_saved(qid, detail, state, list_box, items):
    detail.clear()
    with detail:
        ui.spinner(size="lg")
        ui.label("Running query…").classes("text-gray-500")

    async def _go():
        try:
            data = await run_sync(_run_saved, qid)
            item = store.get(qid)
            state["saved_item"] = item
            state["saved_data"] = data
            _render_saved_detail(detail, state, list_box, items)
        except Exception as e:  # noqa: BLE001
            try:
                ui.notify(str(e), type="negative")
            except Exception:  # noqa: BLE001
                pass

    asyncio.create_task(_go())


def _render_saved_detail(detail, state, list_box, items):
    item = state["saved_item"]
    data = state["saved_data"]
    if not item or not data:
        return
    columns = data["columns"]
    rows = data["rows"]
    layout = item.get("layout") or "table"

    detail.clear()
    with detail:
        # Header
        with ui.row().classes("items-center gap-2 flex-wrap"):
            ui.label(item.get("name") or item.get("question") or "Untitled").classes(
                "text-lg font-bold"
            )
            ui.badge(item.get("db", ""))
            ui.badge(f"{data.get('row_count', 0)} rows")
            ui.space()
            for lyt, icon in [
                ("table", "⊞"),
                ("charts-top", "⊟"),
                ("charts-left", "⊡"),
                ("charts-right", "⊞"),
            ]:
                ui.button(
                    icon,
                    on_click=lambda e, l=lyt: _set_layout(l, state, detail, list_box, items),
                ).props("flat dense" + (" color=blue" if lyt == layout else ""))
            ui.button("+ Chart", on_click=lambda: _add_chart(state, detail, list_box, items)).props(
                "flat dense"
            )
            ui.button(
                "Explain",
                on_click=lambda: _explain_saved(detail, state, list_box, items),
            ).props("flat dense")
            ui.button("SQL", on_click=lambda: _toggle_sql(detail, state, list_box, items)).props(
                "flat dense"
            )
            ui.button("Data", on_click=lambda: _toggle_data(detail, state, list_box, items)).props(
                "flat dense"
            )
            ui.button("CSV", on_click=lambda: _export_csv(data)).props("flat dense")
            ui.button("Delete", on_click=lambda: _delete_saved(item["id"], list_box, items, detail, state)).props(
                "flat dense color=red"
            )

        # Explanation (collapsed by default; toggled by Explain)
        expl = ui.markdown(item.get("explanation") or "").classes(
            "bg-blue-50 p-2 rounded mt-2"
        )
        expl.set_visibility(False)
        gen_spinner = ui.row().classes("mt-2").style("display:none")
        with gen_spinner:
            ui.spinner(size="sm")
            ui.label("Generating explanation…").classes("text-gray-500 text-sm")

        # SQL (hidden by default)
        sql_box = ui.code(item.get("sql", ""), language="sql").classes("mt-2")
        sql_box.set_visibility(False)
        state["saved_refs"]["sql_box"] = sql_box
        state["saved_refs"]["expl"] = expl
        state["saved_refs"]["gen_spinner"] = gen_spinner

        # --- Body layout: arrange charts vs table per the chosen mode ---
        charts_area = None
        table_area = None
        with ui.column().classes("w-full gap-2 mt-2"):
            if layout == "charts-left":
                with ui.row().style("width: 100%; align-items: flex-start"):
                    charts_area = ui.column().style("width: 50%; min-width: 0")
                    table_area = ui.column().style("width: 50%; min-width: 0")
            elif layout == "charts-right":
                with ui.row().style("width: 100%; align-items: flex-start"):
                    table_area = ui.column().style("width: 50%; min-width: 0")
                    charts_area = ui.column().style("width: 50%; min-width: 0")
            elif layout == "charts-top":
                with ui.column().classes("w-full gap-2"):
                    charts_area = ui.column().style("width: 100%")
                    table_area = ui.column().style("width: 100%")
            else:  # table only
                with ui.column().style("width: 100%"):
                    table_area = ui.column().style("width: 100%")

        state["saved_refs"]["table_area"] = table_area
        state["saved_refs"]["charts_area"] = charts_area

        if charts_area is not None:
            _render_charts(charts_area, state, detail, list_box, items)

        # Table area with filter glued on top, then table, then reset
        _render_table(table_area, columns, rows)
        saved_grid = table_area._grid if hasattr(table_area, "_grid") else None
        state["saved_grid"] = saved_grid
        if saved_grid is not None:
            with table_area:
                with ui.row().classes("w-full items-center gap-2 mt-1"):
                    filter_in = ui.input("Filter data…").classes("flex-1").props("dense")
                    filter_in.on("input", lambda e: _filter_table(saved_grid, e.value))
                    ui.button(icon="clear", on_click=lambda: [
                        filter_in.set_value(""),
                        _filter_table(saved_grid, ""),
                    ]).props("flat dense round")


def _render_charts(area, state, detail, list_box, items):
    item = state["saved_item"]
    data = state["saved_data"]
    columns = data["columns"]
    rows = data["rows"]
    area.clear()
    with area:
        for idx, ch in enumerate(item.get("charts") or []):
            _render_one_chart(idx, ch, columns, rows, state, detail, list_box, items)


def _render_one_chart(idx, ch, columns, rows, state, detail, list_box, items):
    with ui.card().classes("w-full mb-2"):
        with ui.row().classes("items-center gap-2 flex-wrap"):
            ui.input(value=ch.get("name", f"Chart {idx+1}")).classes("w-32").on(
                "blur",
                lambda e, i=idx: _rename_chart(i, e.value, state, detail, list_box, items),
            )
            ui.select(
                ["bar", "line", "area", "pie", "scatter", "none"],
                value=ch.get("type", "bar"),
                on_change=lambda e, i=idx: _change_chart(i, "type", e.value, state, detail, list_box, items),
            ).classes("w-24")
            ui.select(
                columns,
                value=ch.get("x"),
                on_change=lambda e, i=idx: _change_chart(i, "x", e.value, state, detail, list_box, items),
            ).classes("w-32")
            ui.select(
                columns,
                value=(ch.get("y") or [None])[0],
                on_change=lambda e, i=idx: _change_chart(i, "y", e.value, state, detail, list_box, items),
            ).classes("w-32")
            pin = ui.switch(
                "Pinned",
                value=bool(ch.get("pinned", False)),
                on_change=lambda e, i=idx: _toggle_pin(i, e.value, state, detail, list_box, items),
            )
            pin.props("color=amber" if ch.get("pinned") else "")
            if len(state["saved_item"].get("charts") or []) >= 1:
                ui.button(
                    "×",
                    on_click=lambda e, i=idx: _remove_chart(i, state, detail, list_box, items),
                ).props("flat dense color=red")
            ui.button(
                "ℹ",
                on_click=lambda q=state["saved_item"], ch=ch: _show_info(
                    q, ch.get("x"), ch.get("y") or [], q.get("explanation")
                ),
            ).props("flat round")
        x = ch.get("x")
        y = ch.get("y") or []
        if not x or x not in columns:
            g = _guess_chart_cols(columns, rows)
            x, y = g["x"], g["y"]
        opts = _echart_options(ch.get("type", "bar"), columns, rows, x, y)
        if opts:
            ui.echart(opts).style("height: 240px; width: 100%")


def _show_info(item, x, y, explanation):
    with ui.dialog() as d, ui.card().classes("max-w-2xl"):
        ui.label(f"{item.get('name','')}  ·  {item.get('db','')}").classes("font-bold")
        if explanation:
            ui.label("Explanation").classes("font-semibold mt-2")
            ui.markdown(explanation)
        ui.label("Query").classes("font-semibold mt-2")
        ui.code(item.get("sql", ""), language="sql")
        ui.button("Close", on_click=d.close)
    d.open()


def _rerender_saved(detail, state, list_box, items):
    ui.timer(0.01, lambda: _render_saved_detail(detail, state, list_box, items), once=True)


def _set_layout(lyt, state, detail, list_box, items):
    state["saved_item"]["layout"] = lyt
    store.upsert(state["saved_item"])
    _rerender_saved(detail, state, list_box, items)


def _add_chart(state, detail, list_box, items):
    item = state["saved_item"]
    columns = state["saved_data"]["columns"]
    rows = state["saved_data"]["rows"]
    g = _guess_chart_cols(columns, rows)
    item.setdefault("charts", []).append(
        {"name": f"Chart {len(item['charts'])+1}", "type": "bar", "x": g["x"], "y": g["y"], "pinned": False}
    )
    if len(item["charts"]) == 1 and (not item.get("layout") or item.get("layout") == "table"):
        item["layout"] = "charts-top"
    store.upsert(item)
    _rerender_saved(detail, state, list_box, items)


def _rename_chart(idx, name, state, detail, list_box, items):
    item = state["saved_item"]
    item["charts"][idx]["name"] = name or f"Chart {idx+1}"
    store.upsert(item)


def _change_chart(idx, key, value, state, detail, list_box, items):
    item = state["saved_item"]
    if key == "y":
        item["charts"][idx]["y"] = [value]
    else:
        item["charts"][idx][key] = value
    store.upsert(item)
    _rerender_saved(detail, state, list_box, items)


def _toggle_pin(idx, value, state, detail, list_box, items):
    item = state["saved_item"]
    item["charts"][idx]["pinned"] = value
    store.upsert(item)
    _rerender_saved(detail, state, list_box, items)


def _remove_chart(idx, state, detail, list_box, items):
    item = state["saved_item"]
    item["charts"].pop(idx)
    if not item["charts"]:
        item["layout"] = "table"
    store.upsert(item)
    _rerender_saved(detail, state, list_box, items)


def _render_table(area, columns, rows):
    grid = ui.aggrid(
        {
            "columnDefs": [
                {"field": c, "headerName": c, "filter": True, "sortable": True}
                for c in columns
            ],
            "rowData": [dict(zip(columns, r)) for r in rows],
            "defaultColDef": {"flex": 1, "minWidth": 80},
            "pagination": len(rows) > 200,
            "paginationPageSize": 200,
        },
        theme="balham",
    )
    grid.style("height: 60vh; width: 100%")
    area._grid = grid
    return grid


def _filter_table(grid, text):
    if grid is None:
        return
    text = text or ""
    try:
        grid.run_grid_method("setGridOption", "quickFilterText", text)
    except Exception:
        try:
            grid.run_grid_method("setQuickFilter", text)
        except Exception:
            pass


def _toggle_sql(detail, state, list_box, items):
    box = state.get("saved_refs", {}).get("sql_box")
    if box is not None:
        box.set_visibility(not box.visible)


def _toggle_data(detail, state, list_box, items):
    area = state.get("saved_refs", {}).get("table_area")
    if area is not None:
        area.set_visibility(not area.visible)


def _export_csv(data):
    import csv as _csv

    buf = io.StringIO()
    w = _csv.writer(buf)
    w.writerow(data["columns"])
    for r in data["rows"]:
        w.writerow([a if a is not None else "" for a in r])
    ui.download(
        io.BytesIO(buf.getvalue().encode("utf-8")),
        "query_export.csv",
        "text/csv",
    )


async def _explain_saved(detail, state, list_box, items):
    item = state["saved_item"]
    refs = state.get("saved_refs", {})
    expl = refs.get("expl")
    spinner = refs.get("gen_spinner")

    # If an explanation already exists, just toggle its visibility
    if item.get("explanation") and expl is not None:
        expl.set_visibility(not expl.visible)
        return

    if spinner is not None:
        spinner.style("display:flex")

    async def _go():
        try:
            text = await _explain(item.get("db", ""), item.get("sql", ""), item.get("question", ""))
            item["explanation"] = text
            store.upsert(item)
            if expl is not None:
                expl.set_content(text)
                expl.set_visibility(True)
        except Exception as e:  # noqa: BLE001
            try:
                ui.notify(f"Explain failed: {e}", type="negative")
            except Exception:  # noqa: BLE001
                pass
        finally:
            if spinner is not None:
                spinner.style("display:none")

    asyncio.create_task(_go())


# --------------------------------------------------------------------------
# Settings builders
# --------------------------------------------------------------------------
def _build_settings_ai():
    llm = cfg_store.get_llm()
    with ui.card().classes("w-full max-w-3xl"):
        ui.label("AI Provider").classes("text-lg font-bold")
        provider = ui.select(
            ["openai", "anthropic", "ollama"], value=llm.get("provider", "openai")
        ).classes("w-64")
        with ui.row().classes("gap-2"):
            model = ui.input("Model", value=llm.get("model", "")).classes("w-64")
            api_key = ui.input("API key", value=llm.get("api_key", ""), password=True).classes(
                "w-64"
            )
        with ui.row().classes("gap-2"):
            base_url = ui.input("Base URL", value=llm.get("base_url", "")).classes("w-64")
            temp = ui.input("Temperature", value=str(llm.get("temperature", 0))).classes(
                "w-32"
            )
        sys_prompt = ui.textarea(
            "System prompt", value=llm.get("system_prompt", "")
        ).classes("w-full")
        with ui.row().classes("gap-2 mt-2"):
            ui.button(
                "Save",
                on_click=lambda: _save_llm(provider, model, api_key, base_url, temp, sys_prompt),
            ).props("color=blue")
            ui.button("Test connection", on_click=lambda: _test_llm()).props("flat")


def _save_llm(provider, model, api_key, base_url, temp, sys_prompt):
    payload = {
        "provider": provider.value,
        "model": model.value,
        "api_key": api_key.value,
        "base_url": base_url.value,
        "temperature": float(temp.value or 0),
        "local": provider.value == "ollama",
        "system_prompt": sys_prompt.value,
    }
    cfg_store.set_llm(payload)
    ui.notify("AI settings saved ✓")


def _test_llm():
    async def _go():
        try:
            from app.core.llm.sql_chain import test_llm_config

            reply = await asyncio.to_thread(test_llm_config, None)
            ui.notify(f"OK · {reply}")
        except Exception as e:  # noqa: BLE001
            ui.notify(f"AI test failed: {e}", type="negative")

    asyncio.create_task(_go())


def _build_settings_dbs():
    dbs = cfg_store.get_databases()
    with ui.card().classes("w-full max-w-3xl"):
        ui.label("Database Connections").classes("text-lg font-bold")
        list_box = ui.column().classes("w-full")
        _render_db_list(list_box, dbs)
        ui.button("+ Add database", on_click=lambda: _add_db(list_box)).props("flat")


def _render_db_list(list_box, dbs):
    list_box.clear()
    with list_box:
        for i, d in enumerate(dbs):
            with ui.card().classes("w-full"):
                with ui.row().classes("items-center gap-2"):
                    ui.label(d.get("name", "unnamed")).classes("font-bold")
                    ui.label(f"{d.get('engine','')} · {d.get('host','')}:{d.get('port','')}").classes(
                        "text-gray-500 text-sm"
                    )
                with ui.row().classes("gap-2 mt-2"):
                    ui.button("Test", on_click=lambda e, i=i: _test_db(i, list_box)).props("flat")
                    ui.button("Delete", on_click=lambda e, i=i: _del_db(i, list_box)).props(
                        "flat color=red"
                    )


def _add_db(list_box):
    dbs = cfg_store.get_databases()
    dbs.append(
        {
            "name": "new_db",
            "engine": "postgres",
            "host": "localhost",
            "port": 5432,
            "database": "",
            "username": "",
            "password": "",
            "windows_auth": False,
            "trust_server_certificate": True,
            "read_only": True,
        }
    )
    cfg_store.set_databases(dbs)
    registry.reload()
    _render_db_list(list_box, dbs)


def _del_db(i, list_box):
    dbs = cfg_store.get_databases()
    dbs.pop(i)
    cfg_store.set_databases(dbs)
    registry.reload()
    _render_db_list(list_box, dbs)


def _test_db(i, list_box):
    async def _go():
        try:
            from app.config.connections import _build_executor
            from app.config.settings import DbConfig

            dbs = cfg_store.get_databases()
            ex = _build_executor(DbConfig(**dbs[i]))
            snap = await asyncio.to_thread(ex.introspect)
            ui.notify(f"OK · {len(snap.tables)} tables")
        except Exception as e:  # noqa: BLE001
            ui.notify(f"Connection failed: {e}", type="negative")

    asyncio.create_task(_go())
