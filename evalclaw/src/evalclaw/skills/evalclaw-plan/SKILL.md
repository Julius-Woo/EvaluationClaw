---
name: evalclaw-plan
description: Phase 1 of EvalClaw. Turn a fuzzy "evaluate model X on Y" request into a structured eval_spec.json via research + self-critique loop.
---

# EvalClaw Phase 1 — Plan (eval_spec)

**Input:** the user's natural-language evaluation request.
**Output:** `<workspace>/evalclaw/<run_id>/eval_spec.json` — a structured spec
that downstream phases consume verbatim.

## eval_spec schema

```json
{
  "test_objective": "Evaluate X model(s) on Y capability for Z reason",
  "test_subject": {
    "type": "llm_api",
    "models": ["gpt-4o-mini", "claude-haiku-4-5"]
  },
  "test_format": "open-ended | multiple_choice",
  "test_content": {
    "dimensions": ["algebra", "geometry", "number_theory"],
    "difficulty_distribution": {"L1": 0.1, "L2": 0.3, "L3": 0.4, "L4": 0.2},
    "potential_issues": ["ambiguous phrasing", "multiple valid answers"]
  },
  "test_scale": 30,
  "metrics": ["exact_match", "accuracy_by_difficulty"],
  "preferred_sources": ["gsm8k", "self_generated"]
}
```

Field guide:
- `test_format` controls which lm-eval `output_type` `evalclaw_compile`
  produces. Use `"multiple_choice"` only if items have a fixed choice list;
  otherwise `"open-ended"`.
- `test_scale` defaults to 30 if the user didn't specify. Keep MVP runs ≤ 100
  unless the user explicitly asks for more.
- `preferred_sources` is a hint — Phase 2 may override based on what's
  actually available.

## How to plan

1. **Research the capability.** Use `web_search` to learn how the field
   typically measures this skill — what benchmarks exist, what metrics are
   standard, what failure modes are known. Cross-check with the internal
   taxonomy hints below before searching, to save tokens.
2. **Draft the eval_spec.** Fill every field. Where the user was silent, pick
   sensible defaults and call them out so they can override.
3. **Self-critique against the 6-item checklist** (next section).
4. **Iterate.** If any checklist item is not satisfied, revise. Hard cap: 5
   iterations. Stop early if the user has already explicitly answered every
   ambiguous point.
5. **Persist.** `write_file` to `<workspace>/evalclaw/<run_id>/eval_spec.json`
   (pretty-printed). Show the user a one-paragraph summary + ask for the
   first confirmation gate.

## Self-critique checklist (all 6 must pass)

For each item, ask "is this *unambiguously* specified?":

1. **Objective** — What capability is being measured? In one sentence.
2. **Subject** — Which models, by exact ID? (vendor + slug)
3. **Format** — Open-ended generation or multiple-choice?
4. **Content** — Which subdimensions, difficulty mix, expected pitfalls?
5. **Scale** — How many items?
6. **Metrics** — Which scoring functions, with what aggregation?

After each draft, score yourself **0–5 per item** and only stop when all six
are ≥ 4. Show the user the scorecard if you exceed 3 iterations.

## Taxonomy quick-reference

Use these as starting points before web-searching:

| Capability        | Typical benchmarks                          | Default metric    |
|-------------------|---------------------------------------------|-------------------|
| Math reasoning    | gsm8k, MATH, aime, minerva-math             | exact_match       |
| Code generation   | humaneval, mbpp, swe-bench, bigcodebench    | pass@1            |
| MCQA / knowledge  | mmlu, mmlu-pro, agieval                     | acc               |
| Reading QA        | squad, drop, narrativeqa                    | exact_match / f1  |
| Translation       | wmt, flores                                 | bleu / chrf       |
| Instruction follow| ifeval, mt-bench, alpacaeval                | llm_judge / acc   |
| RAG QA            | natural_questions, hotpotqa, ms_marco       | exact_match / f1  |

When the user's request maps cleanly onto one of these, **prefer the
established benchmark over self-generation** — Phase 2 will reuse it via
`hf_dataset_search`.

## Exit & handoff

When all 6 checks pass:

1. Write `eval_spec.json`.
2. Tell the user *one* line per dimension of the spec (no JSON dump in chat).
3. Ask: **"Proceed to generation? (yes / refine X)"**
4. On yes, read `evalclaw-generate` and continue.
