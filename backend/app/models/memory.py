"""User profile helpers — backed by ~/.shutong/memory/user.md"""

from app.config import MEMORY_GLOBAL_DIR


def get_user_file() -> str:
    """Return path to user.md, creating default if missing."""
    MEMORY_GLOBAL_DIR.mkdir(parents=True, exist_ok=True)
    user_file = MEMORY_GLOBAL_DIR / "user.md"
    if not user_file.exists():
        user_file.write_text("""---
name: user
description: 用户画像
type: user
importance: 1.0
links: []
---

## 关于我

（请在此填写你的信息，agent 会在对话中自动更新）

- 技术栈：
- 职业：
- 偏好：
- 其他：
""", encoding="utf-8")
    return str(user_file)
