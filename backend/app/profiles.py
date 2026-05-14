"""Configuration profiles for one-click switch between dev (local/free) and prod (API).

Usage:
    # .env
    APP_PROFILE=dev    # Local Ollama + free search
    APP_PROFILE=prod   # DashScope API + Bocha search
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Profile:
    """Production profile."""
    name: str = "prod"

    llm_model: str = "glm-5"
    llm_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    llm_temperature: float = 0.7
    llm_max_tokens: int = 4096

    llm_compress_model: str = "qwen-flash"
    llm_compress_temperature: float = 0.1

    search_backend: str = "bocha"


@dataclass
class DevProfile(Profile):
    """Development profile — local Ollama, free search."""
    name: str = "dev"

    llm_model: str = "qwen3:8b"
    llm_base_url: str = "http://localhost:11434/v1"

    llm_compress_model: str = "qwen3:8b"

    search_backend: str = "ddgs"


PROFILES: dict[str, Profile] = {
    "dev": DevProfile(),
    "prod": Profile(),
}


def get_profile(name: str | None = None) -> Profile:
    if name is None:
        import os
        name = os.getenv("APP_PROFILE", "dev")
    return PROFILES.get(name, DevProfile())
