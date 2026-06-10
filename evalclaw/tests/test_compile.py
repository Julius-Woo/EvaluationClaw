"""Tests for evalclaw_compile."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from evalclaw.tools.compile import CompileTool, _resolve_output_type, _validate_item

# ---------- _resolve_output_type ----------


@pytest.mark.parametrize(
    "format_field, expected",
    [
        ("multiple_choice", "multiple_choice"),
        ("mcq", "multiple_choice"),
        ("MCQ", "multiple_choice"),
        ("multiple-choice question", "multiple_choice"),
        ("open-ended", "generate_until"),
        ("generation", "generate_until"),
        ("open-ended generation with verifiable answers", "generate_until"),
        ("", "generate_until"),
    ],
)
def test_resolve_output_type(format_field: str, expected: str) -> None:
    assert _resolve_output_type({"test_format": format_field}) == expected


def test_resolve_output_type_missing_format_defaults_to_generate() -> None:
    assert _resolve_output_type({}) == "generate_until"


# ---------- _validate_item ----------


def test_validate_open_ended_item_normalizes_fields() -> None:
    item = {
        "question": "2+2?",
        "answer": "4",
        "source": "gsm8k",
        "extra_unused_field": "dropped",
    }
    norm, errs = _validate_item(item, 0, "generate_until")
    assert errs == []
    assert norm == {
        "id": "item_0000",
        "question": "2+2?",
        "answer": "4",
        "source": "gsm8k",
    }


def test_validate_mcq_item_requires_choices_and_gold() -> None:
    item = {"question": "Pick.", "choices": ["a", "b"], "gold": 1}
    norm, errs = _validate_item(item, 3, "multiple_choice")
    assert errs == []
    assert norm["choices"] == ["a", "b"]
    assert norm["gold"] == 1
    assert norm["id"] == "item_0003"


def test_validate_mcq_rejects_gold_out_of_range() -> None:
    item = {"question": "Pick.", "choices": ["a", "b"], "gold": 5}
    _, errs = _validate_item(item, 0, "multiple_choice")
    assert any("outside choices range" in e for e in errs)


def test_validate_open_ended_missing_answer() -> None:
    _, errs = _validate_item({"question": "2+2?"}, 0, "generate_until")
    assert any("missing required field 'answer'" in e for e in errs)


def test_validate_item_non_object() -> None:
    _, errs = _validate_item("not an object", 7, "generate_until")
    assert errs == ["items[7] must be an object, got str"]


def test_validate_mcq_missing_choices() -> None:
    _, errs = _validate_item({"question": "Pick.", "gold": 0}, 0, "multiple_choice")
    assert any("missing required field 'choices'" in e for e in errs)


def test_validate_mcq_choices_must_be_non_empty_list() -> None:
    _, errs = _validate_item(
        {"question": "Pick.", "choices": [], "gold": 0}, 0, "multiple_choice"
    )
    assert any("non-empty list" in e for e in errs)


# ---------- CompileTool integration ----------


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


@pytest.fixture
def tool(workspace: Path) -> CompileTool:
    return CompileTool(workspace=workspace)


async def test_compile_open_ended_writes_three_files(
    workspace: Path, tool: CompileTool
) -> None:
    eval_spec = {
        "test_objective": "evaluate GSM8K-style arithmetic reasoning",
        "test_format": "open-ended",
        "metrics": ["exact_match"],
    }
    items = [
        {"question": "2+2?", "answer": "4", "source": "self_generated"},
        {"question": "3*5?", "answer": "15", "source": "gsm8k"},
    ]
    out = await tool.execute(run_id="math_001", eval_spec=eval_spec, items=items)
    assert "Compiled benchmark with 2 items" in out
    assert "output_type: generate_until" in out

    run_dir = workspace / "evalclaw" / "math_001"
    assert (run_dir / "eval_spec.json").exists()
    assert (run_dir / "items.jsonl").exists()
    assert (run_dir / "task.yaml").exists()

    # eval_spec.json round-trips
    persisted_spec = json.loads((run_dir / "eval_spec.json").read_text())
    assert persisted_spec == eval_spec

    # items.jsonl: one JSON per line, in input order, with IDs populated
    lines = (run_dir / "items.jsonl").read_text().strip().splitlines()
    assert len(lines) == 2
    parsed = [json.loads(line) for line in lines]
    assert parsed[0]["question"] == "2+2?"
    assert parsed[0]["id"] == "item_0000"
    assert parsed[1]["id"] == "item_0001"

    # task.yaml: lm-eval-harness compatible structure
    task_cfg = yaml.safe_load((run_dir / "task.yaml").read_text())
    assert task_cfg["task"] == "evalclaw_math_001"
    assert task_cfg["dataset_path"] == "json"
    assert task_cfg["dataset_kwargs"] == {"data_files": "items.jsonl"}
    assert task_cfg["output_type"] == "generate_until"
    assert task_cfg["doc_to_text"] == "{{question}}"
    assert task_cfg["doc_to_target"] == "{{answer}}"
    assert task_cfg["metric_list"][0]["metric"] == "exact_match"
    assert task_cfg["metadata"]["sources"] == ["gsm8k", "self_generated"]


async def test_compile_mcq_uses_multiple_choice_schema(
    workspace: Path, tool: CompileTool
) -> None:
    eval_spec = {"test_objective": "MMLU-style MC", "test_format": "multiple_choice"}
    items = [
        {"question": "Capital of France?", "choices": ["Paris", "Lyon"], "gold": 0},
    ]
    out = await tool.execute(run_id="mc_001", eval_spec=eval_spec, items=items)
    assert "output_type: multiple_choice" in out

    task_cfg = yaml.safe_load((workspace / "evalclaw" / "mc_001" / "task.yaml").read_text())
    assert task_cfg["output_type"] == "multiple_choice"
    assert task_cfg["doc_to_choice"] == "{{choices}}"
    assert task_cfg["doc_to_target"] == "gold"
    assert task_cfg["metric_list"][0]["metric"] == "acc"


async def test_compile_returns_error_on_validation_failures(
    workspace: Path, tool: CompileTool
) -> None:
    eval_spec = {"test_format": "open-ended"}
    items = [
        {"question": "ok?", "answer": "yes"},
        {"question": "missing answer"},
        "totally invalid",
    ]
    out = await tool.execute(run_id="bad_001", eval_spec=eval_spec, items=items)
    assert out.startswith("Error: 2 validation error(s)")
    # The run_dir should not exist on validation failure (we write only after pass)
    assert not (workspace / "evalclaw" / "bad_001").exists()


async def test_compile_rejects_path_traversal(
    workspace: Path, tool: CompileTool
) -> None:
    out = await tool.execute(
        run_id="../escape",
        eval_spec={"test_format": "open-ended"},
        items=[{"question": "q", "answer": "a"}],
    )
    assert "Error:" in out and "safe path segment" in out


async def test_compile_custom_task_name(workspace: Path, tool: CompileTool) -> None:
    out = await tool.execute(
        run_id="run_x",
        eval_spec={"test_format": "open-ended"},
        items=[{"question": "q", "answer": "a"}],
        task_name="my_custom_task",
    )
    assert "task_name: my_custom_task" in out
    task_cfg = yaml.safe_load((workspace / "evalclaw" / "run_x" / "task.yaml").read_text())
    assert task_cfg["task"] == "my_custom_task"


async def test_compile_without_workspace_errors() -> None:
    # When constructed without a workspace (e.g. CompileTool() in a unit test
    # that forgot the fixture), the tool returns a clear error rather than
    # writing to disk.
    tool = CompileTool(workspace=None)
    out = await tool.execute(
        run_id="x", eval_spec={"test_format": "open-ended"},
        items=[{"question": "q", "answer": "a"}],
    )
    assert "Error:" in out and "no workspace" in out
