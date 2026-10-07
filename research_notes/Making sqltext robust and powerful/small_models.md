# Small (<=1.5B) local text-to-SQL models for a ~1 GB CPU RAM budget (state as of Oct 2026)

Context for the reader: sqltext currently ships `qwen2.5-coder-0.5b-instruct-q4_k_m.gguf` (the official Qwen file, 491 MB on disk) via llama-cpp-python with `n_ctx=4096` (`sqltext/hardware.py`, `sqltext/backends/llama.py`), measured at ~0.7 GB process RAM. Needle (~0.1 GB) leaves roughly 0.8-0.9 GB for the SQL model process. Note that sqltext's own budget heuristic `estimated_ram_gb = file_GiB * 1.25 + 0.3` (`sqltext/hardware.py:24`) is more conservative than the measured figure: under it a GGUF must be <= ~0.48 GiB (~515 MB) to come in under 0.9 GB, or <= ~0.56 GiB (~600 MB) to come in under 1.0 GB. Any 1-1.5B candidate will need that heuristic recalibrated, for example to file size + KV cache + ~0.15 GB, measured.

All benchmark numbers below are execution accuracy (EX). Unless noted, they are **BIRD dev** (n=1534) or **Spider dev** (n=1034). Most small-model headline numbers use **heavy test-time sampling** (8-64 samples plus voting or merging), which is very expensive on CPU. Single-sample (greedy) numbers are rarely published for these models.

---

## Q1. Which <=~1.5B models have the best published Spider / BIRD EX?

### Takeaway
The strongest published small text-to-SQL models are the 2025-2026 SFT+RL fine-tunes of Qwen2.5-Coder: **SLM-SQL-1.5B (67.08 BIRD dev)**, **FINER-SQL-1.5B (63.17 BIRD dev / 80.0 Spider dev)**, **SLM-SQL-0.6B on Qwen3 (59.52)**, **SLM-SQL-0.5B (56.87)**, **DatA-SQL-1.5B (55.86)** and **FINER-SQL-0.5B (50.59-50.85 BIRD / 70.2-75.0 Spider)**. Every one of these headline numbers comes from 8-64 sampled candidates with voting, and most of the models "think" before answering. Older baselines are well behind: Prem-1B-SQL scores 46.0 on BIRD dev, CodeS-1B 50.46, and base Qwen2.5-Coder-1.5B-Instruct about 28 on BIRD dev. OmniSQL (7B+), XiYan (3B+) and Arctic-Text2SQL-R1 (7B) have no variants under 1.5B.

### Cited Findings

