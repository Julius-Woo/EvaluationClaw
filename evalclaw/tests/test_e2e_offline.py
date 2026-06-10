"""End-to-end happy-path verification for EvalClaw, run offline.

This test exercises every artifact the pipeline produces — eval_spec.json,
items.jsonl, task.yaml, results/<model>/results.json, report.md — without
making any real LLM or lm-eval-harness calls. It is the closest thing to a
"does the whole thing fit together" check that we can run in CI.

What it does NOT cover (and what a true online run would add):
  * actually invoking ``lm-eval`` against the written ``task.yaml``
  * real HF dataset download via ``HFDatasetSampleTool``
  * the agent's choice of skill / tool sequencing (that's owned by the skill
    markdown, validated separately in ``test_skills_parse.py``)
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from evalclaw.tools.compile import CompileTool


def _open_ended_spec() -> dict[str, Any]:
    return {
        "test_objective": "Evaluate gpt-4o-mini and claude-haiku-4-5 on grade-school math word problems.",
        "test_subject": {
            "type": "llm_api",
            "models": ["gpt-4o-mini", "claude-haiku-4-5"],
        },
        "test_format": "open-ended",
        "test_content": {
            "dimensions": ["arithmetic", "algebra"],
            "difficulty_distribution": {"L1": 0.5, "L2": 0.5},
            "potential_issues": ["ambiguous phrasing"],
        },
        "test_scale": 20,
        "metrics": ["exact_match"],
        "preferred_sources": ["gsm8k"],
    }


def _mcq_spec() -> dict[str, Any]:
    spec = _open_ended_spec()
    spec["test_objective"] = "Evaluate models on a 4-choice arithmetic MCQ benchmark."
    spec["test_format"] = "multiple_choice"
    spec["metrics"] = ["acc"]
    return spec


def _fake_open_ended_items(n: int = 20) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for i in range(n):
        a, b = i + 1, i + 2
        subdomain = "arithmetic" if i < n // 2 else "algebra"
        difficulty = "L1" if i % 2 == 0 else "L2"
        # Tag half as HF-sourced and half as self-generated to exercise both paths.
        source = "gsm8k" if i < n // 2 else "self_generated"
        items.append({
            "question": f"What is {a} + {b}?",
            "answer": str(a + b),
            "source": source,
            "difficulty": difficulty,
            "subdomain": subdomain,
        })
    return items


def _fake_mcq_items(n: int = 8) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for i in range(n):
        correct = (i + 3) * 2
        distractors = [correct - 2, correct - 1, correct + 1]
        choices = [str(c) for c in [distractors[0], correct, distractors[1], distractors[2]]]
        items.append({
            "question": f"What is {i + 3} times 2?",
            "choices": choices,
            "gold": 1,
            "source": "self_generated",
            "difficulty": "L1",
            "subdomain": "arithmetic",
        })
    return items


def _run_compile(workspace: Path, run_id: str, spec: dict[str, Any], items: list[dict[str, Any]]) -> str:
    tool = CompileTool(workspace=workspace)
    return asyncio.run(tool.execute(run_id=run_id, eval_spec=spec, items=items))


# --------------------------------------------------------------------------- #
# 1. Compile produces the three on-disk artifacts in lm-eval-compatible shape.
# --------------------------------------------------------------------------- #


def test_compile_open_ended_writes_lm_eval_compatible_layout(tmp_path: Path) -> None:
    run_id = "math_e2e_open"
    result = _run_compile(tmp_path, run_id, _open_ended_spec(), _fake_open_ended_items())

    run_dir = tmp_path / "evalclaw" / run_id
    assert run_dir.is_dir(), result

    # Each file the skills/README promise must exist.
    spec_path = run_dir / "eval_spec.json"
    items_path = run_dir / "items.jsonl"
    task_path = run_dir / "task.yaml"
    for p in (spec_path, items_path, task_path):
        assert p.is_file(), f"missing artifact: {p}"

    # eval_spec.json round-trips.
    on_disk_spec = json.loads(spec_path.read_text())
    assert on_disk_spec["test_objective"] == _open_ended_spec()["test_objective"]

    # items.jsonl is one valid JSON object per line, with stable ids.
    lines = items_path.read_text().splitlines()
    assert len(lines) == 20
    parsed = [json.loads(line) for line in lines]
    assert all("id" in item and item["id"].startswith("item_") for item in parsed)
    assert all("question" in item and "answer" in item for item in parsed)

    # task.yaml has every field lm-eval-harness requires for generate_until.
    task_cfg = yaml.safe_load(task_path.read_text())
    assert task_cfg["task"] == f"evalclaw_{run_id}"
    assert task_cfg["dataset_path"] == "json"
    assert task_cfg["dataset_kwargs"]["data_files"] == "items.jsonl"
    assert task_cfg["output_type"] == "generate_until"
    assert task_cfg["doc_to_text"] == "{{question}}"
    assert task_cfg["doc_to_target"] == "{{answer}}"
    assert task_cfg["generation_kwargs"]["max_gen_toks"] > 0
    assert task_cfg["metric_list"][0]["metric"] == "exact_match"

    # The returned text gives the user a runnable invocation hint.
    assert "lm-eval --include_path" in result
    assert f"--tasks evalclaw_{run_id}" in result


def test_compile_mcq_produces_multiple_choice_task(tmp_path: Path) -> None:
    run_id = "mcq_e2e"
    result = _run_compile(tmp_path, run_id, _mcq_spec(), _fake_mcq_items())
    assert "Error" not in result, result

    task_cfg = yaml.safe_load((tmp_path / "evalclaw" / run_id / "task.yaml").read_text())
    assert task_cfg["output_type"] == "multiple_choice"
    assert task_cfg["doc_to_choice"] == "{{choices}}"
    assert task_cfg["doc_to_target"] == "gold"
    assert task_cfg["metric_list"][0]["metric"] == "acc"


# --------------------------------------------------------------------------- #
# 2. The Phase-5 read path: given lm-eval-style results, we can build a report.
# --------------------------------------------------------------------------- #


def _write_fake_lm_eval_results(run_dir: Path, model_slug: str, task_name: str, score: float) -> Path:
    """Fabricate the JSON shape that lm-eval-harness produces under --output_path."""
    out_dir = run_dir / "results" / model_slug
    out_dir.mkdir(parents=True, exist_ok=True)
    results_path = out_dir / "results.json"
    results_path.write_text(json.dumps({
        "results": {
            task_name: {
                "exact_match,none": score,
                "exact_match_stderr,none": 0.04,
                "alias": task_name,
            }
        },
        "config": {"model": model_slug, "tasks": [task_name]},
        "git_hash": "deadbeef",
    }, indent=2))
    return results_path


def test_phase5_read_results_and_render_report(tmp_path: Path) -> None:
    """Mirror what an agent does in evalclaw-report: read each model's
    results.json, extract the headline metric, build a report.md."""
    run_id = "math_e2e_report"
    _run_compile(tmp_path, run_id, _open_ended_spec(), _fake_open_ended_items())
    run_dir = tmp_path / "evalclaw" / run_id

    task_name = f"evalclaw_{run_id}"
    _write_fake_lm_eval_results(run_dir, "gpt-4o-mini", task_name, score=0.85)
    _write_fake_lm_eval_results(run_dir, "claude-haiku-4-5", task_name, score=0.78)

    # Same logic the skill describes: enumerate results/*/results.json,
    # pull the single task's headline metric, build a markdown table.
    headline: list[tuple[str, float, float]] = []
    for results_json in sorted(run_dir.glob("results/*/results.json")):
        model_slug = results_json.parent.name
        payload = json.loads(results_json.read_text())
        task_block = payload["results"][task_name]
        metric_key = next(k for k in task_block if k.startswith("exact_match,"))
        stderr_key = next(k for k in task_block if k.startswith("exact_match_stderr,"))
        headline.append((model_slug, task_block[metric_key], task_block[stderr_key]))

    assert {row[0] for row in headline} == {"gpt-4o-mini", "claude-haiku-4-5"}

    lines = [
        f"# EvalClaw Report — {run_id}",
        "",
        "| Model | exact_match | ± stderr |",
        "|---|---|---|",
    ]
    for slug, score, err in sorted(headline):
        lines.append(f"| {slug} | {score:.2f} | {err:.2f} |")
    report_path = run_dir / "report.md"
    report_path.write_text("\n".join(lines) + "\n")

    rendered = report_path.read_text()
    assert "gpt-4o-mini | 0.85" in rendered
    assert "claude-haiku-4-5 | 0.78" in rendered
    assert run_id in rendered


# --------------------------------------------------------------------------- #
# 3. The whole on-disk tree matches what the coordinator skill documents.
# --------------------------------------------------------------------------- #


def test_workspace_layout_matches_coordinator_skill_spec(tmp_path: Path) -> None:
    run_id = "layout_e2e"
    _run_compile(tmp_path, run_id, _open_ended_spec(), _fake_open_ended_items())
    run_dir = tmp_path / "evalclaw" / run_id

    # Compile-produced artifacts (Phases 1, 3, 4 share files written here).
    assert (run_dir / "eval_spec.json").is_file()
    assert (run_dir / "items.jsonl").is_file()
    assert (run_dir / "task.yaml").is_file()

    # Simulate the rest of Phase 4 + Phase 5 dropping their own files in place.
    _write_fake_lm_eval_results(run_dir, "gpt-4o-mini", f"evalclaw_{run_id}", 0.81)
    (run_dir / "raw_items.jsonl").write_text((run_dir / "items.jsonl").read_text())
    (run_dir / "qc_report.json").write_text(json.dumps({"pass_rate": 1.0, "status": "ok"}))
    (run_dir / "report.md").write_text(f"# EvalClaw Report — {run_id}\n")

    # Every path the coordinator skill (`evalclaw/SKILL.md`) promises exists.
    expected = {
        "eval_spec.json",
        "raw_items.jsonl",
        "items.jsonl",
        "qc_report.json",
        "task.yaml",
        "report.md",
    }
    actual = {p.name for p in run_dir.iterdir() if p.is_file()}
    assert expected.issubset(actual), f"missing: {expected - actual}"
    assert (run_dir / "results" / "gpt-4o-mini" / "results.json").is_file()


# --------------------------------------------------------------------------- #
# 4. Failure modes the pipeline must surface clearly.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("bad_item", [
    {"question": "", "answer": "x"},             # empty required field
    {"answer": "x"},                              # missing question
    {"question": "q", "answer": ""},              # empty answer
])
def test_compile_rejects_malformed_open_ended_item_and_writes_nothing(
    tmp_path: Path, bad_item: dict[str, Any]
) -> None:
    items = _fake_open_ended_items(n=3) + [bad_item]
    result = _run_compile(tmp_path, "bad_e2e", _open_ended_spec(), items)
    assert result.startswith("Error:"), result
    # No partial state should be left around when validation fails.
    assert not (tmp_path / "evalclaw" / "bad_e2e").exists()
