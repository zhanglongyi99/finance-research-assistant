from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.error
from collections.abc import Iterable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urljoin, urlparse

from .http import USER_AGENT, collect_public_url
from .wewe_rss import collect_wewe_rss
from ..config import RAW_DIR
from ..extractors.html import extract_html
from ..models import ResearchItem, stable_id


TEXT_KEYS = ("content", "text", "plain_text", "markdown", "md", "article_content")
HTML_KEYS = ("html", "content_html", "article_html")
TITLE_KEYS = ("title", "name")
AUTHOR_KEYS = ("author", "nickname", "account", "source", "biz_name")
DATE_KEYS = ("published_at", "publish_time", "publishTime", "pub_time", "date")
URL_KEYS = ("url", "link", "article_url", "content_url", "source_url")


def collect_wechat_sources(config: dict) -> Iterable[ResearchItem]:
    sources = config.get("sources", {})
    api_config = sources.get("wechat_api", {}) or {}
    discovery_config = sources.get("wechat_discovery", {}) or {}
    links = sources.get("wechat_article_links", []) or []

    yield from collect_wewe_rss(config)

    for link in links:
        url = link.get("url", "")
        if not url:
            continue
        yield _collect_article_link(link, api_config)

    accounts = sources.get("wechat_accounts", []) or []
    if api_config.get("enabled") and api_config.get("list_endpoint_template"):
        for account in accounts:
            yield from _collect_account_articles(account, api_config)
        return

    if discovery_config.get("enabled", True):
        discovered_any = False
        for account in accounts:
            for link in _discover_account_links(account, discovery_config):
                discovered_any = True
                yield _collect_article_link(link, api_config)
        if discovered_any:
            return

    if not links and api_config.get("emit_placeholders", False):
        for account in accounts:
            yield _account_placeholder(account, api_config)


def _collect_article_link(link: dict[str, Any], api_config: dict[str, Any]) -> ResearchItem:
    if str(link.get("url", "")).startswith("sogou-wechat://"):
        return ResearchItem(
            title=link.get("title") or link.get("url", ""),
            source=link.get("source") or link.get("account") or "微信公众号",
            source_type="wechat",
            url=link.get("url", ""),
            category=link.get("category", ""),
            author_or_team=link.get("author_or_team", ""),
            status="discovered",
            completeness="搜狗微信发现候选，待 wxmp/浏览器/第三方 API 解析正文",
            error=f"原始候选链接：{link.get('candidate_url', '')}",
        )

    if api_config.get("local_parser_enabled", True):
        parsed = _parse_article_with_local_library(
            url=link.get("url", ""),
            source=link.get("source") or link.get("account") or "微信公众号",
            title=link.get("title", ""),
            category=link.get("category", ""),
            author_or_team=link.get("author_or_team", ""),
            api_config=api_config,
        )
        if parsed.status != "need_manual":
            return parsed

    if api_config.get("enabled"):
        parsed = _parse_article_with_api(
            url=link.get("url", ""),
            source=link.get("source") or link.get("account") or "微信公众号",
            title=link.get("title", ""),
            category=link.get("category", ""),
            author_or_team=link.get("author_or_team", ""),
            api_config=api_config,
        )
        if parsed.status != "need_manual":
            return parsed

    direct = collect_public_url(
        url=link.get("url", ""),
        source=link.get("source") or link.get("account") or "微信公众号",
        title=link.get("title", ""),
        source_type="wechat",
        category=link.get("category", ""),
        author_or_team=link.get("author_or_team", ""),
        max_pdf_links=0,
    )[0]
    if direct.status == "need_manual":
        if "weixin.sogou.com/link" in direct.url:
            direct.status = "discovered"
            direct.completeness = "搜狗微信发现候选，待 wxmp/浏览器/第三方 API 解析正文"
            direct.error = direct.error or "搜狗返回中转链接；建议配置 wxmp cookie 或公众号列表 API 获取真实文章 URL。"
        else:
            direct.completeness = "公众号解析 API 未启用，直接抓取失败"
            direct.error = direct.error or "建议启用 config/sources.yaml 中的 wechat_api。"
    else:
        direct.completeness = "公众号公开链接直接抓取"
    return direct


