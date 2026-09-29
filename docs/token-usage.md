# 每日翻译 token 用量

`data/translations/token-usage.json` 从功能上线起永久保存每日聚合，按北京时间请求开始日期、供应商、请求模型、用途（translation / evaluation）分组。每轮 Actions Summary 自动展示当天各模型及供应商合计。

历史查询：

```bash
python scripts/token_usage.py --date 2026-09-29
```

- inputTokens / outputTokens / totalTokens：仅 API 实际返回的有效用量；总量可能包含推理等额外 token，不强制等于输入加输出。
- requests / knownUsageRequests / unknownUsageRequests：尝试请求数、已知用量请求数、未知用量请求数。请求发送前持久化未知计数；没有 usage、网络错误、HTTP 错误、进程中断均不会冒充零消耗。
- 拒收的译文（包括截断、JSON 无效、语义校验失败）仍可能消耗 token，在解析/校验译文前记录 API usage。
- 不将限流器的最大预留额度计入真实消耗，也不推测历史缺失数据；不代表供应商完整账单或组织下其他应用用量。
- 当前范围为本项目全文翻译及每日供应商评估，不包含新闻 AI 评审等其他 API 业务。
- worker 线程共享锁及原子写入；沿用工作流 archive-writes 互斥和每批提交，不支持绕过工作流锁的多进程同时写入。
- 程序在响应后、用量保存前被强制终止时，该请求保留为未知；runner 丢失但尚未 git checkpoint 的记录无法保证恢复。
