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
