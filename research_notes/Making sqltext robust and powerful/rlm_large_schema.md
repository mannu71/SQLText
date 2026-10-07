# RLMs and Agentic Schema Exploration for Large-Schema Text-to-SQL (as of Oct 2026)

Scope: Recursive Language Models (Zhang, Kraska & Khattab, arXiv 2512.24601) and follow-ups; agentic text-to-SQL for large, enterprise-scale schemas (Spider 2.0, BIRD); schema retrieval at scale; robustness patterns; cost/latency. Each section ends with what it means for sqltext's RLM mode: a root Claude Opus 5.5 on Bedrock, tools list_tables / search_schema / describe_tables / run_probe / solve_subquestion / submit_sql, max depth 2, max 16 steps.

Access date for all leaderboard numbers: 2026-10-07. The Spider 2.0 leaderboard page does not show a submission date for each entry (checked).

---

## 1. RLM paper: REPL environment, recursion depth, sub-call models, costs, results, follow-ups, code

### Takeaway
RLMs keep the long input out of the root model's context. The input sits in a Python REPL variable, and the root model writes code that slices it and calls `llm_query` on the pieces. The main experiments use depth 1 (sub-calls are plain LLMs, not further RLMs), with a cheaper sub-model (GPT-5 root, GPT-5-mini sub-calls). An independent 2026 reproduction found that depth 2 hurts accuracy and multiplies latency. For sqltext, max depth 2 is probably too deep as a default.

