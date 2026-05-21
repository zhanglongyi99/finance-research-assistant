from __future__ import annotations

import argparse
import json
import urllib.request
from collections.abc import Iterable
from pathlib import Path

from .collectors.manual import collect_manual_links
from .collectors.web import collect_web_sources
from .collectors.wechat import collect_wechat_sources
from .config import DB_PATH, OUTPUT_DIR, ensure_dirs, load_config
from .dashboard.render import render_dashboard
from .db import (
    count_ai_summaries,
    count_article_images,
    count_image_quality,
    init_db,
    iter_pending_image_summaries,
    iter_pending_ai_summaries,
    iter_pending_summaries,
    list_ai_summaries_for_review,
    list_article_image_summaries,
    list_items,
    list_items_by_ids,
    list_items_with_raw_html,
    list_urls,
    mark_ai_summary_review,
    sample_image_summaries,
    update_ai_summary,
    update_image_quality,
    update_image_summary,
    update_summary,
    upsert_article_images,
    upsert_item,
)
from .extractors.images import content_images, extract_images
from .llm import ModelConfigError, OpenAICompatibleClient, load_model_settings
from .models import ResearchItem
from .quality import assess_ai_summary, classify_vision_summary
from .qa import answer_question
from .reports.briefing import generate_briefing
from .reports.daily import generate_daily_report, generate_report_for_created_today
from .summarizer.ai import summarize_row_with_ai
from .summarizer.local import summarize_row


def main() -> None:
    parser = argparse.ArgumentParser(prog="research-assistant")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init", help="创建目录并初始化 SQLite 数据库")

    collect_parser = subparsers.add_parser("collect", help="采集内容")
    collect_parser.add_argument("--source", choices=["web", "manual", "wechat", "all"], default="all")

    summarize_parser = subparsers.add_parser("summarize", help="为待处理内容生成摘要")
    summarize_parser.add_argument("--pending", action="store_true", help="处理待摘要内容")
    summarize_parser.add_argument("--mode", choices=["local", "ai"], default="local", help="摘要模式，默认使用本地抽取式摘要")
    summarize_parser.add_argument("--limit", type=int, default=0, help="最多处理多少条；0 表示不限制")

    subparsers.add_parser("render-dashboard", help="渲染本地静态网页看板")
    subparsers.add_parser("daily-report", help="用今日入库内容生成一份日报")
    subparsers.add_parser("status", help="输出当前采集状态统计")
    subparsers.add_parser("test-model", help="测试 OpenAI-compatible 模型 API 配置和连通性")
    vision_parser = subparsers.add_parser("test-vision", help="测试模型是否能读取公众号文章图片 URL")
    vision_parser.add_argument("--url", default="", help="直接指定图片 URL")
    vision_parser.add_argument("--image-index", type=int, default=1, help="文章正文图片序号，从 1 开始")
    vision_parser.add_argument("--all-images", action="store_true", help="从全部图片中选择，而不是只选正文图片")
    deep_parser = subparsers.add_parser("deep-summarize", help="用模型为已入库文章生成 AI 深度总结")
    deep_parser.add_argument("--limit", type=int, default=5, help="最多处理多少条；0 表示不限制")
    deep_parser.add_argument("--resummarize", action="store_true", help="重新生成已有 AI 总结")
    index_images_parser = subparsers.add_parser("index-images", help="从文章原始 HTML 提取并入库图片清单")
    index_images_parser.add_argument("--limit", type=int, default=0, help="最多处理多少篇文章；0 表示不限制")
    summarize_images_parser = subparsers.add_parser("summarize-images", help="用视觉模型为正文图片生成摘要")
    summarize_images_parser.add_argument("--limit", type=int, default=10, help="最多处理多少张图片；0 表示不限制")
    summarize_images_parser.add_argument("--resummarize", action="store_true", help="重新生成已有视觉摘要")
    audit_images_parser = subparsers.add_parser("audit-image-summaries", help="自动标记已生成视觉摘要的类型和质量")
    audit_images_parser.add_argument("--limit", type=int, default=0, help="最多复核多少张；0 表示不限制")
    audit_ai_parser = subparsers.add_parser("audit-ai-summaries", help="自动检查 AI 深度总结结构完整性")
    audit_ai_parser.add_argument("--limit", type=int, default=20, help="最多复核多少篇；0 表示不限制")
    briefing_parser = subparsers.add_parser("generate-briefing", help="基于近期入库研报生成 AI 简报")
    briefing_parser.add_argument("--limit", type=int, default=12, help="纳入最近多少篇文章")
    briefing_parser.add_argument("--local", action="store_true", help="不调用模型，仅生成本地兜底简报")
    ask_parser = subparsers.add_parser("ask", help="基于已入库研报进行文章级引用问答")
    ask_parser.add_argument("question", help="要询问的问题")
    ask_parser.add_argument("--limit", type=int, default=6, help="最多引用多少篇文章")
    ask_parser.add_argument("--local", action="store_true", help="只返回检索结果，不调用模型生成答案")
    subparsers.add_parser("run-once", help="采集、摘要、渲染一次跑完")

    args = parser.parse_args()
    if args.command == "init":
        command_init()
    elif args.command == "collect":
        command_collect(args.source)
    elif args.command == "summarize":
        command_summarize(mode=args.mode, limit=args.limit)
    elif args.command == "render-dashboard":
        command_render_dashboard()
    elif args.command == "daily-report":
        command_daily_report()
    elif args.command == "status":
        command_status()
    elif args.command == "test-model":
        command_test_model()
    elif args.command == "test-vision":
        command_test_vision(url=args.url, image_index=args.image_index, content_only=not args.all_images)
    elif args.command == "deep-summarize":
        command_deep_summarize(limit=args.limit, resummarize=args.resummarize)
    elif args.command == "index-images":
        command_index_images(limit=args.limit)
    elif args.command == "summarize-images":
        command_summarize_images(limit=args.limit, resummarize=args.resummarize)
    elif args.command == "audit-image-summaries":
        command_audit_image_summaries(limit=args.limit)
    elif args.command == "audit-ai-summaries":
        command_audit_ai_summaries(limit=args.limit)
    elif args.command == "generate-briefing":
        command_generate_briefing(limit=args.limit, use_ai=not args.local)
    elif args.command == "ask":
        command_ask(question=args.question, limit=args.limit, use_ai=not args.local)
    elif args.command == "run-once":
        command_run_once()


