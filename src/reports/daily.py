from __future__ import annotations

import html
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from ..config import REPORTS_DIR
from ..db import list_items


LOCAL_TZ = timezone(timedelta(hours=8), "Asia/Shanghai")
DAILY_DIR = REPORTS_DIR / "daily"
INDEX_PATH = REPORTS_DIR / "index.html"


WATCH_TERMS = {
    "政策": ("政策", "政治局", "央行", "财政", "降准", "降息", "专项债", "PSL"),
    "利率与债市": ("利率", "债市", "国债", "信用", "转债", "城投", "REITs"),
    "通胀与商品": ("通胀", "CPI", "PPI", "油价", "原油", "煤价", "商品"),
    "权益与风格": ("A股", "港股", "红利", "成长", "AI", "科技", "拥挤", "景气"),
    "外部风险": ("美联储", "美元", "汇率", "地缘", "关税", "海外", "美国"),
    "地产与内需": ("地产", "消费", "社零", "投资", "PMI", "内需"),
}


def generate_daily_report(rows: Iterable, *, reason: str = "run") -> Path | None:
    items = [dict(row) for row in rows]
    if not items:
        return None

    DAILY_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(LOCAL_TZ).replace(microsecond=0)
    day_dir = DAILY_DIR / now.strftime("%Y-%m-%d")
    day_dir.mkdir(parents=True, exist_ok=True)

    report_id = now.strftime("%Y%m%d-%H%M%S")
    metadata = _metadata(report_id, now, items, reason)
    article_payload = [_article_payload(item) for item in items]
    category_sections = _category_sections(article_payload)
    watch_items = _watch_items(article_payload)
    payload = {
        "metadata": metadata,
        "categories": category_sections,
        "watch_items": watch_items,
        "articles": article_payload,
    }

    json_path = day_dir / f"{report_id}.json"
    html_path = day_dir / f"{report_id}.html"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    html_path.write_text(_render_report(payload), encoding="utf-8")
    render_report_index()
    return html_path


def generate_report_for_created_today() -> Path | None:
    today = datetime.now(LOCAL_TZ).date().isoformat()
    rows = [row for row in list_items(limit=5000) if _local_date(row["created_at"]) == today]
    return generate_daily_report(rows, reason="created_today")


