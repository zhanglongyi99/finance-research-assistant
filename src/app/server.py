from __future__ import annotations

import errno
import json
import socket
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from ..config import OUTPUT_DIR, ensure_dirs
from ..db import count_ai_summaries, count_article_images, count_image_quality, image_stats_by_article, list_items
from ..qa import answer_question
from ..reports.briefing import BRIEFING_DIR, generate_briefing


APP_TITLE = "财经助手"


def run_app_server(*, host: str = "127.0.0.1", port: int = 8765, open_browser: bool = False) -> None:
    ensure_dirs()
    server, actual_port = _bind_server(host=host, port=port)
    url = f"http://{host}:{actual_port}/"
    print(f"本地 UI 已启动：{url}")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n本地 UI 已停止。")
    finally:
        server.server_close()


def _bind_server(*, host: str, port: int) -> tuple[ThreadingHTTPServer, int]:
    last_error: OSError | None = None
    for candidate in range(port, port + 10):
        if _port_is_open(host, candidate):
            continue
        try:
            return ThreadingHTTPServer((host, candidate), LocalAppHandler), candidate
        except OSError as error:
            last_error = error
            if error.errno not in {10048, errno.EADDRINUSE}:
                raise
    raise OSError(f"无法启动本地 UI：端口 {port}-{port + 9} 都不可用。") from last_error


def _port_is_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.2)
        return probe.connect_ex((host, port)) == 0


