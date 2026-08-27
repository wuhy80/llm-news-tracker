# 模型脉动 · LLM Pulse

一个零后端、零运行费用的大模型行业情报站。它用 GitHub Actions 定时聚合公开信息源，将结果保存为 JSON，并通过 GitHub Pages 展示日、周、月三个维度的动态。

## 关注范围

- 行业落地：企业采用、产品集成、商业案例与生产部署
- Agent 技术：智能体框架、工具调用、MCP、多智能体与工作流
- 模型发布：新模型、开放权重、能力更新与推理优化
- 评测榜单：Benchmark、Arena、第三方评测与能力对比

## 设计

本项目借鉴了 [Horizon](https://github.com/Thysrael/Horizon) 的多源聚合与去重思路，以及 [AI-Search](https://github.com/Jackychen-12/AI-Search) 的静态归档与零成本部署方式。代码为独立实现，不需要数据库；配置免费模型密钥后可启用 AI 精选、分类、评分与中文摘要，未配置时自动使用本地规则。

```text
RSS / Atom / Google News
          |
          v
scripts/fetch_news.py
抓取 -> 清洗 -> 去重 -> 分类 -> 评分
          |
          v
scripts/fetch_articles.py -> scripts/ai_review.py
正文归档 -> AI 相关性判断 / 分类 / 评分 / 中文摘要
          |
          v
data/news/ + data/articles/YYYY/MM/DD/ (历史永久累积)
          |
          v
Vanilla HTML / CSS / JS -> GitHub Pages
```

## 自动更新

`.github/workflows/update-news.yml` 每天北京时间 08:00 和 15:00 运行，更新分片数据。GitHub 的定时任务可能延迟数十分钟；更新提交会自动触发 Pages 部署。

### 数据目录

数据按用途拆分，避免单个 JSON 随历史增长而持续膨胀：

```text
data/news.json                         # 小型清单：日期、数量、来源
data/news/YYYY/MM/DD.json              # 首页使用的每日轻量索引
data/articles/YYYY/MM/DD/<id>.json     # 完整文章：元数据、正文、摘要、AI 审阅、重点词
data/article-index/<id前两位>.json     # 兼容不带日期的历史文章链接
```

首页只加载当前日、周或月涉及的每日索引，文章页只加载一篇完整记录。可运行 `python scripts/build_news_data.py` 重建索引，运行 `python scripts/validate_data.py` 检查清单、分片、文章与定位表是否一致。

### 重要等级

AI 审核会从相关性、影响力、新颖性、可信度、实用性和时效性六个维度计算 0–100 分，并映射为 1–5 级：

| 等级 | 分数 | 含义 |
| --- | --- | --- |
| 5 级 | 85–100 | 必须关注 |
| 4 级 | 70–84 | 值得精读 |
| 3 级 | 50–69 | 建议浏览 |
| 2 级 | 30–49 | 参考信息 |
| 1 级 | 0–29 | 可以略过 |

跑题内容最高 1 级，无新增信息的转述最高 2 级，未验证传闻最高 3 级。5 级必须具备明确证据且影响力维度不少于 20 分；社区内容只有在包含代码、数据、原始文件或可复现证据时才能达到 4 级以上。前端的 AI 精选默认展示 3 级以上，全部文章保留全部等级。

### 免费模型配置

推荐使用 Google Gemini 免费额度。在仓库 **Settings > Secrets and variables > Actions** 中添加以下任意一个 Repository secret：

- `GEMINI_API_KEY`：首选，默认从 `gemini-3.7-flash` 开始尝试；若当前免费层不开放或额度不足，会依次降级到 3.6、3.5 Flash-Lite、3.1 Flash-Lite 和 2.5 Flash-Lite
- `OPENROUTER_API_KEY`：备用，默认调用 OpenRouter 的 `openrouter/free` 免费路由

同时配置两个密钥时优先使用 Gemini。也可以通过 Repository variables 设置：

- `AI_REVIEW_PROVIDER`：`gemini`、`openrouter` 或留空自动选择
- `AI_REVIEW_MODEL`：覆盖默认模型名称

每轮只审核尚未处理的文章，审核结果按版本写入对应的单篇文章 JSON，不会重复消耗额度。最终实际采用的模型记录在文章记录的 `aiReview.model` 字段中。模型限流、密钥缺失或输出异常时，任务继续使用原有关键词分类与评分，不会阻断新闻更新。

分类和评分均为可审查的本地规则：

- 官方来源获得更高的可信度权重
- 越新的信息获得越高的时效权重
- 发布、评测、开源等高价值关键词提升信号分
- 通过规范化标题去除同一事件的重复报道
- 历史记录只追加和去重，不按时间或条数清理
- 文章默认在站内阅读，优先保存 Feed 公开正文，否则提取原文的纯文本主体
- 定时更新每次补抓 300 篇，手动运行默认补抓 600 篇，逐步覆盖全部历史记录；外部 Reader 仅低频补充
- Google News 中转链接会先还原为发布者地址；源站受限时再尝试公开 Reader 文本接口
- 两条正文通道都失败时站内阅读页回退到摘要，并保留具体失败原因供后续重试
- 只有成功保存正文的文章标题可进入站内阅读；失败项仅保留原文链接
- 已归档正文通过抽取与自动翻译生成中文摘要；翻译受限时使用文章元数据生成中文概述

## 本地运行

需要 Python 3.10 或更新版本。

```bash
python scripts/fetch_news.py
python scripts/fetch_articles.py
# 配置 GEMINI_API_KEY 或 OPENROUTER_API_KEY 后可选运行
python scripts/ai_review.py
python -m http.server 8000
```

然后访问 `http://localhost:8000`。

## 发布到 GitHub Pages

1. 将仓库推送到 GitHub，默认分支设为 `main`。
2. 在仓库的 **Settings > Pages > Build and deployment** 中选择 **GitHub Actions**。
3. 手动运行一次 **Update news**，之后等待 **Deploy GitHub Pages** 完成。

站点不使用构建框架，`.nojekyll` 会确保静态文件按原样发布。

## 调整信息源

编辑 `scripts/fetch_news.py` 中的 `SOURCES`。每个来源支持以下字段：

- `name`：来源名称
- `url`：RSS 或 Atom 地址
- `domain`：用于展示站点图标
- `official`：是否为官方来源
- `hint`：可选的默认分类
- `fallback_urls`：主来源失败时按顺序尝试的备用 RSS / Atom
- `extract_embedded_source`：对新闻索引 Feed 提取原始发布方名称

## 许可

[MIT](LICENSE)
