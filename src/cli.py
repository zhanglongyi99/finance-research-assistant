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
    init_db,
    iter_pending_ai_summaries,
    iter_pending_summaries,
    list_items,
    list_items_by_ids,
    list_urls,
    update_ai_summary,
    update_summary,
    upsert_item,
)
from .extractors.images import content_images, extract_images
from .llm import ModelConfigError, OpenAICompatibleClient, load_model_settings
from .models import ResearchItem
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
        summary = summarize_row_with_ai(row, client)
        update_ai_summary(row["id"], summary, settings.model)
        count += 1
    print(f"AI深度总结完成：模型 {settings.model}，更新 {count} 条。")


def _latest_image_row():
    for row in list_items(limit=5000):
        if row["raw_path"]:
            return row
    return None


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
