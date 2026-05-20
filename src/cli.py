from __future__ import annotations

import argparse
import json
import urllib.request
from collections.abc import Iterable

from .collectors.manual import collect_manual_links
from .collectors.web import collect_web_sources
from .collectors.wechat import collect_wechat_sources
from .config import DB_PATH, OUTPUT_DIR, ensure_dirs, load_config
from .dashboard.render import render_dashboard
from .db import init_db, iter_pending_summaries, list_items, list_items_by_ids, list_urls, update_summary, upsert_item
from .models import ResearchItem
from .reports.daily import generate_daily_report, generate_report_for_created_today
from .summarizer.local import summarize_row


def main() -> None:
    parser = argparse.ArgumentParser(prog="research-assistant")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init", help="创建目录并初始化 SQLite 数据库")

    collect_parser = subparsers.add_parser("collect", help="采集内容")
    collect_parser.add_argument("--source", choices=["web", "manual", "wechat", "all"], default="all")

    summarize_parser = subparsers.add_parser("summarize", help="为待处理内容生成摘要")
    summarize_parser.add_argument("--pending", action="store_true", help="处理待摘要内容")

    subparsers.add_parser("render-dashboard", help="渲染本地静态网页看板")
    subparsers.add_parser("daily-report", help="用今日入库内容生成一份日报")
    subparsers.add_parser("status", help="输出当前采集状态统计")
    subparsers.add_parser("run-once", help="采集、摘要、渲染一次跑完")

    args = parser.parse_args()
    if args.command == "init":
        command_init()
    elif args.command == "collect":
        command_collect(args.source)
    elif args.command == "summarize":
        command_summarize()
    elif args.command == "render-dashboard":
        command_render_dashboard()
    elif args.command == "daily-report":
        command_daily_report()
    elif args.command == "status":
        command_status()
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


def command_summarize() -> None:
    init_db()
    count = 0
    for row in iter_pending_summaries():
        summary = summarize_row(row)
        update_summary(row["id"], summary)
        count += 1
    print(f"摘要完成：更新 {count} 条。")


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
    print("WeWe RSS订阅：" + _format_wewe_feeds())


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
