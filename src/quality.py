from __future__ import annotations


NOISE_TERMS = ("噪声图", "二维码", "头像", "广告", "装饰", "封面", "扫码", "赞赏", "无法读取")
CHART_TERMS = ("图表", "折线", "柱状", "同比", "环比", "占比", "指数", "增速", "%", "百分点", "亿元", "美元")
TABLE_TERMS = ("表格", "表", "清单", "预测", "数据", "指标")
SLIDE_TERMS = ("ppt", "PPT", "页", "目录", "章节", "框架", "示意图")


def classify_vision_summary(summary: str) -> tuple[str, int, str, bool]:
    text = summary.strip()
    if not text:
        return ("unknown", 0, "empty", False)

    lowered = text.lower()
    if any(term.lower() in lowered for term in NOISE_TERMS):
        return ("noise", 1, "auto_noise", False)

    kind = "text"
    if any(term.lower() in lowered for term in CHART_TERMS):
        kind = "chart"
    elif any(term.lower() in lowered for term in TABLE_TERMS):
        kind = "table"
    elif any(term.lower() in lowered for term in SLIDE_TERMS):
        kind = "slide"

    quality = 2
    if len(text) >= 80:
        quality += 1
    if any(char.isdigit() for char in text):
        quality += 1
    if any(term.lower() in lowered for term in ("结论", "核心", "市场含义", "显示", "指向", "意味着")):
        quality += 1
    if kind in {"chart", "table"}:
        quality += 1
    quality = min(5, quality)

    use_for_summary = kind != "noise" and quality >= 3
    status = "auto_useful" if use_for_summary else "auto_low_value"
    return (kind, quality, status, use_for_summary)


def assess_ai_summary(summary: str) -> tuple[str, str]:
    text = summary.strip()
    if not text:
        return ("missing", "AI 总结为空")

    required_sections = ("核心观点", "关键证据/数据", "市场含义", "风险提示", "完整性说明")
    missing = [section for section in required_sections if section not in text]
    if missing:
        return ("needs_review", "缺少固定栏目：" + "、".join(missing))
    if len(text) < 500:
        return ("needs_review", "篇幅偏短，可能未充分覆盖长文")
    if "截断" in text and "分段" not in text:
        return ("needs_review", "提到截断但未说明分段处理，需人工确认")
    return ("auto_pass", "固定结构完整，长度与风险提示基本达标")
