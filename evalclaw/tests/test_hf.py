"""Tests for HuggingFace dataset search/sample tools.

We mock out the network calls — the goal is to cover argument plumbing, the
fallback-split logic, output shaping, and error messages.
"""

from __future__ import annotations

import datetime as dt
import sys
import types
from typing import Any

import pytest

from evalclaw.tools.hf import (
    HFDatasetSampleTool,
    HFDatasetSearchTool,
    _condense_dataset_info,
    _truncate,
)

# ---------- pure-function helpers ----------


def test_truncate_long_string() -> None:
    s = "x" * 500
    out = _truncate(s)
    assert out.endswith("chars]")
    assert len(out) < len(s)


def test_truncate_short_string_passthrough() -> None:
    assert _truncate("hello") == "hello"


def test_truncate_long_list() -> None:
    out = _truncate(list(range(20)))
    assert out[-1].startswith("... [+")


def test_condense_dataset_info_handles_missing_attrs() -> None:
    info = types.SimpleNamespace(id="foo/bar", downloads=10, likes=2, tags=["a", "b"], last_modified=None)
    out = _condense_dataset_info(info)
    assert out["id"] == "foo/bar"
    assert out["downloads"] == 10
    assert out["last_modified"] is None
    assert out["tags"] == ["a", "b"]


def test_condense_dataset_info_serializes_datetime() -> None:
    ts = dt.datetime(2026, 1, 1, 12, 0, tzinfo=dt.timezone.utc)
    info = types.SimpleNamespace(id="x", downloads=0, likes=0, tags=[], last_modified=ts)
    out = _condense_dataset_info(info)
    assert out["last_modified"] == "2026-01-01T12:00:00+00:00"


# ---------- search tool ----------


class _FakeHfApi:
    """Captures list_datasets() args and returns canned summaries."""
    last_kwargs: dict[str, Any] = {}
    return_value: list[Any] = []

    def __init__(self, token: str | None = None) -> None:
        self.token = token

    def list_datasets(self, **kwargs: Any) -> list[Any]:
        type(self).last_kwargs = kwargs
        return list(type(self).return_value)


@pytest.fixture
def fake_hf_api(monkeypatch: pytest.MonkeyPatch) -> type[_FakeHfApi]:
    fake_mod = types.ModuleType("huggingface_hub")
    fake_mod.HfApi = _FakeHfApi  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "huggingface_hub", fake_mod)
    _FakeHfApi.last_kwargs = {}
    _FakeHfApi.return_value = []
    return _FakeHfApi


async def test_search_passes_query_and_filters(fake_hf_api: type[_FakeHfApi]) -> None:
    fake_hf_api.return_value = [
        types.SimpleNamespace(id="openai/gsm8k", downloads=1234, likes=42, tags=["math", "reasoning"], last_modified=None),
        types.SimpleNamespace(id="hendrycks/competition_math", downloads=999, likes=12, tags=["math"], last_modified=None),
    ]
    tool = HFDatasetSearchTool()
    out = await tool.execute(
        query="math reasoning",
        limit=5,
        task_categories=["question-answering"],
        language="en",
    )
    assert "openai/gsm8k" in out
    assert "hendrycks/competition_math" in out
    assert fake_hf_api.last_kwargs["search"] == "math reasoning"
    assert fake_hf_api.last_kwargs["limit"] == 5
    assert fake_hf_api.last_kwargs["task_categories"] == ["question-answering"]
    assert fake_hf_api.last_kwargs["language"] == "en"


async def test_search_caps_limit_to_max(fake_hf_api: type[_FakeHfApi]) -> None:
    fake_hf_api.return_value = []
    tool = HFDatasetSearchTool()
    # Request 999; tool should silently clamp to the internal max (25).
    await tool.execute(query="x", limit=999)
    assert fake_hf_api.last_kwargs["limit"] == 25


async def test_search_empty_result(fake_hf_api: type[_FakeHfApi]) -> None:
    tool = HFDatasetSearchTool()
    out = await tool.execute(query="nonexistent-dataset-xyz")
    assert "No HuggingFace datasets found" in out


async def test_search_handles_api_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_mod = types.ModuleType("huggingface_hub")

    class _ExplodingApi:
        def __init__(self, token: str | None = None) -> None:
            pass

        def list_datasets(self, **_: Any) -> list[Any]:
            raise RuntimeError("network down")

    fake_mod.HfApi = _ExplodingApi  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "huggingface_hub", fake_mod)

    tool = HFDatasetSearchTool()
    out = await tool.execute(query="x")
    assert "Error searching HuggingFace Hub" in out
    assert "network down" in out


# ---------- sample tool ----------


class _FakeSplitInfo:
    """Captures load_dataset calls; returns list-of-dicts as a fake Dataset."""
    available_splits: list[str] = ["test", "train"]
    rows_by_split: dict[str, list[dict[str, Any]]] = {}
    failing_splits: set[str] = set()
    last_load_kwargs: dict[str, Any] = {}
    raise_on_get_splits: Exception | None = None


class _FakeDataset:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def __len__(self) -> int:
        return len(self._rows)

    def __getitem__(self, idx: int) -> dict[str, Any]:
        return dict(self._rows[idx])


