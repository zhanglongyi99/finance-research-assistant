from __future__ import annotations

import html
import json
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..config import OUTPUT_DIR
from ..db import list_article_image_summaries, list_items
from ..llm import OpenAICompatibleClient


LOCAL_TZ = timezone(timedelta(hours=8), "Asia/Shanghai")
BRIEFING_DIR = OUTPUT_DIR / "briefing"


def generate_briefing(*, limit: int = 12, use_ai: bool = True) -> Path:
    BRIEFING_DIR.mkdir(parents=True, exist_ok=True)
    rows = [dict(row) for row in list_items(limit=limit)]
    payload = _build_payload(rows)
    if use_ai and rows:
        payload["briefing"] = _ai_briefing(payload)
        payload["briefing_mode"] = "ai"
    else:
        payload["briefing"] = _local_briefing(payload)
        payload["briefing_mode"] = "local"

    output_stem = "latest" if use_ai else "local"
    json_path = BRIEFING_DIR / f"{output_stem}.json"
    html_path = BRIEFING_DIR / f"{output_stem}.html"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    html_path.write_text(_render_html(payload), encoding="utf-8")
    return html_path


def _build_payload(rows: list[dict]) -> dict:
    now = datetime.now(LOCAL_TZ).replace(microsecond=0)
    articles = []
    for row in rows:
        summary = (row.get("ai_summary") or row.get("summary") or "").strip()
        image_summaries = [
            {
                "image_index": image["image_index"],
                "summary": image["vision_summary"],
                "kind": image["vision_kind"] if "vision_kind" in image.keys() else "",
                "quality": image["vision_quality"] if "vision_quality" in image.keys() else 0,
            }
            for image in list_article_image_summaries(row["id"])
        ]
        articles.append(
            {
                "id": row.get("id") or "",
                "title": row.get("title") or "未命名内容",
                "source": row.get("source") or "",
                "category": row.get("category") or "未分类",
                "published_at": row.get("published_at") or "",
                "url": row.get("url") or "",
                "summary": summary,
                "summary_mode": "ai" if row.get("ai_summary") else "local",
                "image_summary_count": len(image_summaries),
                "image_summaries": image_summaries[:5],
            }
        )
    return {
        "generated_at": now.isoformat(),
        "article_count": len(articles),
        "sources": dict(Counter(article["source"] for article in articles)),
        "categories": dict(Counter(article["category"] for article in articles)),
        "articles": articles,
    }


