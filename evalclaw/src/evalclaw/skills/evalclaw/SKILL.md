---
name: evalclaw
description: Coordinator for building, quality-checking, running, and reporting an LLM benchmark end-to-end. Use whenever the user asks to "evaluate / benchmark / test" one or more models on a capability.
metadata: {"nanobot":{"emoji":"🦀"}}
---

# EvalClaw — automated benchmark pipeline

Use this skill whenever the user wants to **evaluate / benchmark / test** one
or more LLMs on a capability (math, coding, RAG QA, translation, instruction
following, …). EvalClaw turns a fuzzy request into a reproducible run on disk.

## Start fast: claim the objective

As soon as the user's intent is clear, call `long_task` with a self-contained
goal. Example:

```
long_task(goal="Build and execute a 30-item GSM8K-style math reasoning benchmark; target models = [gpt-4o-mini, claude-haiku-4-5]; deliverables = workspace/evalclaw/<run_id>/{eval_spec.json, items.jsonl, task.yaml, results/, report.md}")
```

Then proceed through the 5 phases below. Each phase has its own skill — read
that skill's full markdown (via `read_file` on the path shown by
`build_skills_summary`) before acting on that phase.

## The 5 phases

| # | Phase    | Skill              | Produces                  |
|---|----------|--------------------|---------------------------|
| 1 | Plan     | `evalclaw-plan`    | `eval_spec.json`          |
| 2 | Generate | `evalclaw-generate`| `raw_items.jsonl`         |
| 3 | QC       | `evalclaw-qc`      | `items.jsonl`, `qc_report.json` |
| 4 | Run      | `evalclaw-run`     | `task.yaml`, `results/<model>.json` |
| 5 | Report   | `evalclaw-report`  | `report.md` + memory write |

## Workspace convention

Everything lives under a single, reproducible directory:

```
<workspace>/evalclaw/<run_id>/
  eval_spec.json
  raw_items.jsonl        # generator output, pre-QC
  items.jsonl            # QC-passed items, lm-eval-compatible
  qc_report.json         # per-item flags + summary
  task.yaml              # written by evalclaw_compile
  results/
    <model_slug>.json    # one file per evaluated model
  report.md              # final human-readable summary
```

Pick `run_id` once at the start of the pipeline: a short slug + ISO date is
ideal, e.g. `math_2026-05-24_001`. Use this same `run_id` for every tool call
that takes it.

## Confirmation points (do not skip)

Always pause for human confirmation at these two points — they bracket the
parts of the pipeline that cost real money or time:

1. **After Plan, before Generate.** Show the user the proposed `eval_spec.json`
   summary (objective, scale, format, models, metrics). Ask: *"OK to proceed
   to generation?"*
2. **After QC, before Run.** Show item count, QC pass rate, and the exact
   lm-eval command that will be executed. Ask: *"OK to run on N models?"*

If the user changes scope at either gate, **redo the affected phases** rather
than mutating downstream files in place. Re-running with a new `run_id` is the
safest reset.

## When things go wrong

- **Plan can't converge on an `eval_spec`** (e.g. user requirement is internally
  contradictory): stop, summarize the conflict, ask the user to resolve.
- **Generator can't produce enough items**: report what was produced and ask
  whether to lower the scale or change strategy.
- **QC fails > 50%**: do not loop endlessly. Stop, share the QC report, ask the
  user whether the eval_spec itself needs revision.
- **Runner fails on one model but not others**: continue with the others, mark
  the failed model in the final report.

## Self-improve (Loop 3)

After Report, write a one-paragraph `memory` entry describing what worked for
this capability (which HF dataset matched, which generator prompt produced
high-quality items, which model scored best). Future EvalClaw runs in the same
domain will read this memory and skip the trial-and-error.
