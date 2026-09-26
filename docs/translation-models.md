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
502/503/504, timeouts and invalid output exclude it for one hour. At most three
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
