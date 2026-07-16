"""Load and render the German system prompts under ``prompts/`` (spec §10)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


@lru_cache(maxsize=None)
def _load(name: str) -> str:
    return (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")


def render(name: str, **kwargs: object) -> str:
    """Render a prompt template with ``{placeholder}`` substitution.

    Uses ``str.format_map`` with a defaulting dict so a missing key renders as an
    empty string instead of raising, and literal ``{{ }}`` (JSON braces in the
    template) survive.
    """
    class _Default(dict):
        def __missing__(self, key: str) -> str:  # noqa: D401
            return ""

    return _load(name).format_map(_Default(**{k: str(v) for k, v in kwargs.items()}))
