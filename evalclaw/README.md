# EvalClaw

Automated benchmark construction + execution toolkit, packaged as a nanobot plugin.

EvalClaw turns a fuzzy "evaluate model X on capability Y" request into a fully
reproducible benchmark run on disk — eval spec, generated items, lm-eval-harness
task config, raw results, and a human-readable report.

## What ships

- **Tools** (registered via the `nanobot.tools` entry-point) for the
  deterministic IO steps in a benchmark pipeline:
  - `hf_dataset_search` — search HuggingFace datasets by query/task/language
  - `hf_dataset_sample` — peek at a few rows of a candidate dataset
  - `evalclaw_compile` — emit a lm-eval-harness-compatible
    `task.yaml` + `items.jsonl` + `eval_spec.json` triplet
- **Skills** (registered via the `nanobot.skills` entry-point) that teach a
  nanobot agent how to drive the pipeline:
  - `evalclaw` — coordinator (5-phase map + confirmation gates)
  - `evalclaw-plan`, `evalclaw-generate`, `evalclaw-qc`, `evalclaw-run`,
    `evalclaw-report` — one per phase, each self-contained so the agent
    can re-enter a phase after context compaction

## Install (development)

```bash
# from the repo root, with a venv active
pip install -e ./evalclaw

# for lm-eval execution (Phase 4) — optional until you actually run a benchmark
pip install lm-eval
```

After install, launch `nanobot`; the agent will see the new tools and skills
automatically — no config edits required.

## Quickstart

In a nanobot chat, just ask:

> Build a 20-item GSM8K-style math reasoning benchmark and run it on
> gpt-4o-mini and claude-haiku-4-5.

The agent will:

1. **Plan** — call `evalclaw-plan`, draft an `eval_spec.json`, self-critique
   against the 6-item checklist, show you a summary, and ask for confirmation.
2. **Generate** — pull from gsm8k via `hf_dataset_search` + `hf_dataset_sample`
   where applicable, fill the gap with self-generated items, write
   `raw_items.jsonl`.
3. **QC** — static checks (missing field, malformed MCQ, duplicate, leaked
   answer, ambiguous, off-topic, trivial, too-hard) + optional cold-start
   sanity run. Loops up to 3 times until `pass_rate ≥ 0.9`.
4. **Run** — call `evalclaw_compile` to emit `task.yaml`, show you the exact
   `lm-eval` invocation, ask for confirmation, then execute one run per model.
5. **Report** — read all model results, render `report.md` with headline +
   subdomain/difficulty breakdowns, and write a `memory` entry so the next
   EvalClaw run in this capability can skip trial-and-error.

## Workspace layout

Each run lands under `<nanobot workspace>/evalclaw/<run_id>/`:

```
math_2026-05-24_001/
  eval_spec.json          # Phase 1
  raw_items.jsonl         # Phase 2 (pre-QC)
  items.jsonl             # Phase 3 (QC-passed, lm-eval-compatible)
  qc_report.json          # Phase 3 summary
  task.yaml               # Phase 4 (evalclaw_compile)
  results/
    gpt-4o-mini/results.json
    claude-haiku-4-5/results.json
  report.md               # Phase 5
```

`run_id` is chosen once at the start of the pipeline (e.g.
`math_2026-05-24_001`) and reused for every tool call — that's what makes the
run reproducible from a single directory.

## Confirmation gates

Two human-in-the-loop checkpoints bracket the parts of the pipeline that cost
real money or time:

1. **After Plan, before Generate** — review the proposed `eval_spec`.
2. **After QC, before Run** — review item count + the exact `lm-eval` command.

If you change scope at either gate, the coordinator skill will redo the
affected phases with a new `run_id` rather than mutating files in place.

## Running tests

```bash
pip install -e './evalclaw[dev]'
pytest evalclaw/tests/ -v
```

55 tests cover tool behavior (compile / HF search+sample), skill markdown
validity, and an offline end-to-end pass through the full artifact tree.
