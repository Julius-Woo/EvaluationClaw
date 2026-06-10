"""Validate that all shipped EvalClaw skills are well-formed.

These tests catch frontmatter typos and the most common mistake when editing
prompts — leaving the file unreadable by ``yaml.safe_load``.
"""

from __future__ import annotations

import re

import pytest
import yaml

import evalclaw.skills as skills_pkg

SKILLS_DIR = skills_pkg.SKILLS_DIR
_FRONTMATTER_RE = re.compile(r"^---\s*\r?\n(.*?)\r?\n---\s*\r?\n?", re.DOTALL)

# The 6 skills shipped with the EvalClaw MVP, by directory name.
EXPECTED_SKILLS = {
    "evalclaw",
    "evalclaw-plan",
    "evalclaw-generate",
    "evalclaw-qc",
    "evalclaw-run",
    "evalclaw-report",
}


def _read_skill(name: str) -> tuple[dict, str]:
    """Return the parsed frontmatter dict + body for one skill."""
    path = SKILLS_DIR / name / "SKILL.md"
    text = path.read_text(encoding="utf-8")
    match = _FRONTMATTER_RE.match(text)
    if not match:
        raise AssertionError(f"{name}: missing YAML frontmatter")
    meta = yaml.safe_load(match.group(1))
    if not isinstance(meta, dict):
        raise AssertionError(f"{name}: frontmatter is not a mapping")
    body = text[match.end():]
    return meta, body


def test_all_expected_skills_present() -> None:
    found = {p.parent.name for p in SKILLS_DIR.glob("*/SKILL.md")}
    assert found == EXPECTED_SKILLS, (
        f"Skill set drift. Expected {EXPECTED_SKILLS}, found {found}"
    )


@pytest.mark.parametrize("skill_name", sorted(EXPECTED_SKILLS))
def test_frontmatter_has_required_fields(skill_name: str) -> None:
    meta, body = _read_skill(skill_name)
    assert meta.get("name") == skill_name, (
        f"{skill_name}: frontmatter name={meta.get('name')!r} should match dir"
    )
    description = meta.get("description")
    assert isinstance(description, str) and len(description) >= 20, (
        f"{skill_name}: description must be a meaningful string (got {description!r})"
    )
    assert body.strip(), f"{skill_name}: body is empty"


def test_workspace_path_convention_referenced_in_each_phase_skill() -> None:
    """Every phase skill names the canonical workspace path so the agent
    persists files in the same place regardless of which phase it enters at."""
    for name in EXPECTED_SKILLS - {"evalclaw"}:
        _, body = _read_skill(name)
        assert "workspace/evalclaw/<run_id>" in body or "evalclaw/<run_id>" in body, (
            f"{name}: missing the workspace path convention"
        )


def test_coordinator_references_all_five_phase_skills() -> None:
    """Sanity check that the entry skill knows about its sub-skills by name."""
    _, body = _read_skill("evalclaw")
    for sub in ("evalclaw-plan", "evalclaw-generate", "evalclaw-qc",
                "evalclaw-run", "evalclaw-report"):
        assert sub in body, f"coordinator skill missing reference to {sub}"


def test_skills_dir_is_packaged_path() -> None:
    """The Path exported via `evalclaw.skills.SKILLS_DIR` matches the
    entry-point's resolved value — this is what nanobot's SkillsLoader uses."""
    assert SKILLS_DIR.is_dir()
    assert SKILLS_DIR.name == "skills"
    assert (SKILLS_DIR / "evalclaw" / "SKILL.md").exists()
