"""User configuration API — manage LLM credentials and model selection.

User config only takes effect in prod mode. In dev mode, Ollama defaults are used.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/config", tags=["config"])

CONFIG_PATH = Path(__file__).parent.parent.parent / "data" / "user_config.json"

DEFAULT_DEV_MODEL = "qwen3:8b"


class UserConfig(BaseModel):
    dashscope_api_key: str = Field(default="", description="API Key")
    text_model: str = Field(default="qwen-plus", description="文本模型名称")
    vision_model: str = Field(default="qwen-vl-plus", description="视觉模型名称")
    bocha_api_key: str = Field(default="", description="搜索 API Key（可选）")


def load_user_config() -> UserConfig:
    if not CONFIG_PATH.exists():
        return UserConfig()
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return UserConfig(**data)
    except Exception as e:
        logger.warning("Failed to load user config: %s", e)
        return UserConfig()


def save_user_config(config: UserConfig):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        config.model_dump_json(indent=2, exclude_none=True),
        encoding="utf-8",
    )


def apply_user_config():
    """Apply saved user config to runtime settings (startup hook).

    In dev mode: user config is ignored — always use Ollama defaults.
    In prod mode: user config provides API key and model name.
    """
    from app.config import settings

    profile = settings._resolve_profile()

    if not profile.requires_user_config:
        # Dev mode — ensure Ollama defaults, ignore user config
        logger.info("Dev mode active — using Ollama defaults (model=%s)", settings.llm_model)
        return

    # Prod mode — apply user config
    config = load_user_config()
    settings.llm_api_key = config.dashscope_api_key
    settings.llm_model = config.text_model
    if config.bocha_api_key:
        settings.bocha_api_key = config.bocha_api_key

    logger.info(
        "Prod mode active — user config applied: model=%s, base_url=%s",
        settings.llm_model,
        settings.llm_base_url,
    )


@router.get("")
async def get_config():
    """Return current user config (mask sensitive fields)."""
    from app.config import settings

    config = load_user_config()
    profile = settings._resolve_profile()
    return {
        "dashscope_api_key": mask_key(config.dashscope_api_key),
        "text_model": config.text_model,
        "vision_model": config.vision_model,
        "bocha_api_key": mask_key(config.bocha_api_key),
        "app_profile": settings.app_profile,
        "requires_user_config": profile.requires_user_config,
    }


@router.put("")
async def update_config(config: UserConfig):
    """Save user config and apply to runtime."""
    from app.config import settings

    if not config.text_model:
        return {"error": "text_model is required"}

    profile = settings._resolve_profile()
    if not profile.requires_user_config:
        return {
            "ok": True,
            "note": "当前为开发模式，配置已保存但不会生效。切换到 prod 模式后配置生效。",
        }

    # Merge API key: keep existing if masked or empty
    if not config.dashscope_api_key or "***" in config.dashscope_api_key:
        existing = load_user_config()
        config.dashscope_api_key = existing.dashscope_api_key
    if not config.bocha_api_key or "***" in config.bocha_api_key:
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
