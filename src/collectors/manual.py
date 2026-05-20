from __future__ import annotations

from collections.abc import Iterable

from .http import collect_public_url
from ..models import ResearchItem


def collect_manual_links(config: dict) -> Iterable[ResearchItem]:
    for link in config.get("sources", {}).get("manual_links", []):
        yield from collect_public_url(
            url=link.get("url", ""),
            title=link.get("title", ""),
            source=link.get("source", "manual"),
            source_type=link.get("source_type", "web"),
            category=link.get("category", ""),
            author_or_team=link.get("author_or_team", ""),
        )