**SLM-SQL (Sheng & Xu, arXiv 2507.22478, July 2025)**
- Method: SFT on SynSQL-Think-916K, then GRPO RL, then "corrective self-consistency" inference. BIRD dev EX by base model: Qwen2.5-Coder-0.5B-Instruct **56.87**, Qwen3-0.6B **59.52**, Llama-3.2-1B-Instruct **54.78**, deepseek-coder-1.3b-instruct **62.19**, Qwen2.5-Coder-1.5B-Instruct **67.08** — [arXiv HTML](https://arxiv.org/html/2507.22478); [abstract](https://arxiv.org/abs/2507.22478)
- Spider **test** EX with corrective self-consistency, after BIRD training with no Spider-specific training: 0.5B **73.50**, 1.5B **79.06** — [arXiv HTML](https://arxiv.org/html/2507.22478)
- Inference cost in the paper: **64 sampled SQL generations** plus **8 merge-revision samples** per question — [arXiv HTML](https://arxiv.org/html/2507.22478)
- Untrained baselines reported in the paper: Qwen2.5-Coder-1.5B-Instruct **28.40** BIRD dev; Prem-1B-SQL **46.0** BIRD dev — [arXiv HTML](https://arxiv.org/html/2507.22478). A search-engine summary of the same paper gives base Qwen3-0.6B **38.68** BIRD dev — [arXiv PDF via search](https://arxiv.org/pdf/2507.22478). (Not verified against the table directly.)
- Ablation: removing SFT costs 21.93 points at 0.5B and 8.89 points at 1.5B. Removing corrective self-consistency costs about 5 points — [arXiv HTML](https://arxiv.org/html/2507.22478). This suggests the single-sample accuracy is roughly 5 points below the headline numbers (my inference from the ablation; no explicit greedy figure was found).
- The extraction tool also returned "0.5B: SC baseline 42.13, SFT-only 65.31, SFT+RL 70.60 with SC". That conflicts with the 56.87 headline and may refer to a different metric or subset. **Treat as unverified.** — [arXiv HTML](https://arxiv.org/html/2507.22478)
- Released models (HF collection `cycloneboy/slm-sql`): `cycloneboy/SLM-SQL-0.5B`, `SLM-SQL-0.6B` (Qwen3-0.6B), `SLM-SQL-1.3B` (deepseek-coder-1.3b), `SLM-SQL-1.5B`, the SFT-only `SLM-SQL-Base-{0.5B,0.6B,1B,1.3B,1.5B}`, and merge-revision models `CscSQL-Merge-Qwen2.5-Coder-{0.5B,1.5B}-Instruct` — [model card](https://huggingface.co/cycloneboy/SLM-SQL-0.5B)

**FINER-SQL (Hoang et al., ICDE 2026, arXiv 2605.03465, May 2026)**
- GRPO with dense rewards ("memory reward" plus "atomic", operation-level, reward) on Qwen2.5-Coder bases. Paper Table II: 0.5B **50.85** BIRD dev / **70.2** Spider dev; 1.5B **63.17** / **80.0**; 3B **67.73** / **85.0** — [arXiv HTML](https://arxiv.org/html/2605.03465)
- GitHub README "official EX": FINER-SQL-0.5B-BIRD **50.59** BIRD dev (majority vote 50.85; simple/moderate/challenging 60.11/36.85/33.79). FINER-SQL-0.5B-Spider **75.0** Spider dev (easy/med/hard/extra 91.9/82.5/62.6/42.8). **The Spider 0.5B figure conflicts with the paper's 70.2.** The README model is a Spider-specific checkpoint, which may explain the gap — [GitHub README](https://github.com/thanhdath/finer-sql)
- Decoding: **30 SQL candidates per question at T=1.0 with majority voting**. Reasoning goes in `<think>` before the SQL — [arXiv HTML](https://arxiv.org/html/2605.03465)
- Baselines quoted in the paper (<=5B): CodeS-1B 50.46 BIRD / 77.9 Spider; CodeS-3B 55.02 / 83.4; SQL-R1-3B 54.6 / 78.1; Reasoning-SQL-3B 58.67 BIRD — [arXiv HTML](https://arxiv.org/html/2605.03465)
- On HF only the 0.5B and 3B (plus a 4B BIRD) checkpoints are published: `griffith-bigdata/FINER-SQL-0.5B-BIRD`, `-0.5B-Spider`, `-3B-BIRD`, `-3B-Spider`, `-4B-BIRD`. **No 1.5B checkpoint is on HF** (HF API search, Oct 2026). Unpublished `thanhdath/finer-sql-1_5b-*-ckpt` repos exist with 0 downloads and no cards — [HF search](https://huggingface.co/models?search=FINER-SQL)
- The same group published `griffith-bigdata/Qwen-2.5-Coder-0.5B-SQL-Writer` (Nov 2025). It is an SFT of Qwen2.5-Coder-0.5B-Instruct on `sft_text2sql_v2`, and its card reports no benchmark — [model card](https://huggingface.co/griffith-bigdata/Qwen-2.5-Coder-0.5B-SQL-Writer)

**DatA-SQL-1.5B (`Chinastark/DatA-SQL-1.5B`, Oct 2025)**
- Base Qwen2.5-Coder-1.5B-Instruct, apache-2.0. **55.86 BIRD dev EX at Vote@8** with "reasoning-guided data augmentation". The card claims this is SOTA among open 1.5B models, but it predates or ignores SLM-SQL's 67.08. No paper link — [model card](https://huggingface.co/Chinastark/DatA-SQL-1.5B)

**Prem-1B-SQL (`prem-research/prem-1B-SQL`, Aug 2024 — older)**
- BIRD validation **46%**, BIRD private **test 51.54%** (simple 60.70 / moderate 47.39 / challenging 29.12), Spider **85%** (split unstated, presumably dev). Trained on BIRD train, Spider, a domain dataset, Gretel synthetic data, plus self-correction data — [model card](https://huggingface.co/prem-research/prem-1B-SQL)
- License conflict: the card's YAML says apache-2.0 but the text says "License: [MIT]" — [model card](https://huggingface.co/prem-research/prem-1B-SQL)

**CodeS-1B (`seeklhy/codes-1b`, 2023-2024 — older)**
- StarCoderBase-1B incrementally pre-trained on SQL, 8,192 context, apache-2.0. Fine-tuned variants are `seeklhy/codes-1b-spider`, `codes-1b-bird`, `codes-1b-bird-with-evidence` — [model card](https://huggingface.co/seeklhy/codes-1b); [HF search](https://huggingface.co/models?search=codes-1b)
- 50.46 BIRD dev / 77.9 Spider dev, as cited by FINER-SQL — [arXiv HTML](https://arxiv.org/html/2605.03465)

**Qwen3.5 small (Feb 2026) and SQaLe (2026)**
- `Qwen/Qwen3.5-0.8B` and `Qwen/Qwen3.5-2B` were released 2026-02-28. I found **no published Spider/BIRD numbers for Qwen3.5-0.8B** — [HF](https://huggingface.co/Qwen/Qwen3.5-0.8B)
- `trl-lab/qwen3.5-2b-grpo-bird` is a GRPO-only agentic model. It reaches **54.7 BIRD dev** on 300 questions in a schema-withheld tool-use setting, against **19.3** for untrained Qwen3.5-2B. Siblings `qwen3.5-2b-grpo-sqale` scores 52.3 and `-synsql` 44.3. It is 2B, so it is **over budget**, and these numbers are not comparable to standard schema-in-prompt EX — [model card](https://huggingface.co/trl-lab/qwen3.5-2b-grpo-bird)

**Out-of-budget families (no <=1.5B variant)**
- OmniSQL comes only in 7B/14B/32B — [SynSQL-2.5M card](https://huggingface.co/datasets/seeklhy/SynSQL-2.5M)
- XiYanSQL-QwenCoder's smallest is 3B (`XGenerationLab/XiYanSQL-QwenCoder-3B-2504`, apache-2.0). Its GGUF is 1,930 MB at Q4_K_M and 1,275 MB at Q2_K — [HF API](https://huggingface.co/mradermacher/XiYanSQL-QwenCoder-3B-2504-GGUF)
- Arctic-Text2SQL-R1's smallest is 7B (`Snowflake/Arctic-Text2SQL-R1-7B`) — [HF search](https://huggingface.co/models?search=Arctic-Text2SQL)

**Other small generalists**
- `distil-labs/distil-qwen3-0.6b-text2sql` (apache-2.0, Jan 2026) was distilled from DeepSeek-V3 on ~10k synthetic examples grown from 50 seeds. It scores **74% LLM-as-judge** against 36% for base Qwen3-0.6B, and 40% exact match, on its own test set. **No Spider/BIRD numbers.** The card targets "one or two table definitions" — [model card](https://huggingface.co/distil-labs/distil-qwen3-0.6b-text2sql)
- I found no Spider/BIRD numbers for Gemma 3 1B/270M, LFM2 (350M/700M/1.2B), SmolLM, or Granite small models. HF search shows only hobby fine-tunes with no benchmarks, for example `arirajuns/gemma-3-1b-sql-coder-v1` and `Yuk050/gemma-3-1b-text-to-sql-model`. No LFM2 SQL fine-tunes turned up — [HF search](https://huggingface.co/models?search=gemma-3-1b%20sql)

### Inferences
- Ranking by published BIRD dev EX among HF-downloadable <=1.5B models: SLM-SQL-1.5B (67.1) > SLM-SQL-1.3B (62.2) > SLM-SQL-0.6B (59.5) > SLM-SQL-0.5B (56.9) > DatA-SQL-1.5B (55.9, vote@8) > SLM-SQL-1B (54.8) > FINER-SQL-0.5B (50.6-50.9) ≈ CodeS-1B (50.5) > Prem-1B-SQL (46.0). FINER-SQL-1.5B (63.2) is paper-only.
- The relative ordering is more trustworthy than the absolute numbers. sqltext will use 1 sample (or a few), not 30-64, so expect several points lower in practice.
- BIRD and Spider DBs are small, at 5-10 tables (per the SQaLe card). sqltext's Needle table-selection step is therefore doing work these benchmarks largely skip.

### Gaps
- No reliable **greedy / single-sample** EX found for SLM-SQL or FINER-SQL small models. The papers report voting results.
- MSc-SQL and BASE-SQL were not examined (budget). To my knowledge their small configurations are 7-9B, but this is unverified.
- No Spider/BIRD numbers for Qwen3-1.7B, Qwen3.5-0.8B, Gemma 3, LFM2, SmolLM, or Granite at <=1.5B. A UniQL-paper snippet gave Qwen3-1.7B **36.70 on BIRD** (UniQL's evaluation), which I did not verify — [search snippet, arXiv 2606.08018](https://arxiv.org/pdf/2606.08018).
- No current BIRD/Spider leaderboard entries for <=1.5B models were checked directly.

---

## Q2. Per-model deployability: params, license, GGUF repos, file sizes, RAM, prompt format

### Takeaway
GGUFs exist for nearly every candidate, mostly from **mradermacher**. File sizes depend on the quantizer's choice of embedding precision. For the same 0.5B model, Qwen-official and mradermacher "static" files are ~90 MB larger than bartowski or mradermacher-i1 files. Every RL-tuned SQL model (SLM-SQL, FINER-SQL) expects **its own prompt template and emits `<think>` reasoning before the SQL**, so sqltext would need a template change and an answer extractor. The best-licensed strong options are SLM-SQL-1.5B (apache-2.0, per HF metadata) and DatA-SQL-1.5B (apache-2.0). Most other SLM-SQL checkpoints are **CC-BY-NC-4.0**.

### Cited Findings (file sizes from HF API tree listings, Oct 2026; MB = 10^6 bytes)

| Model (base) | Params | License | GGUF repo(s) | Q4_K_M | Q3_K_M | Q2_K | IQ quants |
|---|---|---|---|---|---|---|---|
| Qwen2.5-Coder-0.5B-Instruct (current) | 0.5B | apache-2.0 | `Qwen/Qwen2.5-Coder-0.5B-Instruct-GGUF`; `bartowski/Qwen2.5-Coder-0.5B-Instruct-GGUF` | 491 (official) / 398 (bartowski) | 432 (official) | 415 (official) | IQ3_M 343 (bartowski) |
| SLM-SQL-0.5B (Qwen2.5-Coder-0.5B) | 0.5B | **cc-by-nc-4.0** | `mradermacher/SLM-SQL-0.5B-GGUF`, `frizynn/SLM-SQL-0.5B-GGUF` | 491 | 432 | 415 | IQ4_XS 428 |
| SLM-SQL-0.6B (Qwen3-0.6B) | 0.6B | **cc-by-nc-4.0** | `mradermacher/SLM-SQL-0.6B-GGUF` | 484 | 414 | 347 | IQ4_XS 452 |
| SLM-SQL-1.3B (deepseek-coder-1.3b) | 1.3B | **cc-by-nc-4.0** | `mradermacher/SLM-SQL-1.3B-GGUF` | 873 | 704 | 560 | IQ4_XS 751 |
| SLM-SQL-1.5B (Qwen2.5-Coder-1.5B) | 1.5B | apache-2.0 (HF metadata; card nearly empty) | `mradermacher/SLM-SQL-1.5B-GGUF`, `frizynn/SLM-SQL-1.5B-GGUF` | 1,117 | 924 | 753 | IQ4_XS 1,026; Q3_K_S 861 |
| FINER-SQL-0.5B-BIRD / -Spider (Qwen2.5-Coder-0.5B) | 0.5B | not stated in HF metadata | `mradermacher/FINER-SQL-0.5B-BIRD-GGUF`, `mradermacher/FINER-SQL-0.5B-Spider-GGUF` | 398 | 355 | 339 | IQ4_XS 351 |
| DatA-SQL-1.5B (Qwen2.5-Coder-1.5B) | 1.5B | apache-2.0 | `mradermacher/DatA-SQL-1.5B-GGUF`, `mradermacher/DatA-SQL-1.5B-i1-GGUF` | 986 | 824 | 676 | IQ3_M 777, IQ3_XS 732, **IQ3_XXS 669**, IQ2_M 601, IQ2_XXS 511 |
| Prem-1B-SQL | ~1.3B (inferred, see below) | apache-2.0 / MIT (conflicting) | `QuantFactory/prem-1B-SQL-GGUF`, `mradermacher/prem-1B-SQL-GGUF`, `mradermacher/prem-1B-SQL-i1-GGUF`, `tensorblock/prem-1B-SQL-GGUF` | 873 | 704 | 560 | IQ3_M 673, IQ3_XXS 581, IQ2_M 552 |
| CodeS-1B-bird | 1B | apache-2.0 | `tensorblock/seeklhy_codes-1b-bird-GGUF` (only Q2_K seen), `tensorblock/seeklhy_codes-1b-spider-GGUF` | — | — | 572 | — |
| Qwen2.5-Coder-1.5B-Instruct | 1.5B | apache-2.0 | `bartowski/Qwen2.5-Coder-1.5B-Instruct-GGUF`; `Qwen/Qwen2.5-Coder-1.5B-Instruct-GGUF` | 986 / 1,117 (official) | 824 | 676 | IQ3_M 777, IQ3_XS 732, IQ2_M 601 |
| Qwen3-0.6B | 0.6B | apache-2.0 | `unsloth/Qwen3-0.6B-GGUF` | 397 | 347 | 296 | UD-IQ3_XXS 282 |
| Qwen3-1.7B | 1.7B | apache-2.0 | `unsloth/Qwen3-1.7B-GGUF` | 1,107 | 940 | 778 | UD-IQ3_XXS 765, UD-IQ2_M 709 |
| Qwen3.5-0.8B (Feb 2026) | 0.8B | (Qwen) | `unsloth/Qwen3.5-0.8B-GGUF`, `bartowski/Qwen_Qwen3.5-0.8B-GGUF`, `ggml-org/Qwen3.5-0.8B-GGUF` | 533 | 470 | — | UD-IQ3_XXS 398, UD-Q2_K_XL 418 |
| Gemma 3 1B it | 1B | Gemma terms | `unsloth/gemma-3-1b-it-GGUF` | 806 | 722 | 690 | UD-IQ2_M 578 |
| Gemma 3 270M it | 0.27B | Gemma terms | `unsloth/gemma-3-270m-it-GGUF` | 253 | 242 | 237 | — |
| LFM2-1.2B / LFM2-700M | 1.2B / 0.7B | LFM license | `LiquidAI/LFM2-1.2B-GGUF`, `LiquidAI/LFM2-700M-GGUF` | 731 / 469 | — | — | — |
| distil-qwen3-0.6b-text2sql | 0.6B | apache-2.0 | GGUF in the model repo itself (per card); `mradermacher/Qwen3-0.6B-text2sql-GGUF` exists but its source model is unverified | ~397 (mradermacher) | 347 | 296 | — |

Sources: HF API tree listings for each repo, e.g. [mradermacher/SLM-SQL-1.5B-GGUF](https://huggingface.co/mradermacher/SLM-SQL-1.5B-GGUF), [mradermacher/DatA-SQL-1.5B-i1-GGUF](https://huggingface.co/mradermacher/DatA-SQL-1.5B-i1-GGUF), [mradermacher/FINER-SQL-0.5B-BIRD-GGUF](https://huggingface.co/mradermacher/FINER-SQL-0.5B-BIRD-GGUF), [mradermacher/prem-1B-SQL-i1-GGUF](https://huggingface.co/mradermacher/prem-1B-SQL-i1-GGUF), [bartowski/Qwen2.5-Coder-1.5B-Instruct-GGUF](https://huggingface.co/bartowski/Qwen2.5-Coder-1.5B-Instruct-GGUF), [unsloth/Qwen3.5-0.8B-GGUF](https://huggingface.co/unsloth/Qwen3.5-0.8B-GGUF), [unsloth/gemma-3-1b-it-GGUF](https://huggingface.co/unsloth/gemma-3-1b-it-GGUF), [LiquidAI/LFM2-1.2B-GGUF](https://huggingface.co/LiquidAI/LFM2-1.2B-GGUF). Licenses come from HF model metadata or cards: [SLM-SQL-0.5B](https://huggingface.co/cycloneboy/SLM-SQL-0.5B), [SLM-SQL-1.5B](https://huggingface.co/cycloneboy/SLM-SQL-1.5B), [DatA-SQL-1.5B](https://huggingface.co/Chinastark/DatA-SQL-1.5B), [Prem-1B-SQL](https://huggingface.co/prem-research/prem-1B-SQL), [CodeS-1B](https://huggingface.co/seeklhy/codes-1b).

**Prompt / output format requirements**
- **SLM-SQL:** reasoning in `<think> ... </think>` and SQL in `<answer> ... </answer>`. The template is in the paper's Appendix F, and inference code is listed as TODO in the repo — [arXiv HTML](https://arxiv.org/html/2507.22478); [model card TODO list](https://huggingface.co/cycloneboy/SLM-SQL-0.5B)
- **FINER-SQL:** reasoning in `<think>` before the SQL. The system prompt begins "You are a meticulous SQL expert. Generate a single, correct SQL query... Output exactly one SQL statement. The SQL must be executable on SQLite." Ready-made prompts are in HF datasets `griffith-bigdata/bird_dev_prompts` and `spider_dev_prompts` — [GitHub README](https://github.com/thanhdath/finer-sql); [arXiv HTML](https://arxiv.org/html/2605.03465)
- **Prem-1B-SQL:** prompts are "tightly coupled with databases" and best built via the `premsql` library, which also offers execution-guided retry (up to 5 by default) — [model card](https://huggingface.co/prem-research/prem-1B-SQL)
- **distil-qwen3-0.6b-text2sql:** a specific XML-wrapped system and user template, with output being one SQL query in uppercase keywords for SQLite — [model card](https://huggingface.co/distil-labs/distil-qwen3-0.6b-text2sql)
- **SQaLe Qwen3.5-2B agents:** tool-calling agent loop, schema never in the prompt, `<think>` each turn, 12,288-token episode budget. Unsuitable for a CPU 1 GB budget — [model card](https://huggingface.co/trl-lab/qwen3.5-2b-grpo-bird)
- **CodeS:** usage via its GitHub repo, 8,192 max length — [model card](https://huggingface.co/seeklhy/codes-1b)

**Architecture facts used for RAM estimates**
- Qwen2.5-Coder-1.5B: 28 layers, 2 KV heads, hidden 1536, 12 attention heads, tied embeddings, vocab 151,936. Qwen2.5-Coder-0.5B: 24 layers, 2 KV heads, hidden 896, 14 heads, tied. SLM-SQL-1.5B config also has `tie_word_embeddings: true` — [Qwen2.5-Coder-1.5B config](https://huggingface.co/Qwen/Qwen2.5-Coder-1.5B-Instruct/blob/main/config.json); [0.5B config](https://huggingface.co/Qwen/Qwen2.5-Coder-0.5B-Instruct/blob/main/config.json); [SLM-SQL-1.5B config](https://huggingface.co/cycloneboy/SLM-SQL-1.5B/blob/main/config.json)
- FINER-SQL's GPU footprint: 0.5B ~3 GB VRAM, 1.5B ~6 GB VRAM (vLLM, 30 candidates); latency 2.60 s / 3.25 s per sample — [arXiv HTML](https://arxiv.org/html/2605.03465)

### Inferences
- **The file-size gap is a quantizer choice, not a model difference.** The official Qwen file and mradermacher static quants keep the token-embedding tensor at higher precision: 0.5B Q4_K_M is 491 MB vs 398 MB from bartowski, and 1.5B is 1,117 MB vs 986 MB. SLM-SQL-1.5B has the same architecture as Qwen2.5-Coder-1.5B (tied embeddings), so a self-made imatrix quant should land at bartowski sizes: IQ3_M ~777 MB, IQ3_XS ~732 MB, IQ3_XXS ~669 MB, Q2_K ~676 MB. mradermacher only published static quants for SLM-SQL-1.5B (smallest Q2_K 753 MB), so an i1 quant would need to be made locally with `llama-quantize --imatrix`.
- Quick win at no accuracy cost: sqltext could switch its current model to the bartowski Q4_K_M (398 MB) and save ~90 MB.
- **KV cache estimate (f16), computed from the configs above:**
  - 1.5B Qwen2.5: 28 x 2 x 128 x 2 x 2 B ≈ 28 KB/token, so ~117 MB at n_ctx=4096 and ~59 MB at 2048.
  - 0.5B: ~12 KB/token, so ~50 MB at 4096.
  - Measured sqltext overhead today is ~0.7 GB process vs a 0.49 GB file, i.e. ~0.2 GB for KV, compute buffers and the Python runtime.
  - So a 1.5B IQ3_XXS (669 MB) + ~117 MB KV + ~150 MB other ≈ **0.94 GB**. IQ2_M (601 MB) or n_ctx 2048 brings it to ~0.85 GB. Q8 or q4 KV-cache quantization in llama.cpp could also shave KV memory.
  - Prem-1B-SQL / SLM-SQL-1.3B at IQ3_XXS/IQ3_M (581-673 MB) also fit.
  - All of these are estimates and must be measured.
- Prem-1B-SQL GGUF sizes are byte-for-byte identical to SLM-SQL-1.3B's (deepseek-coder-1.3b) at every quant (873 / 704 / 560 MB). This strongly suggests Prem-1B-SQL is a ~1.3B DeepSeek-Coder derivative, not literally 1B.
- Gemma 3 1B's Q4_K_M (806 MB) is large for its size because of its ~262k vocab. Combined with no SQL benchmarks, it is a poor fit.

### Gaps
- **CPU tokens/sec:** none of the model cards or papers reports CPU llama.cpp throughput for these SQL fine-tunes. sqltext should benchmark locally.
- FINER-SQL HF license is not set in metadata. The GitHub license was not checked.
- The exact SLM-SQL prompt template (Appendix F) was not retrieved verbatim.
- Whether the GGUF chat templates for SLM-SQL/FINER preserve the `<think>` behaviour was not verified.

---

## Q3. Can a 1.5B model fit under ~0.9 GB with aggressive quantization, and how much accuracy is lost?

### Takeaway
**On size: yes, but only barely.** A Qwen2.5-Coder-1.5B-family model at IQ3_XXS / IQ2_M (601-669 MB file) plus a modest KV cache should come in at ~0.85-0.95 GB. A deepseek-coder-1.3B-family model (SLM-SQL-1.3B, Prem-1B-SQL) at IQ3_M/IQ3_XXS (581-673 MB) fits more comfortably. **On accuracy: I found no published measurement of GGUF Q3/IQ3/IQ2 quantization loss on Spider/BIRD for these models.** The only small-model SQL evidence uses 4-bit NF4. That study found larger models win over spending more inference compute on smaller ones, which weakly favours "1.5B at ~3 bits" over "0.5B at 4 bits", but this needs local verification.

### Cited Findings
- Smallest 1.5B (Qwen2.5-Coder lineage) GGUFs on HF: DatA-SQL-1.5B i1 IQ3_XXS **669 MB**, IQ2_M **601 MB**, IQ2_XXS **511 MB**; bartowski Qwen2.5-Coder-1.5B-Instruct IQ3_XS **732 MB**, IQ2_M **601 MB**; SLM-SQL-1.5B (static only) Q2_K **753 MB**, Q3_K_S **861 MB** — [DatA-SQL i1 GGUF](https://huggingface.co/mradermacher/DatA-SQL-1.5B-i1-GGUF); [bartowski 1.5B](https://huggingface.co/bartowski/Qwen2.5-Coder-1.5B-Instruct-GGUF); [SLM-SQL-1.5B GGUF](https://huggingface.co/mradermacher/SLM-SQL-1.5B-GGUF)
- Qwen3-1.7B: UD-IQ3_XXS **765 MB**, UD-IQ2_M **709 MB** — [unsloth/Qwen3-1.7B-GGUF](https://huggingface.co/unsloth/Qwen3-1.7B-GGUF)
- "Beam Search, Self-Consistency, and the Limits of Inference-Time Scaling for Grammar-Constrained Text-to-SQL in Small Language Models" (arXiv 2608.25761, Aug 2026) evaluated Qwen2.5-Instruct 0.5B-7B, "all at 4-bit precision", on Spider dev (1,034).
  - Beam search and sample+vote both help, "especially on smaller model sizes".
  - Beam search beats sample+vote at matched compute.
  - The "model size vs. inference compute" trade-off "is not advantageous... because moving to a larger model size typically results in higher accuracy".
  - Abstract only; per-size numbers were not retrieved — [arXiv abstract](https://arxiv.org/abs/2608.25761)
- An on-prem text-to-SQL study (arXiv 2606.29733) noted that FP8 API serving landed "∼13 pp below our fp16 level" for some runs. It covers 7B+ models only, and the drop may be confounded by serving differences — [arXiv HTML](https://arxiv.org/html/2606.29733)
- A practitioner blog reports that 4-bit versions "slightly" hurt performance and that quantized models were less accurate generating SQL. This is qualitative only — [nilenso blog, May 2025](https://blog.nilenso.com/blog/2025/05/27/experimenting-with-self-hosted-llms-for-text-to-sql/)

### Inferences
- General llama.cpp experience (not SQL-specific, and not sourced here) is that IQ3-class imatrix quants keep most of the quality while Q2/IQ2 degrade sharply at ~1.5B. SQL is precision-sensitive: one wrong column name means EX = 0. **Recommendation: treat IQ3_XXS/IQ3_XS as the floor for 1.5B, and avoid IQ2.** Measure on sqltext's 16-question set plus a slice of BIRD/Spider dev.
- SLM-SQL-1.5B scores 67.1 BIRD dev at fp16 with voting, against 56.9 for SLM-SQL-0.5B. That ~10-point gap at full precision gives some headroom to absorb 3-bit loss, but this is an untested assumption.

### Gaps
- No source measured GGUF IQ3/IQ2 vs Q4/fp16 EX on Spider/BIRD for any <=1.5B model. This is the main open question and should be measured locally.

---

## Q4. Distilled reasoning ("think") small SQL models: does reasoning help at this size, and what does it cost on CPU?

### Takeaway
Yes, reasoning variants exist and dominate the leaderboard for small models: SLM-SQL, FINER-SQL and DatA-SQL all use CoT/`<think>` plus GRPO. But their gains are measured **with 8-64 sampled candidates plus voting or merging**. The literature does not cleanly separate "reasoning" from "test-time sampling", and on CPU both multiply latency. Larger-model evidence says self-consistency is a poor buy, while execution-error self-correction is a cheap and consistent win.

### Cited Findings
- SLM-SQL trains on SynSQL-Think-916K (CoT) and SynSQL-Merge-Think-310K. It infers with 64 samples plus 8 merge samples. Removing SFT, i.e. the reasoning distillation, costs 21.93 points at 0.5B. Removing corrective self-consistency costs ~5 points — [arXiv HTML](https://arxiv.org/html/2507.22478)
- FINER-SQL uses `<think>` reasoning distilled from 4 teachers (37.6K samples on BIRD train) plus GRPO, with 30 candidates and majority vote. 0.5B latency is **2.60 s/sample on GPU** — [arXiv HTML](https://arxiv.org/html/2605.03465)
- FINER-SQL README: majority vote over 30 candidates gives 50.85 for 0.5B BIRD, while **Recall@30 is 68.32%**. A correct SQL is among the 30 candidates far more often than voting picks it. This points to better selection, such as execution-based checks, rather than more samples — [GitHub README](https://github.com/thanhdath/finer-sql)
- DatA-SQL-1.5B: 55.86 at **Vote@8** with "reasoning-guided data augmentation" — [model card](https://huggingface.co/Chinastark/DatA-SQL-1.5B)
- SQaLe agent models think each turn and are budgeted at 12,288 tokens per episode — [model card](https://huggingface.co/trl-lab/qwen3.5-2b-grpo-bird)
- On 7B-70B models: self-consistency (m=5) added only +0.13 pp on Qwen-32B (not significant) at ~5x tokens. Execution-error **self-correction** gave +1.24 to +3.65 pp consistently. Lexical schema linking *hurt* by 1.56-3.65 pp — [arXiv 2606.29733](https://arxiv.org/html/2606.29733)
- For 4-bit Qwen2.5 0.5B-7B on Spider, inference-time scaling helps small models most, but scaling model size beats scaling samples — [arXiv 2608.25761](https://arxiv.org/abs/2608.25761)
- Prem-1B-SQL relies on execution-guided decoding with up to 5 retries rather than long CoT — [model card](https://huggingface.co/prem-research/prem-1B-SQL)

### Inferences
- **CPU cost estimate.** A `<think>` block typically adds hundreds of tokens per query (assumption, not measured here). At an assumed ~20-60 tok/s for a 0.5-1.5B Q4 model on a laptop CPU (no source found), one reasoning sample costs several seconds, and 30-64 samples are impractical. The deployable pattern for sqltext is 1 reasoning sample (or a few, at most) + execute + retry on error, possibly with small-N voting on result sets.
- Some RL-tuned models may degrade if forced not to think. The SQaLe card explicitly says "Keep thinking enabled". sqltext should test both modes and cap `max_tokens`.

### Gaps
- No published single-sample think vs no-think ablation at <=1.5B on BIRD/Spider was found.
- No CPU latency measurements were found for reasoning SQL SLMs.

---

## Q5. Fine-tuning datasets and recipes for training sqltext's own small model

### Takeaway
High-quality open data is plentiful: SynSQL-2.5M (apache-2.0, with CoT), its SLM-SQL derivatives, BIRD/Spider train mirrors, Gretel synthetic, and SQaLe. Recipes have converged on **SFT on CoT data, then GRPO with execution rewards**. Published runs use multi-GPU (A100-class) resources. I found no published CPU-only LoRA recipe; a consumer-GPU QLoRA SFT of 0.5-1.5B is plausible but not sourced here.

### Cited Findings
- **SynSQL-2.5M** (`seeklhy/SynSQL-2.5M`, apache-2.0, Mar 2025): 2,544,390 `<database, question, SQL, CoT>` samples over 16,583 synthetic SQLite DBs, spanning simple to highly complex (CTEs, multi-join) with 9 linguistic styles. OmniSQL was trained on it plus Spider and BIRD train. It is SQLite-only and English-only — [dataset card](https://huggingface.co/datasets/seeklhy/SynSQL-2.5M)
- **SLM-SQL derivatives**, verified to exist on HF: `cycloneboy/SynsQL-Think-916k` (SQL generation with think), `cycloneboy/SynsQL-Merge-Think-310k` (merge/revision), `cycloneboy/bird_train`. The recipe is SFT then GRPO, then corrective self-consistency — [SLM-SQL card](https://huggingface.co/cycloneboy/SLM-SQL-0.5B); [arXiv](https://arxiv.org/abs/2507.22478)
- **FINER-SQL recipe**: GRPO on Qwen2.5-Coder 0.5B/1.5B/3B using **BIRD train only**, plus 37.6K distilled reasoning samples from 4 teachers. Rewards are format + execution + memory (semantic similarity to verified traces) + atomic (operation-level SQL overlap), and it "runs efficiently on a single 12-24 GB GPU". Code is at `github.com/thanhdath/finer-sql` — [GitHub README](https://github.com/thanhdath/finer-sql); [arXiv HTML](https://arxiv.org/html/2605.03465)
- **SQaLe** (2026): dataset `trl-lab/SQaLe-2-text-to-SQL-Queries` (verified exists). It trains RL-only GRPO from Qwen3.5-2B base, with 18 questions x 8 rollouts per step for 1,800 steps, lr 1e-5, and DAPO clip-higher. Rewards are 3.0 for an exact result match, 1.0 + 0.9·F1 for an executable but wrong query, 0.5 for one that parses but does not execute, and 0 otherwise. A model trained on SQaLe outscores the BIRD-trained one at large-schema search (66.3 vs 54.0 on the SQaLe test) — [model card](https://huggingface.co/trl-lab/qwen3.5-2b-grpo-bird)
- **Prem**: trained on BIRD train (`prem-research/birdbench`), Spider (`prem-research/spider`), `prem-research/domains`, and `gretelai/synthetic_text_to_sql`, plus self-generated error-correction data. The `premsql` library supports LoRA, QLoRA and full fine-tuning — [model card](https://huggingface.co/prem-research/prem-1B-SQL)
- **Distil Labs recipe**: 50 hand-validated seeds, ~10k synthetic examples from DeepSeek-V3, 4 epochs, lr 5e-5 cosine. It takes Qwen3-0.6B from 36% to 74% LLM-judge on their task — [model card](https://huggingface.co/distil-labs/distil-qwen3-0.6b-text2sql). Their dataset `distil-labs/text2sql-synthetic` returned HTTP 401 (gated or private) from the HF API.
- `griffith-bigdata/sft_text2sql_v2`, used for the 0.5B SQL-Writer, also returned 401 (not public) — [model card](https://huggingface.co/griffith-bigdata/Qwen-2.5-Coder-0.5B-SQL-Writer)
- The SQL-Writer SFT hyperparameters: lr 2e-5, effective batch 128, 2 epochs, 2 GPUs — [model card](https://huggingface.co/griffith-bigdata/Qwen-2.5-Coder-0.5B-SQL-Writer)

### Inferences
- The cheapest high-value path for sqltext is probably **not** training from scratch. SLM-SQL-1.5B / -0.6B and FINER-SQL-0.5B already encode SynSQL/BIRD-scale SFT+RL.
- If sqltext does fine-tune, a LoRA SFT on a filtered slice of SynSQL-Think-916k matched to sqltext's prompt format (Needle-selected tables, SQLite) is the obvious first step. It would teach the model the exact prompt sqltext uses, which the off-the-shelf models do not know.
- GRPO needs execution-in-the-loop and GPU rollouts. It is unrealistic on CPU.
- The NC license on most SLM-SQL checkpoints means a self-trained model on apache-2.0 SynSQL would be the clean commercial path. The SLM-SQL-1.5B apache-2.0 tag should be confirmed with the authors, since its card is essentially empty.

### Gaps
- No sourced CPU-only LoRA timing for 0.5-1.5B on SQL data.
- No sourced consumer-GPU (e.g. 8-16 GB) QLoRA wall-clock for SynSQL subsets.
- The license of the SynSQL-Think derivatives was not checked.