def _ai_briefing(payload: dict) -> str:
    client = OpenAICompatibleClient()
    materials = []
    for index, article in enumerate(payload["articles"], start=1):
        image_text = "\n".join(
            f"图片{image['image_index']}：{image['summary']}"
            for image in article["image_summaries"]
            if image.get("summary")
        )
        materials.append(
            f"""[{index}] {article['source']} / {article['category']} / {article['title']}
发布时间：{article['published_at']}
摘要：
{article['summary'][:1800]}
视觉摘要：
{image_text[:1200] or '暂无可用视觉摘要'}
链接：{article['url']}"""
        )

    prompt = f"""请基于以下已入库财经研报材料，生成一份“先结论、后分领域”的投研简报。
只使用材料中的信息，不要编造未出现的事实。引用文章时使用 [序号]。

重要要求：
- 不要平均介绍每篇文章，也不要按文章顺序堆摘要。
- 先判断本期最重要的 1 条主线，再提炼 2-3 条次级主线。
- 如果多篇文章都在讲科技资产、AI 或中游制造，请合并成一个主题，不要重复。
- 每条观点都要尽量说明“为什么重要”和“影响什么资产/行业”。
- 对材料证据不足的地方要写成“待验证”，不要强行下结论。
- 总字数控制在 750-1100 个中文字符，宁可少而清楚。
- 不要罗列过多行业名称；同一条观点最多举 3 个代表行业或资产。
- 分领域观点只保留有信息增量的领域，每个领域 1-2 条，每条不超过 80 个中文字符。
- 本期引用最多列 5 篇最关键材料，不要逐篇凑满。

输出格式必须严格使用以下标题：
一句话结论：
用 1 句话说明本期最核心判断，不超过 60 个中文字符。

最重要的 3 件事：
- 主线1：观点 + 原因 + 影响方向 + 引用，不超过 100 个中文字符
- 主线2：观点 + 原因 + 影响方向 + 引用，不超过 100 个中文字符
- 主线3：观点 + 原因 + 影响方向 + 引用，不超过 100 个中文字符

分领域观点：
宏观与政策：
- ...
权益与行业：
- ...
固收与利率：
- ...
汇率、商品与海外：
- ...

需要跟踪：
- 3 条后续观察清单，按重要性排序。

本期引用：
- [序号] 文章标题：一句话说明这篇材料贡献了什么证据。

材料：
{chr(10).join(materials)}
"""
    return client.chat(
        [
            {"role": "system", "content": "你是严谨的中文财经投研简报助手。"},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
        max_tokens=2400,
    )


def _local_briefing(payload: dict) -> str:
    articles = payload["articles"]
    lines = ["一、今日总览"]
    for article in articles[:5]:
        summary = article["summary"].splitlines()
        first = next((line.strip(" -") for line in summary if line.strip(" -")), article["title"])
        lines.append(f"- [{articles.index(article) + 1}] {article['title']}：{first}")
    lines.extend(["", "二、资产与行业含义", "- 待 AI 简报生成后补充。", "", "三、需要跟踪的问题", "- 复核 AI 深度总结和视觉摘要质量。"])
    return "\n".join(lines)


def _render_html(payload: dict) -> str:
    article_cards = "\n".join(_render_article(index, article) for index, article in enumerate(payload["articles"], start=1))
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>财经助手简报</title>
  <style>
    body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif; color: #172033; background: #eef2f5; line-height: 1.68; }}
    main {{ width: min(1080px, calc(100% - 32px)); margin: 28px auto; }}
    header, section {{ background: #fff; border: 1px solid #d9e1ea; border-radius: 8px; padding: 22px 26px; margin-bottom: 14px; }}
    h1, h2, h3 {{ color: #123c3a; line-height: 1.32; letter-spacing: 0; }}
    h1 {{ margin: 0 0 8px; font-size: 28px; }}
    h2 {{ margin: 0 0 12px; font-size: 21px; }}
    h3 {{ margin: 0 0 8px; font-size: 17px; }}
    a {{ color: #126c68; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .muted {{ color: #5f6b7a; }}
    .briefing {{ display: grid; gap: 12px; }}
    .briefing-lede {{ border: 1px solid #c7dedb; background: #eef8f6; border-radius: 8px; padding: 14px 16px; color: #123c3a; font-weight: 700; }}
    .briefing-section {{ border: 1px solid #d9e1ea; border-radius: 8px; padding: 14px 16px; background: #fff; }}
    .briefing-section h3 {{ margin: 0 0 8px; font-size: 16px; }}
    .briefing-section h4 {{ margin: 12px 0 6px; font-size: 14px; color: #126c68; }}
    .briefing-section ul {{ margin: 0; padding-left: 18px; }}
    .briefing-section li {{ margin: 6px 0; }}
    .briefing-section p {{ margin: 6px 0; }}
    .grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; align-items: start; }}
    .card {{ border: 1px solid #d9e1ea; border-radius: 8px; padding: 14px 16px; background: #fff; }}
    .card h3 {{ font-size: 16px; margin-bottom: 10px; }}
    .card-title {{ display: flex; gap: 8px; align-items: flex-start; }}
    .ref {{ flex: 0 0 auto; border: 1px solid #c9d7e3; border-radius: 6px; color: #126c68; font-size: 12px; line-height: 1; padding: 5px 6px; margin-top: 1px; }}
    .chips {{ display: flex; flex-wrap: wrap; gap: 6px; margin: 8px 0 10px; }}
    .chip {{ border: 1px solid #d9e1ea; border-radius: 999px; padding: 2px 8px; color: #5f6b7a; font-size: 12px; line-height: 1.5; background: #fbfcfd; }}
    .takeaways {{ margin: 8px 0 0 0; padding-left: 18px; }}
    .takeaways li {{ margin: 4px 0; }}
    details {{ margin-top: 10px; color: #334155; }}
    summary {{ cursor: pointer; color: #126c68; font-size: 13px; }}
    .full-summary {{ margin: 10px 0 0; font-size: 14px; line-height: 1.72; color: #334155; }}
    .summary-section {{ border-top: 1px solid #edf2f7; padding-top: 8px; margin-top: 8px; }}
    .summary-section:first-child {{ border-top: 0; padding-top: 0; margin-top: 0; }}
    .summary-section h4 {{ margin: 0 0 6px; color: #123c3a; font-size: 14px; }}
    .summary-section ul {{ margin: 0; padding-left: 18px; }}
    .summary-section li {{ margin: 4px 0; }}
    .summary-paragraph {{ margin: 6px 0; }}
    @media (max-width: 800px) {{ main {{ width: calc(100% - 16px); margin: 8px auto; }} header, section {{ padding: 18px; }} .grid {{ grid-template-columns: 1fr; }} .card {{ padding: 12px 14px; }} }}
  </style>
</head>
<body>
  <main>
    <header>
      <h1>财经助手简报</h1>
      <p class="muted">生成时间：{html.escape(payload['generated_at'])}。模式：{html.escape(payload.get('briefing_mode', 'unknown'))}。覆盖 {payload['article_count']} 篇文章。</p>
    </header>
    <section>
      <h2>投研简报</h2>
      <div class="briefing">{_render_briefing_html(payload['briefing'])}</div>
    </section>
    <section>
      <h2>引用材料</h2>
      <div class="grid">{article_cards}</div>
    </section>
  </main>
</body>
</html>"""


def _render_article(index: int, article: dict) -> str:
    chips = [
        article["source"],
        article["category"],
        article["summary_mode"],
        f"视觉摘要 {article['image_summary_count']}",
    ]
    chip_html = "".join(f"<span class=\"chip\">{html.escape(str(chip))}</span>" for chip in chips if chip)
    takeaways = _summary_takeaways(article["summary"], limit=2)
    takeaways_html = "".join(f"<li>{html.escape(item)}</li>" for item in takeaways) or "<li>暂无摘要。</li>"
    full_summary = _render_summary_html(article["summary"])
    return f"""<div class="card">
  <h3 class="card-title"><span class="ref">[{index}]</span><a href="{html.escape(article['url'])}" target="_blank" rel="noreferrer">{html.escape(article['title'])}</a></h3>
  <div class="chips">{chip_html}</div>
  <ul class="takeaways">{takeaways_html}</ul>
  <details>
    <summary>展开完整摘要</summary>
    <div class="full-summary">{full_summary}</div>
  </details>
</div>"""


def _render_briefing_html(briefing: str) -> str:
    lines = [line.strip() for line in (briefing or "").splitlines() if line.strip()]
    if not lines:
        return '<div class="briefing-section"><p>暂无简报。</p></div>'

    sections: list[tuple[str, list[str]]] = []
    current_title = ""
    current_lines: list[str] = []
    lede = ""

    def flush() -> None:
        nonlocal current_title, current_lines
        if current_title or current_lines:
            sections.append((current_title or "简报", current_lines))
        current_title = ""
        current_lines = []

    for line in lines:
        title = _briefing_title(line)
        if title:
            flush()
            current_title = title
            remainder = _title_remainder(line, title)
            if title == "一句话结论" and remainder:
                lede = remainder
            elif remainder:
                current_lines.append(remainder)
            continue
        if current_title == "一句话结论" and not lede:
            lede = line.strip(" -")
            continue
        current_lines.append(line)
    flush()

    parts = []
    if lede:
        parts.append(f'<div class="briefing-lede">{html.escape(lede)}</div>')
    for title, body_lines in sections:
        if title == "一句话结论":
            continue
        parts.append(_render_briefing_section(title, body_lines))
    return "".join(parts) or '<div class="briefing-section"><p>暂无简报。</p></div>'


def _render_briefing_section(title: str, lines: list[str]) -> str:
    blocks = []
    items: list[str] = []

    def flush_items() -> None:
        nonlocal items
        if items:
            item_html = "".join(f"<li>{html.escape(item)}</li>" for item in items if item)
            blocks.append(f"<ul>{item_html}</ul>")
        items = []

    for line in lines:
        if line.startswith("- "):
            items.append(line[2:].strip())
        elif re.match(r"^[\u4e00-\u9fa5、/]+：$", line):
            flush_items()
            blocks.append(f"<h4>{html.escape(line.rstrip('：'))}</h4>")
        else:
            flush_items()
            blocks.append(f"<p>{html.escape(line)}</p>")
    flush_items()
    return f'<div class="briefing-section"><h3>{html.escape(title)}</h3>{"".join(blocks)}</div>'


def _briefing_title(line: str) -> str:
    normalized = line.strip().strip("#").strip()
    for title in ("一句话结论", "最重要的 3 件事", "最重要的3件事", "分领域观点", "需要跟踪", "本期引用"):
        if normalized.startswith(title):
            return "最重要的 3 件事" if title == "最重要的3件事" else title
    return ""


def _title_remainder(line: str, title: str) -> str:
    value = line.strip().strip("#").strip()
    value = value[len(title):].strip()
    return value.strip("：: ").strip()


def _summary_takeaways(summary: str, *, limit: int = 2) -> list[str]:
    candidates = [line.strip(" -\t") for line in _normalized_summary_lines(summary) if line.startswith("- ")]
    if not candidates:
        cleaned = _clean_summary(summary)
        candidates = [part.strip() for part in cleaned.replace("；", "。").split("。") if part.strip()]
    return [_truncate(item, 92) for item in candidates[:limit]]


def _clean_summary(summary: str) -> str:
    return "\n".join(line.strip() for line in (summary or "").splitlines() if line.strip())


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def _render_summary_html(summary: str) -> str:
    lines = _normalized_summary_lines(summary)
    if not lines:
        return '<p class="summary-paragraph">暂无摘要。</p>'

    sections: list[tuple[str, list[str]]] = []
    paragraphs: list[str] = []
    current_title = ""
    current_items: list[str] = []

    def flush_section() -> None:
        nonlocal current_title, current_items
        if current_title or current_items:
            sections.append((current_title or "摘要", current_items))
        current_title = ""
        current_items = []

    for line in lines:
        section = _section_title(line)
        if section:
            flush_section()
            current_title = section
            continue
        if line.startswith("- "):
            current_items.append(line[2:].strip())
            continue
        if current_title:
            current_items.append(line)
        else:
            paragraphs.append(line)

    flush_section()

    parts = [f'<p class="summary-paragraph">{html.escape(paragraph)}</p>' for paragraph in paragraphs]
    for title, items in sections:
        item_html = "".join(f"<li>{html.escape(item)}</li>" for item in items if item)
        if item_html:
            parts.append(f'<div class="summary-section"><h4>{html.escape(title)}</h4><ul>{item_html}</ul></div>')
        else:
            parts.append(f'<div class="summary-section"><h4>{html.escape(title)}</h4></div>')
    return "".join(parts) or '<p class="summary-paragraph">暂无摘要。</p>'


def _normalized_summary_lines(summary: str) -> list[str]:
    text = _clean_summary(summary)
    if not text:
        return []
    for section in ("核心观点", "关键证据/数据", "市场含义", "风险提示", "完整性说明"):
        text = re.sub(rf"\s*{re.escape(section)}[：:]\s*", f"\n{section}：\n", text)
    text = re.sub(r"\s+-\s+", "\n- ", text)
    return [line.strip() for line in text.splitlines() if line.strip()]


def _section_title(line: str) -> str:
    stripped = line.strip()
    for section in ("核心观点", "关键证据/数据", "市场含义", "风险提示", "完整性说明"):
        if stripped in {section, f"{section}：", f"{section}:"}:
            return section
    return ""