def command_init() -> None:
    ensure_dirs()
    init_db()
    print(f"初始化完成：{DB_PATH}")


def command_collect(source: str) -> list[str]:
    ensure_dirs()
    init_db()
    config = load_config()
    items = list(_collect_items(source, config))
    existing_urls = list_urls()
    changed = 0
    new_ids: list[str] = []
    seen_in_batch: set[str] = set()
    for item in items:
        if not item.url:
            continue
        is_new = item.url not in existing_urls and item.url not in seen_in_batch
        changed += int(upsert_item(item))
        seen_in_batch.add(item.url)
        if is_new and item.id:
            new_ids.append(item.id)
    print(f"采集完成：收到 {len(items)} 条，写入/更新 {changed} 条，新增 {len(new_ids)} 条。")
    return new_ids


def command_summarize(*, mode: str = "local", limit: int = 0) -> None:
    init_db()
    count = 0
    client = OpenAICompatibleClient() if mode == "ai" else None
    for row in iter_pending_summaries():
        if limit and count >= limit:
            break
        summary = summarize_row_with_ai(row, client) if client else summarize_row(row)
        update_summary(row["id"], summary)
        count += 1
    print(f"摘要完成：模式 {mode}，更新 {count} 条。")


def command_render_dashboard() -> None:
    path = render_dashboard()
    print(f"看板已生成：{path}")


def command_daily_report() -> None:
    path = generate_report_for_created_today()
    if path:
        print(f"日报已生成：{path}")
    else:
        print("今日暂无可生成日报的入库内容。")


def command_run_once() -> None:
    command_init()
    new_ids = command_collect("wechat")
    command_summarize()
    if new_ids:
        report_path = generate_daily_report(list_items_by_ids(new_ids), reason="automation_import")
        if report_path:
            print(f"本轮日报已生成：{report_path}")
    else:
        print("本轮没有新增文章，未生成新的日报。")
    command_render_dashboard()
    command_status()
    print(f"本轮完成：打开 {OUTPUT_DIR / 'index.html'} 查看。")


def command_status() -> None:
    rows = list_items(limit=5000)
    statuses: dict[str, int] = {}
    wechat_sources: dict[str, int] = {}
    for row in rows:
        statuses[row["status"]] = statuses.get(row["status"], 0) + 1
        if row["source_type"] == "wechat":
            wechat_sources[row["source"]] = wechat_sources.get(row["source"], 0) + 1
    print(f"数据库记录：{len(rows)} 条")
    print("状态分布：" + _format_counts(statuses))
    print("公众号覆盖：" + _format_counts(wechat_sources))
    print(f"AI深度总结：{count_ai_summaries()} 条")
    total_images, content_images_count, image_summary_count = count_article_images()
    print(f"文章图片：{total_images} 张，正文图 {content_images_count} 张，视觉摘要 {image_summary_count} 张")
    quality_rows = count_image_quality()
    if quality_rows:
        print("视觉摘要质量：" + "，".join(f"{row['vision_kind']}/{row['review_status']}={row['count']}" for row in quality_rows))
    print("WeWe RSS订阅：" + _format_wewe_feeds())


