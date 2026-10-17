# Free translation models

Every translation run refreshes OpenRouter's public model catalog before making
inference requests. Eligible models must expose text input/output, adequate context
and output limits, and zero prices. Only the explicitly approved models below are eligible; free pricing alone
is insufficient. Configuration overrides cannot bypass the allowlist. The request also sets `provider.max_price` for prompt/completion to zero.
A configured paid, missing, or retired model is ignored; there is no paid fallback.

Initial preference: Qwen3.8 27B, Gemma 4 31B, Gemma 4 26B, Nemotron 3 Super.
No other models or random free router may be selected automatically. If all
approved models are unavailable, translation waits instead of lowering quality.
Adding a model requires review of technical English-to-Chinese samples for
omissions, mistranslations, terminology, numbers and code preservation. Current
JSON/Chinese checks are structural validation, not proof of semantic quality.
Directory presence is not a successful inference test: actual translation calls
must pass existing block-ID and Chinese-output validation before being saved.

`data/translations/model-health.json` records directory checks, selection, successful
calls and temporary exclusions. HTTP 404/410 exclude a model for 24 hours;
500/502/503/504 and network timeouts exclude it for one hour. Content/JSON validation errors do not disable a model globally. At most three
models may fail per run. Each attempt counts towards the same request/day budget.
Model failures do not add article backoff; switching models also bypasses the old
model's article retry delay. Existing translated blocks are retained.

Explicit `upstream_provider_shared_pool` 429 errors use model failover; unknown
429 errors remain global pauses. The former handler's positively identified shared-pool
pause is migrated automatically.

401/403 stop the run. Account quota/credit errors (429/402) retain the existing
global pause and are not bypassed by rotating models. Catalog failure fails closed.
No-progress attempts and fatal model failures report a failed Actions run while
persisting progress and health state for the next run.

The existing scheduled translation workflow performs this check on every run
(GitHub scheduling may be delayed). An additional daily ChatGPT check reviews
catalog/health/logs and reports faults or model changes.


## BigModel pilot and resilient translation

`BIGMODEL_API_KEY` is an Actions repository secret. It is used only with
`https://open.bigmodel.cn/api/paas/v4/chat/completions` and the fixed approved free
model `glm-4.7-flash`. No FlashX/paid model, tool calls or paid fallback is enabled.
Official model guide: https://docs.bigmodel.cn/cn/guide/models/free/glm-4.7-flash

With the key configured, a bounded BigModel pilot runs before OpenRouter. The
Actions variables `BIGMODEL_TRANSLATION_REQUEST_LIMIT` (default 8) and
`BIGMODEL_TRANSLATION_DAILY_LIMIT` (default 1000, UTC) are independent local safety
budgets, not claims about the account's official limits. Execution is serial.
Review 5–10 complete technical articles for fidelity, numbers, terminology and
omissions before increasing the pilot budget. No live quality assessment is
claimed merely because structural checks pass. A missing key leaves OpenRouter
operational and the homepage explicitly says BigModel is not configured.

Provider state and circuit breakers are separate. BigModel outages do not consume
OpenRouter's counters or stop its worker, and vice versa. BigModel's 429 and OpenRouter's account/model limits use the shared adaptive
admission controller described below, always honoring longer server hints. Authentication failures stop only that provider.

Each valid response block is saved independently. Invalid, missing or duplicate
blocks have bounded retry backoff in `blockFailures`; retries are single-block
requests, and other blocks/articles continue. Diagnostics contain block/source
hash, output length and validation reason, not API keys or raw responses.
A deferred block never counts as complete. Narrow reference-only labels are
localized deterministically with the exact identifier unchanged. Body, body hash,
block IDs and existing translations are preserved. Per-block provider/model
provenance is added for new translations.

`runtime.json` records each run's result, counts, last output and log link. The
homepage reports published status (not a live heartbeat), flagging data older
than one hour. A run with failed work still commits valid partial progress and
triggers the existing deployment path. Cron is offset from the top of the hour;
completion of a trusted main-branch news update is an additional translation
trigger. Neither route is an exact scheduling guarantee. Historical runs show
multi-hour schedule gaps, but do not establish a provider-side root cause.
The shared archive writer lock remains in place to prevent data races.

## Proactive shared admission control

Official references:
- https://docs.bigmodel.cn/cn/api/rate-limit
- https://openrouter.ai/docs/api_reference/limits

