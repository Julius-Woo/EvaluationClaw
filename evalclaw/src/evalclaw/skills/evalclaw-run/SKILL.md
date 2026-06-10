---
name: evalclaw-run
description: Phase 4 of EvalClaw. Compile the benchmark to lm-eval-harness format and execute it on each target model via the lm-eval CLI.
---

# EvalClaw Phase 4 — Run

**Input:** `<workspace>/evalclaw/<run_id>/items.jsonl` from Phase 3, plus the
target model list from `eval_spec.test_subject.models`.

**Output:** `<workspace>/evalclaw/<run_id>/results/<model_slug>.json` — one
file per model, as written by lm-eval-harness.

## Step 1 — Compile to lm-eval format

Call `evalclaw_compile`. It writes `task.yaml` next to `items.jsonl` and
returns the exact `lm-eval` invocation. Example:

```
evalclaw_compile(
    run_id="math_2026-05-24_001",
    eval_spec={ ...as read from eval_spec.json... },
    items=[ ...as read from items.jsonl... ],
)
```

The tool returns text including a line like:
```
Next: lm-eval --include_path <run_dir> --tasks <task_name> --model openai-chat ...
```

Use that as your command template.

## Step 2 — Run lm-eval per model

For each model in `eval_spec.test_subject.models`:

1. Resolve `--model` and `--model_args` for that model. Common cases:
   - **OpenAI-compatible chat API** (gpt-*, openai/*, most proxies):
     ```
     --model openai-chat --model_args model=<id>,base_url=<url>,api_key=<env>
     ```
   - **Anthropic API**:
     ```
     --model anthropic --model_args model=<id>,api_key=<env>
     ```
   - **Local HuggingFace model**: `--model hf --model_args pretrained=<repo>`
2. Build the `--output_path`:
   ```
   --output_path <run_dir>/results/<model_slug>/
   ```
   where `model_slug` is the model id with `/` → `_` (e.g.
   `claude-haiku-4-5` or `openai_gpt-4o-mini`).
3. **Show the user the full command** before running it. Confirm once. Then
   `exec(cmd=...)`.

Example command:
```
lm-eval \
  --include_path /workspace/evalclaw/math_2026-05-24_001 \
  --tasks evalclaw_math_2026-05-24_001 \
  --model openai-chat \
  --model_args model=gpt-4o-mini,base_url=https://api.openai.com/v1,api_key=<from env> \
  --output_path /workspace/evalclaw/math_2026-05-24_001/results/gpt-4o-mini/ \
  --apply_chat_template \
  --batch_size 4
```

## Step 3 — Concurrency

If the user has > 1 model and accepts the rate-limit risk, run them with
`spawn` in parallel — one subagent per model. Each subagent just exec's its
lm-eval command and reports back.

Otherwise run serially. Either way, stream progress (`exec` will surface
lm-eval's stderr).

## Failure handling

- **API rate limit / timeout on one model**: catch the error, mark that model
  as `"status": "failed"` in a small `run_log.json`, continue with the others.
  Phase 5 will note the omission in the report.
- **lm-eval binary not found**: tell the user to install with
  `pip install lm-eval`. Don't try to install it silently.
- **task.yaml validation error from lm-eval**: re-run `evalclaw_compile` after
  fixing the underlying issue (usually a malformed item that slipped through
  QC); do not edit `task.yaml` by hand — it will be overwritten next time.

## Exit & handoff

When all model runs complete (success or marked failure):
- List the `results/` directory contents to confirm.
- Read `evalclaw-report` and continue.
