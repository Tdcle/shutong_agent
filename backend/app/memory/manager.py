"""Memory Manager — orchestrates L0-L3 memory layers with embedding-based recall and LLM-based dedup."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.config import settings
from app.memory.short_term import ShortTermMemory
from app.memory.store import FileMemoryStore
from app.memory.types import MemoryEntry, MemoryType

logger = logging.getLogger(__name__)


class MemoryManager:
    def __init__(self, llm: ChatOpenAI | None = None):
        self.llm = llm or self._default_llm()
        self.compress_llm = self._default_compress_llm()
        self.global_store = FileMemoryStore(settings.memory_global_dir)
        self._sessions: dict[str, ShortTermMemory] = {}
        self._turn_counters: dict[str, int] = {}
        self._last_decay_check: str = ""  # ISO date of last decay run

    @staticmethod
    def _default_llm() -> ChatOpenAI:
        return ChatOpenAI(
            model=settings.llm_model, api_key=settings.llm_api_key,
            base_url=settings.llm_base_url, temperature=0.3,
            max_tokens=settings.llm_max_tokens,
        )

    @staticmethod
    def _default_compress_llm() -> ChatOpenAI:
        """Lightweight model for summarization — same endpoint, cheaper model."""
        return ChatOpenAI(
            model=settings.llm_compress_model, api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            temperature=settings.llm_compress_temperature,
            max_tokens=1024,
        )

    # ===== L0 Working Memory =====

    def get_session_memory(self, session_id: str) -> ShortTermMemory:
        if session_id not in self._sessions:
            self._sessions[session_id] = ShortTermMemory(
                session_id=session_id, max_messages=settings.short_term_max_messages,
                char_limit=settings.context_char_limit,
            )
        return self._sessions[session_id]

    def remove_session(self, session_id: str):
        self._sessions.pop(session_id, None)

    def apply_decay(self):
        """Decay importance of idle memories. Called periodically after extraction.

        Memories with recall_count >= threshold are "active" → no decay.
        Others lose importance gradually.
        """
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self._last_decay_check == today:
            return
        self._last_decay_check = today

        from app.memory.store import _get_embedding_store
        emb_store = _get_embedding_store(settings.memory_global_dir)
        entries = self.global_store.load_index()
        decayed = 0
        for entry in entries:
            meta = emb_store.get_meta(entry.name)
            recall_count = meta.get("recall_count", 0) if meta else 0

            # Active memories (frequently recalled) don't decay
            if recall_count >= settings.memory_decay_recall_threshold:
                continue

            new_imp = max(0.0, entry.importance - settings.memory_decay_rate)
            if abs(new_imp - entry.importance) > 0.001:
                entry.importance = round(new_imp, 3)
                # Resave with decayed importance
                self.global_store._update_embedding(entry)
                decayed += 1

        if decayed:
            logger.info("Decayed %d idle memories", decayed)
            self.global_store.check_and_compact()

    # ===== L1 Prompt Memory (embedding-filtered) =====

    def load_l1_context(self, project_dir: Path | None = None, query: str | None = None) -> str:
        parts = []
        if project_dir and project_dir.exists():
            context_file = project_dir / ".shutong" / "CONTEXT.md"
            if context_file.exists():
                parts.append(f"## 当前项目上下文\n{context_file.read_text('utf-8').strip()}")

        global_context = self.global_store.load_l1_context(query)
        if global_context:
            parts.append(global_context)

        return "\n\n".join(parts).strip()

    # ===== L2 Persistent Memory =====

    def search_memories(self, query: str, project_dir: Path | None = None) -> list[dict]:
        results = []
        for entry, score in self.global_store.search_by_embedding(query):
            results.append({"name": entry.name, "description": entry.description,
                           "type": entry.type.value, "importance": entry.importance,
                           "similarity": round(score, 4), "source": "global"})
        return results

    def get_memory_content(self, name: str, project_dir: Path | None = None) -> str | None:
        if project_dir:
            pm_dir = project_dir / ".shutong" / "memory"
            if pm_dir.exists():
                project_store = FileMemoryStore(pm_dir)
                entry = project_store.get(name)
                if entry:
                    return entry.to_markdown()
        entry = self.global_store.get(name)
        return entry.to_markdown() if entry else None

    def save_memory(self, name: str, description: str, content: str,
                    memory_type: str = "feedback", importance: float = 0.5,
                    project_dir: Path | None = None) -> str:
        store = self.global_store
        if project_dir and memory_type == "project":
            store = FileMemoryStore(project_dir / ".shutong" / "memory")

        # LLM-based dedup happens in _extract_memories via the prompt.
        # Here we just check: if the name already exists, merge; otherwise create.
        existing = store.get(name)
        if existing:
            logger.info("Merging into existing memory '%s'", name)
            store.merge_entry(name, content)
            return str(existing.file_path)

        entry = MemoryEntry(name=name, description=description,
                          type=MemoryType(memory_type),
                          importance=importance, content=content)
        file_path = store.save(entry)
        store.check_and_compact()
        return str(file_path)

    def delete_memory(self, name: str):
        self.global_store.delete(name)

    def list_memories(self, memory_type: str | None = None) -> list[dict]:
        if memory_type:
            entries = self.global_store.list_by_type(MemoryType(memory_type))
        else:
            entries = self.global_store.load_index()
        return [{"name": e.name, "description": e.description, "type": e.type.value,
                 "importance": e.importance, "updated": e.updated} for e in entries]

    def get_profile(self) -> dict:
        entry = self.global_store.get("user")
        return {"content": entry.content, "name": entry.name} if entry else {}

    # ===== Post-turn extraction =====

    async def post_turn(self, session_id: str, user_msg: str, assistant_msg: str):
        short_term = self.get_session_memory(session_id)
        short_term.add(HumanMessage(content=user_msg))
        short_term.add(AIMessage(content=assistant_msg))

        # ── 压缩：用轻量模型，独立于提取 ──
        if settings.memory_auto_summarize and short_term.should_summarize():
            await short_term.summarize_with(self.compress_llm)

        # ── 提取：独立触发 ──
        self._turn_counters[session_id] = self._turn_counters.get(session_id, 0) + 1
        mode = settings.memory_extract_mode
        should_extract = False
        if mode == "every_turn":
            should_extract = True
        elif mode == "on_compress":
            if self._turn_counters[session_id] >= settings.memory_extract_every_n_turns:
                should_extract = True
                self._turn_counters[session_id] = 0
        # "manual": never

        if should_extract:
            await self._extract_memories(user_msg, assistant_msg)

    async def _extract_memories(self, _label: str, dialogue: str):
        """LLM extracts memories and decides whether to merge or create new ones."""
        # Build list of existing memories so LLM can decide
        existing_list = self._build_existing_list()
        existing_hint = ""
        if existing_list:
            existing_hint = f"""## 已有记忆（如果信息与已有记忆同一主题，请用已有名称更新它）
{existing_list}

