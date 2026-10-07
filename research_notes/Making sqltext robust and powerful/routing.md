# LLM Routing, Cascading and Gateways for sqltext (Needle -> local Qwen -> Bedrock Claude), as of Oct 2026

Context checked in the repo (for applicability): `sqltext/gateway.py` (`Router.classify` / `chain` / `ask`), `sqltext/backends/bedrock.py` (`AnthropicBedrockMantle` + `BetaRefusalFallbackMiddleware`, refusal fallback to `anthropic.claude-opus-5`), `sqltext/verify.py` (grounding checks). Observed: the Bedrock call sets no explicit timeout or `max_retries` (SDK defaults: 10 min timeout, 2 retries), sends `system` as a plain string with no `cache_control`, and the cascade escalates only on hard signals (SQL error, grounding issue, empty rows). No confidence score is computed for the local model's output.

## Q1. Learned routers and cascades (RouteLLM, RouterBench, Martian, Not Diamond, Hybrid LLM, FrugalGPT, AutoMix, Arch-Router): gains, training data, tiny-RAM fit

### Takeaway
Published routers report 2x or more cost reduction at near-strong-model quality, but nearly all were trained on general chat or QA preference data (Chatbot Arena, MMLU, GSM8K). AWS itself warns that pre-trained routers may route poorly for specialised use cases. For sqltext, two things transfer best: the cascade pattern (FrugalGPT, AutoMix: answer cheaply, verify, escalate) and threshold calibration against a target escalation rate (RouteLLM's `calibrate_threshold`). Pre-generation learned routers matter less, because sqltext gets a free, strong post-generation signal: it can execute the SQL.

### Cited Findings
- **RouteLLM** (LMSYS, arXiv 2406.18665) trains routers that choose between a strong and a weak LLM. It covers four router types: similarity-weighted ranking, matrix factorization, a BERT classifier and a causal-LLM classifier. Training uses Chatbot Arena human-preference data, augmented with LLM-judge labels. It reports cost cuts of more than 2x on MT-Bench, MMLU and GSM8K without hurting quality, and the routers transfer when the strong/weak pair is swapped at test time. — [arXiv 2406.18665](https://arxiv.org/abs/2406.18665)
- RouteLLM repo: the `mf` (matrix factorization) router is the recommended one ("very strong and lightweight"). `calibrate_threshold` picks the threshold that gives a target share of strong-model calls (example: 50% strong calls gives a threshold of 0.11593). The README claims up to 85% cost reduction while keeping 95% of GPT-4 performance on MT Bench. **Caveat: the `mf` and `sw_ranking` routers still need an `OPENAI_API_KEY` to compute embeddings**, so question text goes to OpenAI. Apache-2.0, about 5.6k stars. — [RouteLLM GitHub](https://github.com/lm-sys/RouteLLM)
- **Hybrid LLM** (Ding et al., ICLR 2024): a router assigns each query to the small or large model based on predicted difficulty and a desired quality level. It achieves up to 40% fewer large-model calls with no drop in quality, and the quality level can be tuned at test time. — [arXiv 2404.14618](https://arxiv.org/abs/2404.14618v1); [ICLR proceedings](https://proceedings.iclr.cc/paper_files/paper/2024/hash/b47d93c99fa22ac0b377578af0a1f63a-Abstract-Conference.html)
- **FrugalGPT** (Chen, Zaharia, Zou; TMLR 2024) is an LLM cascade that learns which model combinations to use per query. It matches GPT-4 performance with up to 98% cost reduction, or improves accuracy over GPT-4 by 4% at the same cost. — [arXiv 2305.05176](https://arxiv.org/abs/2305.05176); [TMLR listing](https://mlanthology.org/tmlr/2024/chen2024tmlr-frugalgpt)
- **AutoMix** (NeurIPS 2024): the small model answers, then **self-verifies** with few-shot prompting (no fine-tuning, so it works with black-box models). A POMDP router takes the noisy self-verification confidence as its only input and decides whether to escalate. Across 5 LMs and 5 datasets it cuts compute cost by more than 50% at comparable performance. — [arXiv 2310.12963](https://arxiv.org/pdf/2310.12963); [NeurIPS 2024](https://proceedings.neurips.cc/paper_files/paper/2024/hash/ecda225cb187b40ea8edc1f46b03ffda-Abstract.html)
- **RouterBench** (2024): a benchmark of 11 LLMs (6 open, 5 proprietary) over 8 datasets and 64 tasks. Newer benchmarks build on it: LLMRouterBench (arXiv 2601.07206, Jan 2026), "When is Routing Meaningful?" (arXiv 2607.09197) and a four-router comparison on a common interface (arXiv 2608.14641). I did not read these newer papers in full. — [RouterBench arXiv 2403.12031](https://arxiv.org/pdf/2403.12031); [LLMRouterBench](https://arxiv.org/pdf/2601.07206); [2607.09197](https://arxiv.org/pdf/2607.09197); [2608.14641](https://arxiv.org/pdf/2608.14641)
- 2026 survey (Moslem & Kelleher, TMLR 2026, "Dynamic Model Routing and Cascading for Efficient LLM Inference"). Its taxonomy covers difficulty assessment, human-preference alignment, clustering, uncertainty, RL, multimodal routing and cascading, framed by when the decision is made, what information it uses and how it is computed. It says the best strategy depends on deployment constraints, and that routers which generalise across architectures and applications remain an open challenge. — [arXiv 2603.04445](https://arxiv.org/abs/2603.04445)
- **Arch-Router** (Katanemo): a 1.5B model that maps a query to user-defined "domain/action" routing policies written in natural language, with no retraining when policies change. It reports state-of-the-art preference-match accuracy. Weights are on Hugging Face. — [arXiv 2506.16655](https://arxiv.org/pdf/2506.16655); [HF model](https://huggingface.co/katanemo/Arch-Router-1.5B)
- **Not Diamond / OpenRouter Auto**: OpenRouter's `openrouter/auto` was previously powered by Not Diamond. As of 10 Aug 2026 it was replaced by a router driven by a rolling 7-day community spending signal. — [OpenRouter Auto Router docs](https://www.openrouter.ai/docs/guides/routing/routers/auto-router). Not Diamond's own product collects routing metadata by default (request/session IDs, timestamps, routing decisions, token usage, timing and outcome) and collects sensitive content fields only with opt-in consent. — [Not Diamond data dictionary](https://code.notdiamond.ai/docs/data-privacy/data-dictionary/)
- **Martian** is a commercial (SaaS) model router that routes each query to an LLM in real time. I found no public numbers. — [Martian docs](https://docs.withmartian.com/martian-model-router/getting-started/hello-world); [Accenture investment PR](https://newsroom.accenture.com/news/2024/accenture-invests-in-martian-to-bring-dynamic-routing-of-large-language-queries-and-more-effective-ai-systems-to-clients)
- Anthropic's own cost guidance: before building a multi-model cost cascade, first measure the most capable model at a lower `effort` on the same tasks. Prompt caches are model-scoped, so a cascade loses cache reuse across its models. Judge cost per completed task, not per request. — Anthropic claude-api skill reference (Thinking & Effort section; mirrors [platform.claude.com docs](https://platform.claude.com/docs/en/about-claude/pricing.md))

### Inferences
- **Tiny-RAM fit.** RouteLLM's BERT router (about 110M parameters) or a logistic or gradient-boosted classifier on hand-built features fits in RAM easily. The `mf` router is small, but it needs OpenAI embeddings by default; a local embedding model (e.g. a MiniLM-class sentence encoder) would have to replace that for privacy. Arch-Router at 1.5B is 3x larger than the local Qwen2.5-Coder-0.5B, which defeats the purpose. A causal-LLM router (8B in RouteLLM) is out of scope.
- **Most practical upgrade path for sqltext.** Keep the rule-based `classify()` as a prior, and log every routing outcome: question features, which tier answered, whether it escalated, and whether the result was correct (from evals or user feedback). Then fit a small logistic-regression or GBM router on sqltext's own features (tables touched, date words, comparison words, schema size, plus new ones such as question length, number of literals, aggregation words and the local model's confidence). This is Hybrid LLM / FrugalGPT done locally, with no third-party data flow. Use RouteLLM-style calibration to set the threshold for a target Bedrock escalation rate.
- **AutoMix-style self-verification** maps directly onto sqltext's existing post-hoc checks. The grounding check already does a cheap form of it. Q2 shows that execution-aware and verifier signals beat a pre-generation difficulty guess for SQL.
- **Privacy.** RouteLLM `mf`/`sw_ranking` (OpenAI embeddings), Not Diamond, Martian and OpenRouter all send question text, and possibly schema text, to a third party. That conflicts with a local-first design and must be flagged to users.

### Gaps
- Per-benchmark RouteLLM numbers from the paper body (MMLU, GSM8K strong-call percentages) were not extracted; only the abstract and README claims were verified.
- I found no published router trained specifically on text-to-SQL difficulty, and no evaluation of RouteLLM-style routers on BIRD or Spider.
- I found no public quality or cost benchmarks for Martian or Not Diamond.

## Q2. Confidence signals for escalation in text-to-SQL: do they predict correctness?

### Takeaway
For SQL, sequence-level log-probabilities and plain self-consistency are only moderately predictive (AUROC about 0.61-0.68). LLM-judge verification is better (0.72-0.78), and a two-judge ensemble reaches about 0.82. Structure-aware signals do much better than raw logprobs: grammar/AST-aware aggregation, node-level schema validity, type and alias checks, and execution results. sqltext's grounding check is an informal version of these. Fine-tuned verifiers degrade on unseen schemas.

### Cited Findings
- Richardson (arXiv 2607.06799, Jul 2026, "What Predicts Correctness in Text-to-SQL? A Selective-Prediction Study"; single-author preprint, not peer reviewed). On BIRD and Spider, black-box signals scored AUROC 0.61-0.68: string, structural and execution self-consistency, and schema relevance. String self-consistency was best at 0.675. White-box log-probability scored 0.67. LLM-judge verification scored 0.72 (GPT-4o-mini) and 0.78 (Claude). A two-provider judge ensemble reached **0.82 AUROC with ECE 0.03**. At that point the system can answer 27% of questions at 24% selective risk, whereas self-consistency gave no safe low-risk subset. Fine-tuned verifiers scored 0.77-0.79 in-distribution but dropped to about 0.66 on unseen schemas. — [arXiv 2607.06799](https://arxiv.org/abs/2607.06799)
- Entezari Maleki, Pourreza, Rafiei (arXiv 2508.14056, Aug 2025). Without model internals, consistency-based methods work best. With logits, SQL-grammar-aware aggregation of token probabilities helps. **Execution signals add useful information to both approaches.** — [arXiv 2508.14056](https://arxiv.org/abs/2508.14056)
- Hasson & Guo (arXiv 2511.13984, Nov 2025). A per-AST-node error classifier uses features for schema validity, alias resolution, type compatibility and likely typos. It improves average AUC by **+27.44% over token log-probabilities** and stays robust across databases. The node-level output also supports targeted repair and selective execution. — [arXiv 2511.13984](https://arxiv.org/abs/2511.13984)
- Other work found but not read in full: confidence scoring for LLM SQL in supply-chain extraction (translation-consistency, embedding similarity and self-reported confidence) — [arXiv 2506.17203](https://arxiv.org/html/2506.17203v1); "Confidence Estimation for Error Detection in Text-to-SQL Systems" — [arXiv 2501.09527](https://arxiv.org/html/2501.09527v1); ambiguity vs instability in clinical text-to-SQL — [arXiv 2602.12015](https://arxiv.org/html/2602.12015v1).
- llama.cpp exposes per-token probabilities: `n_probs` returns the top-N probabilities per generated token, `post_sampling_probs` returns them after the sampling chain, and an OpenAI-compatible `logprobs`/`top_logprobs` mapping was added to llama-server. llama-cpp-python has `logits_all`. An open issue reports misaligned `token_logprobs` from `Llama.__call__()`, so check the alignment before relying on it. — [llama.cpp server logprobs commit](https://cdn04132025.gitlink.org.cn/replica/llama.cpp/commit/57bb2c40cd94c5a09f5210ed8264cc93b21c4b7e); [llama-cpp-python issue #1983](https://github.com/abetlen/llama-cpp-python/issues/1983)

### Inferences
- **Cheap signals for the local Qwen-0.5B step**, ordered by expected value per CPU-ms:
  1. Static checks the repo already has or can add cheaply: every identifier resolves in the schema, types are compatible, aliases resolve, and question literals appear in the SQL (the existing `grounding_issues`). This is the Hasson & Guo feature family.
  2. Execution checks: the query errors, returns 0 rows, returns an absurd row count, or returns NULL-only aggregates.
  3. Execution-agreement self-consistency: sample k=3-5 at temperature 0.7 from the 0.5B model, run each, and compare result sets. On a 0.5B model this costs k forward passes but stays local.
  4. Mean or minimum token logprob over SQL tokens only, ideally weighted toward identifier and literal tokens (grammar-aware, per 2508.14056).

  Combine these in a small logistic model calibrated on sqltext's eval set rather than using hard if/else gates.
- An **LLM-judge verifier** (Haiku 4.5 judging the local model's SQL) is the strongest single signal in 2607.06799. But a Haiku judge call costs roughly as much as just asking Haiku to write the SQL. It is mainly worth it for selective abstention or warnings, not as a gate in front of Haiku.
- Because verifiers degrade on unseen schemas, avoid fine-tuning a verifier on one DB. Prefer schema-agnostic features plus execution.

### Gaps
- I found no published AUROC numbers specifically for 0.5B-scale models (Qwen2.5-Coder-0.5B) on these signals. All the studies above use larger models. Measure it on sqltext's own evals.
- I found no evidence on how well self-consistency works for *tiny* models, where samples may be consistently wrong.

## Q3. Gateways: LiteLLM, Portkey, Kong, OpenRouter, Envoy AI Gateway, AWS-native (Intelligent Prompt Routing, cross-Region inference, prompt caching): fit for a small Python CLI

### Takeaway
For a small Python CLI that already uses the Anthropic SDK's Bedrock Mantle client, adding a gateway adds more risk and dependency weight than value. LiteLLM's Router has the right feature set (fallbacks, retries, cooldowns, budgets, caching), but it is a large dependency and was hit by a credential-stealing PyPI supply-chain compromise in March 2026. Bedrock Intelligent Prompt Routing does **not** support current Claude models (only Claude 3 Haiku, 3.5 Haiku and 3.5 Sonnet v1/v2), is English-optimised, and cannot learn from application data. The AWS-native features that do apply are cross-Region inference profiles and prompt caching.

### Cited Findings
- **Bedrock Intelligent Prompt Routing** became GA in April 2025. It is a single serverless endpoint that predicts response quality per request and routes between two models of the same family, using a fallback model and a `responseQualityDifference` criterion. — [AWS What's New (GA)](https://aws.amazon.com/about-aws/whats-new/2025/04/amazon-bedrock-intelligent-prompt-routing-generally-available)
- The supported-model table lists only **Anthropic Claude 3 Haiku, Claude 3.5 Haiku, Claude 3.5 Sonnet and Claude 3.5 Sonnet v2** (plus Nova Lite/Pro and Llama 3.x). Claude Haiku 4.5 and the Opus 4.x/5.x models are not listed. The CLI example oddly names `claude-sonnet-4-5` as a placeholder model, which conflicts with the table. Documented limits: "only optimized for English prompts", "can't adjust routing decisions … based on application-specific performance data", and may not be optimal for specialised use cases. It also cannot route to a local model. — [Bedrock user guide: prompt routing](https://docs.aws.amazon.com/bedrock/latest/userguide/prompt-routing.html)
- **Cross-Region inference.** Geographic profiles (`us.`, `eu.`, `apac.`) keep processing within that geography at standard price. **Global profiles route to any commercial Region at roughly 10% lower cost** but give no data residency. There is no extra routing charge, traffic stays on the AWS network, CloudTrail logs the `inferenceRegion` field, and inference profiles don't support Provisioned Throughput. — [Bedrock cross-Region inference](https://docs.aws.amazon.com/bedrock/latest/userguide/cross-region-inference.html)
- **LiteLLM Router** features:
  - routing strategies: simple-shuffle, latency-based, least-busy, usage-based, cost-based
  - `order`-based priority fallbacks
  - `num_retries`, `retry_after`, `RetryPolicy`
  - circuit-breaker-like cooldowns: `allowed_fails` per minute, then `cooldown_time` (default 5 s)
  - `max_parallel_requests`, per-deployment `rpm`/`tpm`
  - `enable_pre_call_checks` (context window / region)
  - in-memory or Redis response caching (`cache_responses`)

  — [LiteLLM routing docs](https://docs.litellm.ai/docs/routing)
- **LiteLLM supply-chain incident.** On 24 Mar 2026, PyPI versions **1.82.7 and 1.82.8** carried a `.pth` payload that stole SSH keys, AWS/GCP/Azure credentials, environment variables (API keys) and more. The publishing token leaked via a compromised Trivy scanner in CI. The versions were live for less than 5 hours and were yanked the same day. — [NetSPI](https://www.netspi.com/blog/executive-blog/ai-ml-pentesting/litellm-supply-chain-compromise/); [pypistats write-up](https://pypistats.com/blog/litellm-pypi-supply-chain-compromise-how-a-popular-llm-proxy-became-a-credential-stealing-backdoor-march-24-2026)
- **OpenRouter** is a third-party SaaS aggregator, and its Auto Router now uses a community-spend signal (see Q1). — [OpenRouter Auto Router](https://www.openrouter.ai/docs/guides/routing/routers/auto-router)

### Inferences
Pros and cons for sqltext:
- **LiteLLM SDK (in-process).**
  - Pros: ready-made fallbacks, retries, cooldowns, budgets and cost tracking across llama.cpp and Bedrock.
  - Cons:
    - It is a heavy dependency for a CLI.
    - It wraps Bedrock behind its own abstraction, so it may lag Anthropic SDK features sqltext already uses: Mantle, `BetaRefusalFallbackMiddleware`, `output_config.effort`, Opus 5.5 rules such as "thinking can't be disabled".
    - The March 2026 incident shows the credential-theft risk of a large transitive dependency in a tool that holds AWS credentials.
  - If adopted, pin exact versions with hashes and make it optional.
  - Recommendation: borrow its *patterns* (Q4) rather than the dependency.
- **LiteLLM Proxy / Portkey / Kong AI Gateway / Envoy AI Gateway** are separate server processes. They suit teams with many apps and central budgets, and are overkill for a single-user CLI. Portkey's hosted gateway and OpenRouter are SaaS, so prompts, schema and possibly data rows leave the AWS boundary (privacy flag). Self-hosted Portkey, Kong or Envoy avoid that but add operations burden.
- **Bedrock Intelligent Prompt Routing:** not applicable. It doesn't cover Haiku 4.5 or Opus 5.5 and is English-only and generic, and sqltext's routing also spans local models.
- **Cross-Region inference profiles:** high value at low cost. Use `us.`/`eu.` (or `global.` if residency doesn't matter) profile IDs to absorb throttling. Confirm the profile ID format the Mantle endpoint expects, because the repo currently uses bare `anthropic.claude-*` IDs.

### Gaps
- Portkey, Kong AI Gateway and Envoy AI Gateway docs were not fetched in this pass. Feature claims about them above are general characterisations, not verified specifics.
- I did not verify whether the Bedrock **Mantle** (Messages API) endpoint accepts `us.`/`global.` inference-profile IDs in the same way as `bedrock-runtime`.
- I did not verify LiteLLM's support for the Mantle endpoint or Opus 5.5-specific parameters.

## Q4. Reliability features to borrow: retries, circuit breakers, timeouts, throttling, budgets, semantic caching, observability

### Takeaway
Most of what a gateway provides can be added in about 150 lines on top of the Anthropic SDK:
- explicit per-tier timeouts and `max_retries`
- a per-backend failure counter with cooldown (LiteLLM's `allowed_fails`/`cooldown_time` pattern)
- typed handling of 429 throttling vs non-retryable 4xx
- token and cost accounting from `response.usage`, with a per-session budget cap
- an exact-match (normalised question + schema hash) SQL cache before trying semantic caching
- OpenTelemetry GenAI spans, viewable in self-hosted Phoenix or Langfuse

### Cited Findings
- Anthropic Python SDK defaults: timeout 10 minutes; `max_retries` 2, with exponential-backoff retries on connection errors, 408, 409, 429 and 5xx. Override with `client.with_options(timeout=..., max_retries=...)` or `anthropic.Timeout(60.0, read=5.0, write=10.0, connect=2.0)`. Timeouts are retried, so wall-clock time can reach `timeout × (max_retries+1)`. Use typed exceptions (`RateLimitError`, `APIStatusError`, `APIConnectionError`) in most-specific-first order rather than one broad catch. — Anthropic claude-api skill reference, Python README (SDK docs: [anthropic-sdk-python](https://github.com/anthropics/anthropic-sdk-python))
- Bedrock `ThrottlingException` (HTTP 429) fires when either the RPM or the TPM quota is exceeded. Quota types: on-demand single-Region, cross-Region (CRIS) and global cross-Region (GCRIS). CRIS routes to less-throttled Regions. — [AWS re:Post knowledge center](https://www.repost.aws/knowledge-center/bedrock-throttling-error); secondary: [hidekazu-konishi throughput guide](https://hidekazu-konishi.com/entry/amazon_bedrock_inference_throughput_and_latency_optimization.html). (A blog cites "50 RPM / 400k TPM for Claude 3.5 Sonnet in us-east-1" as a default. This is unverified and secondary; current Claude 4.5/5.x defaults should be read from the Service Quotas console.)
- **Cache hits don't count against rate limits** on Bedrock (Anthropic section of the prompt-caching guide: "cache hits are not deducted against your rate limit"), so prompt caching also reduces throttling. — [Bedrock prompt caching](https://docs.aws.amazon.com/bedrock/latest/userguide/prompt-caching.html)
- LiteLLM's reliability primitives (circuit-breaker cooldowns, retry policies, `max_parallel_requests`, rpm/tpm limits, response caching) are listed in Q3. — [LiteLLM routing docs](https://docs.litellm.ai/docs/routing)
- Observability:
  - The OpenTelemetry GenAI semantic conventions define model-operation and agent-operation spans, token-usage metrics and events, with provider-specific guidance for Anthropic and AWS Bedrock. — [OneUptime summary of OTel GenAI semconv](https://oneuptime.com/blog/post/2026-02-06-genai-semantic-conventions-llm-monitoring/view)
  - Since a 15 May 2026 release, Arize Phoenix converts OTel GenAI semconv attributes to OpenInference automatically, so OTel-native Anthropic instrumentation renders with message I/O, tool calls and token counts. — [Phoenix release notes 2026-05-15](https://arize.com/docs/phoenix/release-notes/05-2026/05-15-2026-otel-semconv-conversion)
  - Langfuse tracing is OpenTelemetry-based (per the same search results; Langfuse docs not fetched directly).

### Inferences
Concrete items for `gateway.py` and `backends/bedrock.py`:
1. **Timeouts per tier.** Local Qwen gets a wall-clock cap. Haiku gets e.g. `Timeout(60, connect=5)` with `max_retries=3`. Opus RLM root gets a longer timeout, and should stream, because Opus 5.5 always thinks.
2. **Circuit breaker.** After N consecutive failures or throttles from a backend within a window, skip that tier for `cooldown` seconds and log it in `trace`. Today a failing Bedrock step is retried on every question.
3. **Throttling.** Treat `RateLimitError` / 429 as retryable with jittered backoff, honouring `retry-after`. On repeated throttling, fail over from a geographic profile to a `global.` profile, or from the Opus root to Haiku (graceful degradation). Do not escalate further up the cascade on throttling.
4. **Budgets.** Accumulate `usage.input_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens` and `output_tokens` per model. Multiply by a price table and enforce `--max-cost` per session or question. Refuse the RLM path when it would exceed the budget.
5. **Caching question -> SQL.**
   - Start with an exact cache keyed on (normalised question, schema fingerprint, dialect). Semantic caching on embeddings risks returning SQL for a subtly different question, e.g. "2023" vs "2024" or "top 5" vs "top 10". If added, gate it with the same grounding check: the literals in the new question must appear in the cached SQL.
   - Invalidate on schema change.
6. **Tracing.** Emit an OTel span per cascade step. Attributes: route tier, reasons, model, latency, tokens, escalation reason, confidence features. Export to local Phoenix or self-hosted Langfuse. That also produces the labelled data needed to train the learned router in Q1.

### Gaps
- I did not fetch Langfuse, Portkey or Helicone semantic-cache docs, and found no study measuring semantic-cache false-hit rates for text-to-SQL.
- I did not obtain current default Bedrock quotas for Claude Haiku 4.5 and Opus 5.5 (account- and Region-specific; check Service Quotas).

## Q5. Bedrock specifics: prompt caching for Claude, Intelligent Prompt Routing for Anthropic, quotas, Haiku vs Opus pricing

### Takeaway
Prompt caching on Bedrock supports both sqltext models (Haiku 4.5 and Opus 5.5) with up to 4 checkpoints and 5-minute or 1-hour TTLs. The catch is the minimum prefix: **Haiku 4.5 needs at least 4,096 tokens before a cache checkpoint**, while Opus 5.5 needs only 512. A small schema prompt sent to Haiku will therefore silently not cache. Intelligent Prompt Routing does not cover current Claude models. At first-party list prices, Opus 5.5 costs 4x Haiku 4.5 per token ($4/$20 vs $1/$5 per MTok). Bedrock pricing is separate and was not verified.

### Cited Findings
- Bedrock explicit prompt caching table:
  - **Claude Opus 5.5** (`anthropic.claude-opus-5-5`): minimum 512 tokens per checkpoint, max 4 checkpoints, TTL 5 min or 1 h, checkpoints allowed in `system`, `messages` and `tools`.
  - **Claude Haiku 4.5** (`anthropic.claude-haiku-4-5-20251001-v1:0`): minimum **4,096** tokens, 4 checkpoints, TTL 5 min or 1 h.
  - Claude Opus 5 (the repo's refusal-fallback model): minimum 512.

  — [Bedrock prompt caching](https://docs.aws.amazon.com/bedrock/latest/userguide/prompt-caching.html)
- Same page, other caching behaviour:
  - Anthropic models on Bedrock support both implicit (automatic, best-effort) and explicit caching.
  - Simplified cache management: one checkpoint at the end of the static content, and the system looks back about 20 content blocks for the longest match.
  - The checkpoint minimum is cumulative across `tools` -> `system` -> `messages`, and a change to an earlier section invalidates later ones.
  - Placing a checkpoint below the minimum still succeeds, but nothing is cached.
  - The TTL resets on each hit. 1-hour entries must come before 5-minute entries.
  - Cache hits are not deducted from rate limits.
  - Caching works with cross-Region inference, though it "may lead to increased cache writes" under high demand.
  - On-demand only, not batch inference.
  - Response usage fields: `cacheReadInputTokens`/`cacheWriteInputTokens` on Converse; `cache_read_input_tokens`/`cache_creation_input_tokens` on Messages.

  — [Bedrock prompt caching](https://docs.aws.amazon.com/bedrock/latest/userguide/prompt-caching.html)
- Anthropic's platform-availability table: automatic (top-level `cache_control`) caching works on Bedrock, except that the legacy Bedrock integration (Opus 4.6 and earlier) rejects top-level `cache_control` with a 400 and needs explicit breakpoints there. Server-side `fallbacks` are unavailable on Bedrock, so the client-side `BetaRefusalFallbackMiddleware` is the right approach, which matches the repo. — Anthropic claude-api skill reference (`shared/platform-availability.md`)
- Intelligent Prompt Routing supports only Claude 3 Haiku, 3.5 Haiku, 3.5 Sonnet and 3.5 Sonnet v2 among Anthropic models, routes between exactly two models of one family, and is English-optimised. — [Bedrock prompt routing](https://docs.aws.amazon.com/bedrock/latest/userguide/prompt-routing.html)
- First-party list prices (Anthropic, cached 2026-09-25):
  - **Claude Opus 5.5: $4 input / $20 output per MTok, cache reads $0.20/MTok.** The model card says thinking cannot be disabled (adaptive is always on) and default effort is `medium`.
  - **Claude Haiku 4.5: $1 / $5 per MTok**, 200K context. Thinking uses `budget_tokens`, and Haiku 4.5 does not accept `effort`, which the repo's `model_params` already handles.
  - Anthropic states that Bedrock pricing is partner-set and separate.

  — [Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing.md); [AWS Bedrock pricing](https://aws.amazon.com/bedrock/pricing/) (not fetched)
- Cache writes are typically about 1.25x the input price for the 5-minute TTL and reads about 0.1x, per the Anthropic SDK reference. Bedrock says only that writes "can be billed at a rate higher than the standard input token rate". — Anthropic claude-api skill reference; [Bedrock prompt caching](https://docs.aws.amazon.com/bedrock/latest/userguide/prompt-caching.html)
- Global cross-Region profiles cost about 10% less than geographic ones. — [Bedrock cross-Region inference](https://docs.aws.amazon.com/bedrock/latest/userguide/cross-region-inference.html)

### Inferences
- **Prompt caching in sqltext.** The repo sends `system` as a plain joined string with no `cache_control`. Put the static instructions and schema DDL first with a cache breakpoint, and the question after it.
  - Opus 5.5 (RLM root, multi-turn recursive exploration) will cache once the prefix passes 512 tokens. That is a large win, because the RLM resends the schema every turn and Opus thinks on every call.
  - Haiku 4.5 escalations cache only if schema plus instructions reach 4,096 tokens or more. For small DBs the breakpoint is a harmless no-op. For medium and large schemas it saves roughly 90% of the schema input cost on repeated questions within 5 minutes.
  - Use the 1-hour TTL for interactive CLI sessions where questions arrive more than 5 minutes apart.
  - Keep the schema rendering deterministic (sorted tables and columns, no timestamps).
- **Cost ratio.** At list prices, one Opus 5.5 call costs about 4x a Haiku call per token, and Opus always thinks, so output tokens grow. This supports the current design: Haiku for escalations, Opus only for the complex RLM root. Following Anthropic's own advice, also measure "Opus 5.5 at `effort: low`" against "Haiku 4.5" on the complex tier before adding more tiers. Each extra model also splits the cache namespace.
- **Refusal fallback.** It goes to `anthropic.claude-opus-5`, which has a different cache namespace, so a fallback request will be a cache miss. That is acceptable because fallbacks are rare.
- **Intelligent Prompt Routing** is not usable for Haiku 4.5 / Opus 5.5. Revisit only if AWS adds Claude 4.5+ families.

### Gaps
- Actual Bedrock on-demand prices for Haiku 4.5 and Opus 5.5, including cache-write multipliers and the 1-hour TTL premium, were not fetched; check the [Bedrock pricing page](https://aws.amazon.com/bedrock/pricing/).
- Current default RPM/TPM quotas for Haiku 4.5 and Opus 5.5 on Bedrock (geographic vs global profiles) were not found in primary sources.
- I did not confirm whether Intelligent Prompt Routing has added Claude 4.x/5.x families since the doc snapshot. The doc table is the primary evidence that it has not.
