---
name: evalclaw-generate
description: Phase 2 of EvalClaw. Produce a candidate benchmark dataset from HuggingFace reuse (path A) and/or self-generation (path B), tagged by source.
---

# EvalClaw Phase 2 — Generate

**Input:** `<workspace>/evalclaw/<run_id>/eval_spec.json` from Phase 1.
**Output:** `<workspace>/evalclaw/<run_id>/raw_items.jsonl` — one JSON item per
line, each tagged with `"source"`. **Pre-QC**, not yet trusted.

## Item schema

Every emitted item must conform to one of:

**Open-ended:**
```json
{"question": "...", "answer": "...", "source": "gsm8k", "difficulty": "L3", "subdomain": "algebra"}
```

**Multiple choice:**
```json
{"question": "...", "choices": ["A", "B", "C", "D"], "gold": 2, "source": "self_generated", "difficulty": "L2"}
```

`source`, `difficulty`, and `subdomain` are optional but **strongly preferred**
— Phase 3 (QC) and Phase 5 (Report) slice on them.

## Two paths, in priority order

### Path A — reuse an existing dataset (preferred)

1. **Search**: `hf_dataset_search(query="<capability keywords>", limit=10)`.
   Pull from the `eval_spec.preferred_sources` first; otherwise search broadly.
2. **Inspect**: For the top 2–3 candidates, call
   `hf_dataset_sample(repo_id="<candidate>", n=5)`. Look at the columns and
   sample rows.
3. **Decide matching tier**:
   - **Full match** → take items directly; map columns to the canonical
     schema; tag `source=<repo_id>`.
   - **Partial match** (covers some subdomains/difficulties) → take what
     applies; record the gap to fill via Path B.
   - **No match** → fall through to Path B.

How many items to pull from each HF dataset: roughly proportional to its
share of the target subdomain coverage. Don't pull more than `test_scale * 2`
items total — Phase 3 will trim, but huge oversampling wastes tokens.

### Path B — self-generate (fill the gap)

When Path A doesn't fully cover the spec, generate the remainder using a
meta-prompt. Template structure:

```
You are constructing a benchmark item for: {{eval_spec.test_objective}}.
Constraints:
- Subdomain: {{subdomain}}
- Difficulty: {{difficulty}}  (L1=easy, L5=hardest)
- Format: {{eval_spec.test_format}}
- Must have a single verifiable answer.
- Avoid: {{eval_spec.test_content.potential_issues | join(', ')}}

Produce N={{batch_size}} items as a JSON array. Each item:
{ "question": "...", "answer": "...", "subdomain": "{{subdomain}}", "difficulty": "{{difficulty}}" }
```

Batching:
- Generate **10–20 items per call** (smaller batches → higher quality, more
  API calls). Add `"source": "self_generated"` to every item before persisting.
- Loop until you've covered the spec's `difficulty_distribution * subdomains`
  grid. Stop early if hitting `test_scale * 1.5`.

### Optional — multi-trial designers (skip in MVP unless asked)

For high-stakes runs, use `spawn` to launch 2–3 parallel subagents, each given
the same generation prompt but different designer models (via the spawn's
`temperature` and the agent's chosen target model). Then merge their outputs
and let Phase 3 dedupe.

## Persisting

Append items to `raw_items.jsonl` (one JSON per line) as you go. Use
`write_file` for the initial write and read-modify-write for appends — keep
the file under ~10 MB so `read_file` can show all of it later.

After all items are written, **show the user**:
- Total count
- Breakdown by `source` (HF reuse vs. self-generated)
- Breakdown by `subdomain` and `difficulty`

## Common pitfalls

- **Duplicates across paths**: HF items may overlap with self-generated ones
  (e.g. you reused MMLU and also asked the model to make MMLU-style questions).
  Phase 3 dedupes, but try not to make the QC work harder than necessary.
- **HF dataset columns ≠ canonical schema**: gsm8k uses `question`/`answer`,
  MMLU uses `question`/`choices`/`answer` (where `answer` is a letter, not an
  index). **Normalize before writing** — Phase 3 won't fix shape errors.
- **Self-gen with the same model you're evaluating**: avoid. If
  `eval_spec.test_subject.models` includes the generator model, swap to a
  different model for generation, or warn the user about the bias.

## Exit & handoff

When `raw_items.jsonl` is complete and ≥ `test_scale` items:
- Tell the user the source breakdown.
- Read `evalclaw-qc` and continue.
