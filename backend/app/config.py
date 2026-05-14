from __future__ import annotations

from pathlib import Path
from pydantic_settings import BaseSettings

from app.profiles import get_profile

# 数据目录
DATA_DIR = Path(__file__).parent.parent / "data"

# 记忆目录
MEMORY_GLOBAL_DIR = Path.home() / ".shutong" / "memory"

# 当前 profile
_current_profile = get_profile()


class Settings(BaseSettings):
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    # ===== 运行模式 =====
    app_profile: str = "dev"  # "dev" | "prod"

    # ===== 密钥 =====
    llm_api_key: str = "ollama"  # dev 不需要真实 key，prod 从 .env 覆盖

    # ===== LLM（默认值来自 profile）=====
    llm_model: str = _current_profile.llm_model
    llm_base_url: str = _current_profile.llm_base_url
    llm_temperature: float = _current_profile.llm_temperature
    llm_max_tokens: int = _current_profile.llm_max_tokens

    # 压缩模型
    llm_compress_model: str = _current_profile.llm_compress_model
    llm_compress_temperature: float = _current_profile.llm_compress_temperature

    # ===== Embedding（始终本地运行）=====
    embedding_model: str = "BAAI/bge-small-zh-v1.5"
    embedding_recall_threshold: float = 0.3

    # ===== 搜索 =====
    search_backend: str = _current_profile.search_backend
    bocha_api_key: str = ""

    # ===== SQLite =====
    @property
    def sqlite_url(self) -> str:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        return f"sqlite+aiosqlite:///{DATA_DIR / 'agent.db'}"

    # ===== 记忆路径 =====
    @property
    def memory_global_dir(self) -> Path:
        MEMORY_GLOBAL_DIR.mkdir(parents=True, exist_ok=True)
        return MEMORY_GLOBAL_DIR

    # ===== Agent 参数 =====
    max_agent_rounds: int = 10
    max_tool_retries: int = 2
    context_char_limit: int = 20000
    tool_concurrency: int = 3

    # ===== Skill 目录 =====
    skills_dir: str = str(Path(__file__).parent.parent / "skills")

    # ===== 记忆参数 =====
    short_term_max_messages: int = 30
    memory_auto_summarize: bool = True
    memory_auto_extract: bool = True
    memory_extract_mode: str = "on_compress"
    memory_extract_every_n_turns: int = 10
    memory_l1_max_entries: int = 10

    # 衰减
    memory_decay_rate: float = 0.02
    memory_decay_min_importance: float = 0.15
    memory_decay_recall_threshold: int = 2

    # ===== Computer Use =====
    shell_timeout_seconds: int = 120


settings = Settings()
