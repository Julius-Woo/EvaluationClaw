---
name: evalclaw-qc
description: Phase 3 of EvalClaw. Filter raw_items.jsonl through static checks (and optionally a cold-start baseline run) to produce a trustworthy items.jsonl.
---

# EvalClaw Phase 3 — Quality Check (Loop 2)

**Input:** `<workspace>/evalclaw/<run_id>/raw_items.jsonl` from Phase 2.
**Output:**
- `<workspace>/evalclaw/<run_id>/items.jsonl` — QC-passed items only.
- `<workspace>/evalclaw/<run_id>/qc_report.json` — per-item flags + summary.

QC has two layers. **Always do static. Do dynamic only when the user asks for
high-rigor or when stakes are high.**

## Layer 1 — Static QC (mandatory, no model spend)

Read `raw_items.jsonl` in full. For each item, run these checks and assign
flags:

| Flag                | Check                                                         |
|---------------------|---------------------------------------------------------------|
| `MISSING_FIELD`     | Required field empty (`question`, `answer` / `choices`+`gold`)|
| `MALFORMED_MCQ`     | `gold` index outside `choices` range; `choices` not a list    |
| `DUPLICATE`         | Question text is identical (or trivially differs) from another|
| `AMBIGUOUS`         | Multiple plausible answers / underspecified prompt (LLM-judged)|
| `OFF_TOPIC`         | Doesn't measure the eval_spec's `test_objective`              |
| `LEAKED_ANSWER`     | Answer appears verbatim inside the question text              |
| `TRIVIAL`           | A 5-year-old could answer this (e.g. "2+2" when scope is L3+) |
| `TOO_HARD_FOR_SPEC` | Beyond the declared difficulty ceiling                        |

Mechanics:
- **Structural flags** (`MISSING_FIELD`, `MALFORMED_MCQ`, `DUPLICATE`,
  `LEAKED_ANSWER`): do these in code logic, no LLM call needed. Use string
  comparison for dedup; for near-duplicates, normalize whitespace + casefold
  before comparing.
- **Judgment flags** (`AMBIGUOUS`, `OFF_TOPIC`, `TRIVIAL`, `TOO_HARD_FOR_SPEC`):
  batch 10 items per LLM self-check call. Prompt:
  ```
  Given eval_spec.test_objective = "{{objective}}" and target difficulty range
  {{difficulty_range}}, classify each of the following 10 items as
  {PASS | AMBIGUOUS | OFF_TOPIC | TRIVIAL | TOO_HARD_FOR_SPEC}. Return a JSON
  array of objects: [{"id": "...", "verdict": "...", "reason": "..."}, ...].
  ```
- A single hard flag (`MISSING_FIELD`, `MALFORMED_MCQ`, `DUPLICATE`,
  `LEAKED_ANSWER`) → drop the item.
- A soft flag (`AMBIGUOUS`, `OFF_TOPIC`, `TRIVIAL`, `TOO_HARD_FOR_SPEC`) →
  drop unless the user has accepted higher noise tolerance.

## Layer 2 — Dynamic QC / cold-start (optional, costs a few $)

Skip in MVP unless:
- The user asked for a high-confidence benchmark, OR
- Static QC kept > 80% of items (suspiciously high; suggests judge is too lax).

Procedure:
1. Pick a 10–20 item random sample from the post-static set.
2. Run 2 baseline models (configurable; default `gpt-4o-mini` + a slightly
   stronger model) on this sample. Use `spawn` to parallelize.
3. Look at the score distribution:
   - All-correct or all-wrong on a question → likely trivial or broken; flag.
   - Strong model wrong but weak model correct (CAD signal) → suspicious; flag.
   - Spread looks reasonable (mix of right/wrong) → keep.

## The QC loop

Loop counter starts at 1. Each iteration:

1. Compute `pass_rate = passed / total`.
2. If `pass_rate ≥ 0.90` → exit with success.
3. If iteration ≥ 3 → exit with whatever you have, mark the QC as
   `"partial"` in `qc_report.json`.
4. Otherwise: identify which subdomain × difficulty buckets are most
   underfilled after dropping flagged items. **Hand back to `evalclaw-generate`
   with a focused regeneration request** ("regenerate 10 items for
   subdomain=algebra, difficulty=L4"). Reread that skill if needed.

## Files written

**`items.jsonl`** — same schema as raw_items.jsonl, only the passed items, in
deterministic order (sort by `source` then by original index).

**`qc_report.json`** — example:
```json
{
  "run_id": "math_2026-05-24_001",
  "iteration": 2,
  "input_count": 38,
  "passed_count": 30,
  "pass_rate": 0.789,
  "by_flag": {"AMBIGUOUS": 5, "TRIVIAL": 2, "DUPLICATE": 1},
  "by_subdomain": {"algebra": {"in": 12, "out": 10}, "geometry": {"in": 14, "out": 11}},
  "dynamic_qc_ran": false,
  "status": "ok"
}
```

## Exit & handoff

When the loop exits:
- Print the QC report summary to the user.
- Ask the second confirmation: **"Proceed to run on N models?"**
- On yes, read `evalclaw-run` and continue.
