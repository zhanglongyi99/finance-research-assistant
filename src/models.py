from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def stable_id(*parts: str | None) -> str:
    source = "|".join(part or "" for part in parts)
    return sha256(source.encode("utf-8")).hexdigest()[:24]


@dataclass(slots=True)
class ResearchItem:
    title: str
    source: str
    source_type: str
    url: str
    id: str = ""
    author_or_team: str = ""
    category: str = ""
    published_at: str = ""
    pdf_path: str = ""
    raw_path: str = ""
    text: str = ""
    summary: str = ""
    status: str = "collected"
    completeness: str = "公开网页"
    error: str = ""
    created_at: str = ""
    updated_at: str = ""

    def normalized(self) -> "ResearchItem":
        now = utc_now_iso()
        if not self.id:
            self.id = stable_id(self.url, self.title, self.source)
        if not self.created_at:
            self.created_at = now
        self.updated_at = now
        if not self.published_at:
            self.published_at = now
        return self

    @property
    def pdf_file(self) -> Path | None:
        return Path(self.pdf_path) if self.pdf_path else None

