# 模型脉动 · LLM Pulse

一个零后端、零运行费用的大模型行业情报站。它用 GitHub Actions 定时聚合公开信息源，将结果保存为 JSON，并通过 GitHub Pages 展示日、周、月三个维度的动态。

## 关注范围

- 行业落地：企业采用、产品集成、商业案例与生产部署
- Agent 技术：智能体框架、工具调用、MCP、多智能体与工作流
- 模型发布：新模型、开放权重、能力更新与推理优化
- 评测榜单：Benchmark、Arena、第三方评测与能力对比

## 设计

本项目借鉴了 [Horizon](https://github.com/Thysrael/Horizon) 的多源聚合与去重思路，以及 [AI-Search](https://github.com/Jackychen-12/AI-Search) 的静态归档与零成本部署方式。代码为独立实现，不需要 LLM API Key 或数据库。

```text
RSS / Atom / Google News
          |
          v
scripts/fetch_news.py
抓取 -> 清洗 -> 去重 -> 分类 -> 评分
          |
          v
data/news.json + data/articles/ (历史永久累积)
          |
          v
Vanilla HTML / CSS / JS -> GitHub Pages
```

## 自动更新

`.github/workflows/update-news.yml` 每天北京时间 12:15 和 20:15 运行，更新 `data/news.json`。更新提交会自动触发 Pages 部署。

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
