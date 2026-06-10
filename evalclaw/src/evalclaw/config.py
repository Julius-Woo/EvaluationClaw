"""EvalClaw runtime configuration."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field


class EvalClawConfig(BaseModel):
    """Settings consumed by EvalClaw tools and skills.

    These are intentionally permissive defaults — a fresh nanobot install with
    ``pip install -e ./evalclaw`` should work end-to-end without further
    configuration.
    """

    run_dir_root: Path = Field(
        default=Path("evalclaw"),
        description="Subdirectory of the nanobot workspace where each evaluation "
        "run materializes its eval_spec, items, task.yaml, results, and report.",
    )
    default_scale: int = Field(
        default=30,
        ge=1,
        description="Fallback item count when the user does not specify a scale.",
    )
    max_plan_iterations: int = Field(default=5, ge=1)
    max_qc_iterations: int = Field(default=3, ge=1)
    baseline_models: list[str] = Field(
        default_factory=lambda: ["gpt-4o-mini"],
        description="Models used for cold-start QC sampling.",
    )
    lm_eval_cmd: str = Field(
        default="lm-eval",
        description="Executable used for Tier-1 evaluation runs.",
    )
    hf_token_env: str = Field(
        default="HF_TOKEN",
        description="Env var name to read the HuggingFace API token from. The "
        "token is optional — only required for private/gated datasets.",
    )
