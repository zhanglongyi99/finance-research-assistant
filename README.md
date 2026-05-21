# 财经助手

一个本地优先的财经研报助手。当前主线聚焦微信公众号研报监听：用户在 WeWe RSS 中手动添加订阅源，项目自动导入已订阅公众号的全文文章、入库去重、生成摘要、生成日报并渲染静态看板。

长期定位是升级为以研报知识库为基础、以每日 AI 简报和引用式问答为核心的个人财经研究助手。实时行情、财经新闻、盘中提醒和外资行来源暂不作为当前自动化主线。

## 当前状态

- 数据源：本地 WeWe RSS (`http://localhost:4000`) 中已订阅的微信公众号。
- 数据库：SQLite，默认路径为 `data/research.sqlite`。
- 输出：主看板 `output/index.html`，日报索引 `output/reports/index.html`。
- 摘要：本地抽取式摘要已全量生成；AI 深度总结、图片资产索引、视觉摘要、AI 简报和引用式问答原型已跑通。
- 总控文档：后续交接、需求池和进度记录统一维护在 `project_control_center.html`。

## 常用命令

```powershell
python -m src.cli init
python -m src.cli collect --source wechat
python -m src.cli summarize --pending
python -m src.cli render-dashboard
python -m src.cli daily-report
python -m src.cli status
python -m src.cli test-model
python -m src.cli test-vision --image-index 1
python -m src.cli index-images
python -m src.cli summarize-images --limit 10
python -m src.cli audit-image-summaries
python -m src.cli audit-ai-summaries --limit 0
python -m src.cli deep-summarize --limit 5
python -m src.cli generate-briefing --limit 8
python -m src.cli ask "中游制造和权益资产怎么看" --limit 4
python -m src.cli run-once
```

生成的看板在 `output/index.html`，数据库在 `data/research.sqlite`。

## 配置

- `config/sources.yaml`：公众号、WeWe RSS、别名和分类参数。
- `config/keywords.yaml`：关键词、分析师和分类。
- `.env.example`：本地环境变量示例。复制为 `.env` 后填写密钥或模型网关配置。

第一版不会绕过付费墙、验证码或私有接口。当前自动化只监听已添加到 WeWe RSS 的公众号。

## 模型 API

项目预留了 OpenAI-compatible 模型接口，适合接入第三方 GPT 中转站。复制 `.env.example` 为 `.env` 后填写：

```powershell
AI_BASE_URL=https://xfx.plus
AI_API_KEY=your_api_key
AI_MODEL=gpt-5.5
AI_WIRE_API=responses
AI_REASONING_EFFORT=medium
```

默认模型按当前规划设为 `gpt-5.5`，推理强度为 `medium`。如果第三方网关只支持 Chat Completions，将 `AI_WIRE_API` 改为 `chat`。
`AI_SUMMARY_DIRECT_MAX_CHARS` 控制单次直发正文长度，默认 `18000`。超过该长度的长文会按 `AI_SUMMARY_CHUNK_CHARS` 分段交给 API 提取要点，再由 API 汇总成最终深度总结，避免单次请求过大导致网关断连。
模型请求会对连接断开、TLS 握手失败和超时等瞬时网络错误做最多 3 次重试；HTTP 业务错误会直接报出，便于定位配置或额度问题。

测试连通性：

```powershell
python -m src.cli test-model
```

默认摘要仍使用本地抽取式摘要，不会产生模型费用。显式启用模型摘要：

```powershell
python -m src.cli summarize --mode ai --limit 5
```

为已入库文章生成独立的 AI 深度总结，不覆盖本地摘要：

```powershell
python -m src.cli deep-summarize --limit 5
python -m src.cli render-dashboard
```

AI 深度总结会写入本地 SQLite 的 `ai_summary` 字段。生成看板和日报时，会优先展示 AI 总结，没有 AI 总结的文章会回退到本地摘要。

测试模型视觉能力：

```powershell
python -m src.cli test-vision --image-index 1
```

该命令会从最近一篇带原始 HTML 的文章中提取正文图片 URL，并让模型读取图片。当前已验证 `gpt-5.5` 可以直接读取部分微信公众号 `mmbiz.qpic.cn` 图片 URL。

图片资产与视觉摘要入库：

```powershell
python -m src.cli index-images
python -m src.cli summarize-images --limit 10
python -m src.cli deep-summarize --limit 1 --resummarize
```

`index-images` 会从每篇文章的 `raw_path` 原始 HTML 中提取图片，写入本地 SQLite 的 `article_images` 表，并标记头像、封面、二维码、广告、赞赏图等噪声图。`summarize-images` 只默认处理正文图，调用视觉模型读取图片 URL，写入 `vision_summary`、`vision_model` 和 `vision_summary_at`。后续 `deep-summarize` 会把同一文章已存在的视觉摘要作为补充材料并入最终 AI 深度总结。

视觉摘要可自动复核类型与质量：

```powershell
python -m src.cli audit-image-summaries
python -m src.cli audit-ai-summaries --limit 0
```

视觉摘要会被标记为 `chart`、`table`、`slide`、`text` 或 `noise`，并写入质量分与是否用于后续总结。当前小批量扩量后已有 58 张视觉摘要，其中 52 张可用于总结，6 张噪声图会自动排除。

生成投研简报：

```powershell
python -m src.cli generate-briefing --limit 8
```

AI 简报输出到 `output/briefing/latest.html` 和 `output/briefing/latest.json`。生成时会优先使用 AI 深度总结和可用视觉摘要，并按“投研阅读地图”组织内容：宏观经济形势、市场环境与资产含义、风险/黑天鹅/非共识观点、细分领域专业分析、需要跟踪和本期引用。篇幅和主线数量由当天材料密度决定。失败时可加 `--local` 生成本地兜底版，兜底版输出到 `output/briefing/local.html`，不会覆盖最新 AI 简报。

引用式问答原型：

```powershell
python -m src.cli ask "中游制造和权益资产怎么看" --limit 4
```

问答默认只检索已入库研报，回答带文章级引用；证据不足时会说明不足。加 `--local` 可只返回本地检索结果，不调用模型。

## 公众号接入

当前监听的公众号来自 `research_report_channels.html`：

- 郭磊宏观茶座
- 广发证券研究
- 晨明的策略深度思考
- 华创宏观
- 华泰研究
- 兴证策略
- 华泰固收
- 华西研究

自动监听由 `config/sources.yaml` 的 `wechat_accounts` 和 `wewe_rss` 控制。默认流程是：

1. 优先从本地 WeWe RSS (`http://localhost:4000`) 读取已订阅公众号的 fulltext JSON feed，并导入正文。
2. 写入 SQLite，按 URL 去重。
3. 对待处理公众号正文生成摘要。
4. 渲染本地看板。

当前 WeWe RSS 是首选链路。检查本地订阅源：

```powershell
Invoke-WebRequest -Uri 'http://localhost:4000/feeds' -UseBasicParsing
python -m src.cli status
```

如需添加新的公众号，先打开 `http://localhost:4000/dash`，用本地 `AUTH_CODE` 登录，然后在“账号管理”里确认微信读书账号有效；再到“公众号源”用一篇公开 `mp.weixin.qq.com/s/...` 文章链接添加公众号。若账号失效，需要扫码重新登录 WeWe RSS。

## Codex 自动化

项目的日常监听命令是：

```powershell
python -m src.cli run-once
```

它会完成 WeWe RSS 公众号导入、摘要生成和看板更新。建议用 Codex 自动化在每天开盘前和收盘后各运行一次。

## 开源边界

本项目准备以源码和文档为主进行开源。以下内容不应提交到 GitHub：

- `data/`：SQLite 数据库和公众号原始 HTML。
- `output/`：生成的看板、日报和 JSON 导出。
- `pdfs/`：本地下载或归档的 PDF。
- `logs/`：运行日志。
- `.env`：API key、模型网关、账号相关本地配置。
- `config/*cookies*.json`：任何 cookie 或登录态文件。

开源仓库应只包含源码、配置模板、说明文档和不含私密数据的项目规划文档。

## 暂缓来源

网页/PDF、慧博、东方财富、财经媒体转载和 Morgan Stanley Robin Xing / Laura Wang 相关内容暂不进入当前自动化。大摩相关公开信息和正式研报权限差异较大，后续需要单独设计外资行公开内容/授权内容采集模块。
