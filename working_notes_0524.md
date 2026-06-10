# EvalClaw MVP — Working Notes (2026-05-24)

Implementation log for the EvalClaw MVP built on top of the nanobot agent
framework. Companion to `technical_proposal_v3.md` (the design spec).

## 1. Goals recap

Build a slim, end-to-end pipeline that converts a natural-language
"evaluate model X on capability Y" request into a reproducible on-disk
benchmark run, by leaning on nanobot's existing capabilities rather than
reimplementing them. The MVP is the **happy path only** — feedback loops are
expressed in skill markdown (looping rules, exit conditions), not in Python.

**Non-goals for MVP:** dynamic difficulty modeling via IRT, multi-trial
designer ensembles, MCP-based lm-eval integration, GUI.

## 2. Architecture decisions

| Decision | Outcome | Why |
|---|---|---|
| **Skills drive cognitive loops; Python only for deterministic IO** | 6 skill markdowns + 3 Python tools | Hybrid keeps the LLM responsible for judgment (eval_spec drafting, QC verdicts, regeneration targeting) while pinning the brittle filesystem/CLI surfaces to code |
| **Independent subpackage `evalclaw/` registered via entry-points** | Standalone pyproject; install with `pip install -e ./evalclaw` | Keeps the eval surface a drop-in plugin; nanobot core stays unaware. Same install path third parties would take |
| **HF dataset reuse in MVP** | `hf_dataset_search` + `hf_dataset_sample` shipped | Free, rate-limit-friendly, high-value: ~70% of common capabilities already have a curated HF dataset; the agent prefers reuse over self-generation |
| **lm-eval via local CLI, not MCP** | Phase 4 skill builds and `exec()`s the command | One less moving part; users can also re-run the exact command by hand for debugging. `evalclaw_compile` emits the command verbatim in its return value |
| **Upstream patch for `nanobot.skills` entry-point (Option A)** | `nanobot/agent/skills.py` patched | Skill discovery is a generic capability that belongs upstream — there was no reason to fork or duck-punch from the plugin side |

## 3. Final file inventory

### 3.1 Upstream patch — nanobot core

| File | Change |
|---|---|
| `nanobot/agent/skills.py` | +95 lines: `_SKILL_PLUGIN_GROUP = "nanobot.skills"`, `_resolve_skill_plugin_root()`, `discover_plugin_skill_roots()` (cached), `_reset_plugin_skill_roots_cache()` test hook. `SkillsLoader.__init__` accepts `plugin_skill_roots: list[Path] \| None = None` (default = auto-discover; `[]` = disable). `list_skills` + `load_skill` traverse workspace → builtin → plugins in that precedence order |
| `tests/agent/test_skills_loader.py` | +13 lines: autouse fixture `_no_plugin_skill_discovery` monkeypatches discovery to `[]` so the installed evalclaw plugin doesn't contaminate pre-existing tests |
| `tests/agent/test_skills_loader_plugins.py` | NEW, 138 lines, 5 tests: explicit plugin_skill_roots, workspace-shadows-plugin precedence, default auto-discovery (via monkeypatched discoverer), payload type coercion (Path/str/Module), caching behavior |

The entry-point payload format accepts three shapes — `Path`, `str`, or a
Python module/package with `__path__` — covered by `_resolve_skill_plugin_root`.
This gives downstream packages flexibility (we ship the module form via
`evalclaw.skills`, which is the most idiomatic for a packaged plugin).

### 3.2 EvalClaw subpackage — `evalclaw/`

```
evalclaw/
├── pyproject.toml                          # hatchling, entry-points, deps
├── README.md                                # user-facing intro + quickstart
├── src/evalclaw/
│   ├── __init__.py
│   ├── config.py                            # 42L: Pydantic EvalClawConfig
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── compile.py                       # 281L: evalclaw_compile tool
│   │   └── hf.py                            # 286L: HF search + sample tools
│   └── skills/
│       ├── __init__.py                      # exports SKILLS_DIR Path
│       ├── evalclaw/SKILL.md                # 87L coordinator
│       ├── evalclaw-plan/SKILL.md           # 97L
│       ├── evalclaw-generate/SKILL.md       # 107L
│       ├── evalclaw-qc/SKILL.md             # 103L
│       ├── evalclaw-run/SKILL.md            # 93L
│       └── evalclaw-report/SKILL.md         # 112L
└── tests/
    ├── __init__.py
    ├── test_compile.py                      # 218L, 22 tests
    ├── test_hf.py                           # 261L, 16 tests
    ├── test_skills_parse.py                 # 87L, 10 tests
    └── test_e2e_offline.py                  # 270L, 7 tests
```

**Totals:** 1209 lines of production code + skills; 836 lines of tests; 55
plugin tests + 24 upstream tests = 79 tests passing; `ruff check evalclaw/`
clean.

