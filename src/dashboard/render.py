from __future__ import annotations

import html
import json
from datetime import datetime
from pathlib import Path

from ..config import OUTPUT_DIR
from ..db import list_items


def render_dashboard(output_path: Path | None = None) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = output_path or OUTPUT_DIR / "index.html"
    rows = list_items(limit=500)
    data = [dict(row) for row in rows]
    (OUTPUT_DIR / "items.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    output_path.write_text(_render_html(data), encoding="utf-8")
    return output_path


def _render_html(items: list[dict]) -> str:
    cards = "\n".join(_render_card(item) for item in items) or "<p class=\"empty\">暂无内容。先运行采集命令。</p>"
    sources = sorted({item.get("source", "") for item in items if item.get("source")})
    categories = sorted({item.get("category", "") for item in items if item.get("category")})
    statuses = sorted({item.get("status", "") for item in items if item.get("status")})
    min_date, max_date = _date_bounds(items)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AI研报助手看板</title>
  <style>
    :root {{
      --ink: #162033;
      --muted: #5d6978;
      --line: #d9e0e7;
      --paper: #ffffff;
      --soft: #f5f7fa;
      --accent: #126c68;
      --warn: #9f4f16;
      --danger: #9f2f2f;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif;
      color: var(--ink);
      background: #eef2f5;
      line-height: 1.65;
    }}
    header, main {{ width: min(1180px, calc(100% - 32px)); margin: 0 auto; }}
    header {{ padding: 30px 0 18px; }}
    h1 {{ margin: 0 0 6px; color: #123c3a; font-size: 28px; letter-spacing: 0; }}
    .meta {{ margin: 0; color: var(--muted); }}
    .header-links {{ margin-top: 10px; display: flex; gap: 10px; flex-wrap: wrap; }}
    .header-links a {{
      color: #126c68;
      text-decoration: none;
      border: 1px solid var(--line);
      background: #fff;
      border-radius: 7px;
      padding: 5px 9px;
      font-size: 13px;
    }}
    .header-links a:hover {{ border-color: var(--accent); }}
    .filters {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
      margin: 18px 0 10px;
      padding: 14px;
      background: var(--paper);
      border: 1px solid var(--line);
      border-radius: 8px;
    }}
    label {{ display: grid; gap: 5px; color: var(--muted); font-size: 13px; }}
    select, input, button {{
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 7px;
      padding: 8px 10px;
      font: inherit;
      color: var(--ink);
      background: #fff;
    }}
    button {{
      cursor: pointer;
      color: #123c3a;
      background: var(--soft);
    }}
    button:hover {{ border-color: var(--accent); }}
    .toolbar {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin: 0 0 12px;
      color: var(--muted);
      font-size: 13px;
    }}
    .toolbar button {{ width: auto; min-width: 88px; }}
    .items {{ display: grid; gap: 12px; padding-bottom: 32px; }}
    article {{
      background: var(--paper);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 16px 18px;
    }}
    article h2 {{ margin: 0 0 8px; font-size: 18px; letter-spacing: 0; }}
    article h2 a {{ color: #123c3a; text-decoration: none; }}
    article h2 a:hover {{ color: var(--accent); text-decoration: underline; }}
    .chips {{ display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 10px; }}
    .chip {{
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 2px 8px;
      color: var(--muted);
      font-size: 12px;
      background: #fff;
    }}
    .summary {{ white-space: pre-wrap; margin: 0; }}
    .status-need_manual {{ border-left: 4px solid var(--danger); }}
    .status-summary_pending {{ border-left: 4px solid var(--warn); }}
    .status-summarized {{ border-left: 4px solid var(--accent); }}
    .empty {{
      padding: 24px;
      background: var(--paper);
      border: 1px solid var(--line);
      border-radius: 8px;
    }}
    @media (max-width: 960px) {{
      .filters {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    }}
    @media (max-width: 760px) {{
      header, main {{ width: calc(100% - 24px); }}
      .filters {{ grid-template-columns: 1fr; }}
      .toolbar {{ align-items: stretch; flex-direction: column; }}
      .toolbar button {{ width: 100%; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>AI研报助手看板</h1>
    <p class="meta">共 {len(items)} 条内容。数据文件：output/items.json</p>
    <div class="header-links"><a href="reports/index.html">查看日报索引</a></div>
  </header>
  <main>
    <div class="filters">
      <label>来源<select id="source"><option value="">全部</option>{_options(sources)}</select></label>
      <label>分类<select id="category"><option value="">全部</option>{_options(categories)}</select></label>
      <label>状态<select id="status"><option value="">全部</option>{_options(statuses)}</select></label>
      <label>搜索<input id="search" type="search" placeholder="标题、来源、摘要"></label>
      <label>开始日期<input id="startDate" type="date" min="{min_date}" max="{max_date}"></label>
      <label>结束日期<input id="endDate" type="date" min="{min_date}" max="{max_date}"></label>
      <label>时间排序<select id="sortOrder"><option value="desc">最新优先</option><option value="asc">最早优先</option></select></label>
      <label>操作<button id="resetFilters" type="button">重置筛选</button></label>
    </div>
    <div class="toolbar">
      <span id="resultCount">显示 {len(items)} / {len(items)} 条</span>
      <span>当前按发布时间排序</span>
    </div>
    <div class="items" id="items">{cards}</div>
    <p class="empty" id="emptyState" hidden>没有符合当前筛选条件的内容。</p>
  </main>
  <script>
    const itemsContainer = document.getElementById("items");
    const resultCount = document.getElementById("resultCount");
    const emptyState = document.getElementById("emptyState");
    const filterIds = ["source", "category", "status", "search", "startDate", "endDate", "sortOrder"];
    const controls = filterIds.map(id => document.getElementById(id));
    const allCards = Array.from(document.querySelectorAll("article"));

    function timestampOf(card) {{
      const value = Date.parse(card.dataset.publishedAt || "");
      return Number.isFinite(value) ? value : 0;
    }}

    function applyFilters() {{
      const source = document.getElementById("source").value;
      const category = document.getElementById("category").value;
      const status = document.getElementById("status").value;
      const search = document.getElementById("search").value.trim().toLowerCase();
      const startDate = document.getElementById("startDate").value;
      const endDate = document.getElementById("endDate").value;
      const sortOrder = document.getElementById("sortOrder").value;

      let visibleCount = 0;
      const sortedCards = [...allCards].sort((a, b) => {{
        const diff = timestampOf(a) - timestampOf(b);
        if (diff !== 0) return sortOrder === "asc" ? diff : -diff;
        return (a.dataset.title || "").localeCompare(b.dataset.title || "", "zh-CN");
      }});

      sortedCards.forEach(card => {{
        const publishedDate = card.dataset.publishedDate || "";
        const dateOk = (!startDate || (publishedDate && publishedDate >= startDate))
          && (!endDate || (publishedDate && publishedDate <= endDate));
        const ok = (!source || card.dataset.source === source)
          && (!category || card.dataset.category === category)
          && (!status || card.dataset.status === status)
          && dateOk
          && (!search || card.innerText.toLowerCase().includes(search));
        card.hidden = !ok;
        if (ok) visibleCount += 1;
        itemsContainer.appendChild(card);
      }});

      resultCount.textContent = `显示 ${{visibleCount}} / ${{allCards.length}} 条`;
      emptyState.hidden = visibleCount !== 0;
    }}

    controls.forEach(control => control.addEventListener("input", applyFilters));
    document.getElementById("resetFilters").addEventListener("click", () => {{
      controls.forEach(control => {{ control.value = control.id === "sortOrder" ? "desc" : ""; }});
      applyFilters();
    }});
    applyFilters();
  </script>
</body>
</html>"""


def _render_card(item: dict) -> str:
    title = item.get("title") or "未命名内容"
    url = html.escape(item.get("url") or "#")
    summary = html.escape(item.get("summary") or "尚未生成摘要。")
    published_at = item.get("published_at") or ""
    published_date = _date_key(published_at)
    pdf = item.get("pdf_path") or ""
    chips = [
        published_at,
        item.get("source", ""),
        item.get("category", ""),
        item.get("source_type", ""),
        item.get("status", ""),
        item.get("completeness", ""),
    ]
    if pdf:
        chips.append(f"PDF: {pdf}")
    chip_html = "".join(f"<span class=\"chip\">{html.escape(str(chip))}</span>" for chip in chips if chip)
    status_class = f"status-{html.escape(item.get('status') or '')}"
    return f"""<article class="{status_class}" data-source="{html.escape(item.get('source') or '')}" data-category="{html.escape(item.get('category') or '')}" data-status="{html.escape(item.get('status') or '')}" data-published-at="{html.escape(published_at)}" data-published-date="{html.escape(published_date)}" data-title="{html.escape(title)}">
  <h2><a href="{url}" target="_blank" rel="noreferrer">{html.escape(title)}</a></h2>
  <div class="chips">{chip_html}</div>
  <p class="summary">{summary}</p>
</article>"""


def _options(values: list[str]) -> str:
    return "".join(f"<option value=\"{html.escape(value)}\">{html.escape(value)}</option>" for value in values)


def _date_bounds(items: list[dict]) -> tuple[str, str]:
    dates = sorted(date for date in (_date_key(item.get("published_at") or "") for item in items) if date)
    if not dates:
        return "", ""
    return dates[0], dates[-1]


def _date_key(value: str) -> str:
    if not value:
        return ""
    if len(value) >= 10 and value[4:5] == "-" and value[7:8] == "-":
        return value[:10]
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return ""
