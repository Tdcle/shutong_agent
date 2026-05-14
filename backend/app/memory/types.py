"""Memory type definitions and data models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path


class MemoryType(str, Enum):
    USER = "user"
    FEEDBACK = "feedback"
    PROJECT = "project"
    REFERENCE = "reference"


@dataclass
class MemoryEntry:
    name: str
    description: str
    type: MemoryType
    importance: float = 0.5
    created: str = ""
    updated: str = ""
    links: list[str] = field(default_factory=list)
    content: str = ""

    def __post_init__(self):
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if not self.created:
            self.created = now
        if not self.updated:
            self.updated = now

    @property
    def file_path(self) -> str:
        """Relative file path within the memory directory."""
        if self.type == MemoryType.USER:
            return "user.md"
        return f"{self.type.value}/{self.name}.md"

    @property
    def index_line(self) -> str:
        """Single line for MEMORY.md index."""
        return f"- [{self.name}]({self.file_path}) — {self.description}"

    @property
    def frontmatter(self) -> str:
        links_str = "\n".join(f"  - {link}" for link in self.links)
        return f"""---
name: {self.name}
description: {self.description}
type: {self.type.value}
importance: {self.importance}
created: {self.created}
updated: {self.updated}
links:
{links_str if links_str else '  []'}
---"""

    def to_markdown(self) -> str:
        """Full markdown content with frontmatter."""
        return f"{self.frontmatter}\n\n{self.content.strip()}\n"

    @staticmethod
    def from_markdown(file_path: Path, raw: str) -> "MemoryEntry":
        """Parse a markdown file with YAML frontmatter into a MemoryEntry."""
        lines = raw.split("\n")
        if not lines or lines[0].strip() != "---":
            # No frontmatter, treat all as content
            name = file_path.stem
            return MemoryEntry(
                name=name,
                description="",
                type=MemoryType.PROJECT,
                content=raw.strip(),
            )

        # Find frontmatter end
        end_idx = None
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                end_idx = i
                break

        if end_idx is None:
            name = file_path.stem
            return MemoryEntry(name=name, description="", type=MemoryType.PROJECT, content=raw.strip())

        # Parse frontmatter
        fm_lines = lines[1:end_idx]
        fm = _parse_simple_yaml(fm_lines)
        content = "\n".join(lines[end_idx + 1:]).strip()

        return MemoryEntry(
            name=fm.get("name", file_path.stem),
            description=fm.get("description", ""),
            type=MemoryType(fm.get("type", "project")),
            importance=float(fm.get("importance", 0.5)),
            created=fm.get("created", ""),
            updated=fm.get("updated", ""),
            links=fm.get("links", []),
            content=content,
        )


def _parse_simple_yaml(lines: list[str]) -> dict:
    """Minimal YAML parser for frontmatter — avoids pyyaml dependency."""
    result: dict = {}
    current_key = ""
    current_list: list[str] = []

    for line in lines:
        # Skip empty lines
        if not line.strip():
            continue

        # List item
        if line.strip().startswith("- ") and current_key:
            current_list.append(line.strip()[2:])
            continue

        # Flush previous list
        if current_key and current_list is not None:
            result[current_key] = current_list
            current_list = []
            current_key = ""

        # Key: value
        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if value:
                result[key] = value
            else:
                # Might be start of a list
                current_key = key
                current_list = []

    # Flush trailing list
    if current_key and current_list:
        result[current_key] = current_list

    return result
