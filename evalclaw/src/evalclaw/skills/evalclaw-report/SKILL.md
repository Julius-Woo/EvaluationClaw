---
name: evalclaw-report
description: Phase 5 of EvalClaw. Read per-model results, write a human-readable report.md, and save a memory entry capturing what worked.
---

# EvalClaw Phase 5 — Report & Self-improve

**Input:**
- `<workspace>/evalclaw/<run_id>/results/<model>/` (one per model)
- `<workspace>/evalclaw/<run_id>/items.jsonl`
- `<workspace>/evalclaw/<run_id>/eval_spec.json`
- `<workspace>/evalclaw/<run_id>/qc_report.json`

**Output:** `<workspace>/evalclaw/<run_id>/report.md` + one `memory` entry.

## Step 1 — Read all model results

lm-eval writes a directory per `--output_path`, with a top-level results JSON
that looks roughly like:
```json
{
  "results": {
    "evalclaw_math_2026-05-24_001": {
      "exact_match,none": 0.82,
      "exact_match_stderr,none": 0.04
    }
  },
  "samples": { ... per-item input/output/score ... },
  "config": { ... }
}
```

Find each file with `list_dir` then `read_file`. Extract the headline metric
+ stderr per model. Save the per-sample data path for the "examples" section.

## Step 2 — Build `report.md`

Use this template (fill in real values; omit sections that don't apply):

```markdown
# EvalClaw Report — <run_id>

## Objective
<eval_spec.test_objective in 1–2 lines>

## Setup
- Items: <N> (passed QC out of <raw N>)
- Sources: <e.g. gsm8k 20, self_generated 10>
- Format: <open-ended / multiple_choice>
- Metric(s): <exact_match, acc, ...>
- Models tested: <list>

## Headline results

| Model              | <metric>   | ± stderr |
|--------------------|------------|----------|
| gpt-4o-mini        | 0.82       | 0.04     |
| claude-haiku-4-5   | 0.78       | 0.04     |

## Breakdown by subdomain

| Subdomain  | gpt-4o-mini | claude-haiku-4-5 |
|------------|-------------|------------------|
| algebra    | 0.90        | 0.85             |
| geometry   | 0.70        | 0.68             |

(Compute these from the per-sample `samples` data + the `subdomain` tag on
each item.)

## Breakdown by difficulty

(Same table shape, sliced by item.difficulty.)

## Notable items

### Where models disagreed
- **Item id=item_0042** ("If x...") — gpt correct, claude wrong.
  - Claude's output: "..."
  - Reference: "..."

(Pick 2–3 disagreement examples and 1–2 cases where both got it wrong.)

## QC notes
<paste qc_report.json summary>

## Reproducibility
- Workspace: <run_dir>
- Re-run: `lm-eval --include_path <run_dir> --tasks <task_name> ...`
```

`write_file` this to `report.md`. Then show the headline table in chat — do
**not** dump the whole markdown into the conversation.

## Step 3 — Memory write (Self-improve / Loop 3)

Save a `memory` entry under type `project` so future runs benefit. Template:

> **EvalClaw run `<run_id>` — `<capability>`.**
> Built `<N>`-item benchmark via `<source breakdown>`; HF reuse worked / didn't
> for `<repo_ids>`. Generator prompt that produced the cleanest items:
> `<one-line description>`. QC dropped `<X>%` for `<top 2 flags>`. Best model:
> `<name>` (`<metric>=<value>`). Re-do?: `<yes/no, with what change>`.

This memory is what lets the *next* EvalClaw run in the same capability skip
trial-and-error in Phase 1/2.

## Done

After the memory write:
- Call `complete_goal(recap=...)` to release the `long_task`.
- Tell the user where the report lives and what the headline result is.
- Stop. Do not propose follow-ups unless asked.
