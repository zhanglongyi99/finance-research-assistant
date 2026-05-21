from __future__ import annotations

import re
from dataclasses import dataclass

from .db import list_article_image_summaries, list_items
from .llm import OpenAICompatibleClient


@dataclass(frozen=True)
class SearchHit:
    item: dict
    score: int
    evidence: str


def answer_question(question: str, *, limit: int = 6, use_ai: bool = True) -> str:
    hits = search_articles(question, limit=limit)
    if not hits:
        return "没有在已入库研报中找到足够相关的材料。"
    if use_ai:
        return _answer_with_ai(question, hits)
    return _answer_locally(question, hits)


def search_articles(question: str, *, limit: int = 6) -> list[SearchHit]:
    terms = _terms(question)
    hits: list[SearchHit] = []
    for row in list_items(limit=5000):
        item = dict(row)
        summary = (item.get("ai_summary") or item.get("summary") or "").strip()
        haystack = " ".join([item.get("title") or "", item.get("source") or "", item.get("category") or "", summary, item.get("text") or ""])
        score = _score(terms, haystack)
        if score <= 0:
            continue
        hits.append(SearchHit(item=item, score=score, evidence=_evidence(summary or item.get("text") or "", terms)))
    hits.sort(key=lambda hit: (hit.score, hit.item.get("published_at") or ""), reverse=True)
    return hits[:limit]


def _answer_with_ai(question: str, hits: list[SearchHit]) -> str:
    materials = []
    for index, hit in enumerate(hits, start=1):
        item = hit.item
        images = list_article_image_summaries(item["id"])
        image_text = "\n".join(f"图片{row['image_index']}：{row['vision_summary']}" for row in images[:4])
        materials.append(
            f"""[{index}] {item.get('source')} / {item.get('category')} / {item.get('title')}
发布时间：{item.get('published_at')}
证据片段：
{hit.evidence}
摘要：
{(item.get('ai_summary') or item.get('summary') or '')[:1800]}
视觉摘要：
{image_text[:1000] or '暂无可用视觉摘要'}
链接：{item.get('url')}"""
        )
    prompt = f"""请回答用户问题。只能依据下方已入库研报材料，不要使用外部信息或编造。
回答中用 [1]、[2] 这样的编号引用材料。若证据不足，请明确说明不足在哪里。

用户问题：{question}

材料：
{chr(10).join(materials)}

输出格式：
答案：
- ...

依据：
- [编号] 文章标题与关键证据

不足与后续需查：
- ...
"""
    client = OpenAICompatibleClient()
    return client.chat(
        [
            {"role": "system", "content": "你是严谨的中文财经研报问答助手。"},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
        max_tokens=1800,
    )


def _answer_locally(question: str, hits: list[SearchHit]) -> str:
    lines = [f"问题：{question}", "", "答案：", "- 已找到以下相关入库研报，建议基于这些材料继续追问或启用 AI 问答。", "", "依据："]
    for index, hit in enumerate(hits, start=1):
        item = hit.item
        lines.append(f"- [{index}] {item.get('source')} / {item.get('title')}：{hit.evidence[:180]}")
    return "\n".join(lines)


def _terms(question: str) -> list[str]:
    raw_terms = [part for part in re.split(r"[\s,，。！？；;：:、/|]+", question) if len(part) >= 2]
    terms: list[str] = []
    for raw in raw_terms or [question]:
        if len(raw) <= 4:
            terms.append(raw)
            continue
        terms.append(raw)
        for size in (2, 3, 4):
            terms.extend(raw[index:index + size] for index in range(0, len(raw) - size + 1))
    seen: set[str] = set()
    deduped = []
    for term in terms:
        if term and term not in seen:
            seen.add(term)
            deduped.append(term)
    return deduped


def _score(terms: list[str], text: str) -> int:
    lowered = text.lower()
    score = 0
    for term in terms:
        term_lower = term.lower()
        count = lowered.count(term_lower)
        if count:
            score += count * (3 if len(term) >= 4 else 1)
    return score


def _evidence(text: str, terms: list[str]) -> str:
    compact = " ".join(line.strip() for line in text.splitlines() if line.strip())
    if not compact:
        return ""
    lowered = compact.lower()
    positions = [lowered.find(term.lower()) for term in terms if lowered.find(term.lower()) >= 0]
    if not positions:
        return compact[:420]
    start = max(0, min(positions) - 120)
    return compact[start:start + 520]