def _make_fake_datasets_module() -> types.ModuleType:
    mod = types.ModuleType("datasets")

    def get_dataset_split_names(repo_id: str, config_name: str | None = None, token: Any = None) -> list[str]:
        if _FakeSplitInfo.raise_on_get_splits is not None:
            raise _FakeSplitInfo.raise_on_get_splits
        return list(_FakeSplitInfo.available_splits)

    def load_dataset(repo_id: str, name: str | None = None, split: str | None = None, token: Any = None, trust_remote_code: bool = False) -> _FakeDataset:
        _FakeSplitInfo.last_load_kwargs = {
            "repo_id": repo_id, "name": name, "split": split,
            "trust_remote_code": trust_remote_code,
        }
        # Parse the "name[:n]" slice
        base = split.split("[")[0] if split else ""
        if base in _FakeSplitInfo.failing_splits:
            raise RuntimeError(f"split {base!r} failed to load")
        rows = _FakeSplitInfo.rows_by_split.get(base, [])
        if "[:" in (split or ""):
            n = int(split.split("[:")[1].rstrip("]"))
            rows = rows[:n]
        return _FakeDataset(rows)

    mod.get_dataset_split_names = get_dataset_split_names  # type: ignore[attr-defined]
    mod.load_dataset = load_dataset  # type: ignore[attr-defined]
    return mod


@pytest.fixture
def fake_datasets(monkeypatch: pytest.MonkeyPatch) -> type[_FakeSplitInfo]:
    monkeypatch.setitem(sys.modules, "datasets", _make_fake_datasets_module())
    _FakeSplitInfo.available_splits = ["test", "train"]
    _FakeSplitInfo.rows_by_split = {}
    _FakeSplitInfo.failing_splits = set()
    _FakeSplitInfo.last_load_kwargs = {}
    _FakeSplitInfo.raise_on_get_splits = None
    return _FakeSplitInfo


async def test_sample_default_split_test_preferred(fake_datasets: type[_FakeSplitInfo]) -> None:
    fake_datasets.rows_by_split = {
        "test": [{"q": "Q1", "a": "A1"}, {"q": "Q2", "a": "A2"}],
        "train": [{"q": "T1", "a": "T1"}],
    }
    tool = HFDatasetSampleTool()
    out = await tool.execute(repo_id="x/y", n=2)
    assert "Sampled 2 row(s) from x/y" in out
    assert "split='test'" in out
    assert "['q', 'a']" in out
    assert fake_datasets.last_load_kwargs["split"] == "test[:2]"
    assert fake_datasets.last_load_kwargs["trust_remote_code"] is False


async def test_sample_falls_back_when_test_missing(fake_datasets: type[_FakeSplitInfo]) -> None:
    # Only ``train`` exists; tool should fall back automatically.
    fake_datasets.available_splits = ["train"]
    fake_datasets.rows_by_split = {"train": [{"q": "Q1", "a": "A1"}]}
    tool = HFDatasetSampleTool()
    out = await tool.execute(repo_id="x/y", n=1)
    assert "split='train'" in out


async def test_sample_explicit_split_used(fake_datasets: type[_FakeSplitInfo]) -> None:
    fake_datasets.available_splits = ["test", "validation", "train"]
    fake_datasets.rows_by_split = {"validation": [{"q": "V"}]}
    tool = HFDatasetSampleTool()
    out = await tool.execute(repo_id="x/y", split="validation", n=1)
    assert "split='validation'" in out
    assert fake_datasets.last_load_kwargs["split"] == "validation[:1]"


async def test_sample_caps_n_at_50(fake_datasets: type[_FakeSplitInfo]) -> None:
    fake_datasets.rows_by_split = {"test": [{"q": f"Q{i}"} for i in range(100)]}
    tool = HFDatasetSampleTool()
    out = await tool.execute(repo_id="x/y", n=999)
    # Bounded by _SAMPLE_ROWS_MAX (50).
    assert "Sampled 50 row(s)" in out


async def test_sample_returns_error_when_no_split_works(fake_datasets: type[_FakeSplitInfo]) -> None:
    fake_datasets.available_splits = ["test"]
    fake_datasets.failing_splits = {"test"}
    tool = HFDatasetSampleTool()
    out = await tool.execute(repo_id="x/y")
    assert out.startswith("Error sampling 'x/y'")
    assert "could not load any split" in out


async def test_sample_truncates_long_field_values(fake_datasets: type[_FakeSplitInfo]) -> None:
    long_text = "A" * 1000
    fake_datasets.rows_by_split = {"test": [{"question": long_text, "answer": "ok"}]}
    tool = HFDatasetSampleTool()
    out = await tool.execute(repo_id="x/y", n=1)
    assert "chars]" in out  # truncation marker appears
    assert "A" * 1000 not in out  # full text not emitted


async def test_sample_empty_dataset_message(fake_datasets: type[_FakeSplitInfo]) -> None:
    fake_datasets.rows_by_split = {"test": []}
    tool = HFDatasetSampleTool()
    out = await tool.execute(repo_id="x/y", n=5)
    assert "but it is empty" in out
