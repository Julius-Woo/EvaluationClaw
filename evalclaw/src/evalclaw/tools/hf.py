"""HuggingFace dataset search and sampling tools.

These two tools let the agent shop the HF Hub for existing benchmark datasets
without leaving the conversation, avoiding rate-limited web scraping. They are
intentionally narrow — search returns lightweight metadata, sample pulls only a
small slice. Anything larger should be done out-of-band.
"""

from __future__ import annotations

import os
from typing import Any

from nanobot.agent.tools.base import Tool, tool_parameters

# Cap defensively so a stray ``limit=999`` doesn't pull a giant list back into
# the agent's context window.
_SEARCH_RESULT_MAX = 25
_SAMPLE_ROWS_MAX = 50

# Sample-value preview length — keeps the tool output bounded when datasets
# have long-form fields.
_SAMPLE_FIELD_PREVIEW_CHARS = 400


def _hf_token() -> str | None:
    """Read the HF API token from env (HF_TOKEN, then HUGGINGFACE_HUB_TOKEN)."""
    return os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_HUB_TOKEN")


def _truncate(value: Any) -> Any:
    """Truncate long string values for compact tool output."""
    if isinstance(value, str) and len(value) > _SAMPLE_FIELD_PREVIEW_CHARS:
        return value[:_SAMPLE_FIELD_PREVIEW_CHARS] + f"... [+{len(value) - _SAMPLE_FIELD_PREVIEW_CHARS} chars]"
    if isinstance(value, list) and len(value) > 10:
        return value[:10] + [f"... [+{len(value) - 10} items]"]
    return value


