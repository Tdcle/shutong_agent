"""Configuration profiles — dev uses local Ollama, prod uses user-configured API."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Profile:
    """Base profile."""
    name: str = "prod"

    # API endpoint
    llm_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    llm_temperature: float = 0.7
    llm_max_tokens: int = 4096

    # Search backend: "ddgs" (free) or "bocha" (needs API key)
    search_backend: str = "ddgs"

    llm_model: str = "qwen-plus"  # DashScope default, user can override via frontend

    # Whether this profile requires user API key configuration
    requires_user_config: bool = True


@dataclass
class DevProfile(Profile):
    """Development profile — local Ollama, zero config needed."""
    name: str = "dev"

    llm_base_url: str = "http://localhost:11434/v1"
    llm_model: str = "qwen3:8b"
    search_backend: str = "ddgs"
    requires_user_config: bool = False


PROFILES: dict[str, Profile] = {
    "dev": DevProfile(),
    "prod": Profile(),
}


def get_profile(name: str) -> Profile:
    return PROFILES.get(name, DevProfile())