def _parse_article_with_local_library(
    *,
    url: str,
    source: str,
    title: str,
    category: str,
    author_or_team: str,
    api_config: dict[str, Any],
) -> ResearchItem:
    try:
        from wechat_article_parser import parse  # type: ignore
    except ModuleNotFoundError:
        return ResearchItem(
            title=title or url,
            source=source,
            source_type="wechat",
            url=url,
            category=category,
            author_or_team=author_or_team,
            status="need_manual",
            completeness="本地公众号解析库未安装",
            error="运行 python -m pip install wechat-article-parser 后可启用本地解析。",
        )

    try:
        result = parse(url, timeout=int(api_config.get("timeout_seconds") or 12), user_agent=USER_AGENT)
    except Exception as error:
        return ResearchItem(
            title=title or url,
            source=source,
            source_type="wechat",
            url=url,
            category=category,
            author_or_team=author_or_team,
            status="need_manual",
            completeness="本地公众号解析失败",
            error=str(error),
        )

    text = getattr(result, "article_markdown", "") or ""
    raw_html = getattr(result, "raw_html", "") or ""
    raw_path = ""
    if raw_html:
        raw_path = str(_write_wechat_raw(url, raw_html, "html"))
    elif text:
        raw_path = str(_write_wechat_raw(url, text, "md"))

    publish_time = getattr(result, "article_publish_time", "") or ""
    if publish_time:
        publish_time = str(publish_time)

    return ResearchItem(
        title=title or getattr(result, "article_title", "") or url,
        source=source or getattr(result, "mp_name", "") or "微信公众号",
        source_type="wechat",
        author_or_team=author_or_team or getattr(result, "mp_name", "") or "",
        category=category,
        published_at=publish_time,
        url=url,
        raw_path=raw_path,
        text=text[:120000],
        status="summary_pending" if text else "need_manual",
        completeness="本地开源库解析全文" if text else "本地开源库未返回正文",
    )


def _collect_account_articles(account: dict[str, Any], api_config: dict[str, Any]) -> Iterable[ResearchItem]:
    try:
        url = _format_template(api_config.get("list_endpoint_template", ""), account)
        data = _request_json("GET", url, api_config)
        records = _find_article_records(data)
    except Exception as error:
        yield ResearchItem(
            title=f"{account.get('name', '公众号')}：近期文章列表获取失败",
            source=account.get("name", "wechat"),
            source_type="wechat",
            url=f"wechat://{account.get('name', '')}/list",
            category=account.get("category", ""),
            status="need_manual",
            completeness="公众号列表 API 失败",
            error=str(error),
        )
        return

    max_articles = int(api_config.get("max_articles_per_account") or 5)
    if not records:
        yield ResearchItem(
            title=f"{account.get('name', '公众号')}：近期文章列表为空",
            source=account.get("name", "wechat"),
            source_type="wechat",
            url=f"wechat://{account.get('name', '')}/empty-list",
            category=account.get("category", ""),
            status="need_manual",
            completeness="公众号列表 API 无文章",
            error="接口返回中未识别到文章 URL。",
        )
        return

    for record in records[:max_articles]:
        article_url = _first_value(record, URL_KEYS)
        title = _first_value(record, TITLE_KEYS)
        if not article_url:
            yield ResearchItem(
                title=title or f"{account.get('name', '公众号')}：文章缺少 URL",
                source=account.get("name", "wechat"),
                source_type="wechat",
                url=f"wechat://{account.get('name', '')}/{title or 'missing-url'}",
                category=account.get("category", ""),
                status="need_manual",
                completeness="列表中仅有标题",
                error="近期文章列表记录没有可解析 URL。",
            )
            continue
        yield _parse_article_with_api(
            url=article_url,
            source=account.get("name", "wechat"),
            title=title,
            category=account.get("category", ""),
            author_or_team=account.get("author_or_team", ""),
            api_config=api_config,
        )


def _discover_account_links(account: dict[str, Any], discovery_config: dict[str, Any]) -> Iterable[dict[str, Any]]:
    providers = _csv(discovery_config.get("providers", "wxmp,sogou"))
    required_keywords = _csv(discovery_config.get("required_keywords", "研报,周报,快评"))
    max_results = int(discovery_config.get("max_results_per_account") or 8)

    seen: set[str] = set()
    if "wxmp" in providers:
        for link in _discover_with_wxmp(account, discovery_config, required_keywords):
            url = link.get("url", "")
            if url and url not in seen:
                seen.add(url)
                yield link
                if len(seen) >= max_results:
                    return

    if "sogou" in providers:
        for link in _discover_with_sogou(account, discovery_config, required_keywords):
            url = link.get("url", "")
            if url and url not in seen:
                seen.add(url)
                yield link
                if len(seen) >= max_results:
                    return


