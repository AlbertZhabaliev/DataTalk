from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, select_autoescape

from app.core.executor.base import QueryResult

_TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{{ title }}</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
<style>
  body{font-family:system-ui,Segoe UI,Arial;margin:0;padding:24px;background:#f7f8fa}
  h1{font-size:18px} .card{background:#fff;border-radius:10px;padding:16px;
  box-shadow:0 1px 3px rgba(0,0,0,.08);margin-bottom:20px}
  .report{background:#eef6ff;border-left:4px solid #3b82f6;padding:12px 16px;
  border-radius:6px;font-size:14px;line-height:1.5}
  table{border-collapse:collapse;width:100%;font-size:13px;margin-top:8px}
  th,td{border:1px solid #e3e6ea;padding:6px 10px;text-align:left}
  th{background:#f0f2f5}
</style>
</head>
<body>
<h1>{{ title }}</h1>
{% if report %}
<div class="card report">{{ report }}</div>
{% endif %}
<div class="card"><div id="chart" style="height:420px"></div></div>
<div class="card"><table>
  <thead><tr>{% for c in columns %}<th>{{ c }}</th>{% endfor %}</tr></thead>
  <tbody>
  {% for r in rows %}<tr>{% for v in r %}<td>{{ v }}</td>{% endfor %}</tr>{% endfor %}
  </tbody>
</table>
<p style="color:#888;font-size:12px">{{ row_count }} rows{% if truncated %} (truncated){% endif %}</p>
</div>
<script>
var chart = echarts.init(document.getElementById('chart'));
chart.setOption({{ chart_option | safe }});
window.addEventListener('resize', function(){ chart.resize(); });
</script>
</body></html>
"""


def _infer_chart_option(result: QueryResult, chart_type: str | None) -> dict:
    cols, rows = result.columns, result.rows
    kind = (chart_type or "bar").lower()
    if kind in ("table",) or len(cols) < 2:
        return {"title": {"text": "Table view"}, "series": []}
    cat = [str(r[0]) for r in rows]
    ser = [r[1] for r in rows if isinstance(r[1], (int, float))]
    if len(ser) != len(rows):
        return {"title": {"text": "Table view"}, "series": []}
    if kind == "pie":
        return {
            "tooltip": {"trigger": "item"},
            "series": [{
                "type": "pie", "radius": "60%",
                "data": [{"name": c, "value": v} for c, v in zip(cat, ser)],
            }],
        }
    if kind == "line":
        return {
            "tooltip": {"trigger": "axis"},
            "xAxis": {"type": "category", "data": cat},
            "yAxis": {"type": "value"},
            "series": [{"type": "line", "data": ser, "smooth": True}],
        }
    return {
        "tooltip": {"trigger": "axis"},
        "xAxis": {"type": "category", "data": cat},
        "yAxis": {"type": "value"},
        "series": [{"type": "bar", "data": ser}],
    }


def build_dashboard(
    result: QueryResult,
    title: str = "Query result",
    report: str = "",
    chart_type: str | None = None,
) -> str:
    option = _infer_chart_option(result, chart_type)
    env = Environment(autoescape=select_autoescape())
    tmpl = env.from_string(_TEMPLATE)
    return tmpl.render(
        title=title,
        report=report,
        columns=result.columns,
        rows=[[_js(v) for v in r] for r in result.rows],
        row_count=result.row_count,
        truncated=result.truncated,
        chart_option=option,
    )


def _js(v):
    return v if isinstance(v, (int, float, bool)) else str(v)
