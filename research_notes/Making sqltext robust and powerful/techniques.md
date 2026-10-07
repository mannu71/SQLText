# State-of-the-art text-to-SQL techniques for small local models (0.5B–1.5B, CPU) and LLM pipelines (as of Oct 2026)

Context assumed: sqltext already does keyword/token-overlap schema linking + FK neighbours, compact CREATE TABLE DDL with up to 3 sampled values per text column, greedy T=0 decoding, sqlglot parse validation, up to 2 DB-error self-correction retries, and a literal "grounding check" that escalates to a bigger model.

Benchmarks: "BIRD dev" = BIRD development set (1,534 Qs, execution accuracy EX, normally with the "evidence"/external-knowledge hints); "Spider dev/test" = Spider 1.0 EX unless stated (EM = exact-set match, TS = test-suite accuracy).

---

## 1. Schema linking: what works best, and what runs on tiny models / small embeddings

### Takeaway
Schema linking helps in every pipeline that reports an ablation (about +2 to +6 EX points on BIRD dev). The way to lose accuracy is low recall: when a needed column gets filtered out, the generator can't recover it. For a CPU-bound setup, the best-evidenced cheap options are (a) recall-oriented filtering that always keeps PK/FK columns, and (b) a small trained classifier or an embedding ranker (CodeS/RESDSQL style). LLM-based multi-step selection (CHESS) works best but costs several LLM calls per question.

