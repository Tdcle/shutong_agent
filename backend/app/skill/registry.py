"""Skill Registry — migrated from Java ClasspathSkillRegistry + SkillRegistry.

Skills are defined as SKILL.md files in the skills/ directory.
Each skill has a frontmatter with name and description,
and a body with detailed instructions.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class Skill:
    name: str
    description: str
    path: Path
    content: str  # Full SKILL.md content (without frontmatter)

    def to_summary(self) -> str:
        """Short summary for system prompt injection."""
        return f"- **{self.name}**: {self.description}"

    def to_system_message(self) -> str:
        """Full skill content for when LLM loads this skill."""
        return self.content


class SkillRegistry:
    """Manages loaded skills, provides listing and retrieval.

    Equivalent to Java's ClasspathSkillRegistry + SpringAiSkillAdvisor.
    """

    def __init__(self, skills_dir: str | None = None):
        self.skills_dir = Path(skills_dir) if skills_dir else Path("skills")
        self._skills: dict[str, Skill] = {}
        self._loaded = False

    def load_all(self):
        """Scan skills_dir for SKILL.md files and load them."""
        if self._loaded:
            return
        if not self.skills_dir.exists():
            logger.warning("Skills directory not found: %s", self.skills_dir)
            return

        for skill_md in self.skills_dir.rglob("SKILL.md"):
            try:
                skill = self._parse_skill(skill_md)
                self._skills[skill.name] = skill
                logger.info("Loaded skill: %s", skill.name)
            except Exception as e:
                logger.error("Failed to load skill %s: %s", skill_md, e)

        self._loaded = True
        logger.info("Loaded %d skills from %s", len(self._skills), self.skills_dir)

    def reload(self):
        self._skills.clear()
        self._loaded = False
        self.load_all()

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def list_all(self) -> list[Skill]:
        return list(self._skills.values())

    def get_skills_prompt(self) -> str:
        """Generate the skills list injection for system prompt.

        Equivalent to SpringAiSkillAdvisor.before() injecting skill list into system prompt.
        """
        if not self._skills:
            return ""
        lines = ["## 可用技能（Skills）", ""]
        lines.append("你可以使用 `read_skill(skill_name)` 工具按需加载技能的详细说明。")
        lines.append("")
        for skill in self._skills.values():
            lines.append(skill.to_summary())
        lines.append("")
        lines.append("如果当前任务与某个技能相关，请先调用 `read_skill` 读取技能详情。")
        return "\n".join(lines)

    def create_read_skill_tool(self):
        """Create the read_skill tool that allows LLM to load a skill's full content.

        Equivalent to Java's ReadSkillTool.createReadSkillToolCallback().
        """
        from app.tools.base import ToolDef

        def read_skill(skill_name: str) -> str:
            skill = self.get(skill_name)
            if skill is None:
                available = ", ".join(self._skills.keys())
                return f"未找到技能 '{skill_name}'。可用技能: {available}"
            return skill.to_system_message()

        return ToolDef(
            name="read_skill",
            description="读取指定技能的详细使用说明。使用前请先确认技能名称。",
            fn=read_skill,
            parameters={
                "type": "object",
                "properties": {
                    "skill_name": {"type": "string", "description": "技能名称"}
                },
                "required": ["skill_name"],
            },
        )

    def _parse_skill(self, path: Path) -> Skill:
        raw = path.read_text(encoding="utf-8")
        name = "unknown"
        description = ""
        content_start = 0
        lines = raw.split("\n")

        # Parse YAML frontmatter (--- delimited)
        if lines[0].strip() == "---":
            end_idx = None
            for i in range(1, len(lines)):
                if lines[i].strip() == "---":
                    end_idx = i
                    break
            if end_idx:
                frontmatter = "\n".join(lines[1:end_idx])
                for line in frontmatter.split("\n"):
                    line = line.strip()
                    if line.startswith("name:"):
                        name = line.split(":", 1)[1].strip()
                    elif line.startswith("description:"):
                        description = line.split(":", 1)[1].strip()
                content_start = end_idx + 1

        content = "\n".join(lines[content_start:]).strip()
        return Skill(name=name, description=description, path=path, content=content)
