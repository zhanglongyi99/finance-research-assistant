from __future__ import annotations

import sqlite3
import os

from ..llm import OpenAICompatibleClient


SYSTEM_PROMPT = """你是一个严谨的中文财经研报阅读助手。
只基于用户提供的文章内容总结，不要编造原文没有的信息。
如果正文信息不足，请在完整性说明里明确指出。"""


def summarize_row_with_ai(row: sqlite3.Row, client: OpenAICompatibleClient | None = None) -> str:
    text = (row["text"] or "").strip()
    title = row["title"] or "未命名内容"
    source = row["source"] or "未知来源"
    published_at = row["published_at"] or "未知时间"
    completeness = row["completeness"] or "未知完整性"
    if not text:
        text = "正文为空，仅可基于标题线索判断。"
    model = client or OpenAICompatibleClient()
    direct_max_chars = _int_env("AI_SUMMARY_DIRECT_MAX_CHARS", 18000)
    if len(text) > direct_max_chars:
        return _summarize_long_text(
            model,
            title=title,
            source=source,
            published_at=published_at,
            completeness=completeness,
            text=text,
        )

    article_text, material_scope = _prepare_article_text(text, len(text))

    user_prompt = f"""请用固定格式总结以下财经研报/公众号文章。

标题：{title}
来源：{source}
发布时间：{published_at}
文本完整性：{completeness}
本次提供材料：{material_scope}

输出格式必须为：
核心观点：
- 2-4 条，概括作者最重要判断

关键证据/数据：
- 1-3 条，摘出关键数据、政策依据、市场信号或论证证据

市场含义：
- 1-3 条，说明可能影响哪些资产、行业、风格或宏观判断

风险提示：
- 1-3 条，列出原文涉及或从材料边界可推导的主要不确定性

完整性说明：
- 说明总结基于什么材料，是否存在正文不完整、噪声、转载或人工复核需求

文章正文：
{article_text}
"""
    return model.chat(
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.2,
    )


def _summarize_long_text(
    model: OpenAICompatibleClient,
    *,
    title: str,
    source: str,
    published_at: str,
    completeness: str,
    text: str,
) -> str:
    chunk_chars = _int_env("AI_SUMMARY_CHUNK_CHARS", 10000)
    chunks = _split_chunks(text, chunk_chars)
    notes = []
    for index, chunk in enumerate(chunks, start=1):
        notes.append(
            model.chat(
                [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": f"""这是一篇长研报/公众号文章的第 {index}/{len(chunks)} 段。
请只基于本段提取对全文总结有用的信息，不要写完整报告。
如果本段主要是免责声明、公众号按钮、历史文章目录等噪声，请简要标注。

标题：{title}
来源：{source}
发布时间：{published_at}
文本完整性：{completeness}

请按以下格式输出：
段落核心观点：
- ...
段落关键证据/数据：
- ...
段落市场含义/风险：
- ...

本段正文：
{chunk}
""",
                    },
                ],
                temperature=0.2,
                max_tokens=1200,
            )
        )

    combined_notes = "\n\n".join(f"【分段摘要 {i + 1}/{len(notes)}】\n{note}" for i, note in enumerate(notes))
    final_prompt = f"""请基于以下分段摘要，生成整篇财经研报/公众号文章的最终深度总结。
注意：分段摘要覆盖了入库正文全文，原文共 {len(text)} 字符，被拆分为 {len(chunks)} 段处理。
请不要声称正文在某个短句处截断；如果存在噪声或免责声明，只在完整性说明里说明。

标题：{title}
来源：{source}
发布时间：{published_at}
文本完整性：{completeness}

输出格式必须为：
核心观点：
- 2-4 条，概括作者最重要判断

关键证据/数据：
- 1-3 条，摘出关键数据、政策依据、市场信号或论证证据

市场含义：
- 1-3 条，说明可能影响哪些资产、行业、风格或宏观判断

风险提示：
- 1-3 条，列出原文涉及或从材料边界可推导的主要不确定性

完整性说明：
- 说明总结基于完整入库正文的分段摘要，是否存在公众号噪声、免责声明或人工复核需求

分段摘要：
{combined_notes}
"""
    return model.chat(
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": final_prompt},
        ],
        temperature=0.2,
        max_tokens=2400,
    )


def _prepare_article_text(text: str, max_chars: int) -> tuple[str, str]:
    if len(text) <= max_chars:
        return text, f"已提供完整入库正文，共 {len(text)} 字符。"

    head_size = max_chars // 2
    tail_size = max_chars - head_size
    excerpt = "\n\n[以下为文章开头节选]\n" + text[:head_size]
    excerpt += "\n\n[中间正文过长，已省略；以下为文章结尾节选]\n" + text[-tail_size:]
    return excerpt, f"入库正文共 {len(text)} 字符，本次因长度限制提供前后节选共 {max_chars} 字符。"


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name, "").strip()
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _split_chunks(text: str, chunk_chars: int) -> list[str]:
    chunk_chars = max(chunk_chars, 2000)
    return [text[start:start + chunk_chars] for start in range(0, len(text), chunk_chars)]
