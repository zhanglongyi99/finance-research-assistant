from __future__ import annotations

import re
import sqlite3


def summarize_row(row: sqlite3.Row) -> str:
    text = (row["text"] or "").strip()
    title = row["title"] or "未命名内容"
    if not text:
        return _title_only_summary(title, row["completeness"] or "仅标题线索")

    sentences = _split_sentences(text)
    key_sentences = sentences[:4] or [text[:180]]
    evidence = _pick_evidence(sentences)

    return "\n".join(
        [
            "核心观点：",
            *[f"- {sentence}" for sentence in key_sentences[:4]],
            "",
            "关键证据/数据：",
            *([f"- {sentence}" for sentence in evidence[:3]] or ["- 原文未提取到明确数据句，需人工复核。"]),
            "",
            "市场含义：",
            "- 该条内容已完成本地归档；具体市场影响建议结合完整原文和后续人工判断确认。",
            "",
            "完整性说明：",
            f"- 总结基于{row['completeness'] or '公开文本'}；当前为本地抽取式摘要。",
        ]
    )


def _title_only_summary(title: str, completeness: str) -> str:
    return "\n".join(
        [
            "核心观点：",
            f"- 当前仅获得标题线索：{title}",
            "",
            "关键证据/数据：",
            "- 正文或 PDF 文本尚未成功提取。",
            "",
            "市场含义：",
            "- 暂不能可靠判断，需要补充原文后再总结。",
            "",
            "完整性说明：",
            f"- {completeness}。",
        ]
    )


def _split_sentences(text: str) -> list[str]:
    rough = re.split(r"(?<=[。！？.!?])\s+|\n+", text)
    sentences = []
    for sentence in rough:
        cleaned = " ".join(sentence.split())
        if 18 <= len(cleaned) <= 260:
            sentences.append(cleaned)
    return sentences


def _pick_evidence(sentences: list[str]) -> list[str]:
    evidence_markers = ("同比", "环比", "%", "bp", "基点", "增速", "CPI", "PPI", "GDP", "PMI", "利率", "汇率")
    return [sentence for sentence in sentences if any(marker in sentence for marker in evidence_markers)]

