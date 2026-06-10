"""Compile generated benchmark items into an lm-eval-harness task config + JSONL.

The tool takes:
  * ``run_id`` — directory name under ``<workspace>/evalclaw/``
  * ``eval_spec`` — the structured spec produced by the planner skill
  * ``items``   — generated/curated benchmark items

It writes three files under ``<workspace>/evalclaw/<run_id>/``:
  * ``eval_spec.json`` — pretty-printed spec (audit trail)
  * ``items.jsonl``    — one normalized item per line, lm-eval compatible
  * ``task.yaml``      — lm-eval-harness task config that loads ``items.jsonl``

The resulting directory is everything the user needs to run
``lm-eval --include_path <run_dir> --tasks <task_name> ...``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from nanobot.agent.tools.base import Tool, tool_parameters

# Two task formats are supported in MVP. Anything else raises a clear error.
_MCQ_FORMATS = frozenset({
    "multiple_choice", "multiple-choice", "mcq", "mc",
})
_OPEN_ENDED_FORMATS = frozenset({
    "open_ended", "open-ended", "openended", "generation", "generate", "qa",
})

_REQUIRED_OPEN_ENDED_FIELDS = ("question", "answer")
_REQUIRED_MCQ_FIELDS = ("question", "choices", "gold")

# Item fields we preserve in the JSONL on disk. Anything outside this list is
# dropped to keep the dataset deterministic and lm-eval-friendly.
_ITEM_FIELDS_OPEN_ENDED = ("id", "question", "answer", "source", "difficulty", "subdomain")
_ITEM_FIELDS_MCQ = ("id", "question", "choices", "gold", "source", "difficulty", "subdomain")

_RUN_DIR_ROOT = "evalclaw"


def _resolve_output_type(eval_spec: dict[str, Any]) -> str:
    """Map eval_spec.test_format to an lm-eval-harness ``output_type``."""
    raw = str(eval_spec.get("test_format", "")).strip().lower()
    if not raw:
        # Default to open-ended generation when the planner didn't pin a format.
        return "generate_until"
    if raw in _MCQ_FORMATS:
        return "multiple_choice"
    if raw in _OPEN_ENDED_FORMATS:
        return "generate_until"
    # The format hint may be a natural-language phrase like
    # "open-ended generation with verifiable answers"; do keyword detection.
    if "multiple" in raw or "choice" in raw or "mcq" in raw:
        return "multiple_choice"
    return "generate_until"


def _validate_item(item: Any, idx: int, output_type: str) -> tuple[dict[str, Any], list[str]]:
    """Normalize one item and return ``(item_or_empty_dict, error_list)``."""
    errors: list[str] = []
    if not isinstance(item, dict):
        return {}, [f"items[{idx}] must be an object, got {type(item).__name__}"]

    required = _REQUIRED_MCQ_FIELDS if output_type == "multiple_choice" else _REQUIRED_OPEN_ENDED_FIELDS
    for field in required:
        if field not in item or item[field] in (None, ""):
            errors.append(f"items[{idx}] missing required field '{field}'")

    if output_type == "multiple_choice":
        choices = item.get("choices")
        if choices is not None and (not isinstance(choices, list) or not choices):
            errors.append(f"items[{idx}].choices must be a non-empty list")
        gold = item.get("gold")
        if gold is not None:
            if not isinstance(gold, int) or isinstance(gold, bool):
                errors.append(f"items[{idx}].gold must be an integer index")
            elif isinstance(choices, list) and not (0 <= gold < len(choices)):
                errors.append(
                    f"items[{idx}].gold={gold} is outside choices range "
                    f"[0,{len(choices)})"
                )

    if errors:
        return {}, errors

    allowed = _ITEM_FIELDS_MCQ if output_type == "multiple_choice" else _ITEM_FIELDS_OPEN_ENDED
    normalized: dict[str, Any] = {k: item[k] for k in allowed if k in item}
    normalized.setdefault("id", f"item_{idx:04d}")
    return normalized, []


def _build_task_yaml(
    *,
    task_name: str,
    output_type: str,
    items_filename: str,
    eval_spec: dict[str, Any],
    sources: list[str],
) -> dict[str, Any]:
    """Build the lm-eval-harness task config (as a dict; YAML-serialized later)."""
    config: dict[str, Any] = {
        "task": task_name,
        "dataset_path": "json",
        "dataset_kwargs": {"data_files": items_filename},
        # ``datasets.load_dataset("json", ...)`` exposes a single ``train``
        # split; lm-eval is happy as long as ``test_split`` points at one of
        # the loaded splits.
        "test_split": "train",
        "output_type": output_type,
        "doc_to_text": "{{question}}",
    }
    if output_type == "multiple_choice":
        config["doc_to_choice"] = "{{choices}}"
        config["doc_to_target"] = "gold"
        config["metric_list"] = [
            {"metric": "acc", "aggregation": "mean", "higher_is_better": True},
        ]
    else:
        config["doc_to_target"] = "{{answer}}"
        config["generation_kwargs"] = {
            "until": ["\n\n"],
            "max_gen_toks": 512,
            "do_sample": False,
        }
        config["metric_list"] = [
            {"metric": "exact_match", "aggregation": "mean", "higher_is_better": True},
        ]

    config["metadata"] = {
        "version": 1.0,
        "sources": sources,
        "eval_spec_objective": eval_spec.get("test_objective", ""),
    }
    return config


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "run_id": {
                "type": "string",
                "description": "Subdirectory name under workspace/evalclaw/. "
                "Should be unique per run (e.g. 'math_2026-05-24_001').",
                "minLength": 1,
            },
            "eval_spec": {
                "type": "object",
                "description": "Structured evaluation spec from the planner "
                "(test_objective, test_subject, test_format, metrics, etc.).",
            },
            "items": {
                "type": "array",
                "description": "Generated benchmark items. Each item must have "
                "'question' + 'answer' (open-ended), or 'question' + 'choices' "
                "(list) + 'gold' (int index) (multiple-choice).",
                "items": {"type": "object"},
                "minItems": 1,
            },
            "task_name": {
                "type": "string",
                "description": "Optional lm-eval task name. Defaults to "
                "'evalclaw_<run_id>'.",
            },
        },
        "required": ["run_id", "eval_spec", "items"],
    }
)
class CompileTool(Tool):
    """Compile a benchmark into lm-eval-harness on-disk format."""

    def __init__(self, workspace: Path | None = None):
        self._workspace = workspace

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        workspace = Path(ctx.workspace) if getattr(ctx, "workspace", None) else None
        return cls(workspace=workspace)

    @property
    def name(self) -> str:
        return "evalclaw_compile"

    @property
    def description(self) -> str:
        return (
            "Materialize a benchmark into lm-eval-harness format. Writes "
            "eval_spec.json, items.jsonl, and task.yaml under "
            "workspace/evalclaw/<run_id>/. Returns the on-disk paths and the "
            "lm-eval --tasks/--include_path arguments to invoke next."
        )

    def _resolve_run_dir(self, run_id: str) -> Path:
        if self._workspace is None:
            raise RuntimeError(
                "evalclaw_compile has no workspace; ensure the tool was "
                "constructed through nanobot's ToolLoader (ctx.workspace required)."
            )
        # ``run_id`` is treated as a single path segment — reject traversal.
        if "/" in run_id or "\\" in run_id or run_id in ("", ".", ".."):
            raise ValueError(f"run_id must be a single safe path segment, got {run_id!r}")
        return self._workspace / _RUN_DIR_ROOT / run_id

    async def execute(self, **kwargs: Any) -> Any:
        run_id: str = kwargs["run_id"]
        eval_spec: dict[str, Any] = kwargs["eval_spec"]
        items: list[Any] = kwargs["items"]
        task_name: str = kwargs.get("task_name") or f"evalclaw_{run_id}"

        if not isinstance(eval_spec, dict):
            return f"Error: eval_spec must be an object, got {type(eval_spec).__name__}"
        if not isinstance(items, list) or not items:
            return "Error: items must be a non-empty list"

        try:
            run_dir = self._resolve_run_dir(run_id)
        except (RuntimeError, ValueError) as exc:
            return f"Error: {exc}"

        output_type = _resolve_output_type(eval_spec)
        normalized: list[dict[str, Any]] = []
        errors: list[str] = []
        for idx, item in enumerate(items):
            norm, errs = _validate_item(item, idx, output_type)
            if errs:
                errors.extend(errs)
            else:
                normalized.append(norm)

        if errors:
            preview = "\n  - ".join(errors[:10])
            more = f"\n  ... and {len(errors) - 10} more" if len(errors) > 10 else ""
            return (
                f"Error: {len(errors)} validation error(s) compiling "
                f"benchmark for output_type={output_type!r}:\n  - {preview}{more}"
            )

        sources = sorted({
            str(item["source"]) for item in normalized if item.get("source")
        })

        run_dir.mkdir(parents=True, exist_ok=True)
        spec_path = run_dir / "eval_spec.json"
        items_path = run_dir / "items.jsonl"
        task_path = run_dir / "task.yaml"

        spec_path.write_text(
            json.dumps(eval_spec, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        with items_path.open("w", encoding="utf-8") as f:
            for item in normalized:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
        task_config = _build_task_yaml(
            task_name=task_name,
            output_type=output_type,
            items_filename=items_path.name,
            eval_spec=eval_spec,
            sources=sources,
        )
        task_path.write_text(
            yaml.safe_dump(task_config, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )

        return (
            f"Compiled benchmark with {len(normalized)} items.\n"
            f"  run_dir:   {run_dir}\n"
            f"  task_name: {task_name}\n"
            f"  output_type: {output_type}\n"
            f"  sources: {sources or '[]'}\n"
            f"  files: eval_spec.json, items.jsonl, task.yaml\n"
            f"\n"
            f"Next: lm-eval --include_path {run_dir} --tasks {task_name} "
            f"--model openai-chat --model_args model=<MODEL>,base_url=<URL> "
            f"--output_path {run_dir}/results"
        )
