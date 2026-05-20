from __future__ import annotations

import json
import hashlib
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

from ..config import RAW_DIR
from ..extractors.html import extract_html
from ..models import ResearchItem


DEFAULT_BASE_URL = "http://localhost:4000"


def collect_wewe_rss(config: dict) -> Iterable[ResearchItem]:
    sources = config.get("sources", {})
    settings = sources.get("wewe_rss", {}) or {}
    if not settings.get("enabled", True):
        return

    base_url = str(settings.get("base_url") or DEFAULT_BASE_URL).rstrip("/")
    limit = int(settings.get("limit_per_feed") or 20)
    mode = str(settings.get("mode") or "fulltext")
    accounts = _account_alias_map(sources.get("wechat_accounts", []))

    try:
        feeds = _get_json(f"{base_url}/feeds")
    except Exception as error:
        yield ResearchItem(
            title="WeWe RSS 服务不可用",
            source="WeWe RSS",
            source_type="wechat",
            url=base_url,
            status="need_manual",
            completeness="WeWe RSS 读取失败",
            error=str(error),
        )
        return

    for feed in feeds:
        feed_name = feed.get("name") or feed.get("mp_name") or feed.get("title") or ""
        account = accounts.get(feed_name)
        if not account:
            continue
        name = account.get("name") or feed_name
        mp_id = feed.get("id")
        if not mp_id:
            continue
        yield from _collect_feed_items(base_url, mp_id, name, account, limit, mode)


def _collect_feed_items(
    base_url: str,
    mp_id: str,
    name: str,
    account: dict[str, Any],
    limit: int,
    mode: str,
) -> Iterable[ResearchItem]:
    query = urllib.parse.urlencode({"limit": limit, "mode": mode})
    url = f"{base_url}/feeds/{mp_id}.json?{query}"
    try:
        feed = _get_json(url, timeout=120)
    except Exception as error:
        yield ResearchItem(
            title=f"{name}：WeWe RSS feed 读取失败",
            source=name,
            source_type="wechat",
            url=url,
            category=account.get("category", ""),
            author_or_team=account.get("analysts", ""),
            status="need_manual",
            completeness="WeWe RSS feed 读取失败",
            error=str(error),
        )
        return

    for item in feed.get("items", []):
        yield _item_from_wewe_json(item, mp_id, name, account)


def _item_from_wewe_json(item: dict[str, Any], mp_id: str, source: str, account: dict[str, Any]) -> ResearchItem:
    article_id = str(item.get("id") or "")
    url = item.get("url") or item.get("external_url") or f"wewe://{mp_id}/{article_id}"
    html = item.get("content_html") or item.get("content") or ""
    title = item.get("title") or url
    _parsed_title, text, _links = extract_html(html, url) if html else ("", "", [])
    raw_path = str(_write_wechat_raw(url, html, "html")) if html else ""

    return ResearchItem(
        title=title,
        source=source,
        source_type="wechat",
        author_or_team=account.get("analysts", ""),
        category=account.get("category", ""),
        published_at=_normalize_date(item.get("date_published") or item.get("date_modified") or ""),
        url=url,
        raw_path=raw_path,
        text=text[:120000],
        status="summary_pending" if text else "need_manual",
        completeness="WeWe RSS 全文" if text else "WeWe RSS 未返回正文",
    )


def _get_json(url: str, timeout: int = 30) -> Any:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {error.code}: {detail[:300]}") from error


def _normalize_date(value: str) -> str:
    if not value:
        return ""
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).replace(microsecond=0).isoformat()
    except ValueError:
        return value


def _account_alias_map(accounts: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    mapping: dict[str, dict[str, Any]] = {}
    for account in accounts:
        names = [account.get("name", "")]
        aliases = account.get("aliases", "")
        if isinstance(aliases, list):
            names.extend(str(alias) for alias in aliases)
        else:
            names.extend(part.strip() for part in str(aliases).split(","))
        for name in names:
            if name:
                mapping[name] = account
    return mapping


def _write_wechat_raw(url: str, content: str, suffix: str):
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    path = RAW_DIR / f"wechat-{digest}.{suffix}"
    path.write_text(content, encoding="utf-8")
    return path
