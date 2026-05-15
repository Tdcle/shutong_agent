"""Embedding engine — API-based (DashScope compatible), with optional local fallback.

Uses the same OpenAI-compatible endpoint as the LLM. Zero additional setup.
"""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class EmbeddingEngine:
    """Compute embeddings. Auto-detects: HuggingFace models (with '/') run locally via
    sentence-transformers; plain names use the API (DashScope compatible endpoint)."""

    def __init__(self, model_name: str = "text-embedding-v3"):
        self.model_name = model_name
        self._client = None
        self._local_model = None
        # Models with '/' are HuggingFace paths → local; plain names → API
        self._is_local = "/" in model_name

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI
            from app.config import settings
            self._client = OpenAI(api_key=settings.llm_api_key, base_url=settings.llm_base_url)
        return self._client

    def _get_local_model(self):
        if self._local_model is None:
            from sentence_transformers import SentenceTransformer
            logger.info("Loading local embedding model: %s (first use downloads ~100MB)...", self.model_name)
            self._local_model = SentenceTransformer(self.model_name)
            try:
                dim = self._local_model.get_embedding_dimension()
            except AttributeError:
                dim = self._local_model.get_sentence_embedding_dimension()
            logger.info("Local embedding model ready, dim=%d", dim)
        return self._local_model

    def embed(self, texts: list[str]) -> list[list[float]]:
        if self._is_local:
            return self._embed_local(texts)
        return self._embed_api(texts)

    def _embed_local(self, texts: list[str]) -> list[list[float]]:
        model = self._get_local_model()
        embeddings = model.encode(texts, show_progress_bar=False, normalize_embeddings=True)
        return embeddings.tolist()

    def _embed_api(self, texts: list[str]) -> list[list[float]]:
        client = self._get_client()
        try:
            resp = client.embeddings.create(model=self.model_name, input=texts)
            embeddings = sorted(resp.data, key=lambda d: d.index)
            return [e.embedding for e in embeddings]
        except Exception as e:
            logger.error("Embedding API failed: %s", e)
            raise

    def embed_query(self, text: str) -> list[float]:
        results = self.embed([text])
        return results[0]

    @staticmethod
    def cosine_similarity(a: list[float], b: list[float]) -> float:
        """Cosine similarity between two vectors (assumes normalized or handles raw)."""
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(y * y for y in b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)


class EmbeddingStore:
    """Persists embeddings alongside memory files in {memory_dir}/embeddings.json."""

    def __init__(self, memory_dir: Path):
        self.memory_dir = Path(memory_dir)
        self.file_path = self.memory_dir / "embeddings.json"
        self.model_name = "embedding-v1"

    def _load(self) -> dict:
        if not self.file_path.exists():
            return {"model": self.model_name, "entries": {}}
        try:
            return json.loads(self.file_path.read_text("utf-8"))
        except (json.JSONDecodeError, IOError):
            return {"model": self.model_name, "entries": {}}

    def _save(self, data: dict):
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.file_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")

    def get(self, name: str) -> list[float] | None:
        data = self._load()
        entry = data["entries"].get(name)
        return entry["embedding"] if entry else None

    def get_meta(self, name: str) -> dict | None:
        """Get full metadata (embedding + recall_count) for a memory entry."""
        data = self._load()
        return data["entries"].get(name)

    def increment_recall(self, name: str):
        """Track that this memory was recalled (used for decay avoidance)."""
        data = self._load()
        entry = data["entries"].get(name)
        if entry:
            entry["recall_count"] = entry.get("recall_count", 0) + 1
            self._save(data)

    def put(self, name: str, embedding: list[float]):
        data = self._load()
        from datetime import datetime, timezone
        entry = data["entries"].get(name, {})
        entry["embedding"] = embedding
        entry["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        entry.setdefault("recall_count", 0)
        data["entries"][name] = entry
        self._save(data)

    def remove(self, name: str):
        data = self._load()
        data["entries"].pop(name, None)
        self._save(data)

    def search(self, query_embedding: list[float], threshold: float = 0.3,
               top_k: int = 20) -> list[tuple[str, float]]:
        """Return memory names ranked by cosine similarity above threshold."""
        data = self._load()
        scored = []
        for name, entry in data["entries"].items():
            emb = entry.get("embedding")
            if not emb:
                continue
            sim = EmbeddingEngine.cosine_similarity(query_embedding, emb)
            if sim >= threshold:
                scored.append((name, sim))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]

    def rebuild_all(self, engine: EmbeddingEngine, entries: list):
        """Rebuild all embeddings from scratch (e.g., after model change)."""
        data = {"model": self.model_name, "entries": {}}
        texts = []
        names = []
        for e in entries:
            texts.append(f"{e.description}\n{e.content}")
            names.append(e.name)

        if texts:
            logger.info("Computing embeddings for %d memories...", len(texts))
            embeddings = engine.embed(texts)
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
            for name, emb in zip(names, embeddings):
                data["entries"][name] = {"embedding": emb, "updated": now}

        self._save(data)
        logger.info("Rebuilt %d embeddings", len(names))