def command_test_model() -> None:
    try:
        settings = load_model_settings()
        client = OpenAICompatibleClient(settings)
        answer = client.chat(
            [
                {"role": "system", "content": "你是财经助手的模型连通性测试器。"},
                {"role": "user", "content": "请只回复：模型连通正常。"},
            ],
            temperature=0,
            max_tokens=64,
        )
    except ModelConfigError as error:
        print(f"模型配置不完整：{error}")
        return
    except Exception as error:
        print(f"模型连通失败：{error}")
        return
    print(f"模型配置：{settings.model} @ {settings.base_url} ({settings.wire_api}, reasoning={settings.reasoning_effort or 'off'})")
    print(f"模型回复：{answer}")


def command_test_vision(*, url: str = "", image_index: int = 1, content_only: bool = True) -> None:
    settings = load_model_settings()
    client = OpenAICompatibleClient(settings)
    image_url = url.strip()
    title = "手动指定图片"
    if not image_url:
        row = _latest_image_row()
        if not row:
            print("没有找到带原始 HTML 的文章。")
            return
        title = row["title"]
        raw_path = Path(row["raw_path"])
        raw_html = raw_path.read_text(encoding="utf-8", errors="replace")
        images = content_images(raw_html) if content_only else extract_images(raw_html)
        if not images:
            print(f"文章没有可用图片：{title}")
            return
        index = max(image_index, 1) - 1
        if index >= len(images):
            print(f"图片序号超出范围：共有 {len(images)} 张可选图片。")
            return
        image_url = images[index].url
        print(f"文章：{row['source']} / {title}")
        print(f"图片：{image_index}/{len(images)} {image_url}")
    prompt = """请读取这张财经研报/公众号图片。
如果它是图表、表格或PPT页，请提取标题、关键指标、数字、趋势和可能的市场含义。
如果它只是封面、头像、二维码、装饰图或无法读取，请明确说明。
输出保持简洁，用中文。"""
    answer = client.vision(image_url=image_url, prompt=prompt)
    print(f"模型配置：{settings.model} @ {settings.base_url} ({settings.wire_api})")
    print("视觉识别结果：")
    print(answer)


def command_deep_summarize(*, limit: int = 5, resummarize: bool = False) -> None:
    init_db()
    settings = load_model_settings()
    client = OpenAICompatibleClient(settings)
    count = 0
    for row in iter_pending_ai_summaries(limit=limit, resummarize=resummarize):
        print(f"AI总结中：{row['source']} / {row['title']}")
        image_summaries = _image_summaries_for_article(row["id"])
        summary = summarize_row_with_ai(row, client, image_summaries=image_summaries)
        update_ai_summary(row["id"], summary, settings.model)
        count += 1
    print(f"AI深度总结完成：模型 {settings.model}，更新 {count} 条。")


def command_index_images(*, limit: int = 0) -> None:
    init_db()
    scanned = 0
    image_count = 0
    content_count = 0
    changed = 0
    for row in list_items_with_raw_html(limit=limit):
        raw_path = Path(row["raw_path"])
        if not raw_path.exists():
            continue
        raw_html = raw_path.read_text(encoding="utf-8", errors="replace")
        images = extract_images(raw_html)
        if not images:
            continue
        scanned += 1
        image_count += len(images)
        content_count += sum(1 for image in images if image.likely_content)
        changed += upsert_article_images(row["id"], images)
    print(
        f"图片索引完成：扫描 {scanned} 篇带图片文章，发现 {image_count} 张图片，"
        f"其中正文图 {content_count} 张，写入/更新 {changed} 条。"
    )


def command_summarize_images(*, limit: int = 10, resummarize: bool = False) -> None:
    init_db()
    settings = load_model_settings()
    client = OpenAICompatibleClient(settings)
    count = 0
    failed = 0
    for row in iter_pending_image_summaries(limit=limit, resummarize=resummarize):
        print(f"视觉摘要中：{row['article_source']} / {row['article_title']} / 图 {row['image_index']}")
        prompt = _vision_summary_prompt(row)
        try:
            summary = client.vision(image_url=row["url"], prompt=prompt, max_tokens=1200)
        except Exception as error:
            failed += 1
            print(f"视觉摘要失败：图 {row['image_index']}，{error}")
            continue
        kind, quality, review_status, use_for_summary = classify_vision_summary(summary)
        update_image_summary(
            row["id"],
            summary,
            settings.model,
            vision_kind=kind,
            vision_quality=quality,
            review_status=review_status,
            use_for_summary=use_for_summary,
        )
        count += 1
    print(f"视觉摘要完成：模型 {settings.model}，更新 {count} 张图片，失败 {failed} 张。")


