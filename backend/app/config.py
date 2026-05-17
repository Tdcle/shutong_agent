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

    # ===== Embedding =====
    # 模型名含 '/' 使用本地 sentence-transformers；不含 '/' 走 API（Ollama 或 DashScope）
    embedding_model: str = "bge-m3"
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

    # ===== 工作区 =====
    workspaces_base: str = str(Path(__file__).parent.parent / "workspaces")  # session 隔离工作区根目录

    # ===== Sandbox =====
    sandbox_idle_ttl_seconds: int = 1200     # 空闲 20 分钟自动回收
    sandbox_max_lifetime_seconds: int = 3600  # 存活总时长 60 分钟自动回收
    agent_python_executable: str = str(Path(__file__).parent.parent / ".agent_runtime" / "Scripts" / "python.exe")
    python_timeout_seconds: int = 120

    # ===== Computer Use =====
    shell_timeout_seconds: int = 120
    shell_block_network: bool = True
    shell_block_dangerous_commands: bool = True
    shell_reject_absolute_paths: bool = False  # No longer hard-block; user approval suffices
    shell_network_proxy_url: str = "http://127.0.0.1:9"
    shell_max_command_length: int = 2000
    shell_allow_nested_shells: bool = False
    shell_blocked_command_tokens: str = (
        "curl,wget,Invoke-WebRequest,irm,iwr,scp,sftp,ftp,telnet,ssh,"
        "powershell -EncodedCommand,pwsh -EncodedCommand,certutil -urlcache,"
        "bitsadmin,regsvr32,rundll32,mshta,wscript,cscript,wmic,netsh,"
        "schtasks,at,shutdown,restart-computer,stop-computer,taskkill,"
        "takeown,icacls,mklink,subst,format,mountvol,"
        "pip,pip3,conda"
    )


settings = Settings()