All repository OpenRouter inference (translation and AI review) shares
`data/translations/rate-openrouter.json`; BigModel uses `rate-bigmodel.json`.
The archive-writes lock serializes the workflows. Both commit rate state with
progress, retaining cooldowns even across UTC midnight. External clients using
the same account still share provider limits and are outside this local lock.

After a response, wait at least 10 seconds for BigModel and 6 seconds for
OpenRouter (one concurrent request, with jitter). These are conservative local
floors, not advertised account entitlement. On rate errors, the gap doubles up
to 60 seconds; it halves only after 10 successful responses.

Before OpenRouter inference, read `/api/v1/key` and cache the documented
`free_model_daily_requests` counters for at most five minutes. Reserve a request
locally for every attempt; refreshed remote counts may only lower the same-day
remaining balance. Missing/malformed quota or a failed quota check stops that
worker without sending inference, rather than assuming that a configured 1000
requests is the provider's actual allowance. Daily exhausted accounts stop until
the next UTC day plus five minutes; the user-configured local budget also applies.

Honor the later of exponential backoff and server Retry-After (seconds or HTTP
date) / X-RateLimit-Reset (epoch seconds, milliseconds or ISO timestamp). Add
positive jitter. BigModel 1302 is account rate limiting (initial backoff one
minute); 1305 is model/platform overload (15, 30, 60 ... minutes). OpenRouter's
explicit shared-pool errors use a model cooldown (30, 60 ... minutes); unknown
429 uses an account cooldown (5, 10 ... minutes). Backoff base caps at six hours
plus up to 20% jitter; longer server instructions always win. Do not rotate
models to bypass account-level cooldowns. A model-scoped outage may use another
approved model after the common pacing interval.

The API review worker no longer retries OpenRouter 429 responses after a fixed
5/10-second sleep; it persists the same admission state and stops that run.


## 每日服务优先级评估（2026-09-27）

配置 Actions Secrets：`GEMINI_API_KEY`、`GROQ_API_KEY`；继续支持
`BIGMODEL_API_KEY`、`OPENROUTER_API_KEY`。Gemini 项目/Groq 组织必须使用免费档；
模型白名单本身不能保证付费账号不会计费。不会自动升级套餐或启用搜索、工具。

候选组合：GLM-4.7-Flash、Gemini 3.5 Flash-Lite / 3.8 Flash、Groq Qwen3.8-27B /
GPT-OSS-120B，以及 OpenRouter 当前健康且目录报价为零的白名单模型。

每天按 `Asia/Shanghai` 日期最多评估一次，每个候选组合最多一次请求（总计最多 6 次），
使用相同的三段短样本，检查中文、完整块 ID、数字、否定、文件路径、命令及 URL。
样本译文保存在 `data/translations/provider-ranking.json`，便于人工核对。
评估前写入日期检查点，评估阶段结束后立即提交检查点与已消耗额度，再开始正文翻译；
正文翻译超时或重跑时，当天不重复消耗评估请求。评估阶段本身若被强制取消且未能提交，
仍可能丢失尚未提交的预留记录，因此不要通过反复取消任务来重试。
既有冷却与额度限制适用于评估请求和生产请求，不通过评估绕过限流。
每日 07:17（北京时间）的 cron 提供触发机会；其他翻译任务也会检查日期并补评。
GitHub Actions 定时任务可能延迟，不保证准点执行。

分数：60% 自动忠实度/格式代理指标（含近期有效块比例）、25% 成功率、10% 非 429
可用性、5% 延迟；生产数据取最近 7 天并做小样本平滑。它不是人工语义质量评分，
不能证明某模型所有文章都翻译得最好。新服务必须通过样本门槛才能处理正文；失败不会因
速度快而胜出。已评估模型按服务选出最高分者，正文仍保留原有逐块校验与重试机制。
仅因冷却未能复测时可沿用最近两天通过的模型；新模型质量失败不会被自动放行。
排序每天更新，实时冷却与额度不足仍可跳过高分服务。站点显示本轮实际优先级。

### 额度与 pacing

- Groq 官方免费表（2026-09-27）：上述两个模型各列 RPM 30 / RPD 1000 /
  TPM 8000 / TPD 200000；组织实际额度为准。默认仅使用 75%，按模型持久化滚动
  60 秒、24 小时请求/token 预留。使用完整输出上限 + UTF-8 输入字节估算保守预算；
  失败也不退回预留。解析 `x-ratelimit-remaining-*` 与形如 `2m59.56s` 的重置时间。
  请求相关响应头指 RPD，token 相关响应头指 TPM。`GROQ_RPM/TPM/RPD/TPD`
  可按组织控制台配置，观察到更低上限时取较小值。