"""

        prompt = f"""{existing_hint}分析以下对话，提取值得长期记忆的信息。返回 JSON 数组，每条包含：
- name: 英文kebab-case标识。若信息与已有记忆属于**同一主题**，请复用已有名称（后续会自动合并到该文件）
- description: 一句话概述
- type: user/feedback/project/reference
- content: 完整记忆内容（结构化Markdown，直接可写入文件）
- importance: 0.0-1.0

user: 姓名、职业、偏好
feedback: 用户反馈、行为偏好、代码风格
project: 项目信息、任务
reference: 外部链接、API

格式: [{{"name": "...", "description": "...", "type": "feedback", "content": "..."}}]
无则返回 []。"""

        try:
            resp = await self.llm.ainvoke([
                {"role": "system", "content": prompt},
                {"role": "user", "content": dialogue},
            ])
            text = str(resp.content).strip()
            match = re.search(r"\[.*\]", text, re.DOTALL)
            if not match:
                return

            items = json.loads(match.group())
            saved, merged = 0, 0
            for item in items:
                if not item.get("name") or not item.get("content"):
                    continue
                name = item["name"]
                # Check if name already exists → merge, otherwise create
                existing = self.global_store.get(name)
                if existing:
                    self.global_store.merge_entry(name, item["content"])
                    merged += 1
                else:
                    entry = MemoryEntry(
                        name=name, description=item.get("description", ""),
                        type=MemoryType(item.get("type", "feedback")),
                        importance=item.get("importance", 0.5),
                        content=item["content"],
                    )
                    self.global_store.save(entry)
                    saved += 1

            if saved or merged:
                logger.info("Extraction: %d new, %d merged", saved, merged)
                self.global_store.check_and_compact()

            # Run decay check periodically
            self.apply_decay()
        except Exception as e:
            logger.warning("Memory extraction failed: %s", e)

    def _build_existing_list(self) -> str:
        """Build a compact list of existing memories for the LLM dedup prompt."""
        entries = self.global_store.load_index()
        if not entries:
            return ""
        lines = []
        for e in entries:
            lines.append(f"- [{e.name}] ({e.type.value}): {e.description}")
        return "\n".join(lines)
