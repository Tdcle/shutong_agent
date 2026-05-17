"""User configuration API — manage LLM credentials and model selection."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/config", tags=["config"])

CONFIG_PATH = Path(__file__).parent.parent.parent / "data" / "user_config.json"


class UserConfig(BaseModel):
    dashscope_api_key: str = Field(default="", description="阿里云 DashScope API Key")
    text_model: str = Field(default="qwen-plus", description="文本对话模型名称")
    vision_model: str = Field(default="qwen-vl-plus", description="多模态视觉模型名称")
    bocha_api_key: str = Field(default="", description="博查搜索 API Key（可选）")


def load_user_config() -> UserConfig:
    """Load user config from disk, return defaults if missing."""
    if not CONFIG_PATH.exists():
        return UserConfig()
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return UserConfig(**data)
    except Exception as e:
        logger.warning("Failed to load user config: %s", e)
        return UserConfig()


def save_user_config(config: UserConfig):
    """Persist user config to disk."""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        config.model_dump_json(indent=2, exclude_none=True),
        encoding="utf-8",
    )


def apply_user_config():
    """Apply saved user config to runtime settings (called on startup)."""
    config = load_user_config()
    changed = False

    from app.config import settings

    if config.dashscope_api_key:
        settings.llm_api_key = config.dashscope_api_key
        changed = True
    if config.text_model:
        settings.llm_model = config.text_model
        changed = True
    # vision_model is applied per-request when multimodal content is detected
    if config.bocha_api_key:
        settings.bocha_api_key = config.bocha_api_key
        changed = True

    if changed:
        logger.info(
            "User config applied: model=%s, dashscope_key=%s, bocha_key=%s",
            settings.llm_model,
            "***" if settings.llm_api_key != "ollama" else "ollama",
            "***" if settings.bocha_api_key else "(not set)",
        )


@router.get("")
async def get_config():
    """Return current user config (mask sensitive fields)."""
    config = load_user_config()
    return {
        "dashscope_api_key": mask_key(config.dashscope_api_key),
        "text_model": config.text_model,
        "vision_model": config.vision_model,
        "bocha_api_key": mask_key(config.bocha_api_key),
    }


@router.put("")
async def update_config(config: UserConfig):
    """Save user config and apply to runtime."""
    # Merge with existing: if a field is empty, keep the old value
    if not config.text_model:
        return {"error": "text_model is required"}
    if not config.dashscope_api_key or config.dashscope_api_key.startswith("***"):
        # User didn't change the key — keep existing
        existing = load_user_config()
        config.dashscope_api_key = existing.dashscope_api_key
    if config.bocha_api_key and config.bocha_api_key.startswith("***"):
        existing = load_user_config()
        config.bocha_api_key = existing.bocha_api_key

    save_user_config(config)
    apply_user_config()
    logger.info("User config updated: model=%s", config.text_model)
    return {"ok": True}


@router.delete("")
async def reset_config():
    """Reset user config to defaults."""
    if CONFIG_PATH.exists():
        CONFIG_PATH.unlink()
    return {"ok": True}


def mask_key(key: str) -> str:
    if not key or key == "ollama":
        return key
    if len(key) <= 8:
        return "***"
    return key[:4] + "***" + key[-4:]