### Cited Findings
- **Core idea:** the long prompt is treated "as part of an external environment," and the LM "programmatically examine[s], decompose[s], and recursively call[s] itself over snippets." RLMs handle inputs "up to two orders of magnitude beyond model context windows." — [arXiv 2512.24601 abstract](https://arxiv.org/abs/2512.24601)
- **Headline gains (abstract):** median improvement over GPT-5 baselines of 26% vs compaction, 130% vs CodeAct and 13% vs Claude Code, at "comparable cost." A post-trained RLM-Qwen3-8B improves 28.3% on average over base Qwen3-8B and approaches vanilla GPT-5 on three long-context tasks. — [arXiv 2512.24601](https://arxiv.org/abs/2512.24601)
- **REPL design:**
  - The REPL holds (1) a `context` variable containing the prompt string, (2) an `llm_query` function for recursive calls (handles about 500K characters) and (3) `print` for inspection.
  - The system prompt tells the model it "will only be able to see truncated outputs from the REPL environment, so you should use the query LLM function on variables you want to analyze."
  - Only metadata about stdout (its prefix and length) is appended to the root model's history, which prevents context pollution.
  - Source: [arXiv 2512.24601 (HTML full text)](https://arxiv.org/html/2512.24601)
- **Three design choices that set RLMs apart from other scaffolds:** (1) the root model gets a symbolic handle to the prompt rather than the prompt in context; (2) the final answer is built from REPL state via `FINAL()` / `FINAL_VAR` tags, so output length is unbounded; (3) recursion is programmatic, so code loops can call the LM over many slices and do Ω(|P|) or Ω(|P|²) semantic work. — [arXiv 2512.24601 HTML](https://arxiv.org/html/2512.24601)
- **Recursion depth:** the paper defines depth 0 (REPL with no sub-calls), depth 1 (sub-calls to plain LMs, used in the primary experiments) and depth 2–3 (sub-calls to RLMs). — [arXiv 2512.24601 HTML](https://arxiv.org/html/2512.24601)
- **Sub-call model choice:** the GPT-5 runs use GPT-5 as root and GPT-5-mini for recursive calls. The Qwen3-Coder runs use Qwen3-Coder-480B-A35B for both. — [arXiv 2512.24601 HTML](https://arxiv.org/html/2512.24601)
- **Reported results at depth 1:**

  | Benchmark | GPT-5 RLM | Qwen3-Coder RLM | Context length |
  |---|---|---|---|
  | S-NIAH | 62% | 56% | 2^13–2^20 tokens |
  | BrowseComp+ | 91.3% | 44.7% | 6–11M tokens |
  | OOLONG | 56% | 48% | 131K |
  | OOLONG-Pairs (F1) | 58% | 23.1% | 32K |
  | CodeQA | 62% | 56% | 23K–4.2M |

  These figures come from a WebFetch summary of the paper's tables. Verify exact cells against the PDF before quoting them as final. — [arXiv 2512.24601 HTML](https://arxiv.org/html/2512.24601)
- **Cost:**
  - RLM(GPT-5, depth 1) averages about $0.11–$0.99 per query depending on task.
  - On BrowseComp+ the average is $0.99, against $1.50–$2.75 extrapolated for a base model ingesting the full context.
  - Claim: "outperforming base models and common long-context scaffolds by up to 2× the performance while maintaining comparable or cheaper average token costs."
  - Source: [arXiv 2512.24601 HTML](https://arxiv.org/html/2512.24601)
- **Implementation notes:**
  - Sub-calls are sequential and blocking, not asynchronous, in the paper's implementation.
  - Code is wrapped in ```` ```repl ```` blocks.
  - Iterations per recursion level are capped.
  - Source: [arXiv 2512.24601 HTML](https://arxiv.org/html/2512.24601)
- **Failure modes reported in the paper:**
  - Syntax errors propagate; Qwen3-Coder had more Python errors.
  - Prompts are model-sensitive. Qwen3-Coder needed an added instruction to stop it from making excessive sub-calls.
  - Smaller thinking models (Qwen3-235B) ran out of output tokens mid-trajectory.
  - `FINAL` tags are brittle: models sometimes emit a plan as the final answer, so safeguards were needed.
  - Source: [arXiv 2512.24601 HTML](https://arxiv.org/html/2512.24601)
- **Training RLM-Qwen3-8B:**
  - Data: 1,000 trajectories from Qwen3-Coder-480B on LongBenchPro. Zero-score trajectories were filtered out; 16% had wrong FINAL syntax and 13% had wrong variable references.
  - Training: batch size 64, 300 steps, about 48 H100-hours. The trained model also ran about 3× faster at inference.
  - Key insight: "being an effective sub-call model is roughly similar to being a general purpose reasoning model," so training can focus on the root model's REPL-manipulation skill.
  - Source: [arXiv 2512.24601 HTML](https://arxiv.org/html/2512.24601)
- **Open-source code:** https://github.com/alexzhang13/rlm — [arXiv 2512.24601 HTML](https://arxiv.org/html/2512.24601)
- **2026 follow-up: "Think, But Don't Overthink: Reproducing Recursive Language Models" (arXiv 2603.02615, March 2026)**
  - Setup: DeepSeek v3.2 and Kimi K2 on RULER S-NIAH and OOLONG trec_coarse.
  - Depth 1 helps on complex O(N) tasks: DeepSeek on OOLONG went from 0% to 42.1%.
  - Depth 2 degrades results everywhere: DeepSeek fell to 33.7% on OOLONG, and Kimi K2 fell from 86.6% to 55.0%.
  - On simple retrieval (S-NIAH), the base model scored 100%, RLM depth 1 scored 85–90% and depth 2 scored 70%.
  - Latency: DeepSeek on S-NIAH took 3.6 s at base, 89.3 s at depth 1 and 344.5 s at depth 2. Kimi K2 peaked at 545.5 s per query at depth 2.
  - The authors cite "format collapse," parametric hallucination and excessive verification loops at depth 2. They conclude industrial deployment needs better stopping mechanisms and native RLM training.
  - Source: [arXiv 2603.02615](https://arxiv.org/html/2603.02615v1)

### Inferences
- **Mapping to sqltext:** in RLM terms, sqltext's tool set is a structured "REPL." The schema plays the role of the `context` variable; `solve_subquestion` plays the role of `llm_query`.
  - The paper's most important mechanism is that the root model never sees bulk content, only truncated output and metadata.
  - sqltext should apply the same rule to `describe_tables` and `run_probe` output: return heads, row counts and column stats, and keep full payloads in a server-side handle the root can refer to by ID.
- **Recursion depth:** both the paper (main results at depth 1) and the reproduction (depth 2 hurts) suggest a default of depth 1. Allow depth 2 only when an explicit decomposition heuristic fires, for example a question with ≥2 independent sub-aggregations over disjoint table sets.
- **Sub-call model choice:** a cheap sub-model with a frontier root (GPT-5 → GPT-5-mini) is the paper's own configuration, which supports sqltext's design of routing `solve_subquestion` to a local small model first.
  - The paper's training insight cuts both ways: sub-call quality ≈ general reasoning quality.
  - So the small local model should be given narrow, well-scoped subquestions with an explicit table subset, which sqltext already does.
- **Final-answer brittleness:** the paper's `FINAL` tag problem maps to `submit_sql`. Keeping `submit_sql` as the only terminal action, validated and test-run, is the right analogue. Also reject submissions that are prose or a plan rather than SQL.

### Gaps
- I found no published application of RLMs specifically to text-to-SQL or schema exploration.
- I did not verify the alexzhang13 blog post or an MIT CSAIL write-up directly. The numbers above come from the arXiv paper.
- I did not retrieve exact per-task token counts or variance from the paper's Table 1.

---

## 2. Agentic text-to-SQL systems for large schemas: Spider 2.0 / BIRD systems, tool sets, scores

### Takeaway
On Spider 2.0, which has enterprise schemas often with more than 1,000 columns and BigQuery/Snowflake dialects, scores went from about 17% (o1-preview, late 2024) to above 90% on Spider 2.0-Snow and about 76% on Spider 2.0-Lite by Oct 2026. The leading entries are proprietary agents. The open, documented systems (ReFoRCE, AutoLink) share the same core loop:
1. compress or link the schema,
2. explore with small probe SQL against the live DB,
3. generate several candidates with execution-based self-refinement,
4. vote.

Leaderboard scores for the same method are much higher than the scores in their own papers. Treat cross-source comparisons with care.

### Cited Findings
- **Benchmark sizes:**
  - Spider 2.0-Snow: 547 examples, all on Snowflake.
  - Spider 2.0-Lite: 547 examples across BigQuery, Snowflake and SQLite.
  - Spider 2.0-DBT: 68 examples on DuckDB plus dbt.
  - Databases often contain "over 1,000 columns." o1-preview solved 17.1% of Spider 2.0 tasks. GPT-4o scored 10.1%, compared with 86.6% on Spider 1.0.
  - Source: [Spider 2.0 leaderboard](https://spider2-sql.github.io/)
- **Spider 2.0-Snow, top entries (accessed 2026-10-07, no per-entry dates shown):**
  1. Genloop Sentinel Agent v2 Pro — 96.70
  2. Native mini (usenative.ai) — 96.53
  3. QUVI-3 + Gemini-3-pro-preview (DAQUV) — 94.15
  4. TCDataAgent-SQL with Contextual Scaling Engine (Tencent Cloud) — 93.97
  5. Prism Swarm with Deepthink + Claude-Sonnet-4.5 (Paytm) — 90.49
  6. QUVI-3 + Claude-Opus-4.6 — 86.28
  7. Ask Data with Relational Knowledge Graph (AT&T CDO and RelationalAI) — 86.28
  8. ByteBrain-Agent (ByteDance) — 84.10
  9. Deepinsight Agent (Ant Group) — 83.00

  Source: [Spider 2.0 leaderboard](https://spider2-sql.github.io/)
- **Spider 2.0-Lite, top entries (accessed 2026-10-07):**
  1. Tianqiong Data Agent + GLM 5.2 (Tencent) — 76.23
  2. DecisionX Agent — 74.95
  3. ktx (Kaelio) — 73.67
  4. DivSkill-SQL (Snowflake AI Research × UCSD) — 73.13
  5. SOMA-SQL (Oracle OCI AI Science) — 72.02
  6. Databao Agent (JetBrains) — 69.65
  7. QUVI-2.3 + Claude-Opus-4.5 — 65.81
  8. Claude Code Agent + Sonnet (Nodal Data) — 61.2
  9. ReFoRCE + o3 (Hao AI Lab × Snowflake) — 55.21

  Source: [Spider 2.0 leaderboard](https://spider2-sql.github.io/)
- **Spider 2.0-DBT, top entries:** SignalPilot Agent 65.6; Databao Agent 60.29; Spider-Agent + GPT-5.4 35.29; Spider-Agent + Claude-3.7-Sonnet 14.70. — [Spider 2.0 leaderboard](https://spider2-sql.github.io/)
- **Baselines and open methods on the leaderboard:**
  - Snow: ReFoRCE + o3 62.89; ReFoRCE + o1-preview 31.26; AutoLink + DeepSeek-R1 54.84; Spider-Agent + Claude-4-Sonnet 25.78.
  - Lite: AutoLink + DeepSeek-R1 52.28; Spider-Agent + Claude-Sonnet-4.5 41.86; LinkAlign + DeepSeek-R1 33.09; LinkAlign + DeepSeek-V3 24.86.
  - Source: [Spider 2.0 leaderboard](https://spider2-sql.github.io/)
- **Conflict: paper scores vs leaderboard scores.**
  - The ReFoRCE paper reports 35.83 (Snow) and 36.56 (Lite) with o3, while the leaderboard lists ReFoRCE + o3 at 62.89 (Snow) and 55.21 (Lite). — [ReFoRCE arXiv 2502.00675](https://arxiv.org/abs/2502.00675) vs [leaderboard](https://spider2-sql.github.io/)
  - The AutoLink paper reports 34.92% EX on Lite, while the leaderboard lists 52.28. — [AutoLink](https://arxiv.org/html/2511.17190v1) vs [leaderboard](https://spider2-sql.github.io/)
  - The leaderboard notes that scores may change "as we continually check the accuracy of evaluation metrics," and that an evaluation-suite issue was fixed on 2024-10-29. — [leaderboard](https://spider2-sql.github.io/)
  - The likely cause is later re-evaluation or gold fixes, or updated submissions. I could not confirm which.
- **ReFoRCE (Hao AI Lab × Snowflake):**
  - Components: (a) database compression by pattern-based table grouping plus LLM-guided schema linking; (b) self-refinement across dialects; (c) majority-vote consensus; (d) iterative column exploration with execution feedback.
  - Compression: grouping tables with shared prefixes/suffixes and keeping one representative cut the GA360 DDL from more than 50 MB to under 2 MB (96% compression).
  - Schema linking is used only when the schema exceeds 50K tokens. Recall is 98.42% on Lite and 98.47% on Snow.
  - Refinement and voting: up to 5 refinement iterations; k = 8 candidates at temperature 1.0; a candidate counts as high-confidence only if it has a unique maximum vote.
  - Column exploration runs only on low-confidence (tied) cases, about 100 of 547. It allows at most 10 probe SQL queries, each with LIMIT 20, ordered "from simple to complex."
  - Cost per example: about 1.69 LLM calls and 15.44K tokens without exploration; about 10 LLM calls, 12 DB calls and 18K tokens on hard examples.
  - Ablations: removing compression costs −3.65 EX (Snow) and −3.48 EX (Lite); removing column exploration costs −2.56 / −2.37. Providing gold tables adds only +0.37 EX, but +6.95 EX@8.
  - Model scaling: o3 35.83 / 36.56; o4-mini 29.80 / 31.99; GPT-4o 20.84 / 21.76.
  - Source: [ReFoRCE arXiv HTML](https://arxiv.org/html/2502.00675)
- **AutoLink (AAAI 2026):** schema linking as an agent loop with four actions:
  - `@explore_schema`: runs exploratory SQL on the live DB for metadata and value distributions.
  - `@retrieve_schema`: semantic search over a vector store of columns; the agent can rewrite the query and infer column names or concepts.
  - `@verify_schema`: runs a minimal SQL query and turns DB errors into signals about missing schema.
  - `@add_schema`: commits elements to the linked set.
  - Other details:
    - Initial schema: approximate-nearest-neighbour (ANN) top-50 or top-100 over a column-level vector index (BGE-large-en-v1.5), where each column is represented by its name, parent table, type and description.
    - Limits: at most 10 turns; on average 5.79 turns are used, with diminishing returns beyond 4–6.
    - Tokens: 21.2K per example on Spider 2.0-Lite (vs 81.1K for ReFoRCE and 171.9K for SQL-to-Schema); 8.0K on BIRD-dev.
    - Strict recall: 91.2% on Spider 2.0-Lite (SQL-to-Schema 64.0%, RSL-SQL 52.0%, LinkAlign 36.4%); 97.4% on BIRD-dev (CHESS 89.7%).
    - EX: 34.92% on Lite (DeepSeek-R1); 68.71% on BIRD-dev (Gemini-1.5-Pro), vs CHESS 68.31%.
    - On schemas with more than 3,000 columns, AutoLink keeps about 90% recall while baselines fall below 40%.
  - Source: [AutoLink arXiv 2511.17190](https://arxiv.org/html/2511.17190v1); code at [github.com/wzy416/AutoLink](https://github.com/wzy416/AutoLink)
- **BIRD:**
  - CHESS reported 65% (dev) and 66.69% (test) EX at submission. — [CHESS arXiv 2405.16755](https://arxiv.org/html/2405.16755v1)
  - Alpha-SQL (Monte Carlo Tree Search, MCTS; zero-shot, 32B open model, no fine-tuning) reached 69.7% on BIRD-dev, +2.5 over the best prior zero-shot GPT-4o approach. — [Alpha-SQL arXiv 2502.17248](https://arxiv.org/pdf/2502.17248)
  - As reported by a secondary aggregator for 2026-07-10, the BIRD test top was AskData + GPT-4o 81.95%, then Agentar-Scale-SQL 81.67%; human performance is 92.96%. — [beancount.io research log (secondary)](https://beancount.io/de/bean-labs/research-logs/2026/06/06/bird-benchmark-text-to-sql-real-database-gap); Agentar-Scale-SQL paper: [arXiv 2509.24403](https://arxiv.org/pdf/2509.24403)
  - Annotation errors are reported to be widespread in text-to-SQL benchmarks, and re-ranking under corrected labels changes the order. — [Kang, "Pervasive Annotation Errors Break Text-to-SQL Benchmarks and Leaderboards"](https://ddkang.substack.com/p/pervasive-annotation-errors-break)

### Inferences
- The tool set of the documented open SOTA (AutoLink) is very close to sqltext's:

  | AutoLink | sqltext |
  |---|---|
  | `retrieve_schema` | `search_schema` |
  | `explore_schema` | `run_probe` |
  | `verify_schema` | partly `submit_sql` validation |
  | `add_schema` | no equivalent |

  The two things sqltext lacks are:
  - (a) a **vector index over column-level documents** (name + table + type + description). sqltext's search is keyword-only.
  - (b) an explicit **working-set / "add_schema" state** that the agent builds and that sub-calls and final generation consume, instead of re-describing tables.
- ReFoRCE's evidence suggests sqltext should add:
  - (a) **table-family compression** in `list_tables`. Group sharded or date-suffixed tables such as `events_20240101..20241231` into one entry with a pattern and count; this matters a lot for BigQuery-style datasets like GA360.
  - (b) **candidate voting with execution-result equivalence** before `submit_sql`.
  - (c) **gating expensive exploration on low confidence** rather than always exploring.
- The leaderboard top is dominated by agents with heavy context engineering (knowledge graphs, "contextual scaling engines"). The AT&T/RelationalAI "Relational Knowledge Graph" entry reaches 86.28 on Snow, which supports investing in a structured schema graph.

### Gaps
- I did not retrieve primary details on the Spider-Agent tool set, the original DIN-SQL and MAC-SQL agents, LinkAlign's method, SQL-of-Thought or DivSkill-SQL, so they are not described here.
- Most leaderboard leaders (Genloop, Native, QUVI, TCDataAgent) have no public papers I could verify.
- I could not determine per-entry submission dates or the exact reason paper scores differ from leaderboard scores.

---

## 3. Schema retrieval at scale: embeddings, hierarchy, compression, documentation, join paths

### Takeaway
What works at 1,000–3,000+ columns:
- column-level embedding retrieval with rich text (name + table + type + description) and a generous initial top-k (50–100);
- followed by agentic expansion and verification against the live DB;
- plus collapsing sharded or repetitive tables;
- plus value or literal indexes with fuzzy and hybrid retrieval.

Industrial systems add curated semantic layers (synonyms, verified queries, business definitions) and search over existing assets such as dashboards, notebooks and prior queries.

### Cited Findings
- **Column-level embedding index:** AutoLink embeds every column as "column name, parent table's name, data type, and description" with BGE-large-en-v1.5 and takes an ANN top-k "relatively large (e.g., 50 or 100)" as the start set, which the agent then expands. — [AutoLink](https://arxiv.org/html/2511.17190v1)
- **Schema compression:** grouping tables by name pattern reduced GA360 DDL from >50 MB to <2 MB (−96%). LLM schema linking is applied only above 50K tokens. Removing compression costs about 3.5 EX points. — [ReFoRCE](https://arxiv.org/html/2502.00675)
- **Value and literal retrieval:**
  - Cortex Analyst indexes high-cardinality or messy columns (e.g., `MENU_ITEM`) with Cortex Search, using hybrid retrieval: keyword with synonym expansion, plus vector, then re-ranking.
  - At question time it fetches the most similar literals and passes them to the SQL generator, which handles cases like "chicken biryani" vs "Chicken Biriyani" that sample values miss.
  - Source: [Snowflake engineering blog, 2024-11-12](https://www.snowflake.com/en/engineering-blog/cortex-analyst-cortex-search-integration)
- **Curated semantic layer:**
  - Cortex Analyst relies on semantic model files with synonyms, verified queries and metrics.
  - Its Context Enrichment Agent "retrieves verified queries and relevant database literals."
  - It generates SQL in two steps: first against a simplified logical schema, then post-processes to the physical schema.
  - Source: [Snowflake "Cortex Analyst behind the scenes", 2024-08-14](https://snowflake.com/engineering-blog/snowflake-cortex-analyst-behind-the-scenes)
- **Knowledge search over enterprise assets:**
  - Databricks Genie builds a search index from "workspace tables, notebooks, dashboards, documents, and files," giving "up to 40% improvement" on table-discovery benchmarks.
  - Genie Code is integrated with Unity Catalog: table semantics, column semantics, lineage and popular assets.
  - Sources: [Databricks blog "Pushing the frontier of data agents with Genie", 2026-05-08](https://www.databricks.com/blog/pushing-frontier-data-agents-genie); [Medium practice guide (secondary)](https://medium.com/dbsql-sme-engineering/databricks-genie-code-full-practice-guide-and-examples-f3bcb13596f2)
- **Knowledge graph of schema relations:** "Ask Data with Relational Knowledge Graph" (AT&T CDO and RelationalAI) scores 86.28 on Spider 2.0-Snow. — [Spider 2.0 leaderboard](https://spider2-sql.github.io/)
- **Recall matters more than precision at large scale:** AutoLink found EX correlates with strict recall ("higher SRR tends to exhibit higher EX"). Baselines' recall collapses below 40% at >3,000 columns. — [AutoLink](https://arxiv.org/html/2511.17190v1)

### Inferences
- Upgrade `search_schema` from keyword matching to **hybrid search**: BM25 over names and comments, plus embedding search over column documents, plus a separate **value index** (fuzzy or trigram) for categorical columns. Return a ranked list with table.column, type, comment and match reason, capped at about 50 items.
- Precompute a **table-family map** (regex on suffixes such as dates, shard numbers or regions) and an **FK / inferred-join graph**. Infer joins from name and type matches plus value-overlap sampling.
  - Expose the graph as a tool, e.g. `find_join_path(tables) -> shortest join path with keys`.
  - This replaces many probe steps. It is my inference from the KG-based leaderboard entries; I found no ablation for it.
- Ingest column comments, dbt docs, verified or past queries, and dashboard SQL as retrievable "knowledge." Both Cortex Analyst (verified queries) and Genie (prior assets) report large gains from this.

### Gaps
- I found no sourced, quantitative study (in this session) on hierarchical database → table → column retrieval versus flat column retrieval.
- I found no sourced, quantitative evaluation of FK-graph join-path discovery on Spider 2.0.
- LinkAlign's multi-database retrieval approach was not retrieved in detail.

---

## 4. Robustness patterns: step budgets, error handling, context blowup, caching, verification, ambiguity

### Takeaway
The documented patterns are:
- small, capped exploration (≤10 probes, LIMIT 20; ≤10 agent turns, with gains saturating at 4–6);
- execution-feedback refinement capped at about 5 iterations;
- multi-candidate voting with a high-confidence rule;
- converting DB errors into diagnostic signals;
- keeping bulk output out of the root model's context.

Production systems add an upfront classifier that rejects or clarifies ambiguous questions, and a compiler-based error-correction loop.

### Cited Findings
- **Step and turn budgets:**
  - AutoLink caps at 10 turns. The average is 5.79, and accuracy gains diminish beyond 4–6 turns.
  - Token use stays "nearly unchanged" as max turns increase, which suggests early convergence.
  - Source: [AutoLink](https://arxiv.org/html/2511.17190v1)
- **Probe limits:** ReFoRCE allows at most 10 exploration queries with LIMIT 20 rows, applied only to low-confidence cases, and at most 5 refinement iterations. — [ReFoRCE](https://arxiv.org/html/2502.00675)
- **Errors as signals:** AutoLink's `@verify_schema` runs minimal SQL and converts database errors into "diagnostic signals about missing elements." — [AutoLink](https://arxiv.org/html/2511.17190v1)
- **Compiler-validated correction loop:** Cortex Analyst's Error Correction Agent validates candidates with Snowflake's SQL compiler and loops back to fix errors. A Synthesizer Agent picks the final SQL from multiple candidates produced by different LLMs. — [Snowflake engineering blog](https://snowflake.com/engineering-blog/snowflake-cortex-analyst-behind-the-scenes)
- **Ambiguity handling:**
  - Cortex Analyst's Classification Agent labels each question as ambiguous, non-data, non-SQL or answerable.
  - It rejects ambiguous questions "upfront, rather than responding with potentially misleading answers," and offers "a list of similar questions that can be answered confidently."
  - Source: [Snowflake engineering blog](https://snowflake.com/engineering-blog/snowflake-cortex-analyst-behind-the-scenes)
  - A multi-agent setup with LLM-as-a-Judge "will not query data under ambiguous assumptions." — [Snowflake search snippet (snowflake.com/?p=458942)](https://snowflake.com/?p=458942); I did not fetch this page in full.
- **Parallel trajectories:** Genie samples "multiple trajectories" and aggregates findings to compensate for the lack of verifiable tests in data analysis. The blog acknowledges latency and token trade-offs. — [Databricks Genie blog](https://www.databricks.com/blog/pushing-frontier-data-agents-genie)
- **Consensus voting:** ReFoRCE uses k = 8 candidates and majority vote on execution results. A unique maximum is required for high confidence; ties trigger column exploration. — [ReFoRCE](https://arxiv.org/html/2502.00675)
- **Keeping the context clean:** RLM appends only stdout metadata (prefix and length) to history, and the model is told it sees only truncated output. — [arXiv 2512.24601 HTML](https://arxiv.org/html/2512.24601)
- **Recursion overthinking:** deeper recursion produces "excessive verification loops" and format collapse, and needs better stopping mechanisms. — [arXiv 2603.02615](https://arxiv.org/html/2603.02615v1)

### Inferences
These are concrete design changes for sqltext.
1. **Budgets by phase rather than one 16-step pool:**
   - discovery ≤6 steps;
   - probes ≤10, LIMIT 20;
   - sub-questions ≤3;
   - refine ≤3–5 after the first `submit_sql` failure.
   
   Force `submit_sql` when 2 steps remain.
2. **Structured tool errors:** return `{error_type: unknown_column | unknown_table | syntax | timeout | permission, hint, nearest_names[]}` and do not count the first occurrence of each error type against the budget. Fuzzy-match unknown identifiers against the schema index, as in AutoLink's verify-as-signal.
3. **Context hygiene:**
   - Truncate `run_probe` output to 20 rows and about 2K characters, with column stats.
   - Have `describe_tables` return compressed DDL (columns + types + comments + key hints) and collapse table families.
   - Keep a running "working schema" note; after N steps, summarize older tool results into it.
4. **Caching:** memoize `search_schema`, `describe_tables` and probe results per (database, schema version) across questions. Keep the schema index and the value index on disk.
5. **Verification before submit:**
   - Generate 2–5 candidates (cheap model or higher temperature), execute them, and vote by result-set equivalence.
   - When `solve_subquestion` returns results, check sub-answer consistency: row counts and key overlap with the final query's CTEs.
   - Run sanity checks: empty result, all-NULL columns, unexpected fan-out from joins (duplicate primary keys), and an expected answer shape matching the question.
6. **Ambiguity:** add an upfront classify step. If the question is ambiguous and the session is interactive, return clarifying options. Otherwise state the assumption in the output and choose the most common interpretation, optionally using the parallel-interpretations approach from Genie.

### Gaps
- I found no sourced quantitative evidence on the effect of conversation summarization or compaction specifically for SQL agents. RLM's comparison against compaction (+26% median for RLM) is the nearest evidence.
- No paper retrieved here measures how much a clarifying-question step improves accuracy.

---

## 5. Cost/latency trade-offs and cheaper models for sub-calls

### Takeaway
Use a strong root with a cheap sub-model (RLM: GPT-5 + GPT-5-mini). Gate expensive steps (exploration, voting) on low confidence, as ReFoRCE does for about 100 of 547 cases. Keep recursion shallow. Production systems (Genie, Cortex Analyst) use multi-LLM designs, with different models for planning, search, generation and judging.

### Cited Findings
- **RLM cost:**
  - GPT-5 root with GPT-5-mini sub-calls costs $0.11–$0.99 average per query.
  - This is comparable to or cheaper than base models that ingest the full context ($1.50–$2.75 extrapolated on BrowseComp+).
  - Source: [arXiv 2512.24601 HTML](https://arxiv.org/html/2512.24601)
- **Latency at depth:** going from depth 1 to depth 2 raised S-NIAH latency from 89.3 s to 344.5 s (DeepSeek v3.2). Kimi K2 reached 545.5 s per query at depth 2. — [arXiv 2603.02615](https://arxiv.org/html/2603.02615v1)
- **Token efficiency of agentic linking:** AutoLink uses 21.2K tokens per Spider 2.0-Lite example, vs 81.1K for ReFoRCE and 171.9K for SQL-to-Schema. — [AutoLink](https://arxiv.org/html/2511.17190v1)
- **ReFoRCE cost profile:** about 1.69 LLM calls and 15.4K tokens per easy example; about 10 LLM calls, 12 DB calls and 18K tokens on hard examples. Exploration is triggered only on ties. — [ReFoRCE](https://arxiv.org/html/2502.00675)
- **Genie multi-LLM design:** different LLMs handle planning, search sub-agents, code generation and judges, optimized with GEPA across Opus, GPT and Gemini variants. Accuracy rose from 32% (a leading coding agent) to more than 90% on an internal benchmark, "while also significantly reducing the costs and latency." — [Databricks blog, 2026-05-08](https://www.databricks.com/blog/pushing-frontier-data-agents-genie)
- **Cortex Analyst** uses multiple LLMs (Meta Llama, Mistral) as SQL-generation agents, with a synthesizer selecting among them. — [Snowflake engineering blog](https://snowflake.com/engineering-blog/snowflake-cortex-analyst-behind-the-scenes)
- **Small models in RLMs:** RLM-Qwen3-8B, after about 48 H100-hours of training on 1,000 filtered trajectories, improved 28.3% median over base Qwen3-8B and ran about 3× faster. — [arXiv 2512.24601 HTML](https://arxiv.org/html/2512.24601)
- **Model strength matters for the root:** ReFoRCE drops from 35.83 (o3) to 29.80 (o4-mini) to 20.84 (GPT-4o) on Snow. — [ReFoRCE](https://arxiv.org/html/2502.00675)

### Inferences
- Keep Opus 5.5 as the root planner and final SQL writer. Route the following to the local small model, or a Haiku-class Bedrock model as fallback:
  - `search_schema` query rewriting;
  - candidate generation for voting;
  - sub-question SQL;
  - result-sanity judging.
- Escalate a sub-question to a stronger model only when its validation fails, which matches sqltext's existing router-first design.
- Make the expensive path conditional: run the single-shot or router path first. Enter the RLM / agent loop only when:
  - (a) the schema is larger than the context threshold (ReFoRCE uses 50K tokens),
  - (b) the router's candidates disagree, or
  - (c) validation or execution fails.
- Bound worst-case latency: run sub-calls in parallel where independent (the RLM paper's sequential sub-calls are a known limitation), set per-probe timeouts, and use Bedrock prompt caching for the stable system prompt and schema summary.
- A trajectory dataset of successful sqltext RLM runs could later fine-tune the local model as a better sub-caller or even root. This follows the RLM-Qwen3-8B recipe; its value for SQL is untested.

### Gaps
- I did not find per-query dollar costs for Spider 2.0 leaderboard agents.
- I did not find published Bedrock-specific cost/latency figures for Opus 5.5 with prompt caching in this session.
- The Databricks "32% → 90%" figure is on an undisclosed internal benchmark and cannot be independently verified.
