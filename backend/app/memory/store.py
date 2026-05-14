"""File-based memory store with embedding-powered retrieval."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from app.memory.types import MemoryEntry, MemoryType

logger = logging.getLogger(__name__)

INDEX_L1_MAX_LINES = 100
INDEX_COMPACT_THRESHOLD = 200

# Lazy-loaded singletons
_embedding_engine = None
_embedding_stores: dict[str, object] = {}

def _get_embedding_engine():
    global _embedding_engine
    if _embedding_engine is None:
        from app.memory.embedding import EmbeddingEngine
        from app.config import settings
        _embedding_engine = EmbeddingEngine(settings.embedding_model)
    return _embedding_engine

def _get_embedding_store(base_dir: Path):
    global _embedding_stores
    key = str(base_dir)
    if key not in _embedding_stores:
        from app.memory.embedding import EmbeddingStore
        _embedding_stores[key] = EmbeddingStore(base_dir)
    return _embedding_stores[key]


class FileMemoryStore:
    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)
        self._ensure_dirs()
        self._init_defaults()

    def _ensure_dirs(self):
        self.base_dir.mkdir(parents=True, exist_ok=True)
        for t in MemoryType:
            if t != MemoryType.USER:
                (self.base_dir / t.value).mkdir(exist_ok=True)

    def _init_defaults(self):
        index_file = self.base_dir / "MEMORY.md"
        if not index_file.exists():
            index_file.write_text(
                "# 书童记忆索引\n\n"
                "暂无记忆。随着使用，书童会自动在此记录重要信息。\n\n"
                "## User\n\n## Feedback\n\n## Project\n\n## Reference\n",
                encoding="utf-8",
            )
        user_file = self.base_dir / "user.md"
        if not user_file.exists():
            user_file.write_text(
                "---\nname: user\ndescription: 用户画像\ntype: user\nimportance: 1.0\nlinks: []\n---\n\n"
                "## 关于我\n\n（随着对话进行，agent 会自动更新此文件）\n",
                encoding="utf-8",
            )

    # ===== Read =====

    def load_index(self, max_lines: int | None = None) -> list[MemoryEntry]:
        index_file = self.base_dir / "MEMORY.md"
        if not index_file.exists():
            return self._scan_and_rebuild_index()

        lines = index_file.read_text(encoding="utf-8").split("\n")
        entries = []
        for line in lines:
            line = line.strip()
            if line.startswith("- [") and "](" in line and " — " in line:
                try:
                    rest = line[3:]
                    name, rest = rest.split("]", 1)
                    rest = rest.lstrip("(")
                    file_path, rest = rest.split(")", 1)
                    description = rest.lstrip(" — ")
                    full_path = self.base_dir / file_path
                    if full_path.exists():
                        entry = self._read_entry(full_path)
                        if entry:
                            entries.append(entry)
                except (ValueError, IndexError):
                    continue

        if max_lines is not None and len(entries) > max_lines:
            entries = entries[:max_lines]
        return entries

    def load_l1_context(self, query: str | None = None) -> str:
        """L1 context: user.md (always) + top N memories by embedding similarity."""
        from app.config import settings
        parts = []

        user_file = self.base_dir / "user.md"
        if user_file.exists():
            entry = self._read_entry(user_file)
            if entry and entry.content:
                parts.append(f"## 用户画像\n{entry.content}")

        entries = self.load_index()
        if entries:
            if query:
                top = self._rank_by_embedding(query, entries, settings.memory_l1_max_entries)
            else:
                entries.sort(key=lambda e: e.importance, reverse=True)
                top = entries[: settings.memory_l1_max_entries]
            if top:
                lines = ["## 相关记忆"]
                for e in top:
                    lines.append(e.index_line)
                parts.append("\n".join(lines))
        else:
            parts.append("（暂无持久记忆）")

        return "\n\n".join(parts).strip()

    def get(self, name: str) -> MemoryEntry | None:
        file_path = self._find_by_name(name)
        if file_path and file_path.exists():
            return self._read_entry(file_path)
        return None

    def list_by_type(self, memory_type: MemoryType) -> list[MemoryEntry]:
        entries = []
        type_dir = self.base_dir / memory_type.value
        if type_dir.is_dir():
            for f in sorted(type_dir.glob("*.md")):
                if f.name.startswith("_"):
                    continue
                entry = self._read_entry(f)
                if entry:
                    entries.append(entry)
        return entries

    def search_by_embedding(self, query: str, top_k: int = 20) -> list[tuple[MemoryEntry, float]]:
        """Search memories by embedding cosine similarity."""
        from app.config import settings
        engine = _get_embedding_engine()
        emb_store = _get_embedding_store(self.base_dir)
        query_emb = engine.embed_query(query)
        results = emb_store.search(query_emb, threshold=settings.embedding_recall_threshold, top_k=top_k)
        entries = []
        for name, score in results:
            entry = self.get(name)
            if entry:
                entries.append((entry, score))
        return entries

    # ===== Write =====

    def save(self, entry: MemoryEntry) -> Path:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        entry.updated = now
        if not entry.created:
            entry.created = now

        file_path = self.base_dir / entry.file_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(entry.to_markdown(), encoding="utf-8")

        # Update embedding
        self._update_embedding(entry)

        self._upsert_index(entry)
        if entry.type != MemoryType.USER:
            self._update_type_index(entry)

        logger.info("Saved memory: %s", entry.name)
        return file_path

    def merge_entry(self, existing_name: str, new_content: str) -> Path:
        entry = self.get(existing_name)
        if not entry:
            raise ValueError(f"Memory not found: {existing_name}")
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        entry.updated = now
        entry.content = entry.content.strip() + f"\n\n_更新于 {now}_\n\n{new_content.strip()}"
        entry.importance = min(1.0, entry.importance + 0.05)
        return self.save(entry)

    def delete(self, name: str) -> bool:
        file_path = self._find_by_name(name)
        if not file_path or not file_path.exists():
            return False
        file_path.unlink()
        self._remove_from_index(name)
        # Remove embedding
        emb_store = _get_embedding_store(self.base_dir)
        emb_store.remove(name)
        logger.info("Deleted memory: %s", name)
        return True

    def rebuild_embeddings(self):
        engine = _get_embedding_engine()
        emb_store = _get_embedding_store(self.base_dir)
        entries = self.load_index()
        emb_store.rebuild_all(engine, entries)

    # ===== Embedding =====

    def _update_embedding(self, entry: MemoryEntry):
        try:
            engine = _get_embedding_engine()
            emb_store = _get_embedding_store(self.base_dir)
            text = f"{entry.description}\n{entry.content}"
            emb = engine.embed_query(text)
            emb_store.put(entry.name, emb)
        except Exception as e:
            logger.warning("Failed to update embedding for %s: %s", entry.name, e)

    def _rank_by_embedding(self, query: str, entries: list[MemoryEntry], top_k: int) -> list[MemoryEntry]:
        from app.config import settings
        engine = _get_embedding_engine()
        emb_store = _get_embedding_store(self.base_dir)
        query_emb = engine.embed_query(query)
        scored = []
        for entry in entries:
            cached = emb_store.get(entry.name)
            sim = engine.cosine_similarity(query_emb, cached) if cached else 0.0
            scored.append((sim, entry))
        scored.sort(key=lambda x: x[0], reverse=True)
        top = [e for s, e in scored[:top_k] if s >= settings.embedding_recall_threshold]

        # Track recall counts for decay avoidance
        for entry in top:
            emb_store.increment_recall(entry.name)

        return top

    # ===== Index management =====

    def _upsert_index(self, entry: MemoryEntry):
        index_file = self.base_dir / "MEMORY.md"
        current_lines = (
            index_file.read_text(encoding="utf-8").split("\n")
            if index_file.exists()
            else ["# 书童记忆索引\n", "\n"]
        )
        new_line = entry.index_line
        name_pattern = f"[{entry.name}]"
        replaced = False
        for i, line in enumerate(current_lines):
            if name_pattern in line:
                current_lines[i] = new_line
                replaced = True
                break
        if not replaced:
            type_header = self._type_header(entry.type)
            inserted = False
            for i, line in enumerate(current_lines):
                if line.strip() == type_header:
                    j = i + 1
                    while j < len(current_lines) and current_lines[j].strip().startswith("- "):
                        j += 1
                    current_lines.insert(j, new_line)
                    inserted = True
                    break
            if not inserted:
                current_lines.append(f"\n## {type_header}")
                current_lines.append(new_line)
        index_file.write_text("\n".join(current_lines), encoding="utf-8")

    def _remove_from_index(self, name: str):
        index_file = self.base_dir / "MEMORY.md"
        if not index_file.exists():
            return
        lines = index_file.read_text(encoding="utf-8").split("\n")
        name_pattern = f"[{name}]"
        lines = [l for l in lines if name_pattern not in l]
        index_file.write_text("\n".join(lines), encoding="utf-8")

    def check_and_compact(self):
        index_file = self.base_dir / "MEMORY.md"
        if not index_file.exists():
            return
        lines = index_file.read_text(encoding="utf-8").split("\n")
        entry_lines = [l for l in lines if l.strip().startswith("- [") and "](" in l]
        if len(entry_lines) <= INDEX_COMPACT_THRESHOLD:
            return
        logger.info("Index at %d entries, compacting...", len(entry_lines))
        entries = self.load_index()
        entries.sort(key=lambda e: e.importance, reverse=True)
        keep_names = set()
        for i, entry in enumerate(entries):
            if i < INDEX_COMPACT_THRESHOLD * 0.8:
                keep_names.add(entry.name)
            elif entry.importance >= 0.6:
                keep_names.add(entry.name)
        kept = [e for e in entries if e.name in keep_names]
        self._rebuild_index(kept)
        logger.info("Compacted index from %d to %d entries", len(entries), len(kept))

    def _rebuild_index(self, entries: list[MemoryEntry]):
        lines = ["# 书童记忆索引\n"]
        for t in MemoryType:
            type_entries = [e for e in entries if e.type == t]
            if type_entries:
                type_entries.sort(key=lambda e: e.importance, reverse=True)
                lines.append(f"\n## {self._type_header(t)}")
                for e in type_entries:
                    lines.append(e.index_line)
        (self.base_dir / "MEMORY.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _scan_and_rebuild_index(self) -> list[MemoryEntry]:
        entries = []
        for md_file in self.base_dir.rglob("*.md"):
            if md_file.name.startswith(("MEMORY.md", "_index")):
                continue
            entry = self._read_entry(md_file)
            if entry:
                entries.append(entry)
        self._rebuild_index(entries)
        return entries

    def _update_type_index(self, entry: MemoryEntry):
        type_index = self.base_dir / entry.type.value / "_index.md"
        entries = self.list_by_type(entry.type)
        entries.sort(key=lambda e: e.importance, reverse=True)
        lines = [f"# {self._type_header(entry.type)} 记忆索引\n"]
        for e in entries:
            lines.append(e.index_line)
        type_index.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _find_by_name(self, name: str) -> Path | None:
        if name == "user":
            candidate = self.base_dir / "user.md"
            return candidate if candidate.exists() else None
        for t in MemoryType:
            if t == MemoryType.USER:
                continue
            candidate = self.base_dir / t.value / f"{name}.md"
            if candidate.exists():
                return candidate
        return None

    def _read_entry(self, file_path: Path) -> MemoryEntry | None:
        try:
            raw = file_path.read_text(encoding="utf-8")
            return MemoryEntry.from_markdown(file_path, raw)
        except Exception as e:
            logger.warning("Failed to read memory %s: %s", file_path, e)
            return None

    @staticmethod
    def _type_header(memory_type: MemoryType) -> str:
        return {
            MemoryType.USER: "User",
            MemoryType.FEEDBACK: "Feedback",
            MemoryType.PROJECT: "Project",
            MemoryType.REFERENCE: "Reference",
        }.get(memory_type, str(memory_type.value))