## 4. Tools — deterministic IO

### 4.1 `evalclaw_compile` (`evalclaw/tools/compile.py`)

Compiles a `(eval_spec, items)` pair into the three files lm-eval-harness
needs to run: `eval_spec.json` (audit trail), `items.jsonl` (one JSON per
line), `task.yaml` (lm-eval task config).

Key behaviors worth flagging:

- **`output_type` resolution** maps `eval_spec.test_format` to lm-eval's
  `multiple_choice` vs. `generate_until`. Recognizes a handful of aliases
  (`mcq`, `mc`, `multiple-choice`, `open-ended`, `qa`, …) and falls back to
  keyword detection for natural-language formats like
  `"open-ended generation with verifiable answers"`. Default is
  `generate_until`.
- **Item validation** rejects on missing required fields, non-list `choices`,
  non-int `gold`, out-of-range `gold`. Errors aggregate with the first 10
  shown; the run dir is **not created** on validation failure (no half-state).
- **Path traversal protection**: `run_id` must be a single safe path segment
  (rejects `/`, `\`, `.`, `..`, empty string).
- **task.yaml shape:** for `generate_until`, includes `generation_kwargs`
  (`until: ["\n\n"]`, `max_gen_toks: 512`, `do_sample: False`) and an
  `exact_match` metric. For `multiple_choice`, uses `doc_to_choice` +
  `doc_to_target: gold` with an `acc` metric. Metadata block carries the
  sources list and the eval objective for downstream audit.
- **Return value** ends with a runnable `lm-eval --include_path ... --tasks
  ... --output_path <run_dir>/results` hint so Phase 4's skill can copy/paste.

### 4.2 `hf_dataset_search` + `hf_dataset_sample` (`evalclaw/tools/hf.py`)

Both `read_only=True`.

- **Search** wraps `huggingface_hub.HfApi.list_datasets`. Accepts `query`,
  `task_categories`, `language`, `sort` (default `downloads`), `limit`
  (clamped to 25). Returns a compact text summary (id, downloads, likes,
  tags) — context-efficient compared to JSON dumps.
- **Sample** wraps `datasets.load_dataset(split=f"{split}[:{n}]")`. If
  `split` is unspecified, auto-detects via `get_dataset_split_names` and
  falls back through `test → validation → train`. Forces
  `trust_remote_code=False`. `n` clamped to 50. Long string/list values are
  truncated for context efficiency.
- **Auth**: `_hf_token()` reads `HF_TOKEN` or `HUGGINGFACE_HUB_TOKEN` from
  env — no token required for public datasets.

Tested with 16 mock-based tests + one live network smoke test (returned
`openai/gsm8k` correctly for `query="gsm8k"`).

## 5. Skills — the cognitive driver

Six markdown files, each self-contained: the agent re-enters a phase by
reading just that file — no implicit dependency on prior conversation
context (important for surviving context compaction mid-run).

| Skill | Phase | Frontmatter description (one-line summary) |
|---|---|---|
| `evalclaw` | Coordinator | "Use whenever the user asks to evaluate/benchmark/test one or more models on a capability" |
| `evalclaw-plan` | 1 | Turn fuzzy request → `eval_spec.json` via 6-item checklist self-critique loop (≤ 5 iter) |
| `evalclaw-generate` | 2 | HF reuse (Path A) + ZSB self-generation (Path B), tagged by source |
| `evalclaw-qc` | 3 | Static 8-flag filter + optional cold-start; loop until pass_rate ≥ 0.9 or 3 iter |
| `evalclaw-run` | 4 | Compile + per-model `lm-eval` exec (3 `--model` templates: openai-chat / anthropic / hf) |
| `evalclaw-report` | 5 | Read `results/*/results.json`, render report.md, write memory entry |

Design rules baked into every phase skill:
- **Workspace path convention** (`<workspace>/evalclaw/<run_id>/...`) is
  restated in every skill so a single skill can be entered cold.
- **Loop exit conditions** (checklist score ≥ 4, pass_rate ≥ 0.9, iter caps)
  are hardcoded in markdown — no Python orchestrator.
- **Confirmation gates** at two points: after Plan (review spec) and after
  QC (review the lm-eval command). Enforced by the coordinator skill.
- **Self-improve (Loop 3)**: `evalclaw-report` ends with a templated memory
  write under `type: project`, so future EvalClaw runs in the same
  capability inherit "what worked" without re-deriving it.

## 6. Tests

| File | Count | Covers |
|---|---:|---|
| `tests/test_compile.py` | 22 | output_type resolution (8 parameterized), item validation (7 cases), open-ended + MCQ write, validation failure, path traversal, custom task_name, no-workspace error |
| `tests/test_hf.py` | 16 | search filters, limit clamping, empty results, API exceptions, sample split fallback, n cap, all-splits-fail, long value truncation, empty dataset |
| `tests/test_skills_parse.py` | 10 | All 6 expected skills present, frontmatter `name`/`description` valid, body non-empty, each phase skill references workspace path convention, coordinator references all 5 phase skills, `SKILLS_DIR` resolves to packaged path |
| `tests/test_e2e_offline.py` | 7 | Offline end-to-end: compile produces lm-eval-compatible artifacts (open-ended + MCQ), Phase-5 read flow renders report.md from fabricated lm-eval results, full workspace layout matches coordinator spec, malformed items get rejected with no partial state |
| `tests/agent/test_skills_loader_plugins.py` (upstream) | 5 | Entry-point discovery: explicit roots, workspace precedence, auto-discovery, type coercion (Path/str/Module), caching |
| `tests/agent/test_skills_loader.py` (upstream, modified) | 19 | Pre-existing tests, all still pass with autouse fixture that disables plugin discovery to keep test space hermetic |

**Why "offline" e2e instead of live lm-eval:** the two data contracts that
actually matter are (a) the lm-eval YAML schema we emit and (b) the lm-eval
results JSON shape we read. The offline test exercises both. A failure in
the middle (real lm-eval call) would necessarily be in lm-eval or the model
API, not in EvalClaw — and would also cost money to run in CI.

## 7. Setup notes

- Repo uses an externally-managed system Python; venv lives at `.venv/`.
  Always use `.venv/bin/pip` / `.venv/bin/pytest` / `.venv/bin/ruff`.
- After `pip install -e ./evalclaw`, the entry-points become visible
  immediately — no need to restart anything. Verified by direct call:
  `SkillsLoader(workspace=Path("/tmp/...")).list_skills()` returns all 6
  evalclaw skills with valid frontmatter.
- **Important** for nanobot core's own skills loader tests: the autouse
  `_no_plugin_skill_discovery` fixture in `tests/agent/test_skills_loader.py`
  is what keeps those 14 pre-existing tests passing after the evalclaw
  plugin is installed in dev mode. Don't remove it.

## 8. Known limitations / explicitly deferred

| Item | Why deferred | Where it would slot in |
|---|---|---|
| IRT / dynamic difficulty modeling | Adds heavy stats dep + meaningful UX surface for a feature that's not on the critical path | New tool `evalclaw_irt_analyze`; `evalclaw-report` would call it |
| Multi-trial designer ensemble (Phase 2 spawn) | Skill markdown documents how to do it via `spawn`; no Python code needed | Add a `--ensemble` flag to the user-facing prompt; skill already references it |
| Adaptive sampling / CAD | Same as IRT — value-add but not core to "make a benchmark from scratch" | New tool reading `qc_report.json` to recommend additions |
| Heuristic lm-eval `--model` detection | Skill markdown lists 3 templates by hand; agent picks based on model id substring | Could become a small Python helper if it grows fragile |
| Pre-flight `lm-eval` availability check | Skill tells user to `pip install lm-eval` on missing binary | Could become a precondition in the run skill |

## 9. Verification commands

```bash
# Full evalclaw test suite (55 tests)
.venv/bin/pytest evalclaw/tests/ -v

# Plus the upstream skills loader tests affected by our patch (24 more)
.venv/bin/pytest evalclaw/tests/ tests/agent/test_skills_loader.py \
                 tests/agent/test_skills_loader_plugins.py -q
# expected: 79 passed

# Lint
.venv/bin/ruff check evalclaw/
# expected: All checks passed!
```

## 10. What a real end-to-end would look like

Outside this MVP's scope, but documenting the recipe so it's ready when you
want to validate live:

```bash
pip install -e ./evalclaw
pip install lm-eval
export OPENAI_API_KEY=...
export ANTHROPIC_API_KEY=...   # optional
nanobot   # then say: "build a 20-item GSM8K-style math benchmark, test gpt-4o-mini"
```

Expected artifacts after the agent finishes:

```
~/.nanobot/workspace/evalclaw/math_2026-05-24_001/
  eval_spec.json
  raw_items.jsonl
  items.jsonl
  qc_report.json
  task.yaml
  results/gpt-4o-mini/results.json
  report.md
```

Cost ballpark for a 20-item run on gpt-4o-mini: < $0.05 in model spend.

## 11. PR-ready scope summary

If split into PRs against the nanobot repo, the cleanest break is:

1. **PR 1 — Upstream**: `nanobot/agent/skills.py` entry-point support + new
   plugin tests + autouse fixture in existing tests. (~250 lines net.)
2. **PR 2 — EvalClaw subpackage**: the entire `evalclaw/` tree as a single
   commit. No nanobot code touched. Reviewer can install with
   `pip install -e ./evalclaw` and validate by running the 55 tests.