def _discover_with_wxmp(
    account: dict[str, Any],
    discovery_config: dict[str, Any],
    required_keywords: list[str],
) -> Iterable[dict[str, Any]]:
    cookies_file = Path(discovery_config.get("wxmp_cookies_file", "config/wxmp_cookies.json"))
    if not cookies_file.is_absolute():
        cookies_file = Path.cwd() / cookies_file
    if not cookies_file.exists():
        return

    try:
        from wxmp.spider import TimeRangeSpider  # type: ignore
        from wxmp.tools.time_manager import TimeRange  # type: ignore
    except ModuleNotFoundError:
        return

    try:
        lookback_days = int(discovery_config.get("lookback_days") or 14)
        end = datetime.now()
        begin = end - timedelta(days=lookback_days)
        spider = TimeRangeSpider.from_cookies_file(str(cookies_file))
        bizs = spider.load_or_search_bizs([account.get("name", "")])
        frame = spider.search_articles_content(bizs, TimeRange(begin=begin, end=end))
        records = frame.to_dict("records") if hasattr(frame, "to_dict") else []
    except Exception:
        return

    for record in records:
        url = _first_value(record, URL_KEYS + ("content_url", "article_url", "link"))
        title = _first_value(record, TITLE_KEYS)
        if not url or not _matches_report_keywords(title + " " + json.dumps(record, ensure_ascii=False), required_keywords):
            continue
        yield {
            "title": title,
            "url": url,
            "source": account.get("name", "微信公众号"),
            "category": account.get("category", ""),
            "author_or_team": account.get("analysts", ""),
        }


def _discover_with_sogou(
    account: dict[str, Any],
    discovery_config: dict[str, Any],
    required_keywords: list[str],
) -> Iterable[dict[str, Any]]:
    for query in _build_queries(account, discovery_config):
        search_url = "https://weixin.sogou.com/weixin?type=2&query=" + quote(query, safe="")
        try:
            body, _content_type = _request_bytes("GET", search_url, {"timeout_seconds": 8})
            html = body.decode("utf-8", errors="replace")
        except Exception:
            continue
        for title, url, snippet in _parse_sogou_results(html):
            combined = f"{title} {snippet}"
            if not url or not _matches_report_keywords(combined, required_keywords):
                continue
            stable_url = _stable_sogou_candidate_url(account.get("name", "微信公众号"), title, url)
            yield {
                "title": title,
                "url": stable_url,
                "candidate_url": url,
                "source": account.get("name", "微信公众号"),
                "category": account.get("category", ""),
                "author_or_team": account.get("analysts", ""),
            }


def _build_queries(account: dict[str, Any], discovery_config: dict[str, Any]) -> list[str]:
    templates = _csv(discovery_config.get("query_templates", "{name} 研报,{name} 周报,{analysts} {keywords}"))
    values = {
        "name": account.get("name", ""),
        "analysts": account.get("analysts", ""),
        "keywords": account.get("keywords", ""),
        "category": account.get("category", ""),
    }
    queries = []
    for template in templates:
        query = template.format(**values)
        query = " ".join(part for part in re.split(r"[,，]\s*", query) if part).strip()
        if query and query not in queries:
            queries.append(query)
    return queries


def _parse_sogou_results(html: str) -> list[tuple[str, str, str]]:
    results: list[tuple[str, str, str]] = []
    for match in re.finditer(r"<h3[^>]*>.*?<a[^>]+href=[\"'](?P<href>[^\"']+)[\"'][^>]*>(?P<title>.*?)</a>.*?</h3>(?P<tail>.*?)</li>", html, re.S | re.I):
        href = _clean_sogou_url(match.group("href"))
        title = _strip_html(match.group("title"))
        snippet = _strip_html(match.group("tail"))
        if href:
            results.append((title, href, snippet))
    if results:
        return results

    for href in re.findall(r"href=[\"']([^\"']*(?:mp\.weixin\.qq\.com|weixin\.sogou\.com/link)[^\"']*)[\"']", html, re.I):
        url = _clean_sogou_url(href)
        if url:
            results.append(("", url, ""))
    return results


def _clean_sogou_url(href: str) -> str:
    href = html_unescape(href)
    href = urljoin("https://weixin.sogou.com", href)
    parsed = urlparse(href)
    if "mp.weixin.qq.com" in parsed.netloc:
        return href
    params = parse_qs(parsed.query)
    for key in ("url", "link"):
        if params.get(key):
            candidate = unquote(params[key][0])
            if "mp.weixin.qq.com" in candidate:
                return candidate
    if "weixin.sogou.com/link" in href:
        return href
    return ""