### Cited Findings
- **CHESS** (Stanford, 2024) Schema Selector runs three LLM tools in sequence: `filter_column` (per-column relevance, always keeping PK/FK), `select_tables`, then `select_columns`. Ablation on BIRD dev: removing column filtering costs −2.72, table selection −6.12, column selection −5.44, and entity & context retrieval −4.76 EX points. The schema selector cuts LLM tokens about 5×. CHESS reports 71.10% on BIRD test with a high compute budget and about 83% fewer LLM calls than comparable methods. With open-source models only (fine-tuned DeepSeek generator, Llama-3-70B for the other agents) it reaches 61.5% on BIRD dev — [CHESS arXiv 2405.16755](https://arxiv.org/html/2405.16755)
- **CodeS** schema filter: a trained "schema item classifier" scores tables and columns against the question. It keeps the top-k1 tables and top-k2 columns per table (k1=6, k2=10 for SFT; k1=5, k2=6 for few-shot). Few-shot (3-shot) ablation: without the schema filter, CodeS-1B on BIRD dev drops 25.42 → 23.53 (−1.89) and on Spider dev TS 57.4 → 55.0 (−2.4). For CodeS-7B, removing the filter cost −0.8 on Spider TS but was +0.39 on BIRD, and the paper notes slower generation from longer inputs — [CodeS arXiv 2402.16347](https://arxiv.org/html/2402.16347)
- **RESDSQL** (AAAI 2023) decouples schema linking from skeleton parsing. A ranking-enhanced encoder (a cross-encoder that classifies schema items) passes only relevant tables/columns to a T5 decoder, which generates a SQL skeleton first. I did not retrieve its numeric results — [RESDSQL arXiv 2302.05965](https://arxiv.org/abs/2302.05965)
- **DIN-SQL**: removing its LLM schema-linking module drops CodeX-Davinci on Spider dev from 69.9 to 65.9 EX — [DIN-SQL arXiv 2304.11015](https://arxiv.org/html/2304.11015)
- **MCS-SQL**: recall-oriented schema linking uses 3 table prompts and 3 column prompts with n=20 samples each, merged by set union to maximise recall. Ablation on BIRD dev: schema linking +2.1, sample table contents +2.4. Their error analysis attributes only about 20% of failures to schema-linking errors — [MCS-SQL arXiv 2405.07467](https://arxiv.org/html/2405.07467)
- **"The Death of Schema Linking?"** (2024): newer strong LLMs "are adept at utilizing relevant schema elements during generation even in the presence of large numbers of irrelevant ones", and "imperfect schema linking can often exclude required columns". With the full schema plus augmentation, selection and correction they reached 71.83% on BIRD — [arXiv 2408.07702](https://arxiv.org/abs/2408.07702)
- **XiYan-SQL M-Schema**: a semi-structured schema format (types, PK marks, example values). Across four LLMs it beat plain DDL by an average of +2.03 points — [XiYan-SQL arXiv 2411.08599](https://arxiv.org/html/2411.08599)
- **OmniSQL** prompt format (the strongest open text-to-SQL models, built on Qwen2.5-Coder): CREATE TABLE DDL with column descriptions, representative values and question-relevant values, all as per-column comments — [OmniSQL arXiv 2503.02240](https://arxiv.org/html/2503.02240)
- **Static embeddings for CPU**: model2vec `potion-base-8M` is about 8 MB and `potion-base-32M` about 30 MB. The base package's only major dependency is numpy, and inference is claimed to be "up to 500 times faster on CPU" than the source sentence-transformer. Usage: `StaticModel.from_pretrained(...).encode([...])` — [model2vec GitHub](https://github.com/MinishLab/model2vec)

### Inferences
- The "death of schema linking" result applies to strong long-context LLMs. A 0.5B–1.5B model has a weak context budget and is easily distracted, so the CodeS-style filtering evidence is the better guide at small scale. Even there, the CodeS-7B BIRD ablation shows filtering can be neutral, so tune for recall, not precision.
- A cheap upgrade path for sqltext's keyword linker:
  1. Score each column with max(token overlap, model2vec/bge-small cosine between the question and "table.column + description + sample values").
  2. Keep the top-k tables (about 5–6) and the top-k columns per table (about 6–10).
  3. Always keep PK/FK and join-path columns.
  4. Also keep any column whose values matched in value retrieval (section 2).
  5. Use the full schema for small databases (for example, under about 30 columns), where filtering buys little.
- For the escalation (bigger-LLM) path, pass the full or lightly filtered schema rather than the tiny-model filter's output, so that a filter miss doesn't propagate.

### Gaps
- I found no head-to-head published numbers for schema linking with bge-small, all-MiniLM or model2vec embeddings on BIRD/Spider. Recommending them rests on their size and speed, not on direct text-to-SQL ablations.
- I didn't retrieve RESDSQL's numbers, or its cross-encoder's recall@k.

---

## 2. Value retrieval / entity matching (LSH, BM25, fuzzy)

### Takeaway
Retrieving database values that match question phrases is worth roughly +2.5 to +4.8 EX points on BIRD dev in three independent ablations (CHESS, CHASE-SQL, CodeS). On Spider it does almost nothing, because Spider literals mostly appear verbatim in the question. It is cheap: an offline index plus string similarity, and no LLM calls if keywords are extracted heuristically.

### Cited Findings
- **CHESS** value retrieval runs in three stages:
  1. An LLM extracts keywords and keyphrases (few-shot).
  2. A MinHash-LSH index over all unique DB values, built offline, returns candidates. This cut lookup from "5 minutes to 5 seconds" versus naive scanning.
  3. Candidates are re-ranked by embedding cosine (text-embedding-3-small, top-10), then by edit distance, keeping the single best value per keyword-column pair.
  Removing entity & context retrieval cost −4.76 on BIRD dev — [CHESS](https://arxiv.org/html/2405.16755)
- **CHASE-SQL** also uses LSH-based value retrieval. Removing it cost −2.92 EX on BIRD dev — [CHASE-SQL arXiv 2410.01943](https://arxiv.org/html/2410.01943)
- **CodeS** "coarse-to-fine" value retriever: a BM25 index (Lucene) pulls hundreds of candidate values from the whole DB, then longest-common-substring (LCS) matching scores them precisely. Ablation, 3-shot: CodeS-1B BIRD dev 25.42 → 22.23 (−3.19) without it; CodeS-7B BIRD −2.48; Spider TS only −0.2. A human evaluation cites its "crucial role in generating SQL query predicates" — [CodeS](https://arxiv.org/html/2402.16347)
- **MCS-SQL**: adding sample table contents gave +2.4 on BIRD dev — [MCS-SQL](https://arxiv.org/html/2405.07467)
- **OmniSQL** puts "question-relevant values" in its prompt as column comments, so open SOTA models are trained to expect them — [OmniSQL](https://arxiv.org/html/2503.02240)

### Inferences
- sqltext's "3 sampled distinct values per text column" helps the model learn value formats, but it rarely surfaces the value the question actually refers to. Add a question-conditioned value retriever:
  - **Offline**: for each text column with fewer than N distinct values (cap it, say 50k per column), store the distinct values. Index them with `datasketch` MinHashLSH (character 3-gram shingles) or with `rank_bm25` / SQLite FTS5.
  - **Online**:
    1. Extract n-grams or quoted spans and capitalised spans from the question (no LLM).
    2. Query the index.
    3. Re-rank with `rapidfuzz` ratio or LCS.
    4. Inject the top matches as `-- matched values: 'X'` comments on their columns, and force-include those columns in the schema.
- This feeds the existing grounding check directly. Once the retriever has mapped question values to DB values (for example "NY" → 'New York'), a SQL literal that equals a retrieved DB value should count as grounded, which would cut needless escalations.
- SQLite FTS5 (`trigram` tokenizer) is a zero-dependency option for SQLite targets. For other dialects, a pure-Python MinHash/BM25 index persisted to disk works.

### Gaps
- I found no published ablation that isolates LSH vs BM25 vs fuzzy matching against each other. The available numbers compare "with" vs "without" a retriever, each in its own pipeline.
- I found no numbers for value retrieval with 0.5B–1.5B models specifically. CodeS-1B (−3.19) is the closest.

---

## 3. Candidate generation + selection (self-consistency, execution voting, selection agents)

### Takeaway
Voting over execution results is the most reliable inference-time gain. It is large for weak or untuned models (+10 points for Qwen2.5-Coder-7B on BIRD dev at 8 samples) and smaller for strong fine-tuned ones (+2 to +2.5). Trained pairwise or selection models beat majority voting by about 2.7 to 4.2 points, but they need a fine-tuned selector. At 0.5B–1.5B, sampling with voting does help: about +12 to +15 points at 8 samples on Spider dev in the one controlled 2026 study. That study found beam search beats sample+vote at equal budget, and found that moving up one model size beats spending 8× compute on the same model.

### Cited Findings
- **OmniSQL** (greedy → majority vote over 8 samples at T=0.8, voting on execution results), BIRD dev:

  | Model | Greedy | Majority vote (8) | Gain |
  |---|---|---|---|
  | Qwen2.5-Coder-7B-Instruct (base) | 50.9 | 61.3 | +10.4 |
  | OmniSQL-7B | 63.9 | 66.1 | |
  | OmniSQL-14B | 64.2 | 65.9 | |
  | OmniSQL-32B | 64.5 | 67.0 | |

  On Spider dev, voting slightly hurt OmniSQL-7B (81.2 → 79.3) — [OmniSQL](https://arxiv.org/html/2503.02240)
- **Grammar-constrained small models**, Spider dev (Qwen2.5-Instruct, 4-bit; Chermsirivatana & MacCormick, Aug 2026):

  | Model | Unconstrained greedy | Constrained sample+vote, budget 1 → 8 | Constrained beam, budget 1 → 8 |
  |---|---|---|---|
  | 0.5B | 0.135 | 0.113 → 0.248 | 0.139 → 0.245 |
  | 1.5B | 0.326 | 0.299 → 0.450 | 0.351 → 0.505 |
  | 3B | 0.477 | | 0.445 → 0.562 |
  | 7B | 0.643 | | 0.600 → 0.654 |

  Other findings from the same study:
  - Beam search beat sample+vote at matched budget in 11 of 16 configurations (p<0.05), and in none did sampling win.
  - Gains from extra budget shrink with model size: about 0.15 at 1.5B vs about 0.05 at 7B.
  - An 8× inference budget closed only 50–76% of the gap to the next model size.
  — [arXiv 2608.25761](https://arxiv.org/html/2608.25761)
- **SLM-SQL** (Jul 2025): SFT + RL (GRPO-style) on Qwen2.5-Coder-0.5B/1.5B and other 0.5–1.5B models, with data derived from SynSQL-2.5M. It adds "corrective self-consistency" (CSC): a merge/revision pass over the voted candidates. The abstract reports BIRD dev EX of 56.87% (0.5B) and 67.08% (1.5B), with an average +31.4 points across five SLMs. The paper's table, as I extracted it, gives higher figures: 0.5B SFT+RL 70.60 SC / 72.08 CSC; 1.5B 75.15 SC / 76.72 CSC; untrained base 42.13 (0.5B) and 63.54 (1.5B) with SC. These conflict with the abstract and look implausibly high for BIRD dev, so treat the table figures as unverified and possibly mis-extracted. Other details: SC used 16 samples, the peak used 64 generation + 8 revision samples, CSC adds about +1.5 to +3 points over SC, and the 16-sample run on BIRD dev took about 0.83 h on one RTX 4090D — [SLM-SQL arXiv 2507.22478](https://arxiv.org/abs/2507.22478), [HTML](https://arxiv.org/html/2507.22478)
- **CHASE-SQL** (Gemini; three generators × 7 candidates = 21, sampled at T=0.5 and 1.8), BIRD dev:
  - Self-consistency 68.84 vs pairwise selection agent 73.01 (+4.17).
  - Oracle upper bound across all candidates is 82.79, a 9.78-point gap to the selector, which shows selection is the bottleneck.
  - The fine-tuned binary selector reached 71.01% pairwise accuracy (Gemini-1.5-flash), against 63.98% for untuned Gemini-1.5-pro.
  — [CHASE-SQL](https://arxiv.org/html/2410.01943)
- **XiYan-SQL**: multi-generator ensemble (fine-tuned generators + ICL generator + refiner) with a fine-tuned selection model. Selection beats self-consistency by about +2.74 on BIRD dev; reported 75.63% on BIRD and 89.65% on Spider test — [XiYan-SQL](https://arxiv.org/html/2411.08599)
- **MCS-SQL**: 5 prompts with different few-shot sets × n=20 samples, then a confidence filter, then LLM multiple-choice selection with voting. BIRD test 65.5%, Spider test 89.6%. Ablation on BIRD dev: MCS with a single prompt +2.1, multiple prompts a further +1.3. CHESS characterises MCS-SQL as about 100 LLM calls per question — [MCS-SQL](https://arxiv.org/html/2405.07467), [CHESS](https://arxiv.org/html/2405.16755)
- **DAIL-SQL**: GPT-4 on Spider dev, 82.4 → 86.6 with self-consistency, "very time consuming" — [DAIL-SQL arXiv 2308.15363](https://arxiv.org/html/2308.15363)
- **Alpha-SQL** (MCTS over SQL-construction actions, zero-shot, 32B open model with no fine-tuning): 69.7% on BIRD dev — [Alpha-SQL arXiv 2502.17248](https://arxiv.org/abs/2502.17248)

### Inferences
- For a CPU-bound 0.5B–1.5B model, the cheapest high-ROI pattern is a confidence-gated vote, sketched below. Most easy questions stop at one sample, so the average cost stays near 1–2 generations.
  1. Generate greedily.
  2. Only if a cheap signal flags doubt (sqlglot or execution error, grounding-check failure, empty result, or low mean token logprob — `logprobs=` is available in llama-cpp-python), draw k=3–5 extra samples (T≈0.7–0.8, or vary the prompt with different few-shots or schema order).
  3. Execute every candidate with a row limit and timeout.
  4. Cluster candidates by a normalised result set (sorted rows, rounded floats).
  5. Pick the largest cluster, breaking ties by shortest SQL or highest logprob.
- Given the 2026 result (beam > sampling under grammar constraints, and a bigger model > more samples), the existing "escalate to bigger model" path is probably a better use of compute than large k on the 0.5B model. Prefer a 1.5B base model over a 0.5B one with voting where RAM allows.
- Execution-result clustering also gives a natural escalation trigger: low agreement (for example, the top cluster under 50% of samples) means "escalate".

### Gaps
- I found no CPU latency measurements for sampling or beam search with llama.cpp at 0.5B–1.5B on text-to-SQL.
- The SLM-SQL table vs abstract discrepancy is unresolved.
- I found no evidence on trained pairwise selectors at 0.5B–1.5B scale.

---

## 4. Constrained / grammar-guided decoding

### Takeaway
Constrained decoding has strong evidence for fine-tuned seq2seq models (PICARD: +5 to +10 EX on Spider dev). For modern instruction-tuned LLMs the evidence is mixed. Full SQL grammars are often incomplete (no `IN (...)` lists, `IS NOT NULL`, outer joins) and can hurt: −3 to −4 points at 3B/7B, and −20 points for Llama-70B on Spider. Lighter, schema-aware identifier constraints (TTD-SQL) keep accuracy, raise the executable-SQL rate by up to +17.7 points on BIRD, and speed up decoding about 7–20%. The most practical approach for sqltext is a light constraint: restrict identifiers to real tables/columns. Avoid a full SQL grammar.

### Cited Findings
- **PICARD** (incremental parsing that rejects inadmissible tokens during beam search; beam 4, top-2 guards), Spider dev EX:

  | Model | Without PICARD | With PICARD |
  |---|---|---|
  | T5-Base | 57.9 | 68.4 |
  | T5-Large | 67.2 | 72.9 |
  | T5-3B | 71.4 | 76.3 |
  | T5-3B with DB content | 74.4 | 79.3 |

  Test, T5-3B with DB content: EX 70.1 → 75.1 — [PICARD arXiv 2109.05093](https://arxiv.org/abs/2109.05093) (Table 1 of the paper PDF)
- **TTD-SQL** (EMNLP 2025 Industry; Adobe/IIT Tirupati):
  - **Method**: precomputed token-level prefix trees over SQL keywords, table names and column names. When only one continuation is valid, the token is auto-filled with no forward pass. Table trees trigger after FROM/JOIN/ON, column trees after `table.`. It is training-free and plug-and-play.
  - **Spider results**: Qwen2.5-Coder-32B AR 80.5 → TTD 80.7 EX. Generic grammar-constrained decoding (GCD, SynCode) gives 82.1 for Qwen on Spider but collapses Llama-70B from 80.9 to 59.2.
  - **BIRD results**: GCD gives 50.2 vs AR 54.6 for Qwen and 36.4 vs 56.6 for Llama-70B. TTD 54.6 matches AR. CodeLlama-7B executable rate on BIRD rises 57.6 → 67.8 (+10.2 abs.; the paper headlines it as "+17.7%").
  - **Speed**: auto-fill covers 7–16% of tokens, giving up to +19.96% token rate (CodeLlama 8.97 → 10.76 tok/s on Spider).
  - **CoT**: plain chain-of-thought dropped CodeLlama on BIRD from 23.2 to 12.0.
  — [TTD-SQL ACL Anthology 2025.emnlp-industry.90](https://aclanthology.org/2025.emnlp-industry.90.pdf)
- **Chermsirivatana & MacCormick (2026)**, schema-aware grammar restricting identifiers to real tables/columns, Qwen2.5-Instruct 4-bit, Spider dev:
  - Constrained greedy beat unconstrained slightly at 0.5B (0.139 vs 0.135) and 1.5B (0.351 vs 0.326).
  - It lost at 3B (−0.032) and 7B (−0.043), because the grammar could not express `IN` value lists, `IS NOT NULL`, outer joins or abbreviated aliases.
  — [arXiv 2608.25761](https://arxiv.org/html/2608.25761)
- **TTD-SQL related work** notes that Synchromesh adds about 22% overhead for schema validation, and that PICARD's parser integration slows inference — [TTD-SQL](https://aclanthology.org/2025.emnlp-industry.90.pdf)
- **llama.cpp GBNF**: BNF with regex-like features (`*`, `+`, `?`, `{m,n}`, char classes, token matching `<[id]>`) and a JSON-Schema → GBNF converter. The docs warn that chained optionals like `x? x? x?...` "may result in extremely slow sampling"; use `x{0,N}` instead — [llama.cpp grammars README](https://github.com/ggml-org/llama.cpp/blob/master/grammars/README.md)
- **llama-cpp-python** exposes `grammar: Optional[LlamaGrammar]`, `logits_processor: Optional[LogitsProcessorList]` and `logprobs: Optional[int]` on `create_completion`, `__call__` and `create_chat_completion` — [llama-cpp-python API reference](https://llama-cpp-python.readthedocs.io/en/latest/api-reference/)
- **XGrammar** (MLSys 2025) splits tokens into context-independent ones (prechecked) and context-dependent ones (checked at runtime), uses a persistent stack, and claims "up to 100x speedup over existing solutions" and "near-zero overhead" when co-scheduled with GPU execution — [XGrammar arXiv 2411.15100](https://arxiv.org/abs/2411.15100)

### Inferences
- **Recommended light constraint**:
  - Generate the GBNF per query from the *linked* schema, with an `ident` rule that is an alternation of the exact allowed table/column names (quoted and unquoted forms) plus an alias pattern.
  - Leave everything else permissive (free text for expressions).
  - Alternatively, use a llama-cpp-python `logits_processor` that applies a TTD-style prefix trie only after `FROM`/`JOIN` and after `alias.`.
  - This mostly removes hallucinated identifiers, the error class that self-correction retries currently spend calls on, while avoiding the grammar-incompleteness losses.
- **Avoid a full SQL CFG** unless it is thoroughly tested against sqlglot's dialect coverage. The 2026 study and TTD-SQL both show incompleteness costs that grow with model strength.
- **Latency on CPU**: llama.cpp grammar sampling is done on the CPU per token, so a large identifier alternation adds per-token overhead. I found no published numbers for this setup; benchmark it. A trie-based logits processor that activates only in identifier positions is likely cheaper.
- **Cheaper alternative to constraining**: post-hoc identifier repair with sqlglot. Parse, find unknown columns or tables, and fuzzy-map each to the nearest schema name (rapidfuzz) when the match is unambiguous. This costs zero extra LLM calls and covers much of the same failure mode.

### Gaps
- I found no published CPU latency numbers for llama-cpp-python GBNF with SQL grammars.
- I found no direct accuracy evidence for llguidance or outlines on text-to-SQL at 0.5B–1.5B.
- PICARD's evidence is for fine-tuned T5 (2021) and may not transfer to instruction-tuned decoder LLMs.

---

## 5. Decomposition / chain-of-thought for small models

### Takeaway
Decomposition and CoT prompting help large models: DIN-SQL gives about +10 points with GPT-4/CodeX, and CHASE-SQL's divide-and-conquer CoT gives +6 points with Gemini. Prompted CoT is unreliable or harmful for small models: it gave +4.5 points for the smaller CodeX-Cushman but −11 points for CodeLlama-7B on BIRD. Small models only benefit from CoT when they are *trained* to reason (OmniSQL, SLM-SQL with `<think>` tags plus RL).

### Cited Findings
- **DIN-SQL** (schema linking, then query classification, then decomposed generation, then self-correction):
  - GPT-4 reaches 85.3% on Spider test and 55.9% on BIRD test; 50.72% on BIRD dev (+4 over baseline GPT-4).
  - On Spider dev, CodeX-Cushman (the smaller model) goes 43.1 → 47.6. The authors note prompting decomposition is an emergent ability tied to scale.
  - Self-correction prompts: "generic" correction suits CodeX (69.9 vs 68.7 for "gentle"), while GPT-4 prefers "gentle" (74.2 vs 70.0), because "giving a generic prompt can hurt performance" for a model that produces fewer bugs.
  — [DIN-SQL](https://arxiv.org/html/2304.11015)
- **CHASE-SQL** single-candidate BIRD dev:
  - Zero-shot CoT 57.75; divide & conquer CoT 63.92; query-plan CoT 63.62; online synthetic examples 67.09.
  - With the query fixer (max 3 iterations): 65.77, 65.51 and 68.02.
  — [CHASE-SQL](https://arxiv.org/html/2410.01943)
- **TTD-SQL**: zero-shot "Let's think step by step" CoT reduced CodeLlama-7B BIRD EX from 23.2 to 12.0 and Qwen2.5-Coder-32B BIRD from 54.6 to 49.6 — [TTD-SQL](https://aclanthology.org/2025.emnlp-industry.90.pdf)
- **OmniSQL** trains on CoT solutions. Every SynSQL-2.5M sample has a chain-of-thought followed by the final SQL — [OmniSQL](https://arxiv.org/html/2503.02240)
- **SLM-SQL** trains 0.5–1.5B models to emit `<think>…</think><answer>SQL</answer>` via SFT + RL (SynSQL-Think-916K) — [SLM-SQL](https://arxiv.org/html/2507.22478)

### Inferences
- For a generic 0.5B–1.5B instruct model, keep direct SQL output with no CoT. Reserve CoT or decomposition prompts for the escalation model.
- If sqltext can choose its local model, a small model trained with SQL-specific CoT data (SLM-SQL-style or OmniSQL-style) is where small-scale reasoning gains come from. I did not verify whether SLM-SQL checkpoints exist in GGUF form.
- CoT also multiplies output tokens, which is costly on CPU. That is another reason to avoid it in the fast path.

### Gaps
- I found no study of query-plan or decomposition prompting on *untuned* 0.5B–1.5B models.

---

## 6. Few-shot example selection and question-SQL memory

### Takeaway
Selecting few-shot examples by similarity to the question (with domain words masked) and to the SQL skeleton gives about +3 points over random selection for GPT-4 on Spider dev. Few-shot examples were the single largest ablation component in MCS-SQL (+4.8 on BIRD dev). A store of verified question-SQL pairs from the same DB is a strong, cheap source of examples. Caveat: models fine-tuned for text-to-SQL can get *worse* with in-context examples.

### Cited Findings
- **DAIL-SQL** selection masks domain-specific words in the question, ranks examples by masked-question similarity, and keeps those whose predicted-SQL skeleton similarity exceeds a threshold τ. Organisation: question-SQL pairs only, with no schema in examples, to save tokens.
  - 1-shot GPT-4 on Spider dev: random 77.4, question similarity 78.8, masked question 79.1, DAIL 80.2.
  - 5-shot: 82.4, rising to 86.6 with self-consistency.
  - Fine-tuned LLaMA models show "sudden decrease" in accuracy when examples are added.
  — [DAIL-SQL](https://arxiv.org/html/2308.15363)
- **MCS-SQL** ablation on BIRD dev: few-shot examples +4.8, the largest single component. Example selection uses question similarity and masked-question similarity — [MCS-SQL](https://arxiv.org/html/2405.07467)
- **CodeS** uses a "question-pattern-aware demonstration retriever" over both the original and entity-stripped questions (SimCSE embeddings) — [CodeS](https://arxiv.org/html/2402.16347)
- **XiYan-SQL**'s ICL generator uses skeleton-based example selection that masks named entities — [XiYan-SQL](https://arxiv.org/html/2411.08599)
- **CHASE-SQL**'s "online synthetic examples" generator, which synthesises examples specific to the DB instance, was its best single generator (67.09 on BIRD dev, vs 57.75 zero-shot CoT) — [CHASE-SQL](https://arxiv.org/html/2410.01943)

### Inferences
- **Implementation**: keep a per-database SQLite/JSON store of (question, SQL, skeleton) that were verified — by user acceptance, by agreement between the escalation model and the local model, or by a successful execution with non-empty results that passed the grounding check.
- **Retrieval**:
  1. Mask values and literals in the question (numbers, quoted spans, matched DB values from section 2).
  2. Embed with model2vec or bge-small.
  3. Take the top 2–3 examples. Optionally re-rank by sqlglot-normalised skeleton similarity to the greedy draft.
- **Exact or near-exact cache hits** (cosine above about 0.95 on the masked question, with the same value slots): reuse the stored SQL with substituted literals and skip generation entirely. This is the largest compute saving available.
- **Fine-tuned model caveat**: if the local model is a text-to-SQL fine-tune (for example, an OmniSQL-style one), test whether few-shot helps or hurts (per DAIL-SQL) before enabling it.

### Gaps
- I found no published accuracy numbers for "verified-pair memory" caching in deployed text-to-SQL systems.
- I found no measurements of few-shot gains at 0.5B–1.5B.

---

## 7. Error correction beyond feeding back DB errors

### Takeaway
Execution-feedback fixers give about +2 to +8 points depending on the base generator. CHASE-SQL's query fixer is worth +3.78 on BIRD dev in ablation, and CHESS's revision tool +6.80. They work best when they react to more signals than hard errors, such as empty results, NULL-heavy results, or schema mismatches. With small models, cheap rule-based or AST fixes and execution-signal checks are higher ROI than more LLM rounds.

### Cited Findings
- **CHASE-SQL query fixer**: up to 3 iterations, fed with execution feedback including syntax errors and empty results. It adds about +2 to +2.3 per generator, for example 63.92 → 65.77, and +7.76 to +10.27 relative to the zero-shot baseline as reported. Removing it costs −3.78 on BIRD dev — [CHASE-SQL](https://arxiv.org/html/2410.01943)
- **CHESS** revision tool: −6.80 on BIRD dev when removed, the largest single ablation. CHESS also uses unit-test-style candidate evaluation in its higher-budget setting — [CHESS](https://arxiv.org/html/2405.16755)
- **DIN-SQL** self-correction: removing it drops CodeX on Spider dev from 69.9 to 67.3. Prompt tone matters: "gentle" vs "generic" flips depending on model strength — [DIN-SQL](https://arxiv.org/html/2304.11015)
- **XiYan-SQL** includes a SQL Refiner that corrects logical and syntax errors from execution feedback as one of its candidate generators — [XiYan-SQL](https://arxiv.org/html/2411.08599)
- **SLM-SQL** "corrective self-consistency" adds a trained merge/revision model over the SC candidates, at about +1.5 to +3 points (but see the caveat in section 3) — [SLM-SQL](https://arxiv.org/html/2507.22478)

### Inferences
- Rule-based / sqlglot fixes to run before any LLM retry (zero LLM cost):
  - Fuzzy-repair unknown identifiers to schema names.
  - Add missing JOINs along the FK path when a referenced column's table is absent.
  - Fix quoting and case of string literals by mapping them to the exact DB value found by value retrieval (for example `'new york'` → `'New York'`).
  - Transpile dialect (`sqlglot.transpile`).
  - Add `LIMIT` and `ORDER BY` consistency when the question says "top N" or "highest".
- Treat "executes but returns 0 rows" or "all NULLs" as a soft error that triggers one retry or the vote path, as CHASE does with empty results.
- Use DIN-SQL's finding when writing retry prompts: phrase the feedback for a weak model as a concrete instruction (for example "column X does not exist; available: A, B, C"), not an open "check for bugs".

### Gaps
- I didn't retrieve primary sources for SQL-critic / critic-model papers or for ReFoRCE, so I have no quantified gains for them.
- I found no ablations of rule-based fixes on BIRD/Spider.

---

## 8. Highest-ROI ordering for a CPU-bound ~1 GB setup

### Takeaway
Rank by gain per compute. Zero-LLM-cost retrieval and repair (value retrieval, identifier repair, verified-pair memory) come first. Next comes cheap decoding control (identifier-only constraints). Execution voting comes last and should be gated by confidence. Prefer escalating to a bigger model over heavy sampling on the 0.5B model, since one model size up beats 8× inference compute.

### Cited Findings
- Value retrieval on BIRD dev: −4.76 (CHESS, combined with context retrieval), −2.92 (CHASE) and −3.19 / −2.48 (CodeS 1B / 7B) when removed. Offline LSH cuts lookup to seconds — [CHESS](https://arxiv.org/html/2405.16755), [CHASE-SQL](https://arxiv.org/html/2410.01943), [CodeS](https://arxiv.org/html/2402.16347)
- Execution-result voting: +10.4 (Qwen2.5-Coder-7B, 8 samples, BIRD dev) and +12 to +15 (0.5B/1.5B, budget 8, constrained, Spider dev) — [OmniSQL](https://arxiv.org/html/2503.02240), [arXiv 2608.25761](https://arxiv.org/html/2608.25761)
- 8× inference closes only 50–76% of the gap to the next model size — [arXiv 2608.25761](https://arxiv.org/html/2608.25761)
- Schema-aware token constraints keep accuracy and raise the executable rate and speed. Full grammars can hurt — [TTD-SQL](https://aclanthology.org/2025.emnlp-industry.90.pdf), [arXiv 2608.25761](https://arxiv.org/html/2608.25761)
- Small models fine-tuned with SQL-specific SFT + RL reach about 57% (0.5B) and 67% (1.5B) on BIRD dev per the SLM-SQL abstract. Model choice therefore dominates prompting tricks at this scale — [SLM-SQL](https://arxiv.org/abs/2507.22478)
- Static embeddings (model2vec, about 8–30 MB, numpy-only) make embedding-based linking and example retrieval feasible within the RAM budget — [model2vec](https://github.com/MinishLab/model2vec)

### Inferences (suggested priority for sqltext)
1. **Question-conditioned value retrieval.** Use MinHash-LSH, BM25 or SQLite FTS5 trigram, re-rank with rapidfuzz/LCS, and inject matches as column comments. Expected gain is about +3 on BIRD-like data, with zero LLM calls. It also makes the grounding check more accurate, so fewer queries escalate.
2. **sqlglot identifier and literal repair** before any LLM retry: zero LLM calls; directly targets hallucinated columns.
3. **Verified question-SQL memory.** Cache hits skip generation. Retrieve the top 2–3 masked-similar examples as few-shot (DAIL-style), checking first that this helps the chosen model.
4. **Recall-oriented embedding + keyword hybrid schema linking** with model2vec, always keeping PK/FK and value-matched columns, and passing the full schema for small DBs and for the escalation model.
5. **Identifier-only constraint** (per-query GBNF alternation or a trie `logits_processor`), measured for CPU overhead. Avoid full SQL grammars.
6. **Confidence-gated execution voting** (k=3–5 samples, or beam if the constrained path is used), triggered only on low logprob, errors, empty results or grounding failure. Low agreement means escalate.
7. **Model choice.** If RAM allows, a 1.5B text-to-SQL-tuned model (SLM-SQL / OmniSQL-style data) beats any prompting trick on a generic 0.5B model. Keep CoT for the escalation model only.

### Gaps
- None of the cited works measures CPU-only wall-clock cost on a 1 GB RAM budget. All the numbers come from GPU or API settings.
- I didn't verify whether GGUF builds of SLM-SQL or OmniSQL-small checkpoints exist.