- Gemini 官方不承诺统一免费额度。默认 `GEMINI_RPM=1`、`GEMINI_TPM=8000`、
  `GEMINI_RPD=8` 是本项目本地试运行预算，**不是官方账号配额**，同样保留 25%
  余量（正数最少 1）。设置这些 Actions Variables 时应抄录项目/模型控制台额度。
  未确认额度时保持小规模试用；处理 429 的 `QuotaFailure` / `RetryInfo`，学习更低
  上限和等待时间。日界线使用 `America/Los_Angeles`，自动处理夏令时。
- 同一提供商同一模型的每日评估、正文翻译共用 rate 文件；本项目各写任务串行。
  其他应用若共用组织/项目也会消耗额度，应为它们留余量。未知 429 保守冻结整个服务，
  不轮换密钥或模型绕过它。认证/参数错误暂停 24 小时，避免反复无效请求。
- 新服务最多 16 次正文请求/轮，正文块批次上限 1200 字符 / 4 块、输出 2048 tokens；
  超过保守 token 预算的单个长块交给其他提供商。每个服务最多运行约 10 分钟，单次
  请求超时 180 秒；未完成内容仍保存在队列中。每日 token/请求预算先于每轮上限。

官方参考：
- https://console.groq.com/docs/rate-limits
- https://console.groq.com/docs/openai
- https://console.groq.com/docs/reasoning
- https://ai.google.dev/gemini-api/docs/pricing
- https://ai.google.dev/gemini-api/docs/rate-limits
- https://ai.google.dev/gemini-api/docs/openai

## Parallel provider execution

After the once-daily evaluation, eligible providers run concurrently in one Actions
process, with exactly one worker per provider (at most four). Each keeps its existing
request/token admission checks, free-model allowlist, quota accounting and cooldowns.
Evaluation completes before workers start. Ranking determines eligibility/model
selection and submission order; it does not serialize providers or guarantee which
thread claims the first article.

A shared coordinator initializes the queue once and exclusively assigns each article
to one provider across chunks. Network calls and rate-control waits do not hold the
queue lock. Completion, deferral or worker exit releases ownership. Idle workers
wait for active owners to release unfinished work, bounded by the worker deadline;
all previously saved blocks are reloaded before another provider resumes. Content
backoff remains in force. Process interruption discards only in-memory ownership,
so the next workflow can resume persisted progress without stale leases.

Each worker exclusively owns its provider's state, rate and health files. Article
sidecars are saved incrementally under article ownership. Shared metrics use a
read/modify/write lock; the main thread updates runtime status and rebuilds the
global index/queue after workers finish. The Actions archive-writes concurrency
group remains mandatory: do not run multiple translation processes against the
same directory. Git commit and deployment remain centralized in the workflow.

## Groq accounting and bounded continuation

Groq admission retains the 75% configured/observed quota safety margin. Requests
reserve input bytes plus output capacity before calling the API. Successful parsed
responses carrying valid prompt_tokens, completion_tokens and total_tokens settle
the current reservation to actual total usage for the rolling daily budget.
Minute admission retains max(reserved, actual) and observed server headers still
win. Errors, missing usage, interruptions and historical reservations stay fully
reserved; request counts are never refunded. Gemini accounting is unchanged.

Both historical representations of Groq HTTP 400 json_validate_failed are repaired
in worker state. Genuine account/model cooldowns and token ledgers remain intact.

Groq defaults to at most 48 requests or 1200 seconds per worker run, also bounded
by the common request limit and all provider quotas. Chunk size stays at 1200
characters / 4 blocks until live usage and quality justify changing it.

Cron remains best-effort. In the existing Actions job, a finished batch with model
output stopped only by its per-run time/request budget can continue for up to two
additional batches (three total). Each batch checkpoints validated-on-ingestion
translations and consumed quota using existing contents permission before further
API calls. No new Actions permission or workflow dispatch is used. The final
sidecar validation and deployment remain in the existing workflow.

No continuation for cooldowns, empty queues, failed-only output, stale runtime
files or exhausted daily budgets. Existing request/daily/interval/model environment
configuration remains in force. The archive-writes lock is held for the bounded
job. This improves work per scheduled execution, but cannot eliminate cron delays
or guarantee work between jobs; it may hold the archive lock for about an hour.
