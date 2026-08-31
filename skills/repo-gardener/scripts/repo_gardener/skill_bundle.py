from __future__ import annotations

import sys
from pathlib import Path


def bundled_skill_path() -> Path:
    """Return the complete portable Skill bundled with this distribution."""
    candidates = (
        Path(sys.prefix) / "share" / "repo-gardener",
        Path(__file__).resolve().parents[2],
    )
    required = (
        Path("SKILL.md"),
        Path("agents/openai.yaml"),
        Path("references/finding-schema.md"),
        Path("references/safety-policy.md"),
        Path("scripts/run_repo_gardener.py"),
        Path("scripts/repo_gardener/__init__.py"),
    )
    for candidate in candidates:
        if all((candidate / relative).is_file() for relative in required):
            return candidate.resolve()
    raise FileNotFoundError(
        "the portable repo-gardener Skill is missing from this installation"
    )
