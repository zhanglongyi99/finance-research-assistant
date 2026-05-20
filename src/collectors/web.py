from __future__ import annotations

from collections.abc import Iterable

from .http import collect_public_url
from ..models import ResearchItem


def collect_web_sources(config: dict) -> Iterable[ResearchItem]:
    for source in config.get("sources", {}).get("web_sources", []):
        yield from collect_public_url(
            url=source.get("url", ""),
            source=source.get("name", "web"),
            source_type=source.get("source_type", "web"),
            category=source.get("category", ""),
        )