def _condense_dataset_info(info: Any) -> dict[str, Any]:
    """Map a ``DatasetInfo`` from huggingface_hub to a JSON-safe summary dict."""
    last_modified = getattr(info, "last_modified", None)
    return {
        "id": getattr(info, "id", None),
        "downloads": getattr(info, "downloads", None),
        "likes": getattr(info, "likes", None),
        "tags": list(getattr(info, "tags", []) or [])[:15],
        "last_modified": last_modified.isoformat() if last_modified else None,
    }


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Free-text search query (e.g. 'math reasoning', "
                "'humaneval', 'mmlu'). Matches against dataset names and "
                "descriptions on huggingface.co.",
                "minLength": 1,
            },
            "limit": {
                "type": "integer",
                "description": f"Number of results to return (default 10, max {_SEARCH_RESULT_MAX}).",
                "minimum": 1,
                "maximum": _SEARCH_RESULT_MAX,
            },
            "task_categories": {
                "type": "array",
                "description": "Optional HF task category filter, e.g. "
                "['question-answering'], ['text-generation'].",
                "items": {"type": "string"},
            },
            "language": {
                "type": "string",
                "description": "Optional ISO-639 language code filter (e.g. 'en', 'zh').",
            },
            "sort": {
                "type": "string",
                "description": "Sort field. Default 'downloads' (most popular first).",
                "enum": ["downloads", "likes", "trending_score", "last_modified"],
            },
        },
        "required": ["query"],
    }
)
class HFDatasetSearchTool(Tool):
    """Search HuggingFace Hub for datasets matching a query."""

    @property
    def name(self) -> str:
        return "hf_dataset_search"

    @property
    def description(self) -> str:
        return (
            "Search HuggingFace Hub for datasets matching a free-text query, "
            "optionally filtered by task category or language. Returns a list "
            "of dataset summaries (id, downloads, likes, tags, last_modified). "
            "Use this before generating items from scratch — many capabilities "
            "have well-curated benchmarks already on HF."
        )

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, **kwargs: Any) -> Any:
        from huggingface_hub import HfApi

        query: str = kwargs["query"]
        limit: int = min(int(kwargs.get("limit") or 10), _SEARCH_RESULT_MAX)
        task_categories = kwargs.get("task_categories") or None
        language = kwargs.get("language") or None
        sort = kwargs.get("sort") or "downloads"

        api = HfApi(token=_hf_token())
        try:
            results = list(api.list_datasets(
                search=query,
                limit=limit,
                sort=sort,
                direction=-1,
                task_categories=task_categories,
                language=language,
            ))
        except Exception as exc:
            return f"Error searching HuggingFace Hub: {exc}"

        if not results:
            return f"No HuggingFace datasets found for query={query!r}."

        summaries = [_condense_dataset_info(r) for r in results]
        lines = [
            f"Found {len(summaries)} HuggingFace dataset(s) for query={query!r}",
            "(sorted by " + sort + "; use hf_dataset_sample to inspect any of them):",
            "",
        ]
        for s in summaries:
            tag_preview = ", ".join(s["tags"][:6]) if s["tags"] else "—"
            lines.append(
                f"- {s['id']} | downloads={s['downloads']} likes={s['likes']} "
                f"| tags: {tag_preview}"
            )
        return "\n".join(lines)


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "repo_id": {
                "type": "string",
                "description": "HuggingFace dataset repo id, e.g. 'gsm8k', "
                "'openai/MMMLU', 'cais/mmlu'.",
                "minLength": 1,
            },
            "config_name": {
                "type": "string",
                "description": "Optional dataset configuration name "
                "(e.g. 'main', 'all', a subject for MMLU). If omitted, the "
                "default config is used.",
            },
            "split": {
                "type": "string",
                "description": "Split to sample from. Defaults to 'test', "
                "falls back to 'train' if not present.",
            },
            "n": {
                "type": "integer",
                "description": f"Number of rows to fetch (default 5, max {_SAMPLE_ROWS_MAX}).",
                "minimum": 1,
                "maximum": _SAMPLE_ROWS_MAX,
            },
        },
        "required": ["repo_id"],
    }
)
class HFDatasetSampleTool(Tool):
    """Download a small sample of a HuggingFace dataset and return its rows."""

    @property
    def name(self) -> str:
        return "hf_dataset_sample"

    @property
    def description(self) -> str:
        return (
            "Fetch a small sample (default 5, max 50 rows) of a HuggingFace "
            "dataset for shape inspection. Useful for deciding whether a "
            "candidate dataset matches the eval_spec before adopting it. "
            "Returns the rows as a list along with the discovered column names."
        )

    @property
    def read_only(self) -> bool:
        return True

    def _load_split(
        self,
        repo_id: str,
        config_name: str | None,
        split: str | None,
        n: int,
    ) -> tuple[list[dict[str, Any]], str, list[str]]:
        """Try the requested split, fall back to common alternates.

        Returns ``(rows, actual_split, available_splits)``.
        """
        from datasets import get_dataset_split_names, load_dataset

        token = _hf_token()
        # Discover splits up-front so we can return useful errors.
        try:
            available = list(get_dataset_split_names(
                repo_id, config_name=config_name, token=token,
            ))
        except Exception:
            available = []

        candidates: list[str] = []
        if split:
            candidates.append(split)
        for fallback in ("test", "validation", "train"):
            if fallback not in candidates:
                candidates.append(fallback)

        last_exc: Exception | None = None
        for candidate in candidates:
            if available and candidate not in available:
                continue
            try:
                ds = load_dataset(
                    repo_id,
                    name=config_name,
                    split=f"{candidate}[:{n}]",
                    token=token,
                    trust_remote_code=False,
                )
            except Exception as exc:
                last_exc = exc
                continue
            rows = [dict(ds[i]) for i in range(len(ds))]
            return rows, candidate, available
        # Nothing worked.
        msg = (
            f"could not load any split for {repo_id!r} "
            f"(config={config_name!r}). Available splits: {available or 'unknown'}."
        )
        if last_exc is not None:
            msg += f" Last error: {last_exc}"
        raise RuntimeError(msg)

    async def execute(self, **kwargs: Any) -> Any:
        repo_id: str = kwargs["repo_id"]
        config_name = kwargs.get("config_name") or None
        split = kwargs.get("split") or None
        n: int = min(int(kwargs.get("n") or 5), _SAMPLE_ROWS_MAX)

        try:
            rows, actual_split, available = self._load_split(
                repo_id, config_name, split, n,
            )
        except RuntimeError as exc:
            return f"Error sampling {repo_id!r}: {exc}"
        except Exception as exc:
            return f"Error sampling {repo_id!r}: {exc}"

        if not rows:
            return f"Loaded {repo_id!r} split={actual_split!r} but it is empty."

        columns = list(rows[0].keys())
        truncated_rows = [{k: _truncate(v) for k, v in row.items()} for row in rows]
        lines = [
            f"Sampled {len(rows)} row(s) from {repo_id}"
            + (f" (config={config_name})" if config_name else "")
            + f" split={actual_split!r}.",
            f"Available splits: {available or '[unknown]'}",
            f"Columns: {columns}",
            "",
            "Rows:",
        ]
        for i, row in enumerate(truncated_rows):
            lines.append(f"  [{i}] {row}")
        return "\n".join(lines)
