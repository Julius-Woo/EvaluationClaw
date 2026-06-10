"""EvalClaw skill bundle.

This subpackage is referenced from ``pyproject.toml`` as the ``nanobot.skills``
entry-point value. The ``SkillsLoader`` will treat ``__path__[0]`` as a
directory containing one subdirectory per skill (each with its own
``SKILL.md``).
"""

from pathlib import Path

SKILLS_DIR: Path = Path(__file__).parent