class LocalAppHandler(BaseHTTPRequestHandler):
    server_version = "FinanceResearchAssistant/0.1"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in {"/", "/index.html"}:
            self._send_html(_render_app_shell())
            return
        if parsed.path == "/api/status":
            self._send_json(_status_payload())
            return
        if parsed.path == "/api/items":
            params = parse_qs(parsed.query)
            limit = _int_param(params, "limit", 200)
            self._send_json(_items_payload(limit=limit))
            return
        if parsed.path == "/api/briefing":
            self._send_json(_briefing_payload())
            return
        self._send_error(HTTPStatus.NOT_FOUND, "未找到页面。")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        body = self._read_json_body()
        if parsed.path == "/api/ask":
            question = str(body.get("question") or "").strip()
            if not question:
                self._send_error(HTTPStatus.BAD_REQUEST, "问题不能为空。")
                return
            limit = int(body.get("limit") or 6)
            use_ai = bool(body.get("use_ai", True))
            try:
                answer = answer_question(question, limit=limit, use_ai=use_ai)
            except Exception as error:
                self._send_json({"ok": False, "error": str(error)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
                return
            self._send_json({"ok": True, "answer": answer})
            return
        if parsed.path == "/api/generate-briefing":
            limit = int(body.get("limit") or 8)
            use_ai = bool(body.get("use_ai", True))
            try:
                path = generate_briefing(limit=limit, use_ai=use_ai)
            except Exception as error:
                self._send_json({"ok": False, "error": str(error)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
                return
            self._send_json({"ok": True, "path": str(path), "briefing": _briefing_payload()})
            return
        self._send_error(HTTPStatus.NOT_FOUND, "未找到接口。")

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    def _send_html(self, html: str) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def _send_json(self, payload: dict[str, Any] | list[Any], *, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    def _send_error(self, status: HTTPStatus, message: str) -> None:
        self._send_json({"ok": False, "error": message}, status=status)


def _status_payload() -> dict[str, Any]:
    rows = [dict(row) for row in list_items(limit=5000)]
    total_images, content_images_count, image_summary_count = count_article_images()
    status_counts: dict[str, int] = {}
    source_counts: dict[str, int] = {}
    category_counts: dict[str, int] = {}
    for row in rows:
        _bump(status_counts, row.get("status") or "unknown")
        _bump(source_counts, row.get("source") or "unknown")
        _bump(category_counts, row.get("category") or "unknown")
    return {
        "ok": True,
        "article_count": len(rows),
        "ai_summary_count": count_ai_summaries(),
        "image_count": total_images,
        "content_image_count": content_images_count,
        "image_summary_count": image_summary_count,
        "status_counts": status_counts,
        "source_counts": source_counts,
        "category_counts": category_counts,
        "image_quality": [dict(row) for row in count_image_quality()],
        "latest_briefing": _briefing_meta(),
        "output_dir": str(OUTPUT_DIR),
    }


def _items_payload(*, limit: int = 200) -> dict[str, Any]:
    image_stats = image_stats_by_article()
    items = []
    for row in list_items(limit=limit):
        item = dict(row)
        stats = image_stats.get(item["id"], {})
        summary = (item.get("ai_summary") or item.get("summary") or "").strip()
        items.append(
            {
                "id": item["id"],
                "title": item.get("title") or "",
                "source": item.get("source") or "",
                "category": item.get("category") or "",
                "published_at": item.get("published_at") or "",
                "url": item.get("url") or "",
                "status": item.get("status") or "",
                "summary_mode": "ai" if item.get("ai_summary") else "local",
                "summary": summary,
                "ai_review_status": item.get("ai_review_status") or "",
                "image_count": int(stats.get("image_count") or 0),
                "content_image_count": int(stats.get("content_image_count") or 0),
                "vision_summary_count": int(stats.get("vision_summary_count") or 0),
                "usable_vision_count": int(stats.get("usable_vision_count") or 0),
            }
        )
    return {"ok": True, "items": items}


def _briefing_payload() -> dict[str, Any]:
    path = BRIEFING_DIR / "latest.json"
    if not path.exists():
        return {"ok": False, "error": "尚未生成 AI 阅读地图。"}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        return {"ok": False, "error": f"阅读地图 JSON 无法解析：{error}"}
    data["ok"] = True
    data["html_path"] = str(BRIEFING_DIR / "latest.html")
    return data


def _briefing_meta() -> dict[str, Any]:
    payload = _briefing_payload()
    if not payload.get("ok"):
        return {"exists": False}
    return {
        "exists": True,
        "generated_at": payload.get("generated_at"),
        "article_count": payload.get("article_count"),
        "briefing_mode": payload.get("briefing_mode"),
        "briefing_chars": len(payload.get("briefing") or ""),
    }


def _bump(counts: dict[str, int], key: str) -> None:
    counts[key] = counts.get(key, 0) + 1


def _int_param(params: dict[str, list[str]], key: str, default: int) -> int:
    try:
        return int((params.get(key) or [default])[0])
    except (TypeError, ValueError):
        return default


def _render_app_shell() -> str:
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{APP_TITLE}</title>
  <style>
    :root {{
      --ink: #172033;
      --muted: #5f6b7a;
      --line: #d9e1ea;
      --paper: #ffffff;
      --soft: #f5f7fa;
      --soft-2: #edf6f4;
      --accent: #126c68;
      --accent-dark: #123c3a;
      --danger: #9f2f2f;
      --warn: #9f4f16;
      color-scheme: light;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", "PingFang SC", Arial, sans-serif;
      color: var(--ink);
      background: #eef2f5;
      line-height: 1.62;
    }}
    .shell {{ display: grid; grid-template-columns: 232px minmax(0, 1fr); min-height: 100vh; }}
    aside {{ border-right: 1px solid var(--line); background: #fbfcfd; padding: 22px 16px; position: sticky; top: 0; height: 100vh; }}
    main {{ padding: 22px; }}
    h1, h2, h3 {{ margin: 0; color: var(--accent-dark); line-height: 1.28; letter-spacing: 0; }}
    h1 {{ font-size: 23px; }}
    h2 {{ font-size: 22px; }}
    h3 {{ font-size: 16px; margin-bottom: 8px; }}
    p {{ margin: 0; }}
    a {{ color: var(--accent); text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .muted {{ color: var(--muted); }}
    .brand {{ display: grid; gap: 4px; margin-bottom: 18px; }}
    .brand span {{ color: var(--muted); font-size: 13px; }}
    nav {{ display: grid; gap: 6px; }}
    nav button {{
      width: 100%;
      border: 1px solid transparent;
      border-radius: 7px;
      padding: 9px 10px;
      text-align: left;
      background: transparent;
      color: var(--ink);
      cursor: pointer;
      font: inherit;
    }}
    nav button:hover, nav button.active {{ border-color: #c7dedb; background: var(--soft-2); color: var(--accent-dark); }}
    .side-block {{ margin-top: 20px; padding-top: 16px; border-top: 1px solid var(--line); font-size: 13px; color: var(--muted); display: grid; gap: 8px; }}
    .topbar {{ display: flex; justify-content: space-between; align-items: flex-start; gap: 14px; margin-bottom: 14px; }}
    .actions {{ display: flex; gap: 8px; flex-wrap: wrap; justify-content: flex-end; }}
    button, input, select, textarea {{
      border: 1px solid var(--line);
      border-radius: 7px;
      padding: 8px 10px;
      color: var(--ink);
      background: #fff;
      font: inherit;
    }}
    button {{ cursor: pointer; background: var(--soft); color: var(--accent-dark); }}
    button.primary {{ background: var(--accent); border-color: var(--accent); color: #fff; }}
    button:disabled {{ opacity: 0.6; cursor: wait; }}
    input, select, textarea {{ width: 100%; }}
    textarea {{ min-height: 96px; resize: vertical; }}
    .grid {{ display: grid; gap: 12px; }}
    .metrics {{ grid-template-columns: repeat(5, minmax(0, 1fr)); }}
    .metric, .panel, .card {{
      background: var(--paper);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px 16px;
    }}
    .metric strong {{ display: block; color: var(--accent); font-size: 25px; line-height: 1.1; }}
    .metric span {{ display: block; margin-top: 4px; color: var(--muted); font-size: 12px; }}
    .view {{ display: none; }}
    .view.active {{ display: grid; gap: 14px; }}
    .briefing {{ display: grid; gap: 10px; }}
    .briefing-section h3 {{ margin-bottom: 8px; }}
    .briefing-section ul {{ margin: 0; padding-left: 18px; }}
    .briefing-section li {{ margin: 6px 0; }}
    .briefing-section strong {{ color: var(--accent-dark); }}
    .filters {{ display: grid; grid-template-columns: 1.2fr repeat(3, minmax(0, 160px)); gap: 10px; }}
    .article-list {{ display: grid; gap: 10px; }}
    .article-title {{ display: flex; justify-content: space-between; gap: 10px; align-items: flex-start; }}
    .article-title h3 {{ margin: 0; }}
    .chips {{ display: flex; flex-wrap: wrap; gap: 6px; margin: 8px 0; }}
    .chip {{ border: 1px solid var(--line); border-radius: 999px; padding: 2px 8px; color: var(--muted); font-size: 12px; background: #fbfcfd; }}
    .summary {{ margin-top: 8px; color: #27364a; white-space: pre-wrap; }}
    .qa-form {{ display: grid; grid-template-columns: minmax(0, 1fr) 160px; gap: 10px; align-items: end; }}
    .answer {{ white-space: pre-wrap; }}
    .status-table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
    .status-table th, .status-table td {{ border: 1px solid var(--line); padding: 8px 10px; text-align: left; vertical-align: top; }}
    .status-table th {{ background: var(--soft-2); color: var(--accent-dark); }}
    .notice {{ color: var(--muted); font-size: 13px; }}
    .error {{ color: var(--danger); }}
    @media (max-width: 980px) {{
      .shell {{ grid-template-columns: 1fr; }}
      aside {{ position: static; height: auto; }}
      nav {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .metrics {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .filters, .qa-form {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <div class="shell">
    <aside>
      <div class="brand">
        <h1>{APP_TITLE}</h1>
        <span>本地投研工作台</span>
      </div>
      <nav>
        <button class="active" data-view="briefingView">阅读地图</button>
        <button data-view="libraryView">研报库</button>
        <button data-view="qaView">引用问答</button>
        <button data-view="statusView">运行状态</button>
      </nav>
      <div class="side-block">
        <a href="/api/briefing" target="_blank">阅读地图 JSON</a>
        <a href="/api/items" target="_blank">研报库 JSON</a>
        <span>本地 SQLite / 本地 API / 静态输出共用。</span>
      </div>
    </aside>
    <main>
      <div class="topbar">
        <div>
          <h2 id="viewTitle">阅读地图</h2>
          <p class="muted" id="viewSubtitle">按本轮研报生成的投研阅读地图。</p>
        </div>
        <div class="actions">
          <button id="reloadBtn" type="button">刷新数据</button>
          <button class="primary" id="generateBriefingBtn" type="button">重新生成阅读地图</button>
        </div>
      </div>

      <section class="view active" id="briefingView">
        <div class="grid metrics" id="metrics"></div>
        <div class="panel">
          <div class="briefing" id="briefing">加载中...</div>
        </div>
      </section>

      <section class="view" id="libraryView">
        <div class="panel">
          <div class="filters">
            <input id="searchInput" type="search" placeholder="搜索标题、摘要、来源">
            <select id="sourceFilter"><option value="">全部来源</option></select>
            <select id="categoryFilter"><option value="">全部分类</option></select>
            <select id="summaryModeFilter"><option value="">全部摘要</option><option value="ai">AI 总结</option><option value="local">本地摘要</option></select>
          </div>
        </div>
        <div class="notice" id="libraryCount"></div>
        <div class="article-list" id="articleList"></div>
      </section>

      <section class="view" id="qaView">
        <div class="panel grid">
          <textarea id="questionInput" placeholder="输入你想基于已入库研报追问的问题"></textarea>
          <div class="qa-form">
            <label class="notice"><input id="useAiInput" type="checkbox" checked style="width:auto;margin-right:6px;">调用模型生成回答</label>
            <button class="primary" id="askBtn" type="button">提问</button>
          </div>
        </div>
        <div class="panel answer" id="answerBox">等待提问。</div>
      </section>

      <section class="view" id="statusView">
        <div class="grid metrics" id="statusMetrics"></div>
        <div class="panel" id="statusDetails"></div>
      </section>
    </main>
  </div>

  <script>
    const state = {{ status: null, items: [], briefing: null }};
    const titles = {{
      briefingView: ["阅读地图", "按本轮研报生成的投研阅读地图。"],
      libraryView: ["研报库", "浏览、筛选和复核已入库材料。"],
      qaView: ["引用问答", "基于本地研报库进行文章级引用问答。"],
      statusView: ["运行状态", "查看数据、摘要、图片和模型产物状态。"],
    }};

    function escapeHtml(value) {{
      return String(value ?? "").replace(/[&<>"']/g, char => ({{ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }}[char]));
    }}
    function formatInline(value) {{
      return escapeHtml(value).replace(/\\*\\*(.+?)\\*\\*/g, "<strong>$1</strong>");
    }}
    function setBusy(button, busy, label) {{
      button.disabled = busy;
      if (label) button.textContent = busy ? label : button.dataset.label;
    }}
    function metric(label, value) {{
      return `<div class="metric"><strong>${{escapeHtml(value)}}</strong><span>${{escapeHtml(label)}}</span></div>`;
    }}
    function sectionHtml(title, lines) {{
      const items = [];
      const blocks = [];
      function flush() {{
        if (items.length) {{
          blocks.push(`<ul>${{items.splice(0).map(item => `<li>${{formatInline(item)}}</li>`).join("")}}</ul>`);
        }}
      }}
      for (const raw of lines) {{
        const line = raw.trim();
        if (!line) continue;
        if (line.startsWith("- ")) items.push(line.slice(2).trim());
        else {{ flush(); blocks.push(`<p>${{formatInline(line)}}</p>`); }}
      }}
      flush();
      return `<div class="briefing-section"><h3>${{escapeHtml(title)}}</h3>${{blocks.join("")}}</div>`;
    }}
    function renderBriefingText(text) {{
      const known = ["本期阅读地图", "宏观经济形势", "市场环境与资产含义", "风险、黑天鹅与非共识观点", "细分领域专业分析", "需要跟踪", "本期引用"];
      const sections = [];
      let title = "阅读地图";
      let lines = [];
      for (const raw of String(text || "").split(/\\r?\\n/)) {{
        const line = raw.trim();
        if (!line) continue;
        const match = known.find(item => line.startsWith(item));
        if (match) {{
          if (lines.length || sections.length) sections.push([title, lines]);
          title = match;
          const rest = line.slice(match.length).replace(/^[:：]\\s*/, "");
          lines = rest ? [rest] : [];
        }} else {{
          lines.push(line);
        }}
      }}
      if (lines.length || !sections.length) sections.push([title, lines]);
      return sections.map(([sectionTitle, sectionLines]) => sectionHtml(sectionTitle, sectionLines)).join("");
    }}
    async function fetchJson(url, options) {{
      const response = await fetch(url, options);
      const data = await response.json();
      if (!response.ok || data.ok === false) throw new Error(data.error || "请求失败");
      return data;
    }}
    async function loadAll() {{
      const [status, items, briefing] = await Promise.all([
        fetchJson("/api/status"),
        fetchJson("/api/items?limit=500"),
        fetchJson("/api/briefing").catch(error => ({{ ok: false, error: error.message }})),
      ]);
      state.status = status;
      state.items = items.items || [];
      state.briefing = briefing;
      renderStatus();
      renderLibrary();
      renderBriefing();
    }}
    function renderStatus() {{
      const s = state.status || {{}};
      const cards = [
        metric("入库文章", s.article_count || 0),
        metric("AI 深度总结", s.ai_summary_count || 0),
        metric("图片资产", s.image_count || 0),
        metric("正文图", s.content_image_count || 0),
        metric("视觉摘要", s.image_summary_count || 0),
      ].join("");
      document.getElementById("metrics").innerHTML = cards;
      document.getElementById("statusMetrics").innerHTML = cards;
      const sourceRows = Object.entries(s.source_counts || {{}}).map(([key, value]) => `<tr><td>${{escapeHtml(key)}}</td><td>${{value}}</td></tr>`).join("");
      const qualityRows = (s.image_quality || []).map(row => `<tr><td>${{escapeHtml(row.vision_kind)}}</td><td>${{escapeHtml(row.review_status)}}</td><td>${{row.count}}</td></tr>`).join("");
      document.getElementById("statusDetails").innerHTML = `
        <h3>来源分布</h3>
        <table class="status-table"><tbody>${{sourceRows}}</tbody></table>
        <h3 style="margin-top:14px;">视觉摘要质量</h3>
        <table class="status-table"><thead><tr><th>类型</th><th>状态</th><th>数量</th></tr></thead><tbody>${{qualityRows || "<tr><td colspan='3'>暂无</td></tr>"}}</tbody></table>
      `;
    }}
    function renderBriefing() {{
      const box = document.getElementById("briefing");
      if (!state.briefing || !state.briefing.ok) {{
        box.innerHTML = `<div class="notice error">${{escapeHtml(state.briefing?.error || "暂无阅读地图")}}</div>`;
        return;
      }}
      box.innerHTML = renderBriefingText(state.briefing.briefing || "");
    }}
    function fillFilters() {{
      const sources = [...new Set(state.items.map(item => item.source).filter(Boolean))].sort();
      const categories = [...new Set(state.items.map(item => item.category).filter(Boolean))].sort();
      const source = document.getElementById("sourceFilter");
      const category = document.getElementById("categoryFilter");
      if (source.options.length <= 1) source.insertAdjacentHTML("beforeend", sources.map(value => `<option>${{escapeHtml(value)}}</option>`).join(""));
      if (category.options.length <= 1) category.insertAdjacentHTML("beforeend", categories.map(value => `<option>${{escapeHtml(value)}}</option>`).join(""));
    }}
    function renderLibrary() {{
      fillFilters();
      const search = document.getElementById("searchInput").value.trim().toLowerCase();
      const source = document.getElementById("sourceFilter").value;
      const category = document.getElementById("categoryFilter").value;
      const mode = document.getElementById("summaryModeFilter").value;
      const filtered = state.items.filter(item => {{
        const haystack = `${{item.title}} ${{item.source}} ${{item.category}} ${{item.summary}}`.toLowerCase();
        return (!search || haystack.includes(search)) && (!source || item.source === source) && (!category || item.category === category) && (!mode || item.summary_mode === mode);
      }});
      document.getElementById("libraryCount").textContent = `显示 ${{filtered.length}} / ${{state.items.length}} 篇`;
      document.getElementById("articleList").innerHTML = filtered.map(item => `
        <article class="card">
          <div class="article-title">
            <h3><a href="${{escapeHtml(item.url)}}" target="_blank" rel="noreferrer">${{escapeHtml(item.title)}}</a></h3>
            <span class="chip">${{escapeHtml(item.summary_mode)}}</span>
          </div>
          <div class="chips">
            <span class="chip">${{escapeHtml(item.published_at || "")}}</span>
            <span class="chip">${{escapeHtml(item.source || "")}}</span>
            <span class="chip">${{escapeHtml(item.category || "")}}</span>
            <span class="chip">视觉 ${{item.usable_vision_count || 0}}/${{item.vision_summary_count || 0}}</span>
          </div>
          <p class="summary">${{escapeHtml((item.summary || "暂无摘要").slice(0, 520))}}</p>
        </article>
      `).join("") || `<div class="panel notice">没有符合条件的研报。</div>`;
    }}
    async function ask() {{
      const button = document.getElementById("askBtn");
      button.dataset.label = button.textContent;
      const question = document.getElementById("questionInput").value.trim();
      if (!question) return;
      setBusy(button, true, "生成中...");
      document.getElementById("answerBox").textContent = "生成中...";
      try {{
        const data = await fetchJson("/api/ask", {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify({{ question, limit: 6, use_ai: document.getElementById("useAiInput").checked }}),
        }});
        document.getElementById("answerBox").textContent = data.answer;
      }} catch (error) {{
        document.getElementById("answerBox").textContent = `失败：${{error.message}}`;
      }} finally {{
        setBusy(button, false);
      }}
    }}
    async function regenerateBriefing() {{
      const button = document.getElementById("generateBriefingBtn");
      button.dataset.label = button.textContent;
      setBusy(button, true, "生成中...");
      try {{
        const data = await fetchJson("/api/generate-briefing", {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify({{ limit: 8, use_ai: true }}),
        }});
        state.briefing = data.briefing;
        await loadAll();
        switchView("briefingView");
      }} catch (error) {{
        document.getElementById("briefing").innerHTML = `<div class="notice error">生成失败：${{escapeHtml(error.message)}}</div>`;
      }} finally {{
        setBusy(button, false);
      }}
    }}
    function switchView(id) {{
      document.querySelectorAll(".view").forEach(view => view.classList.toggle("active", view.id === id));
      document.querySelectorAll("nav button").forEach(button => button.classList.toggle("active", button.dataset.view === id));
      document.getElementById("viewTitle").textContent = titles[id][0];
      document.getElementById("viewSubtitle").textContent = titles[id][1];
    }}
    document.querySelectorAll("nav button").forEach(button => button.addEventListener("click", () => switchView(button.dataset.view)));
    ["searchInput", "sourceFilter", "categoryFilter", "summaryModeFilter"].forEach(id => document.getElementById(id).addEventListener("input", renderLibrary));
    document.getElementById("reloadBtn").addEventListener("click", loadAll);
    document.getElementById("askBtn").addEventListener("click", ask);
    document.getElementById("generateBriefingBtn").addEventListener("click", regenerateBriefing);
    loadAll().catch(error => {{
      document.getElementById("briefing").innerHTML = `<div class="notice error">加载失败：${{escapeHtml(error.message)}}</div>`;
    }});
  </script>
</body>
</html>"""