def _stable_sogou_candidate_url(source: str, title: str, raw_url: str) -> str:
    key = title or raw_url
    return f"sogou-wechat://{stable_id(source, key)}"


def _strip_html(value: str) -> str:
    value = re.sub(r"<script.*?</script>|<style.*?</style>", "", value, flags=re.S | re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    return " ".join(html_unescape(value).split())


def html_unescape(value: str) -> str:
    import html

    return html.unescape(value)


def _matches_report_keywords(text: str, keywords: list[str]) -> bool:
    if not keywords:
        return True
    return any(keyword and keyword in text for keyword in keywords)


def _csv(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def _parse_article_with_api(
    *,
    url: str,
    source: str,
    title: str,
    category: str,
    author_or_team: str,
    api_config: dict[str, Any],
) -> ResearchItem:
    try:
        data = _request_article(url, api_config)
        article = data.get("data") if isinstance(data.get("data"), dict) else data
        parsed = _item_from_article_json(article, url, source, title, category, author_or_team)
        if parsed.text:
            return parsed
        readable = _fetch_readable_html(url, api_config)
        if readable:
            return _item_from_html(readable, url, source, title, category, author_or_team)
        return parsed
    except Exception as error:
        fallback = collect_public_url(
            url=url,
            source=source,
            title=title,
            source_type="wechat",
            category=category,
            author_or_team=author_or_team,
            max_pdf_links=0,
        )[0]
        if fallback.status == "need_manual":
            fallback.completeness = "公众号解析 API 失败"
            fallback.error = str(error)
        return fallback


def _request_article(url: str, api_config: dict[str, Any]) -> dict[str, Any]:
    endpoint = api_config.get("parse_endpoint", "")
    method = (api_config.get("method") or "POST").upper()
    if not endpoint:
        raise ValueError("wechat_api.parse_endpoint 未配置")
    if method == "GET":
        return _request_json("GET", _template_with_url(endpoint, url), api_config)
    return _request_json("POST", endpoint, api_config, {"url": url})


def _fetch_readable_html(url: str, api_config: dict[str, Any]) -> str:
    template = api_config.get("readable_endpoint_template", "")
    if not template:
        return ""
    body, _content_type = _request_bytes("GET", _template_with_url(template, url), api_config)
    return body.decode("utf-8", errors="replace")


def _item_from_article_json(
    article: dict[str, Any],
    url: str,
    source: str,
    title: str,
    category: str,
    author_or_team: str,
) -> ResearchItem:
    html_content = _first_value(article, HTML_KEYS)
    raw_path = ""
    if html_content:
        parsed_title, text, _links = extract_html(html_content, url)
        raw_path = str(_write_wechat_raw(url, html_content, "html"))
    else:
        parsed_title = ""
        text = _first_value(article, TEXT_KEYS)
        if text:
            raw_path = str(_write_wechat_raw(url, text, "txt"))

    return ResearchItem(
        title=title or _first_value(article, TITLE_KEYS) or parsed_title or url,
        source=source or _first_value(article, AUTHOR_KEYS) or "微信公众号",
        source_type="wechat",
        author_or_team=author_or_team or _first_value(article, AUTHOR_KEYS),
        category=category,
        published_at=str(_first_value(article, DATE_KEYS) or ""),
        url=url,
        raw_path=raw_path,
        text=text[:120000] if text else "",
        status="summary_pending" if text else "need_manual",
        completeness="公众号解析 API 完整正文" if text else "公众号解析 API 未返回正文",
    )


def _item_from_html(
    html: str,
    url: str,
    source: str,
    title: str,
    category: str,
    author_or_team: str,
) -> ResearchItem:
    parsed_title, text, _links = extract_html(html, url)
    return ResearchItem(
        title=title or parsed_title or url,
        source=source or "微信公众号",
        source_type="wechat",
        author_or_team=author_or_team,
        category=category,
        url=url,
        raw_path=str(_write_wechat_raw(url, html, "html")),
        text=text[:120000],
        status="summary_pending" if text else "need_manual",
        completeness="公众号解析 API 可读页面" if text else "公众号解析 API 可读页面无正文",
    )


def _account_placeholder(account: dict[str, Any], api_config: dict[str, Any]) -> ResearchItem:
    if api_config.get("enabled"):
        completeness = "公众号列表 API 未配置"
        error = "已启用 wechat_api，但未配置 list_endpoint_template；可先在 wechat_article_links 放文章链接。"
    else:
        completeness = "公众号 API 未启用"
        error = "在 config/sources.yaml 配置 wechat_api，或先把文章链接放入 wechat_article_links。"
    return ResearchItem(
        title=f"{account.get('name', '公众号')}：待接入公众号解析",
        source=account.get("name", "wechat"),
        source_type="wechat",
        url=f"wechat://{account.get('name', '')}",
        category=account.get("category", ""),
        status="need_manual",
        completeness=completeness,
        error=error,
    )


def _request_json(
    method: str,
    url: str,
    api_config: dict[str, Any],
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body, content_type = _request_bytes(method, url, api_config, payload)
    text = body.decode("utf-8", errors="replace")
    if "json" not in content_type.lower() and not text.strip().startswith(("{", "[")):
        raise ValueError(f"接口未返回 JSON：{content_type or 'unknown content type'}")
    parsed = json.loads(text)
    if isinstance(parsed, list):
        return {"items": parsed}
    if not isinstance(parsed, dict):
        raise ValueError("接口 JSON 不是对象或列表")
    return parsed


def _request_bytes(
    method: str,
    url: str,
    api_config: dict[str, Any],
    payload: dict[str, Any] | None = None,
) -> tuple[bytes, str]:
    curl = shutil.which("curl.exe") or shutil.which("curl")
    if not curl:
        raise RuntimeError("curl is not available")

    timeout = int(api_config.get("timeout_seconds") or 12)
    token_env = api_config.get("token_env", "")
    token = os.getenv(token_env, "") if token_env else ""
    auth_header = api_config.get("auth_header", "Authorization")
    auth_prefix = api_config.get("auth_prefix", "Bearer ")

    with tempfile.TemporaryDirectory() as temp_dir:
        header_path = Path(temp_dir) / "headers.txt"
        body_path = Path(temp_dir) / "body.bin"
        command = [
            curl,
            "--location",
            "--silent",
            "--show-error",
            "--max-time",
            str(timeout),
            "--user-agent",
            USER_AGENT,
            "--dump-header",
            str(header_path),
            "--output",
            str(body_path),
        ]
        if token:
            command.extend(["--header", f"{auth_header}: {auth_prefix}{token}"])
        if payload is not None:
            command.extend(["--request", method, "--header", "Content-Type: application/json", "--data", json.dumps(payload, ensure_ascii=False)])
        elif method != "GET":
            command.extend(["--request", method])
        command.append(url)

        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout + 3)
        if result.returncode != 0:
            raise urllib.error.URLError(result.stderr.strip() or f"curl exited with {result.returncode}")
        headers = header_path.read_text(encoding="iso-8859-1", errors="replace")
        return body_path.read_bytes(), _content_type_from_headers(headers)


def _find_article_records(data: Any) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if isinstance(data, dict):
        if _first_value(data, URL_KEYS) or _first_value(data, TITLE_KEYS):
            records.append(data)
        for value in data.values():
            records.extend(_find_article_records(value))
    elif isinstance(data, list):
        for value in data:
            records.extend(_find_article_records(value))
    return records


def _first_value(data: Any, keys: tuple[str, ...]) -> str:
    if isinstance(data, dict):
        for key in keys:
            value = data.get(key)
            if isinstance(value, (str, int, float)) and str(value).strip():
                return str(value).strip()
        for value in data.values():
            nested = _first_value(value, keys)
            if nested:
                return nested
    elif isinstance(data, list):
        for value in data:
            nested = _first_value(value, keys)
            if nested:
                return nested
    return ""


def _format_template(template: str, values: dict[str, Any]) -> str:
    safe_values = {key: quote(str(value), safe="") for key, value in values.items()}
    return template.format(**safe_values)


def _template_with_url(template: str, url: str) -> str:
    return template.format(url=quote(url, safe=""))


def _write_wechat_raw(url: str, content: str, suffix: str) -> Path:
    import hashlib

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    path = RAW_DIR / f"wechat-{digest}.{suffix}"
    path.write_text(content, encoding="utf-8")
    return path


def _content_type_from_headers(headers: str) -> str:
    content_type = ""
    for line in headers.splitlines():
        if line.lower().startswith("content-type:"):
            content_type = line.split(":", 1)[1].strip()
    return content_type
