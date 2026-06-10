"""Tests for ``nanobot.skills`` entry-point plugin loading in SkillsLoader."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from nanobot.agent import skills as skills_module
from nanobot.agent.skills import SkillsLoader


def _write_skill(base: Path, name: str, body: str = "# Skill\n") -> Path:
    skill_dir = base / name
    skill_dir.mkdir(parents=True)
    path = skill_dir / "SKILL.md"
    path.write_text(f"---\n---\n\n{body}", encoding="utf-8")
    return path


def test_plugin_skill_roots_explicit_path(tmp_path: Path) -> None:
    """Passing ``plugin_skill_roots`` directly bypasses entry-point discovery."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    builtin = tmp_path / "builtin"
    builtin.mkdir()
    plugin_root = tmp_path / "plugin"
    plugin_root.mkdir()
    _write_skill(plugin_root, "evalclaw", body="# EvalClaw plugin skill\n")

    loader = SkillsLoader(
        workspace,
        builtin_skills_dir=builtin,
        plugin_skill_roots=[plugin_root],
    )
    entries = loader.list_skills(filter_unavailable=False)
    assert [e["name"] for e in entries] == ["evalclaw"]
    assert entries[0]["source"] == "plugin"

    content = loader.load_skill("evalclaw")
    assert content is not None
    assert "EvalClaw plugin skill" in content


def test_workspace_skill_shadows_plugin_skill(tmp_path: Path) -> None:
    """When the same skill name exists in workspace and plugin, workspace wins."""
    workspace = tmp_path / "ws"
    workspace_skills = workspace / "skills"
    workspace_skills.mkdir(parents=True)
    plugin_root = tmp_path / "plugin"
    plugin_root.mkdir()
    _write_skill(workspace_skills, "evalclaw", body="# workspace override\n")
    _write_skill(plugin_root, "evalclaw", body="# plugin original\n")
    builtin = tmp_path / "builtin"
    builtin.mkdir()

    loader = SkillsLoader(
        workspace,
        builtin_skills_dir=builtin,
        plugin_skill_roots=[plugin_root],
    )
    entries = loader.list_skills(filter_unavailable=False)
    assert len(entries) == 1
    assert entries[0]["source"] == "workspace"
    assert "workspace override" in (loader.load_skill("evalclaw") or "")


def test_plugin_skill_roots_default_uses_discovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default ``plugin_skill_roots=None`` triggers entry-point discovery."""
    plugin_root = tmp_path / "plugin"
    plugin_root.mkdir()
    _write_skill(plugin_root, "evalclaw", body="# discovered\n")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    builtin = tmp_path / "builtin"
    builtin.mkdir()

    monkeypatch.setattr(
        skills_module, "discover_plugin_skill_roots", lambda: [plugin_root]
    )
    loader = SkillsLoader(workspace, builtin_skills_dir=builtin)
    assert loader.plugin_skill_roots == [plugin_root]
    names = [e["name"] for e in loader.list_skills(filter_unavailable=False)]
    assert names == ["evalclaw"]


def test_resolve_skill_plugin_root_accepts_path_str_module(tmp_path: Path) -> None:
    """``_resolve_skill_plugin_root`` handles Path, str, and Python module values."""
    assert skills_module._resolve_skill_plugin_root(tmp_path) == tmp_path
    assert skills_module._resolve_skill_plugin_root(str(tmp_path)) == tmp_path

    pkg = types.ModuleType("fake_skills_pkg")
    pkg.__path__ = [str(tmp_path)]  # type: ignore[attr-defined]
    assert skills_module._resolve_skill_plugin_root(pkg) == tmp_path

    assert skills_module._resolve_skill_plugin_root(42) is None


def test_discover_plugin_skill_roots_caches(monkeypatch: pytest.MonkeyPatch) -> None:
    """Subsequent calls reuse the first lookup; cache reset clears the memo."""
    skills_module._reset_plugin_skill_roots_cache()
    calls = {"n": 0}

    class _FakeEP:
        name = "fake"

        def load(self):
            return Path("/tmp/fake")

    def fake_entry_points(group: str):
        calls["n"] += 1
        return [_FakeEP()] if group == "nanobot.skills" else []

    monkeypatch.setattr(skills_module, "entry_points", fake_entry_points)
    first = skills_module.discover_plugin_skill_roots()
    second = skills_module.discover_plugin_skill_roots()
    assert first == [Path("/tmp/fake")]
    assert second is first
    assert calls["n"] == 1

    skills_module._reset_plugin_skill_roots_cache()
    skills_module.discover_plugin_skill_roots()
    assert calls["n"] == 2


@pytest.fixture(autouse=True)
def _clear_plugin_cache():
    skills_module._reset_plugin_skill_roots_cache()
    yield
    skills_module._reset_plugin_skill_roots_cache()


def teardown_module(module: object) -> None:
    sys.modules.pop("fake_skills_pkg", None)
