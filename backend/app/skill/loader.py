"""Skill Loader — handles dynamic loading and unloading of skills."""

from __future__ import annotations

import logging
from pathlib import Path
from watchfiles import awatch

from app.skill.registry import SkillRegistry

logger = logging.getLogger(__name__)


class SkillLoader:
    """Manages skill loading with optional filesystem watching for hot-reload."""

    def __init__(self, registry: SkillRegistry):
        self.registry = registry

    def load_from_dir(self, skills_dir: str):
        """Load all skills from a directory."""
        self.registry.skills_dir = Path(skills_dir)
        self.registry.load_all()

    def create_skill(self, name: str, description: str, content: str) -> str:
        """Programmatically create a new SKILL.md file."""
        skills_dir = self.registry.skills_dir
        skill_dir = skills_dir / name
        skill_dir.mkdir(parents=True, exist_ok=True)

        frontmatter = f"---\nname: {name}\ndescription: {description}\n---\n"
        skill_file = skill_dir / "SKILL.md"
        skill_file.write_text(frontmatter + content, encoding="utf-8")

        self.registry.reload()
        return str(skill_file)

    def delete_skill(self, name: str):
        """Remove a skill directory."""
        import shutil
        skill_dir = self.registry.skills_dir / name
        if skill_dir.exists():
            shutil.rmtree(skill_dir)
            self.registry.reload()

    async def watch_and_reload(self):
        """Watch skills directory for changes and auto-reload."""
        try:
            async for changes in awatch(str(self.registry.skills_dir)):
                logger.info("Skills directory changed, reloading...")
                self.registry.reload()
        except ImportError:
            logger.warning("watchfiles not installed, skill hot-reload disabled")
