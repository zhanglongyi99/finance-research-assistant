from __future__ import annotations

import html
import json
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

    json_path = BRIEFING_DIR / "latest.json"
    html_path = BRIEFING_DIR / "latest.html"
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

    prompt = f"""请基于以下已入库财经研报材料，生成一份投研晨会式简报。
只使用材料中的信息，不要编造未出现的事实。引用文章时使用 [序号]。

输出格式：
一、今日总览
- 3-5 条，概括最重要的宏观、政策、资产或行业线索

二、资产与行业含义
- 权益：
- 债券/利率：
- 汇率/商品/其他：

三、需要跟踪的问题
- 3-5 条，写成后续观察清单

四、引用文章
- 按 [序号] 标出最相关的文章标题

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
    .briefing {{ white-space: pre-wrap; }}
    .grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }}
    .card {{ border: 1px solid #d9e1ea; border-radius: 8px; padding: 14px; }}
    .chips {{ display: flex; flex-wrap: wrap; gap: 6px; }}
    .chip {{ border: 1px solid #d9e1ea; border-radius: 999px; padding: 2px 8px; color: #5f6b7a; font-size: 12px; }}
    @media (max-width: 800px) {{ main {{ width: calc(100% - 24px); }} .grid {{ grid-template-columns: 1fr; }} }}
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
      <div class="briefing">{html.escape(payload['briefing'])}</div>
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
        f"[{index}]",
        article["source"],
        article["category"],
        article["summary_mode"],
        f"视觉摘要 {article['image_summary_count']}",
    ]
    chip_html = "".join(f"<span class=\"chip\">{html.escape(str(chip))}</span>" for chip in chips if chip)
    brief = article["summary"][:260] or "暂无摘要。"
    return f"""<div class="card">
  <h3><a href="{html.escape(article['url'])}" target="_blank" rel="noreferrer">{html.escape(article['title'])}</a></h3>
  <div class="chips">{chip_html}</div>
  <p>{html.escape(brief)}</p>
</div>"""
