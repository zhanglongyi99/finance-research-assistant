from __future__ import annotations

import html
import re
from dataclasses import dataclass
from html.parser import HTMLParser


@dataclass(frozen=True)
class ArticleImage:
    url: str
    alt: str = ""
    ratio: float = 0.0
    width: int = 0
    height: int = 0
    likely_content: bool = True


class ImageExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.images: list[ArticleImage] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "img":
            return
        attrs_map = {key.lower(): value or "" for key, value in attrs}
        url = attrs_map.get("data-src") or attrs_map.get("src") or ""
        url = html.unescape(url.strip())
        if not url or url.startswith("data:"):
            return
        alt = html.unescape(attrs_map.get("alt", "").strip())
        ratio = _float(attrs_map.get("data-ratio", "0"))
        width = _int(attrs_map.get("data-w") or attrs_map.get("width") or "0")
        height = _int(attrs_map.get("data-h") or attrs_map.get("height") or "0")
        likely_content = _is_likely_content_image(url=url, alt=alt, attrs=attrs_map)
        self.images.append(
            ArticleImage(
                url=url,
                alt=alt,
                ratio=ratio,
                width=width,
                height=height,
                likely_content=likely_content,
            )
        )


def extract_images(raw_html: str) -> list[ArticleImage]:
    parser = ImageExtractor()
    parser.feed(raw_html)
    deduped: list[ArticleImage] = []
    seen: set[str] = set()
    for image in parser.images:
        if image.url in seen:
            continue
        seen.add(image.url)
        deduped.append(image)
    return deduped


def content_images(raw_html: str) -> list[ArticleImage]:
    return [image for image in extract_images(raw_html) if image.likely_content]


def _is_likely_content_image(*, url: str, alt: str, attrs: dict[str, str]) -> bool:
    joined = " ".join(
        [
            url,
            alt,
            attrs.get("class", ""),
            attrs.get("id", ""),
            attrs.get("title", ""),
            attrs.get("data-type", ""),
            attrs.get("data-cropselx1", ""),
        ]
    ).lower()
    noise_markers = (
        "cover_image",
        "avatar",
        "qlogo",
        "wx_follow_avatar",
        "profile",
        "qr",
        "qrcode",
        "barcode",
        "reward",
        "logo",
        "icon",
        "profile_photo",
        "js_profile",
        "share",
        "赞赏",
        "二维码",
        "扫码",
        "关注",
        "头像",
        "封面",
        "名片",
        "广告",
        "海报",
    )
    if any(marker in joined for marker in noise_markers):
        return False
    if "mmbiz.qpic.cn" not in url and "qpic.cn" not in url:
        return False
    width = _int(attrs.get("data-w") or attrs.get("width") or "0")
    height = _int(attrs.get("data-h") or attrs.get("height") or "0")
    if width and height and width <= 96 and height <= 96:
        return False
    ratio = _float(attrs.get("data-ratio", "0"))
    if ratio and (ratio < 0.08 or ratio > 5):
        return False
    return True


def _float(value: str) -> float:
    try:
        return float(value)
    except ValueError:
        return 0.0


def _int(value: str) -> int:
    match = re.search(r"\d+", value or "")
    if not match:
        return 0
    return int(match.group(0))