def command_audit_image_summaries(*, limit: int = 0) -> None:
    init_db()
    count = 0
    for row in sample_image_summaries(limit=limit or 100000):
        kind, quality, review_status, use_for_summary = classify_vision_summary(row["vision_summary"] or "")
        update_image_quality(
            row["id"],
            vision_kind=kind,
            vision_quality=quality,
            review_status=review_status,
            use_for_summary=use_for_summary,
        )
        count += 1
    print(f"视觉摘要自动复核完成：更新 {count} 张。")
    for row in count_image_quality():
        print(f"- {row['vision_kind']} / {row['review_status']}: {row['count']}")


def command_audit_ai_summaries(*, limit: int = 20) -> None:
    init_db()
    count = 0
    statuses: dict[str, int] = {}
    for row in list_ai_summaries_for_review(limit=limit):
        status, note = assess_ai_summary(row["ai_summary"] or "")
        mark_ai_summary_review(row["id"], status, note)
        statuses[status] = statuses.get(status, 0) + 1
        count += 1
    print(f"AI深度总结自动复核完成：更新 {count} 篇。")
    print("复核状态：" + _format_counts(statuses))


def command_generate_briefing(*, limit: int = 12, use_ai: bool = True) -> None:
    path = generate_briefing(limit=limit, use_ai=use_ai)
    print(f"简报已生成：{path}")


def command_ask(*, question: str, limit: int = 6, use_ai: bool = True) -> None:
    try:
        answer = answer_question(question, limit=limit, use_ai=use_ai)
    except Exception as error:
        if use_ai:
            print(f"AI问答失败，回退本地检索：{error}")
            answer = answer_question(question, limit=limit, use_ai=False)
        else:
            raise
    print(answer)


def _latest_image_row():
    for row in list_items(limit=5000):
        if row["raw_path"]:
            return row
    return None


def _vision_summary_prompt(row) -> str:
    alt = row["alt"] or "无"
    ratio = row["ratio"] or 0
    width = row["width"] or 0
    height = row["height"] or 0
    return f"""请读取这张财经研报/公众号正文图片，并生成可入库复用的视觉摘要。

文章标题：{row["article_title"]}
来源：{row["article_source"]}
图片序号：{row["image_index"]}
图片 alt：{alt}
图片尺寸线索：ratio={ratio}, width={width}, height={height}

要求：
- 如果是图表、表格或PPT页，提取标题、关键指标、数字、趋势、结论和可能的市场含义。
- 如果是目录页，提取主要章节和研究主题。
- 如果仍然像封面、头像、二维码、广告或装饰图，请明确标注“噪声图”，并简述原因。
- 不要编造看不见的数字；看不清时写“未能辨认”。
- 输出中文，控制在 4-8 行。"""


def _image_summaries_for_article(article_id: str) -> list[str]:
    summaries = []
    for row in list_article_image_summaries(article_id):
        meta = f"图片 {row['image_index']}"
        if row["alt"]:
            meta += f"（alt: {row['alt']}）"
        summaries.append(f"{meta}：\n{row['vision_summary']}")
    return summaries


def _format_counts(counts: dict[str, int]) -> str:
    if not counts:
        return "无"
    return "，".join(f"{key}={value}" for key, value in sorted(counts.items()))


def _format_wewe_feeds() -> str:
    config = load_config()
    settings = config.get("sources", {}).get("wewe_rss", {}) or {}
    if not settings.get("enabled", True):
        return "未启用"
    base_url = str(settings.get("base_url") or "http://localhost:4000").rstrip("/")
    try:
        with urllib.request.urlopen(f"{base_url}/feeds", timeout=8) as response:
            feeds = json.loads(response.read().decode("utf-8", errors="replace"))
    except Exception as error:
        return f"读取失败：{error}"
    names = [feed.get("name") or feed.get("mp_name") or feed.get("title") for feed in feeds]
    names = [name for name in names if name]
    return f"{len(names)}个：" + "，".join(names)


def _collect_items(source: str, config: dict) -> Iterable[ResearchItem]:
    if source in {"web", "all"}:
        yield from collect_web_sources(config)
    if source in {"manual", "all"}:
        yield from collect_manual_links(config)
    if source in {"wechat", "all"}:
        yield from collect_wechat_sources(config)


if __name__ == "__main__":
    main()
