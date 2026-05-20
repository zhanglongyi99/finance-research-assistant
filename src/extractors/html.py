from __future__ import annotations

from html.parser import HTMLParser
from urllib.parse import urljoin


class HtmlTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self.text_parts: list[str] = []
        self.pdf_links: list[str] = []
        self._skip_depth = 0
        self._in_title = False
        self._current_href = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript"}:
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True
        if tag == "a":
            attrs_map = {key.lower(): value or "" for key, value in attrs}
            self._current_href = attrs_map.get("href", "")
            if self._current_href.lower().split("?", 1)[0].endswith(".pdf"):
                self.pdf_links.append(self._current_href)
        if tag in {"p", "br", "div", "li", "tr", "h1", "h2", "h3"}:
            self.text_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self._skip_depth:
            self._skip_depth -= 1
        if tag == "title":
            self._in_title = False
        if tag == "a":
            self._current_href = ""

    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if not text or self._skip_depth:
            return
        if self._in_title:
            self.title += text
        self.text_parts.append(text)

    def parsed_text(self) -> str:
        lines = []
        for chunk in "".join(part if part == "\n" else part + " " for part in self.text_parts).splitlines():
            line = " ".join(chunk.split())
            if line:
                lines.append(line)
        return "\n".join(lines)


def extract_html(html: str, base_url: str = "") -> tuple[str, str, list[str]]:
    parser = HtmlTextExtractor()
    parser.feed(html)
    pdf_links = [urljoin(base_url, link) for link in parser.pdf_links]
    return parser.title.strip(), parser.parsed_text(), sorted(set(pdf_links))