def render_report_index() -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    records = []
    for json_path in sorted(DAILY_DIR.glob("*/*.json"), reverse=True) if DAILY_DIR.exists() else []:
        try:
            payload = json.loads(json_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        metadata = payload.get("metadata", {})
        html_path = json_path.with_suffix(".html")
        records.append(
            {
                "title": metadata.get("title") or "未命名日报",
                "generated_at": metadata.get("generated_at") or "",
                "article_count": metadata.get("article_count") or 0,
                "category_count": metadata.get("category_count") or 0,
                "path": _relative_path(html_path, REPORTS_DIR),
                "reason": metadata.get("reason") or "",
            }
        )
    INDEX_PATH.write_text(_render_index(records), encoding="utf-8")
    return INDEX_PATH


def _metadata(report_id: str, now: datetime, items: list[dict], reason: str) -> dict:
    categories = {item.get("category") or "未分类" for item in items}
    sources = Counter(item.get("source") or "未知来源" for item in items)
    return {
        "id": report_id,
        "title": f"{now:%Y-%m-%d} 财经助手日报",
        "generated_at": now.isoformat(),
        "reason": reason,
        "article_count": len(items),
        "category_count": len(categories),
        "source_count": len(sources),
        "sources": dict(sources),
        "summary_mode": "当前使用已有本地摘要；后续可替换为逐篇深度总结模型。",
    }


def _article_payload(item: dict) -> dict:
    summary = item.get("summary") or "尚未生成摘要。"
    return {
        "id": item.get("id") or "",
        "title": item.get("title") or "未命名内容",
        "source": item.get("source") or "",
        "category": item.get("category") or "未分类",
        "published_at": item.get("published_at") or "",
        "published_local": _local_datetime(item.get("published_at") or ""),
        "url": item.get("url") or "",
        "brief_summary": _brief_summary(summary),
        "deep_summary_status": "待接入模型深度总结；当前沿用本地摘要。",
        "watch_labels": _labels_for_text(" ".join([item.get("title") or "", summary])),
    }


def _category_sections(articles: list[dict]) -> list[dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for article in articles:
        groups[article["category"]].append(article)

    sections = []
    for category, rows in sorted(groups.items(), key=lambda pair: pair[0]):
        source_counts = Counter(row["source"] for row in rows)
        labels = Counter(label for row in rows for label in row["watch_labels"])
        sections.append(
            {
                "category": category,
                "article_count": len(rows),
                "sources": dict(source_counts),
                "today_summary": _category_summary(category, rows, labels),
                "focus_labels": [label for label, _count in labels.most_common(5)],
            }
        )
    return sections


def _watch_items(articles: list[dict]) -> list[dict]:
    candidates = []
    for article in articles:
        for label in article["watch_labels"]:
            candidates.append(
                {
                    "label": label,
                    "title": article["title"],
                    "source": article["source"],
                    "category": article["category"],
                    "url": article["url"],
                    "reason": _watch_reason(label),
                }
            )
    deduped = []
    seen: set[tuple[str, str]] = set()
    for item in candidates:
        key = (item["label"], item["url"])
        if key not in seen:
            seen.add(key)
            deduped.append(item)
    return deduped[:20]


def _category_summary(category: str, rows: list[dict], labels: Counter) -> str:
    label_text = "、".join(label for label, _count in labels.most_common(3)) or "暂无突出标签"
    titles = "；".join(row["title"] for row in rows[:3])
    return f"{category}今日导入 {len(rows)} 篇，主要来自 {', '.join(sorted({row['source'] for row in rows}))}。重点线索：{label_text}。代表文章：{titles}。"


def _brief_summary(summary: str) -> str:
    lines = []
    for raw_line in summary.splitlines():
        line = raw_line.strip(" -\t")
        if not line or line.endswith("："):
            continue
        lines.append(line)
        if len(lines) >= 3:
            break
    return "；".join(lines) if lines else summary[:220]


def _labels_for_text(text: str) -> list[str]:
    labels = []
    for label, terms in WATCH_TERMS.items():
        if any(term.lower() in text.lower() for term in terms):
            labels.append(label)
    return labels


def _watch_reason(label: str) -> str:
    return {
        "政策": "涉及政策或流动性变化，可能影响市场风险偏好和资产定价。",
        "利率与债市": "涉及利率、信用或转债线索，适合纳入债市跟踪。",
        "通胀与商品": "涉及价格、通胀或商品成本，可能影响盈利和政策预期。",
        "权益与风格": "涉及权益风格、行业景气或科技主题，适合观察资金轮动。",
        "外部风险": "涉及海外政策、汇率或地缘变量，需要关注外部扰动。",
        "地产与内需": "涉及地产、消费、投资或 PMI，关系到内需修复判断。",
    }.get(label, "值得人工复核。")


def _render_report(payload: dict) -> str:
    metadata = payload["metadata"]
    categories = payload["categories"]
    watch_items = payload["watch_items"]
    articles = payload["articles"]
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(metadata["title"])}</title>
  <style>
    :root {{
      --ink: #172033;
      --muted: #5f6b7a;
      --line: #d9e1ea;
      --paper: #ffffff;
      --soft: #f5f7fa;
      --accent: #126c68;
      --warn: #9f4f16;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif;
      color: var(--ink);
      background: #eef2f5;
      line-height: 1.68;
    }}
    main {{ width: min(1120px, calc(100% - 32px)); margin: 30px auto; }}
    header, section {{
      background: var(--paper);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 22px 26px;
      margin-bottom: 14px;
    }}
    h1, h2, h3 {{ line-height: 1.35; letter-spacing: 0; color: #123c3a; }}
    h1 {{ margin: 0 0 8px; font-size: 28px; }}
    h2 {{ margin: 0 0 12px; font-size: 21px; }}
    h3 {{ margin: 0 0 8px; font-size: 17px; }}
    p {{ margin: 8px 0; }}
    a {{ color: #126c68; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .meta, .muted {{ color: var(--muted); }}
    .metrics {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 10px;
      margin-top: 14px;
    }}
    .metric {{ background: var(--soft); border: 1px solid var(--line); border-radius: 8px; padding: 12px; }}
    .metric strong {{ display: block; font-size: 24px; color: var(--accent); }}
    .grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }}
    .card {{ border: 1px solid var(--line); border-radius: 8px; padding: 14px; background: #fff; }}
    .chips {{ display: flex; flex-wrap: wrap; gap: 6px; margin: 8px 0; }}
    .chip {{ border: 1px solid var(--line); border-radius: 999px; padding: 2px 8px; color: var(--muted); font-size: 12px; }}
    .watch {{ border-left: 4px solid var(--warn); }}
    ol, ul {{ margin: 8px 0 0 20px; padding: 0; }}
    li {{ margin: 6px 0; }}
    @media (max-width: 800px) {{
      main {{ width: calc(100% - 24px); margin: 12px auto; }}
      header, section {{ padding: 18px; }}
      .metrics, .grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <h1>{html.escape(metadata["title"])}</h1>
      <p class="meta">生成时间：{html.escape(metadata["generated_at"])}。{html.escape(metadata["summary_mode"])}</p>
      <div class="metrics">
        <div class="metric"><strong>{metadata["article_count"]}</strong><span>本次导入文章</span></div>
        <div class="metric"><strong>{metadata["category_count"]}</strong><span>覆盖领域</span></div>
        <div class="metric"><strong>{metadata["source_count"]}</strong><span>来源公众号</span></div>
        <div class="metric"><strong>{len(watch_items)}</strong><span>注意事项</span></div>
      </div>
    </header>
    <section>
      <h2>今日领域总结</h2>
      <div class="grid">{''.join(_render_category(section) for section in categories)}</div>
    </section>
    <section>
      <h2>需要特殊注意</h2>
      {''.join(_render_watch(item) for item in watch_items) or '<p class="muted">本次导入未识别到特别突出的风险/政策/资产线索。</p>'}
    </section>
    <section>
      <h2>今日导入文章</h2>
      {''.join(_render_article(article) for article in articles)}
    </section>
  </main>
</body>
</html>"""


def _render_category(section: dict) -> str:
    labels = "".join(f"<span class=\"chip\">{html.escape(label)}</span>" for label in section["focus_labels"])
    return f"""<div class="card">
  <h3>{html.escape(section["category"])} · {section["article_count"]} 篇</h3>
  <div class="chips">{labels}</div>
  <p>{html.escape(section["today_summary"])}</p>
</div>"""


def _render_watch(item: dict) -> str:
    return f"""<div class="card watch">
  <h3>{html.escape(item["label"])}：<a href="{html.escape(item["url"])}" target="_blank" rel="noreferrer">{html.escape(item["title"])}</a></h3>
  <p class="muted">{html.escape(item["source"])} / {html.escape(item["category"])}</p>
  <p>{html.escape(item["reason"])}</p>
</div>"""


def _render_article(article: dict) -> str:
    labels = "".join(f"<span class=\"chip\">{html.escape(label)}</span>" for label in article["watch_labels"])
    return f"""<div class="card">
  <h3><a href="{html.escape(article["url"])}" target="_blank" rel="noreferrer">{html.escape(article["title"])}</a></h3>
  <div class="chips">
    <span class="chip">{html.escape(article["published_local"])}</span>
    <span class="chip">{html.escape(article["source"])}</span>
    <span class="chip">{html.escape(article["category"])}</span>
    {labels}
  </div>
  <p>{html.escape(article["brief_summary"])}</p>
  <p class="muted">{html.escape(article["deep_summary_status"])}</p>
</div>"""


def _render_index(records: list[dict]) -> str:
    rows = "\n".join(
        f"""<tr>
  <td><a href="{html.escape(record["path"])}">{html.escape(record["title"])}</a></td>
  <td>{html.escape(record["generated_at"])}</td>
  <td>{record["article_count"]}</td>
  <td>{record["category_count"]}</td>
  <td>{html.escape(record["reason"])}</td>
</tr>"""
        for record in records
    )
    if not rows:
        rows = '<tr><td colspan="5">暂无日报。</td></tr>'
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>财经助手日报索引</title>
  <style>
    body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif; color: #172033; background: #eef2f5; }}
    main {{ width: min(980px, calc(100% - 32px)); margin: 32px auto; background: #fff; border: 1px solid #d9e1ea; border-radius: 8px; padding: 24px; }}
    h1 {{ margin: 0 0 14px; color: #123c3a; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ border: 1px solid #d9e1ea; padding: 10px 12px; text-align: left; }}
    th {{ background: #eef5f4; }}
    a {{ color: #126c68; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
  </style>
</head>
<body>
  <main>
    <h1>财经助手日报索引</h1>
    <table>
      <thead><tr><th>报告</th><th>生成时间</th><th>文章数</th><th>领域数</th><th>类型</th></tr></thead>
      <tbody>{rows}</tbody>
    </table>
  </main>
</body>
</html>"""


def _relative_path(path: Path, base: Path) -> str:
    return path.resolve().relative_to(base.resolve()).as_posix()


def _local_datetime(value: str) -> str:
    parsed = _parse_datetime(value)
    if not parsed:
        return value
    return parsed.astimezone(LOCAL_TZ).strftime("%Y-%m-%d %H:%M")


def _local_date(value: str) -> str:
    parsed = _parse_datetime(value)
    if not parsed:
        return value[:10]
    return parsed.astimezone(LOCAL_TZ).date().isoformat()


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        match = re.match(r"^\d{4}-\d{2}-\d{2}", value)
        if not match:
            return None
        return datetime.fromisoformat(match.group(0)).replace(tzinfo=LOCAL_TZ)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed
