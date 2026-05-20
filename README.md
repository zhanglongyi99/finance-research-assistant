# 财经助手

一个本地优先的财经研报助手。当前主线聚焦微信公众号研报监听：用户在 WeWe RSS 中手动添加订阅源，项目自动导入已订阅公众号的全文文章、入库去重、生成摘要、生成日报并渲染静态看板。

长期定位是升级为以研报知识库为基础、以每日 AI 简报和引用式问答为核心的个人财经研究助手。实时行情、财经新闻、盘中提醒和外资行来源暂不作为当前自动化主线。

## 当前状态

- 数据源：本地 WeWe RSS (`http://localhost:4000`) 中已订阅的微信公众号。
- 数据库：SQLite，默认路径为 `data/research.sqlite`。
- 输出：主看板 `output/index.html`，日报索引 `output/reports/index.html`。
- 摘要：当前为本地抽取式摘要，后续计划接入 OpenAI-compatible 模型 API。
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

测试连通性：

```powershell
python -m src.cli test-model
```

默认摘要仍使用本地抽取式摘要，不会产生模型费用。显式启用模型摘要：

```powershell
python -m src.cli summarize --mode ai --limit 5
```

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
