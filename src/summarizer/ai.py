from __future__ import annotations

import sqlite3

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

    user_prompt = f"""请用固定格式总结以下财经研报/公众号文章。

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
- 说明总结基于什么材料，是否存在正文不完整、噪声、转载或人工复核需求

文章正文：
{text[:12000]}
"""
    model = client or OpenAICompatibleClient()
    return model.chat(
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.2,
    )
