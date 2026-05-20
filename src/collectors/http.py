from __future__ import annotations

import hashlib
import mimetypes
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from ..config import PDF_DIR, RAW_DIR
from ..extractors.html import extract_html
from ..extractors.pdf import extract_pdf_text
from ..models import ResearchItem


USER_AGENT = "Mozilla/5.0 (compatible; ResearchAssistant/0.1; +local)"


def fetch_url(url: str, timeout: int = 5) -> tuple[bytes, str]:
    if shutil.which("curl.exe") or shutil.which("curl"):
        return _fetch_with_curl(url, timeout)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        content_type = response.headers.get("Content-Type", "")
        return response.read(), content_type


def _fetch_with_curl(url: str, timeout: int) -> tuple[bytes, str]:
    curl = shutil.which("curl.exe") or shutil.which("curl")
    if not curl:
        raise RuntimeError("curl is not available")

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
            url,
        ]
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout + 3)
        if result.returncode != 0:
            raise urllib.error.URLError(result.stderr.strip() or f"curl exited with {result.returncode}")
        content_type = _content_type_from_headers(header_path.read_text(encoding="iso-8859-1", errors="replace"))
        return body_path.read_bytes(), content_type


def _content_type_from_headers(headers: str) -> str:
    content_type = ""
    for line in headers.splitlines():
        if line.lower().startswith("content-type:"):
            content_type = line.split(":", 1)[1].strip()
    return content_type


def collect_public_url(
    *,
    url: str,
    source: str,
    title: str = "",
    source_type: str = "web",
    category: str = "",
    author_or_team: str = "",
    max_pdf_links: int = 1,
) -> list[ResearchItem]:
    try:
        content, content_type = fetch_url(url)
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        return [
            ResearchItem(
                title=title or url,
                source=source,
                source_type=source_type,
                url=url,
                category=category,
                author_or_team=author_or_team,
                status="need_manual",
                completeness="抓取失败",
                error=str(error),
            )
        ]

    if _looks_like_pdf(url, content_type):
        return [_item_from_pdf(content, url, source, title, source_type, category, author_or_team)]

    html = content.decode(_guess_encoding(content_type), errors="replace")
    parsed_title, text, pdf_links = extract_html(html, url)
    raw_path = _write_raw(url, html)
    item = ResearchItem(
        title=title or parsed_title or url,
        source=source,
        source_type=source_type,
        url=url,
        category=category,
        author_or_team=author_or_team,
        raw_path=str(raw_path),
        text=text[:120000],
        status="summary_pending" if text else "need_manual",
        completeness="公开网页" if text else "仅标题或空正文",
    )

    items = [item]
    for pdf_url in pdf_links[:max_pdf_links]:
        items.append(
            collect_public_url(
                url=pdf_url,
                source=source,
                title="",
                source_type="pdf",
                category=category,
                author_or_team=author_or_team,
                max_pdf_links=0,
            )[0]
        )
    return items


def _item_from_pdf(
    content: bytes,
    url: str,
    source: str,
    title: str,
    source_type: str,
    category: str,
    author_or_team: str,
) -> ResearchItem:
    path = _write_pdf(url, content)
    text = extract_pdf_text(path)
    return ResearchItem(
        title=title or Path(urlparse(url).path).name or url,
        source=source,
        source_type=source_type if source_type != "web" else "pdf",
        url=url,
        category=category,
        author_or_team=author_or_team,
        pdf_path=str(path),
        text=text[:120000],
        status="summary_pending" if text else "need_manual",
        completeness="公开PDF" if text else "PDF已下载，未提取文本",
    )


def _write_raw(url: str, html: str) -> Path:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    path = RAW_DIR / f"{digest}.html"
    path.write_text(html, encoding="utf-8")
    return path


def _write_pdf(url: str, content: bytes) -> Path:
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    suffix = Path(urlparse(url).path).suffix or mimetypes.guess_extension("application/pdf") or ".pdf"
    path = PDF_DIR / f"{digest}{suffix}"
    path.write_bytes(content)
    return path


def _looks_like_pdf(url: str, content_type: str) -> bool:
    return "application/pdf" in content_type.lower() or url.lower().split("?", 1)[0].endswith(".pdf")


def _guess_encoding(content_type: str) -> str:
    for part in content_type.split(";"):
        part = part.strip()
        if part.lower().startswith("charset="):
            return part.split("=", 1)[1] or "utf-8"
    return "utf-8"
